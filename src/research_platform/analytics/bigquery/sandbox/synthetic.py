"""Deterministic synthetic OpenAlex cycle for the #103 BigQuery sandbox measure.

One cycle is two publication batches over the same synthetic Works corpus:

- **Batch 1.** Seeded raw gzip JSONL Works assets (default 4 x 2,500 works,
  one distinct ``updated_date`` each, plus one small undated asset for the
  NULL-date edge cases) and one synthetic ``deleted_ids.csv.gz``. Every asset
  goes through the real Step 12/13 path, one asset per run
  (``run_openalex_works_local_ingest`` / ``run_openalex_deletions_local_ingest``
  with ``backend="memory"``), and the resulting canonical snapshot is written
  with the Stage 1 exporter. Loaded into empty targets, every batch-1 decision
  is ``INSERT``.
- **Batch 2.** ``compose_delta_staging`` ingests a second set of raw assets into
  a fresh in-memory store (so the analytical MERGE, not the canonical store,
  arbitrates precedence) and adds verbatim replays of batch-1 rows. The result
  is a ``works_staging`` / ``work_topics_staging`` pair that hits every
  ``PublicationDecision``: INSERT, IDENTICAL, APPLY_UPDATE (including a DELETED
  tombstone dated the same as its ACTIVE target), STALE, CONFLICT and
  RESTORE_REQUIRED, plus the NULL-source-date (STALE) and both-dates-NULL
  (CONFLICT) edge cases. Expected decisions come from
  ``contracts.decide_analytical_works_merge`` and are labelled SEMANTIC_ONLY.

Determinism: record content comes from string-seeded ``random.Random``
instances, gzip members carry ``mtime=0``, the ingest clock is a fixed step
clock and pipeline run ids are pinned from the seed, so the same seed, scale
and anchor date reproduce every file byte for byte (under the same Python and
PyArrow versions, which the manifest records).

Synthetic identifiers use a reserved range (``SYNTHETIC_ID_BASE``, 13 digits)
far above real OpenAlex short ids. DOIs use the ``10.5555`` test prefix, URLs
use ``example.org``, and no ORCID, ROR or ISSN values are generated, so no
synthetic value can name a real person, organization or serial.

Outputs are derived data: write them outside the repository or under the
git-ignored ``data/sample/``. Nothing here touches the network, GCP or BigQuery.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import platform
import random
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, BinaryIO, Final, Literal, Self
from uuid import UUID, uuid5

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import yaml
from pydantic import Field, model_validator

from research_platform.analytics.bigquery.contracts import (
    PublicationDecision,
    ValidationLabel,
    decide_analytical_works_merge,
    is_accepted_publication_decision,
    is_relationship_refresh_eligible,
)
from research_platform.canonical.memory import CanonicalSnapshot, InMemoryCanonicalStore
from research_platform.canonical.openalex.export import (
    PARQUET_COMPRESSION,
    PARQUET_FORMAT_VERSION,
    CanonicalParquetExport,
    canonical_table_to_arrow,
    export_snapshot_to_parquet,
    read_canonical_parquet,
    validate_canonical_arrow_table,
    write_canonical_parquet,
)
from research_platform.canonical.openalex.schemas import CANONICAL_SCHEMAS
from research_platform.config.models import SettingsModel, StorageConfig
from research_platform.control.memory import InMemoryControlStore
from research_platform.control.models import PipelineRun, PipelineRunStatus
from research_platform.ingestion.decode_limits import JsonlDecodeLimits
from research_platform.ingestion.deletion_limits import CsvDeletionDecodeLimits
from research_platform.ingestion.deletion_pipeline import (
    run_openalex_deletions_local_ingest,
)
from research_platform.ingestion.pipeline import run_openalex_works_local_ingest
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.deletion_metadata import (
    DELETION_PUBLIC_URI,
    OpenAlexDeletionAssetMetadata,
)
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
from research_platform.storage.local import LocalObjectStore

__all__ = [
    "DEFAULT_ANCHOR_DATE",
    "DEFAULT_SCALE",
    "DEFAULT_SEED",
    "DELTA_CASE_DECISIONS",
    "DeltaCase",
    "DeltaStaging",
    "EXPECTED_DECISIONS_FILE_NAME",
    "EXPECTED_DECISIONS_SCHEMA",
    "LARGE_SCALE",
    "MANIFEST_FILE_NAME",
    "MAX_RAW_ASSET_BYTES",
    "NullTopicInjection",
    "SYNTHETIC_GENERATOR_VERSION",
    "SYNTHETIC_ID_BASE",
    "SYNTHETIC_SCALES",
    "SyntheticCycleError",
    "SyntheticCyclePlan",
    "SyntheticCycleResult",
    "SyntheticIngestRun",
    "SyntheticRawAsset",
    "SyntheticScale",
    "TINY_SCALE",
    "WORK_TOPICS_STAGING_INJECTION_SCHEMA",
    "build_batch1_raw_assets",
    "build_batch2_raw_assets",
    "check_raw_asset_limits",
    "compose_delta_staging",
    "ingest_synthetic_assets",
    "inject_null_topic_id",
    "is_synthetic_id",
    "main",
    "plan_synthetic_cycle",
    "resolve_scale",
    "synthetic_id",
    "write_synthetic_cycle",
]

# Bump when generated content changes for the same seed/scale/anchor.
SYNTHETIC_GENERATOR_VERSION: Final = 1
DEFAULT_SEED: Final = 103
# Lineage dates are offsets from this anchor. Pass a recent anchor for a live
# run so DAY partitions on lineage_source_updated_date are recent; the default
# is fixed so the default cycle is reproducible.
DEFAULT_ANCHOR_DATE: Final = date(2026, 10, 1)

# Real OpenAlex short ids have at most 10 digits today (W ~4.5e9). Every
# synthetic entity id is <prefix><SYNTHETIC_ID_BASE + n>: 13 digits, starting
# at 9.9e12, far above that range.
SYNTHETIC_ID_BASE: Final = 9_900_000_000_000
_SYNTHETIC_ID_CEILING: Final = 10_000_000_000_000

# Step 12/13 local ingestion refuses configs above this compressed bound.
MAX_RAW_ASSET_BYTES: Final = 25_000_000

MANIFEST_FILE_NAME: Final = "manifest.json"
EXPECTED_DECISIONS_FILE_NAME: Final = "expected_decisions.json"
MANIFEST_VERSION: Final = 1

_RAW_DIR: Final = "raw"
_BATCH1_DIR: Final = "batch1"
_BATCH2_DIR: Final = "batch2"
_INJECTION_DIR: Final = "injection"
_WORKS_STAGING_FILE: Final = "works_staging.parquet"
_WORK_TOPICS_STAGING_FILE: Final = "work_topics_staging.parquet"
_INJECTION_FILE: Final = "work_topics_staging_null_topic_id.parquet"

_SEED_NAMESPACE: Final = "rip-103-synthetic"
_RUN_ID_NAMESPACE: Final = UUID("6d1f0c2e-1030-4c5b-9a7e-5f1d0b103103")
_LINEAGE_STEP_DAYS: Final = 7
_INGEST_WORKER_ID: Final = "synthetic-worker"

# Mirrors the Stage 1 exporter's pinned writer options; used only for the
# all-nullable injection file, which write_canonical_parquet refuses by design.
_INJECTION_WRITE_OPTIONS: Final[Mapping[str, Any]] = {
    "compression": PARQUET_COMPRESSION,
    "version": PARQUET_FORMAT_VERSION,
    "use_dictionary": True,
    "write_statistics": True,
    "data_page_version": "1.0",
    "coerce_timestamps": "us",
    "allow_truncated_timestamps": False,
    "store_schema": True,
}

AssetKind = Literal["works", "deletions"]


class SyntheticCycleError(ValueError):
    """The synthetic cycle cannot be planned, generated, ingested or written."""


class DeltaCase(StrEnum):
    """Why one batch-2 staging row exists; each maps to one expected decision."""

    INSERT_NEW = "INSERT_NEW"
    IDENTICAL_REPLAY = "IDENTICAL_REPLAY"
    APPLY_UPDATE_NEWER = "APPLY_UPDATE_NEWER"
    APPLY_UPDATE_TOMBSTONE = "APPLY_UPDATE_TOMBSTONE"
    STALE_OLDER = "STALE_OLDER"
    STALE_NULL_SOURCE_DATE = "STALE_NULL_SOURCE_DATE"
    CONFLICT_EQUAL_DATE = "CONFLICT_EQUAL_DATE"
    CONFLICT_NULL_DATES = "CONFLICT_NULL_DATES"
    RESTORE_REQUIRED = "RESTORE_REQUIRED"


DELTA_CASE_DECISIONS: Final[Mapping[DeltaCase, PublicationDecision]] = {
    DeltaCase.INSERT_NEW: PublicationDecision.INSERT,
    DeltaCase.IDENTICAL_REPLAY: PublicationDecision.IDENTICAL,
    DeltaCase.APPLY_UPDATE_NEWER: PublicationDecision.APPLY_UPDATE,
    DeltaCase.APPLY_UPDATE_TOMBSTONE: PublicationDecision.APPLY_UPDATE,
    DeltaCase.STALE_OLDER: PublicationDecision.STALE,
    DeltaCase.STALE_NULL_SOURCE_DATE: PublicationDecision.STALE,
    DeltaCase.CONFLICT_EQUAL_DATE: PublicationDecision.CONFLICT,
    DeltaCase.CONFLICT_NULL_DATES: PublicationDecision.CONFLICT,
    DeltaCase.RESTORE_REQUIRED: PublicationDecision.RESTORE_REQUIRED,
}

EXPECTED_DECISIONS_SCHEMA: Final = pa.schema(
    [
        pa.field("work_id", pa.string(), nullable=False),
        pa.field("delta_case", pa.string(), nullable=False),
        pa.field("source_activity_state", pa.string(), nullable=False),
        pa.field("publication_decision", pa.string(), nullable=False),
        pa.field("accepted", pa.bool_(), nullable=False),
        pa.field("relationship_refresh_eligible", pa.bool_(), nullable=False),
    ]
)

# The planned work_topics_staging DDL is all NULLABLE so a REQUIRED violation
# can be injected at publish time; this file carries exactly that shape.
WORK_TOPICS_STAGING_INJECTION_SCHEMA: Final = pa.schema(
    [item.with_nullable(True) for item in CANONICAL_SCHEMAS["work_topics"]]
)


class SyntheticScale(SettingsModel):
    """Sizes of one synthetic cycle: the batch-1 corpus and the batch-2 delta mix."""

    name: str = Field(default="default", pattern=r"^[a-z][a-z0-9-]{0,31}$")
    batch1_assets: int = Field(default=4, strict=True, ge=1, le=8)
    works_per_asset: int = Field(default=2_500, strict=True, ge=1, le=50_000)
    undated_works: int = Field(default=10, strict=True, ge=1, le=50_000)
    batch1_deletions: int = Field(default=250, strict=True, ge=1, le=100_000)
    insert_new: int = Field(default=200, strict=True, ge=1, le=50_000)
    identical_replay: int = Field(default=200, strict=True, ge=1, le=50_000)
    apply_update_newer: int = Field(default=200, strict=True, ge=1, le=50_000)
    apply_update_tombstone: int = Field(default=100, strict=True, ge=1, le=50_000)
    stale_older: int = Field(default=100, strict=True, ge=1, le=50_000)
    stale_null_source_date: int = Field(default=10, strict=True, ge=1, le=50_000)
    conflict_equal_date: int = Field(default=100, strict=True, ge=1, le=50_000)
    conflict_null_dates: int = Field(default=10, strict=True, ge=1, le=50_000)
    restore_required: int = Field(default=100, strict=True, ge=1, le=50_000)

    @model_validator(mode="after")
    def mix_fits_the_corpus(self) -> Self:
        if self.restore_required > self.batch1_deletions:
            raise ValueError("restore_required needs that many batch-1 deletions")
        if self.conflict_null_dates > self.undated_works:
            raise ValueError("conflict_null_dates needs that many undated batch-1 works")
        if self.conflict_equal_date + self.apply_update_tombstone > self.works_per_asset:
            raise ValueError(
                "conflict_equal_date + apply_update_tombstone must fit in one batch-1 asset"
            )
        demand = (
            self.conflict_equal_date
            + self.apply_update_tombstone
            + self.batch1_deletions
            + self.identical_replay
            + self.apply_update_newer
            + self.stale_older
            + self.stale_null_source_date
        )
        if demand > self.batch1_assets * self.works_per_asset:
            raise ValueError("batch-2 targets and deletions exceed the dated batch-1 works")
        newer = self.insert_new + self.apply_update_newer + self.restore_required
        undated = self.stale_null_source_date + self.conflict_null_dates
        max_records = JsonlDecodeLimits().max_records
        if newer > max_records or undated > max_records:
            raise ValueError("a batch-2 asset would exceed JsonlDecodeLimits.max_records")
        if self.batch1_deletions + self.apply_update_tombstone > CsvDeletionDecodeLimits().max_ids:
            raise ValueError("a deletion asset would exceed CsvDeletionDecodeLimits.max_ids")
        return self

    @property
    def batch1_dated_works(self) -> int:
        return self.batch1_assets * self.works_per_asset

    @property
    def batch1_works(self) -> int:
        return self.batch1_dated_works + self.undated_works

    @property
    def dataset_scale(self) -> str:
        """``BenchmarkResult.dataset_scale`` label for a cycle at this scale."""
        return f"synthetic-{self.batch1_works}-works"

    def case_counts(self) -> dict[DeltaCase, int]:
        return {
            DeltaCase.INSERT_NEW: self.insert_new,
            DeltaCase.IDENTICAL_REPLAY: self.identical_replay,
            DeltaCase.APPLY_UPDATE_NEWER: self.apply_update_newer,
            DeltaCase.APPLY_UPDATE_TOMBSTONE: self.apply_update_tombstone,
            DeltaCase.STALE_OLDER: self.stale_older,
            DeltaCase.STALE_NULL_SOURCE_DATE: self.stale_null_source_date,
            DeltaCase.CONFLICT_EQUAL_DATE: self.conflict_equal_date,
            DeltaCase.CONFLICT_NULL_DATES: self.conflict_null_dates,
            DeltaCase.RESTORE_REQUIRED: self.restore_required,
        }

    @property
    def batch2_works(self) -> int:
        return sum(self.case_counts().values())


DEFAULT_SCALE: Final = SyntheticScale()
TINY_SCALE: Final = SyntheticScale(
    name="tiny",
    works_per_asset=30,
    undated_works=3,
    batch1_deletions=6,
    insert_new=4,
    identical_replay=4,
    apply_update_newer=4,
    apply_update_tombstone=3,
    stale_older=3,
    stale_null_source_date=2,
    conflict_equal_date=3,
    conflict_null_dates=2,
    restore_required=3,
)
# Ten times the default corpus (8 x 12,500 dated works) and delta mix. Each
# dated asset is ~5x a default one, well inside the decode limits. Generation is
# slow: InMemoryCanonicalStore rebuilds its relationship maps on every work
# upsert, so batch-1 ingest grows with the square of the corpus (~13 min at
# default scale, so roughly a day here). The cycle is still synthetic, not a
# representative slice.
LARGE_SCALE: Final = SyntheticScale(
    name="large",
    batch1_assets=8,
    works_per_asset=12_500,
    undated_works=100,
    batch1_deletions=2_500,
    insert_new=2_000,
    identical_replay=2_000,
    apply_update_newer=2_000,
    apply_update_tombstone=1_000,
    stale_older=1_000,
    stale_null_source_date=100,
    conflict_equal_date=1_000,
    conflict_null_dates=100,
    restore_required=1_000,
)
SYNTHETIC_SCALES: Final[Mapping[str, SyntheticScale]] = {
    DEFAULT_SCALE.name: DEFAULT_SCALE,
    TINY_SCALE.name: TINY_SCALE,
    LARGE_SCALE.name: LARGE_SCALE,
}


def resolve_scale(scale: SyntheticScale | str) -> SyntheticScale:
    """Return a ``SyntheticScale`` for an instance or a preset name."""
    if isinstance(scale, SyntheticScale):
        return scale
    preset = SYNTHETIC_SCALES.get(scale)
    if preset is None:
        raise SyntheticCycleError(
            f"unknown synthetic scale {scale!r}; presets: {sorted(SYNTHETIC_SCALES)}"
        )
    return preset


def synthetic_id(prefix: str, number: int) -> str:
    """Short canonical id ``<prefix><SYNTHETIC_ID_BASE + number>`` in the reserved range."""
    if prefix not in {"W", "A", "I", "S", "P", "T", "F"}:
        raise SyntheticCycleError(f"unsupported synthetic id prefix {prefix!r}")
    value = SYNTHETIC_ID_BASE + number
    if number < 1 or value >= _SYNTHETIC_ID_CEILING:
        raise SyntheticCycleError("synthetic id number is outside the reserved range")
    return f"{prefix}{value}"


def is_synthetic_id(short_id: str) -> bool:
    """True when ``short_id`` is an OpenAlex-style id inside the reserved range."""
    if len(short_id) < 2 or not short_id[1:].isdigit():
        return False
    value = int(short_id[1:])
    return SYNTHETIC_ID_BASE < value < _SYNTHETIC_ID_CEILING


def _openalex_url(prefix: str, number: int) -> str:
    return f"https://openalex.org/{synthetic_id(prefix, number)}"


def _rng(seed: int, *parts: object) -> random.Random:
    # String seeds are hashed with SHA-512, so they do not depend on PYTHONHASHSEED.
    return random.Random("|".join([_SEED_NAMESPACE, f"seed={seed}", *map(str, parts)]))


# ---------------------------------------------------------------------------
# Plan: dates, assets and which work plays which batch-2 role.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _EntityPools:
    """Sizes of the shared dimension pools; payloads are pure functions of ids."""

    works: int
    authors: int
    institutions: int
    publishers: int
    sources: int
    topics: int
    funders: int
    keywords: int
    mesh: int

    @classmethod
    def for_scale(cls, scale: SyntheticScale) -> _EntityPools:
        works = scale.batch1_works + scale.insert_new
        return cls(
            works=works,
            authors=max(8, works * 3 // 4),
            institutions=max(4, works // 40),
            publishers=max(2, works // 500),
            sources=max(3, works // 80),
            topics=max(6, min(400, works // 25)),
            funders=max(2, works // 250),
            keywords=max(6, min(300, works // 30)),
            mesh=max(4, min(200, works // 50)),
        )


@dataclass(frozen=True)
class SyntheticCyclePlan:
    """Every deterministic choice of one cycle, derived from seed, scale and anchor."""

    seed: int
    scale: SyntheticScale
    anchor_date: date
    batch1_dates: tuple[date, ...]
    batch1_asset_works: tuple[tuple[int, ...], ...]
    undated_works: tuple[int, ...]
    batch1_deleted_works: tuple[int, ...]
    batch1_deleted_date: date
    equal_date_asset_index: int
    older_date: date
    newer_date: date
    cases: Mapping[DeltaCase, tuple[int, ...]]
    pools: _EntityPools

    @property
    def batch1_snapshot_date(self) -> date:
        return self.anchor_date - timedelta(days=4)

    @property
    def batch1_deletion_snapshot_date(self) -> date:
        return self.anchor_date - timedelta(days=2)

    @property
    def batch2_snapshot_date(self) -> date:
        return self.anchor_date

    @property
    def equal_date(self) -> date:
        return self.batch1_dates[self.equal_date_asset_index]

    def case_work_ids(self, case: DeltaCase) -> tuple[str, ...]:
        return tuple(synthetic_id("W", number) for number in self.cases[case])

    def case_by_work_id(self) -> dict[str, DeltaCase]:
        return {
            work_id: case for case in DeltaCase for work_id in self.case_work_ids(case)
        }

    def run_id(self, label: str) -> UUID:
        """Pinned pipeline run id for one ingest run (reproducible lineage)."""
        return uuid5(
            _RUN_ID_NAMESPACE,
            f"seed={self.seed};anchor={self.anchor_date.isoformat()};run={label}",
        )

    def clock_start(self, batch: int) -> datetime:
        return datetime.combine(self.anchor_date + timedelta(days=batch), time(), tzinfo=UTC)


def plan_synthetic_cycle(
    *,
    seed: int = DEFAULT_SEED,
    scale: SyntheticScale | str = DEFAULT_SCALE,
    anchor_date: date = DEFAULT_ANCHOR_DATE,
) -> SyntheticCyclePlan:
    """Decide dates, asset membership, batch-1 deletions and the batch-2 roles.

    Work numbers ``1..dated`` belong to the dated batch-1 assets, the next
    ``undated_works`` to the undated asset and the next ``insert_new`` are the
    batch-2 INSERTs. Roles are disjoint except RESTORE_REQUIRED, which is a
    subset of the batch-1 deletions by definition.
    """
    resolved = resolve_scale(scale)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise SyntheticCycleError("seed must be an integer")
    count = resolved.batch1_assets
    step = timedelta(days=_LINEAGE_STEP_DAYS)
    batch1_dates = tuple(anchor_date - step * (count - index) for index in range(count))
    per_asset = resolved.works_per_asset
    asset_works = tuple(
        tuple(range(index * per_asset + 1, (index + 1) * per_asset + 1))
        for index in range(count)
    )
    dated = resolved.batch1_dated_works
    undated = tuple(range(dated + 1, dated + resolved.undated_works + 1))
    inserts = tuple(
        range(resolved.batch1_works + 1, resolved.batch1_works + resolved.insert_new + 1)
    )
    equal_index = 1 if count > 1 else 0

    rng = _rng(seed, "plan")
    equal_pool = list(asset_works[equal_index])
    rng.shuffle(equal_pool)
    conflict = equal_pool[: resolved.conflict_equal_date]
    tombstone = equal_pool[
        resolved.conflict_equal_date : resolved.conflict_equal_date
        + resolved.apply_update_tombstone
    ]
    reserved = set(conflict) | set(tombstone)
    rest = [number for number in range(1, dated + 1) if number not in reserved]
    rng.shuffle(rest)

    def take(size: int) -> list[int]:
        nonlocal rest
        taken, rest = rest[:size], rest[size:]
        return taken

    deleted = take(resolved.batch1_deletions)
    identical = take(resolved.identical_replay)
    apply_update = take(resolved.apply_update_newer)
    stale = take(resolved.stale_older)
    stale_null = take(resolved.stale_null_source_date)
    restore = rng.sample(sorted(deleted), resolved.restore_required)
    undated_pool = list(undated)
    rng.shuffle(undated_pool)
    conflict_null = undated_pool[: resolved.conflict_null_dates]

    cases = {
        DeltaCase.INSERT_NEW: inserts,
        DeltaCase.IDENTICAL_REPLAY: identical,
        DeltaCase.APPLY_UPDATE_NEWER: apply_update,
        DeltaCase.APPLY_UPDATE_TOMBSTONE: tombstone,
        DeltaCase.STALE_OLDER: stale,
        DeltaCase.STALE_NULL_SOURCE_DATE: stale_null,
        DeltaCase.CONFLICT_EQUAL_DATE: conflict,
        DeltaCase.CONFLICT_NULL_DATES: conflict_null,
        DeltaCase.RESTORE_REQUIRED: restore,
    }
    return SyntheticCyclePlan(
        seed=seed,
        scale=resolved,
        anchor_date=anchor_date,
        batch1_dates=batch1_dates,
        batch1_asset_works=asset_works,
        undated_works=undated,
        batch1_deleted_works=tuple(sorted(deleted)),
        batch1_deleted_date=anchor_date - timedelta(days=3),
        equal_date_asset_index=equal_index,
        older_date=batch1_dates[0] - step,
        newer_date=anchor_date,
        cases={case: tuple(sorted(numbers)) for case, numbers in cases.items()},
        pools=_EntityPools.for_scale(resolved),
    )


# ---------------------------------------------------------------------------
# Raw assets: seeded OpenAlex-shaped Works JSONL and deletion CSV, gzip mtime=0.
# ---------------------------------------------------------------------------

_WORK_TYPES: Final = (
    "article",
    "book-chapter",
    "review",
    "preprint",
    "dataset",
    "dissertation",
    "book",
    "editorial",
)
_WORK_TYPE_WEIGHTS: Final = (70, 8, 6, 5, 4, 3, 2, 2)
_LANGUAGES: Final = ("en", "de", "fr", "es", "zh", "ja", "pt")
_LANGUAGE_WEIGHTS: Final = (85, 3, 3, 3, 3, 2, 1)
_OA_STATUSES: Final = ("closed", "gold", "green", "hybrid", "bronze", "diamond")
_OA_WEIGHTS: Final = (45, 15, 15, 10, 10, 5)
_LICENSES: Final = ("cc-by", "cc-by-nc", "cc-by-sa", "cc0", None)
_COUNTRIES: Final = ("US", "GB", "DE", "FR", "CN", "JP", "IN", "BR", "CA", "AU")
_INSTITUTION_TYPES: Final = ("education", "healthcare", "company", "government", "facility")
_AUTHOR_COUNTS: Final = (1, 2, 3, 4, 5, 6, 7, 8)
_AUTHOR_COUNT_WEIGHTS: Final = (10, 18, 20, 18, 13, 9, 7, 5)

# Variant suffixes keep every batch-2 payload distinguishable from batch 1.
_VARIANT_SUFFIXES: Final[Mapping[str, str]] = {
    "revised": "revised",
    "restored": "restored",
    "superseded": "superseded copy",
    "conflicting": "conflicting copy",
    "undated": "undated copy",
}


@dataclass(frozen=True)
class SyntheticRawAsset:
    """One generated raw asset: exact bytes plus the metadata ingestion needs."""

    name: str
    batch: int
    kind: AssetKind
    metadata: OpenAlexAssetMetadata | OpenAlexDeletionAssetMetadata
    payload: bytes = field(repr=False)
    record_count: int
    decompressed_bytes: int
    max_record_bytes: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    @property
    def file_name(self) -> str:
        suffix = ".jsonl.gz" if self.kind == "works" else ".csv.gz"
        return f"{self.name}{suffix}"

    @property
    def relative_path(self) -> str:
        return f"{_RAW_DIR}/batch{self.batch}/{self.file_name}"

    def summary(self) -> dict[str, object]:
        updated = self.metadata.updated_date
        return {
            "name": self.name,
            "batch": self.batch,
            "kind": self.kind,
            "path": self.relative_path,
            "asset_id": self.metadata.asset_id,
            "file_uri": self.metadata.file_uri,
            "snapshot_date": self.metadata.snapshot_date.isoformat(),
            "updated_date": None if updated is None else updated.isoformat(),
            "record_count": self.record_count,
            "byte_size": len(self.payload),
            "decompressed_bytes": self.decompressed_bytes,
            "max_record_bytes": self.max_record_bytes,
            "sha256": self.sha256,
        }


def _source_object(number: int, pools: _EntityPools) -> dict[str, object]:
    publisher = None if number % 11 == 0 else 1 + number % pools.publishers
    return {
        "id": _openalex_url("S", number),
        "display_name": f"Synthetic Journal {number:05d}",
        "type": "repository" if number % 10 == 0 else "journal",
        "issn_l": None,
        "host_organization": None if publisher is None else _openalex_url("P", publisher),
        "host_organization_name": (
            None if publisher is None else f"Synthetic Publisher {publisher:04d}"
        ),
    }


def _institution_object(rng: random.Random, pools: _EntityPools) -> dict[str, object]:
    if rng.random() < 0.01:
        return {"id": None, "display_name": None}
    number = rng.randint(1, pools.institutions)
    return {
        "id": _openalex_url("I", number),
        "display_name": f"Synthetic Institution {number:05d}",
        "ror": None,
        "country_code": _COUNTRIES[number % len(_COUNTRIES)],
        "type": _INSTITUTION_TYPES[number % len(_INSTITUTION_TYPES)],
    }


def _authorships(rng: random.Random, pools: _EntityPools) -> list[dict[str, object]]:
    count = rng.choices(_AUTHOR_COUNTS, weights=_AUTHOR_COUNT_WEIGHTS)[0]
    authorships: list[dict[str, object]] = []
    for index in range(count):
        if rng.random() < 0.01:
            author: dict[str, object] = {"id": None, "display_name": None}
            raw_name = f"Unattributed author {index + 1}"
        else:
            number = rng.randint(1, pools.authors)
            raw_name = f"Synthetic Author {number:06d}"
            author = {"id": _openalex_url("A", number), "display_name": raw_name, "orcid": None}
        roll = rng.random()
        institutions: list[dict[str, object]] | None
        if roll < 0.02:
            institutions = None
        else:
            size = 0 if roll < 0.15 else 2 if roll > 0.87 else 1
            institutions = [_institution_object(rng, pools) for _ in range(size)]
        position = "first" if index == 0 else "last" if index == count - 1 else "middle"
        authorships.append(
            {
                "author_position": position,
                "author": author,
                "institutions": institutions,
                "is_corresponding": index == 0,
                "raw_author_name": raw_name,
            }
        )
    return authorships


def _topics(rng: random.Random, pools: _EntityPools) -> list[dict[str, object]]:
    numbers = rng.sample(range(1, pools.topics + 1), min(rng.randint(1, 3), pools.topics))
    return [
        {
            "id": _openalex_url("T", number),
            "display_name": f"Synthetic Topic {number:04d}",
            "score": round(rng.uniform(0.3, 1.0), 4),
        }
        for number in numbers
    ]


def _keywords(rng: random.Random, pools: _EntityPools) -> list[dict[str, object]]:
    numbers = rng.sample(range(1, pools.keywords + 1), min(rng.randint(1, 4), pools.keywords))
    return [
        {
            "id": f"https://openalex.org/keywords/synthetic-keyword-{number:04d}",
            "display_name": f"synthetic keyword {number}",
            "score": round(rng.uniform(0.2, 0.9), 4),
        }
        for number in numbers
    ]


def _mesh(rng: random.Random, pools: _EntityPools) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for _ in range(rng.randint(1, 3)):
        number = rng.randint(1, pools.mesh)
        qualified = rng.random() < 0.5
        qualifier = rng.randint(1, 40)
        entries.append(
            {
                "descriptor_ui": f"DSYN{number:05d}",
                "descriptor_name": f"Synthetic descriptor {number}",
                "qualifier_ui": f"QSYN{qualifier:03d}" if qualified else None,
                "qualifier_name": f"synthetic qualifier {qualifier}" if qualified else None,
                "is_major_topic": rng.random() < 0.3,
            }
        )
    return entries


def _grants(rng: random.Random, pools: _EntityPools, work_number: int) -> list[dict[str, object]]:
    grants: list[dict[str, object]] = []
    for index in range(rng.randint(1, 2)):
        if rng.random() < 0.05:
            grants.append(
                {
                    "funder": None,
                    "funder_display_name": None,
                    "award_id": f"SYN-{work_number}-{index + 1}",
                }
            )
            continue
        number = rng.randint(1, pools.funders)
        grants.append(
            {
                "funder": _openalex_url("F", number),
                "funder_display_name": f"Synthetic Funder {number:04d}",
                "award_id": f"SYN-{work_number}-{index + 1}",
            }
        )
    return grants


def _references(rng: random.Random, pools: _EntityPools, work_number: int) -> list[object]:
    references: list[object] = []
    for index in range(rng.randint(5, 25)):
        roll = rng.random()
        if roll < 0.01:
            references.append(None)
        elif roll < 0.02:
            references.append(f"not-an-openalex-id-{index}")
        elif roll < 0.10:
            # Inside the reserved range but outside the corpus: TARGET_ABSENT.
            references.append(_openalex_url("W", pools.works + rng.randint(1, 1_000_000)))
        else:
            target = rng.randint(1, pools.works)
            if target == work_number:
                target = target % pools.works + 1
            references.append(_openalex_url("W", target))
    return references


def _base_work_record(
    number: int, *, seed: int, pools: _EntityPools, anchor_date: date
) -> dict[str, object]:
    rng = _rng(seed, "work", number)
    year = (
        None
        if rng.random() < 0.02
        else anchor_date.year - min(int(rng.expovariate(0.15)), 35)
    )
    publication_date = (
        None
        if year is None
        else f"{year:04d}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    )
    title = None if rng.random() < 0.03 else f"Synthetic work {number}"
    status = rng.choices(_OA_STATUSES, weights=_OA_WEIGHTS)[0]
    is_oa = status != "closed"
    license_value = rng.choice(_LICENSES) if is_oa else None
    record: dict[str, object] = {
        "id": _openalex_url("W", number),
        "doi": (
            f"https://doi.org/10.5555/rip.synthetic.{number}" if rng.random() < 0.88 else None
        ),
        "title": title,
        "display_name": title,
        "publication_year": year,
        "publication_date": publication_date,
        "type": rng.choices(_WORK_TYPES, weights=_WORK_TYPE_WEIGHTS)[0],
        "language": rng.choices(_LANGUAGES, weights=_LANGUAGE_WEIGHTS)[0],
        "cited_by_count": min(int(rng.paretovariate(1.2)) - 1, 5_000),
        "created_date": (
            anchor_date - timedelta(days=60 + rng.randint(0, 3_000))
        ).isoformat(),
        "updated_date": None,
    }
    if rng.random() >= 0.02:
        record["open_access"] = {"is_oa": is_oa, "oa_status": status}

    primary: dict[str, object] | None = None
    if rng.random() < 0.92:
        primary = {
            "source": _source_object(rng.randint(1, pools.sources), pools),
            "is_oa": is_oa,
            "landing_page_url": f"https://example.org/synthetic/works/{number}",
            "pdf_url": f"https://example.org/synthetic/works/{number}.pdf" if is_oa else None,
            "license": license_value,
            "version": "publishedVersion",
        }
    record["primary_location"] = primary
    roll = rng.random()
    if roll >= 0.04:
        locations: list[dict[str, object]] = [] if roll < 0.06 or primary is None else [
            dict(primary)
        ]
        if roll >= 0.06 and rng.random() < 0.3:
            locations.append(
                {
                    "source": _source_object(rng.randint(1, pools.sources), pools),
                    "is_oa": True,
                    "landing_page_url": f"https://repository.example.org/synthetic/{number}",
                    "pdf_url": None,
                    "license": None,
                    "version": "acceptedVersion",
                }
            )
        record["locations"] = locations

    roll = rng.random()
    if roll < 0.01:
        pass  # authorships key absent -> MISSING
    elif roll < 0.02:
        record["authorships"] = None
    elif roll < 0.04:
        record["authorships"] = []
    else:
        record["authorships"] = _authorships(rng, pools)

    roll = rng.random()
    if roll < 0.01:
        record["topics"] = None
    elif roll < 0.04:
        record["topics"] = []
    else:
        record["topics"] = _topics(rng, pools)

    roll = rng.random()
    if roll < 0.05:
        pass
    elif roll < 0.30:
        record["keywords"] = []
    else:
        record["keywords"] = _keywords(rng, pools)

    roll = rng.random()
    if roll < 0.55:
        pass
    elif roll < 0.65:
        record["mesh"] = []
    else:
        record["mesh"] = _mesh(rng, pools)

    roll = rng.random()
    if roll < 0.05:
        record["referenced_works"] = []
        record["referenced_works_count"] = 0
    else:
        references = _references(rng, pools, number)
        record["referenced_works"] = references
        record["referenced_works_count"] = len(references)

    roll = rng.random()
    if roll < 0.10:
        pass
    elif roll < 0.70:
        record["grants"] = []
    else:
        record["grants"] = _grants(rng, pools, number)
    return record


def _work_record(
    plan: SyntheticCyclePlan,
    number: int,
    *,
    updated_date: date | None,
    variant: str | None = None,
) -> dict[str, object]:
    """One Works record; ``variant`` changes payload so it differs from batch 1."""
    record = _base_work_record(
        number, seed=plan.seed, pools=plan.pools, anchor_date=plan.anchor_date
    )
    record["updated_date"] = None if updated_date is None else updated_date.isoformat()
    if variant is None:
        return record
    suffix = _VARIANT_SUFFIXES[variant]
    rng = _rng(plan.seed, "work", number, variant)
    title = record["title"] or f"Synthetic work {number}"
    record["title"] = f"{title} ({suffix})"
    record["display_name"] = record["title"]
    cited = record["cited_by_count"]
    assert isinstance(cited, int)
    record["cited_by_count"] = cited + rng.randint(1, 40)
    if variant == "revised":
        record["topics"] = _topics(rng, plan.pools)
    return record


def _gzip(body: bytes) -> bytes:
    return gzip.compress(body, compresslevel=9, mtime=0)


def _works_asset(
    *,
    name: str,
    batch: int,
    snapshot_date: date,
    updated_date: date | None,
    records: Sequence[Mapping[str, object]],
) -> SyntheticRawAsset:
    lines = [
        json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
        for record in records
    ]
    body = b"".join(lines)
    payload = _gzip(body)
    partition = "" if updated_date is None else f"updated_date={updated_date.isoformat()}/"
    metadata = OpenAlexAssetMetadata.model_validate(
        {
            "source": "openalex",
            "snapshot_date": snapshot_date,
            "entity": "works",
            "file_uri": f"s3://openalex/data/jsonl/works/{partition}synthetic_{name}.gz",
            "byte_size": len(payload),
            "record_count": len(lines),
            "updated_date": updated_date,
            "content_format": "jsonl",
        }
    )
    asset = SyntheticRawAsset(
        name=name,
        batch=batch,
        kind="works",
        metadata=metadata,
        payload=payload,
        record_count=len(lines),
        decompressed_bytes=len(body),
        max_record_bytes=max((len(line) for line in lines), default=0),
    )
    check_raw_asset_limits(asset)
    return asset


def _deletion_asset(
    *,
    name: str,
    batch: int,
    snapshot_date: date,
    rows: Sequence[tuple[str, date]],
) -> SyntheticRawAsset:
    lines = [b"work_id,deleted_date\n"] + [
        f"{work_id},{deleted.isoformat()}\n".encode("ascii")
        for work_id, deleted in sorted(rows)
    ]
    body = b"".join(lines)
    payload = _gzip(body)
    metadata = OpenAlexDeletionAssetMetadata.model_validate(
        {
            "source": "openalex",
            "snapshot_date": snapshot_date,
            "entity": "works-deletions",
            "file_uri": DELETION_PUBLIC_URI,
            "byte_size": len(payload),
            "updated_date": snapshot_date,
            "content_format": "csv",
        }
    )
    asset = SyntheticRawAsset(
        name=name,
        batch=batch,
        kind="deletions",
        metadata=metadata,
        payload=payload,
        record_count=len(rows),
        decompressed_bytes=len(body),
        max_record_bytes=max(len(line) for line in lines),
    )
    check_raw_asset_limits(asset)
    return asset


def check_raw_asset_limits(
    asset: SyntheticRawAsset,
    *,
    jsonl_limits: JsonlDecodeLimits | None = None,
    deletion_limits: CsvDeletionDecodeLimits | None = None,
    max_file_size_bytes: int = MAX_RAW_ASSET_BYTES,
) -> None:
    """Refuse an asset the bounded Step 12/13 decoders would reject."""
    if len(asset.payload) > max_file_size_bytes:
        raise SyntheticCycleError(f"{asset.name}: compressed size exceeds max_file_size_bytes")
    if asset.kind == "works":
        limits = jsonl_limits or JsonlDecodeLimits()
        if asset.decompressed_bytes > limits.max_decompressed_bytes:
            raise SyntheticCycleError(f"{asset.name}: exceeds max_decompressed_bytes")
        if asset.record_count > limits.max_records:
            raise SyntheticCycleError(f"{asset.name}: exceeds max_records")
        if asset.max_record_bytes > limits.max_record_bytes:
            raise SyntheticCycleError(f"{asset.name}: a record exceeds max_record_bytes")
        return
    bounds = deletion_limits or CsvDeletionDecodeLimits()
    if asset.decompressed_bytes > bounds.max_decompressed_bytes:
        raise SyntheticCycleError(f"{asset.name}: exceeds max_decompressed_bytes")
    if asset.record_count > bounds.max_ids:
        raise SyntheticCycleError(f"{asset.name}: exceeds max_ids")
    if asset.max_record_bytes > bounds.max_row_bytes:
        raise SyntheticCycleError(f"{asset.name}: a row exceeds max_row_bytes")


def build_batch1_raw_assets(plan: SyntheticCyclePlan) -> tuple[SyntheticRawAsset, ...]:
    """Dated Works assets, the undated Works asset, then the deletion CSV (ingest order)."""
    assets = [
        _works_asset(
            name=f"b1_works_{index + 1}",
            batch=1,
            snapshot_date=plan.batch1_snapshot_date,
            updated_date=updated,
            records=[_work_record(plan, number, updated_date=updated) for number in numbers],
        )
        for index, (updated, numbers) in enumerate(
            zip(plan.batch1_dates, plan.batch1_asset_works, strict=True)
        )
    ]
    assets.append(
        _works_asset(
            name="b1_works_undated",
            batch=1,
            snapshot_date=plan.batch1_snapshot_date,
            updated_date=None,
            records=[
                _work_record(plan, number, updated_date=None) for number in plan.undated_works
            ],
        )
    )
    assets.append(
        _deletion_asset(
            name="b1_deletions",
            batch=1,
            snapshot_date=plan.batch1_deletion_snapshot_date,
            rows=[
                (synthetic_id("W", number), plan.batch1_deleted_date)
                for number in plan.batch1_deleted_works
            ],
        )
    )
    return tuple(assets)


def build_batch2_raw_assets(plan: SyntheticCyclePlan) -> tuple[SyntheticRawAsset, ...]:
    """Batch-2 observations, in ingest order (the deletion CSV follows its carriers).

    IDENTICAL replays are not raw assets: they are batch-1 rows replayed
    verbatim, because the checksum covers the whole batch-1 asset.
    """
    cases = plan.cases
    equal_date = plan.equal_date
    equal_records = [
        _work_record(plan, number, updated_date=equal_date, variant="conflicting")
        for number in cases[DeltaCase.CONFLICT_EQUAL_DATE]
    ] + [
        # Tombstone carriers: the batch-1 payload re-observed at the same date,
        # then deleted at that date by the batch-2 ledger below.
        _work_record(plan, number, updated_date=equal_date)
        for number in cases[DeltaCase.APPLY_UPDATE_TOMBSTONE]
    ]
    newer_records = (
        [
            _work_record(plan, number, updated_date=plan.newer_date)
            for number in cases[DeltaCase.INSERT_NEW]
        ]
        + [
            _work_record(plan, number, updated_date=plan.newer_date, variant="revised")
            for number in cases[DeltaCase.APPLY_UPDATE_NEWER]
        ]
        + [
            _work_record(plan, number, updated_date=plan.newer_date, variant="restored")
            for number in cases[DeltaCase.RESTORE_REQUIRED]
        ]
    )
    older_records = [
        _work_record(plan, number, updated_date=plan.older_date, variant="superseded")
        for number in cases[DeltaCase.STALE_OLDER]
    ]
    undated_records = [
        _work_record(plan, number, updated_date=None, variant="undated")
        for number in cases[DeltaCase.STALE_NULL_SOURCE_DATE]
        + cases[DeltaCase.CONFLICT_NULL_DATES]
    ]
    snapshot = plan.batch2_snapshot_date
    return (
        _works_asset(
            name="b2_works_equal_date",
            batch=2,
            snapshot_date=snapshot,
            updated_date=equal_date,
            records=equal_records,
        ),
        _works_asset(
            name="b2_works_newer",
            batch=2,
            snapshot_date=snapshot,
            updated_date=plan.newer_date,
            records=newer_records,
        ),
        _works_asset(
            name="b2_works_older",
            batch=2,
            snapshot_date=snapshot,
            updated_date=plan.older_date,
            records=older_records,
        ),
        _works_asset(
            name="b2_works_undated",
            batch=2,
            snapshot_date=snapshot,
            updated_date=None,
            records=undated_records,
        ),
        _deletion_asset(
            name="b2_deletions",
            batch=2,
            snapshot_date=snapshot,
            rows=[
                (synthetic_id("W", number), equal_date)
                for number in cases[DeltaCase.APPLY_UPDATE_TOMBSTONE]
            ],
        ),
    )


# ---------------------------------------------------------------------------
# Ingest through the real Step 12/13 local path, one asset per run.
# ---------------------------------------------------------------------------


class _PinnedRunControlStore(InMemoryControlStore):
    """``InMemoryControlStore`` whose next pipeline run id is pinned by the caller.

    The Step 12/13 wrappers mint run ids with ``uuid4``; pinning them from the
    seed makes the ``run_id`` lineage column, and so every exported byte,
    reproducible. Every other control-plane rule is the parent's.
    """

    def __init__(self) -> None:
        super().__init__()
        self._next_run_id: UUID | None = None

    def pin_next_run_id(self, run_id: UUID) -> None:
        self._next_run_id = run_id

    def create_pipeline_run(self, run: PipelineRun) -> PipelineRun:
        if self._next_run_id is None:
            raise SyntheticCycleError("synthetic ingest requires a pinned run id")
        pinned, self._next_run_id = self._next_run_id, None
        return super().create_pipeline_run(run.model_copy(update={"run_id": pinned}))


class _StepClock:
    """Deterministic clock: each call returns the previous value plus one second."""

    def __init__(self, start: datetime) -> None:
        self._current = start

    def __call__(self) -> datetime:
        current = self._current
        self._current = current + timedelta(seconds=1)
        return current


class _SyntheticAssetConnector:
    """Serves one in-memory synthetic asset through the connector port."""

    def __init__(self, asset: SyntheticRawAsset) -> None:
        self._asset = asset

    def discover_metadata(self) -> OpenAlexSampleSelection:
        metadata = self._asset.metadata
        if not isinstance(metadata, OpenAlexAssetMetadata):
            raise SyntheticCycleError("only Works assets are discoverable")
        return OpenAlexSampleSelection(
            max_files=1,
            max_file_size_bytes=MAX_RAW_ASSET_BYTES,
            eligible_count=1,
            selected=(metadata,),
            skipped=(),
        )

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        if asset.uri != self._asset.metadata.file_uri:
            raise SyntheticCycleError("fetch requested an asset this connector does not serve")
        return io.BytesIO(self._asset.payload)


@dataclass(frozen=True)
class SyntheticIngestRun:
    """Outcome of one ingest run (one asset)."""

    name: str
    batch: int
    kind: AssetKind
    run_id: str
    asset_id: str
    status: str
    stats: Mapping[str, int]

    def summary(self) -> dict[str, object]:
        return {
            "name": self.name,
            "batch": self.batch,
            "kind": self.kind,
            "run_id": self.run_id,
            "asset_id": self.asset_id,
            "status": self.status,
            "stats": dict(self.stats),
        }


def _write_local_config(directory: Path, landing: Path) -> Path:
    config = {
        "environment": "local",
        "log_level": "WARNING",
        "source": "openalex",
        "storage": {"backend": "local", "landing_path": str(landing)},
        "warehouse": {
            "transactional": "postgres",
            "analytical": "duckdb",
            "postgres_dsn_env": "POSTGRES_DSN",
            "duckdb_path": ":memory:",
        },
        "sample_selection": {"max_files": 1, "max_file_size_bytes": MAX_RAW_ASSET_BYTES},
    }
    path = directory / "local.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")
    return path


def _ingest_works(
    asset: SyntheticRawAsset,
    *,
    config_path: Path,
    control: _PinnedRunControlStore,
    canonical: InMemoryCanonicalStore,
    objects: LocalObjectStore,
    clock: Callable[[], datetime],
) -> SyntheticIngestRun:
    result = run_openalex_works_local_ingest(
        config_path,
        backend="memory",
        control_store=control,
        canonical_store=canonical,
        object_store=objects,
        connector=_SyntheticAssetConnector(asset),  # type: ignore[arg-type]
        worker_id=_INGEST_WORKER_ID,
        now=clock,
    )
    stats = result.stats
    if (
        result.run.status is not PipelineRunStatus.SUCCESS
        or result.skipped_reason is not None
        or stats is None
    ):
        raise SyntheticCycleError(f"{asset.name}: Works ingest did not publish")
    counts = {
        "works_inserted": stats.works_inserted,
        "works_identical": stats.works_identical,
        "works_replaced": stats.works_replaced,
        "works_stale": stats.works_stale,
        "record_provenance_count": stats.record_provenance_count,
    }
    expected = asset.record_count
    if stats.works_inserted != expected or stats.record_provenance_count != expected:
        raise SyntheticCycleError(f"{asset.name}: expected every synthetic work to insert")
    return SyntheticIngestRun(
        name=asset.name,
        batch=asset.batch,
        kind=asset.kind,
        run_id=str(result.run.run_id),
        asset_id=asset.metadata.asset_id,
        status=result.run.status.value,
        stats=counts,
    )


def _ingest_deletions(
    asset: SyntheticRawAsset,
    *,
    config_path: Path,
    control: _PinnedRunControlStore,
    canonical: InMemoryCanonicalStore,
    objects: LocalObjectStore,
    clock: Callable[[], datetime],
) -> SyntheticIngestRun:
    metadata = asset.metadata
    assert isinstance(metadata, OpenAlexDeletionAssetMetadata)
    # Wraps ingest_deletion_asset with the same run lifecycle as Works ingest.
    result = run_openalex_deletions_local_ingest(
        config_path,
        backend="memory",
        deletion_asset=metadata,
        control_store=control,
        canonical_store=canonical,
        object_store=objects,
        connector=_SyntheticAssetConnector(asset),
        worker_id=_INGEST_WORKER_ID,
        now=clock,
    )
    stats = result.stats
    if (
        result.run.status is not PipelineRunStatus.SUCCESS
        or result.skipped_reason is not None
        or stats is None
    ):
        raise SyntheticCycleError(f"{asset.name}: deletion ingest did not publish")
    counts = {
        "rows_seen": stats.rows_seen,
        "unique_ids": stats.unique_ids,
        "duplicates": stats.duplicates,
        "deleted": stats.deleted,
        "already_deleted": stats.already_deleted,
        "unknown": stats.unknown,
        "stale": stats.stale,
        "conflicts": stats.conflicts,
    }
    if stats.deleted != asset.record_count:
        raise SyntheticCycleError(f"{asset.name}: expected every listed work to be deleted")
    return SyntheticIngestRun(
        name=asset.name,
        batch=asset.batch,
        kind=asset.kind,
        run_id=str(result.run.run_id),
        asset_id=metadata.asset_id,
        status=result.run.status.value,
        stats=counts,
    )


def ingest_synthetic_assets(
    assets: Sequence[SyntheticRawAsset],
    *,
    plan: SyntheticCyclePlan,
    batch: int,
) -> tuple[CanonicalSnapshot, tuple[SyntheticIngestRun, ...]]:
    """Ingest ``assets`` in order into one fresh in-memory store; return its snapshot.

    Each asset is its own pipeline run (``max_files=1``). Raw bytes land in a
    temporary local ObjectStore that is removed afterwards; the caller keeps
    the generated bytes.
    """
    if any(asset.batch != batch for asset in assets):
        raise SyntheticCycleError(f"every asset must belong to batch {batch}")
    control = _PinnedRunControlStore()
    canonical = InMemoryCanonicalStore()
    clock = _StepClock(plan.clock_start(batch))
    runs: list[SyntheticIngestRun] = []
    with tempfile.TemporaryDirectory(prefix="rip-synthetic-ingest-") as scratch:
        work_dir = Path(scratch)
        landing = work_dir / "landing"
        config_path = _write_local_config(work_dir, landing)
        objects = LocalObjectStore(StorageConfig(backend="local", landing_path=landing))
        for asset in assets:
            control.pin_next_run_id(plan.run_id(asset.name))
            ingest = _ingest_works if asset.kind == "works" else _ingest_deletions
            runs.append(
                ingest(
                    asset,
                    config_path=config_path,
                    control=control,
                    canonical=canonical,
                    objects=objects,
                    clock=clock,
                )
            )
    return canonical.snapshot(), tuple(runs)


# ---------------------------------------------------------------------------
# Batch-2 delta composition and expected decisions.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeltaStaging:
    """Batch-2 staging tables plus the decisions they must produce."""

    works_staging: pa.Table
    work_topics_staging: pa.Table
    expected_decisions: pa.Table
    raw_assets: tuple[SyntheticRawAsset, ...]
    ingest_runs: tuple[SyntheticIngestRun, ...]

    def decision_counts(self) -> dict[str, int]:
        counts = Counter(self.expected_decisions.column("publication_decision").to_pylist())
        return {decision.value: counts.get(decision.value, 0) for decision in PublicationDecision}

    def case_counts(self) -> dict[str, int]:
        counts = Counter(self.expected_decisions.column("delta_case").to_pylist())
        return {case.value: counts.get(case.value, 0) for case in DeltaCase}


def _with_canonical_schema(table_name: str, table: pa.Table) -> pa.Table:
    return table.replace_schema_metadata(CANONICAL_SCHEMAS[table_name].metadata)


def _require_unique(table: pa.Table, keys: Sequence[str], label: str) -> None:
    rows = zip(*(table.column(key).to_pylist() for key in keys), strict=True)
    seen: set[tuple[object, ...]] = set()
    for row in rows:
        if row in seen:
            raise SyntheticCycleError(f"{label} is not unique on {tuple(keys)}")
        seen.add(row)


def _expected_decisions(
    works_staging: pa.Table,
    batch1_works: pa.Table,
    case_by_work_id: Mapping[str, DeltaCase],
) -> pa.Table:
    lineage_columns = [
        "work_id",
        "source_checksum_sha256",
        "lineage_source_updated_date",
        "activity_state",
    ]
    targets = {row["work_id"]: row for row in batch1_works.select(lineage_columns).to_pylist()}
    rows: list[dict[str, object]] = []
    for source in works_staging.select(lineage_columns).to_pylist():
        work_id = source["work_id"]
        target = targets.get(work_id)
        decision = decide_analytical_works_merge(
            target_exists=target is not None,
            target_checksum=None if target is None else target["source_checksum_sha256"],
            target_lineage_date=(
                None if target is None else target["lineage_source_updated_date"]
            ),
            target_activity_state=None if target is None else target["activity_state"],
            source_checksum=source["source_checksum_sha256"],
            source_lineage_date=source["lineage_source_updated_date"],
            source_activity_state=source["activity_state"],
        )
        case = case_by_work_id.get(work_id)
        if case is None:
            raise SyntheticCycleError(f"staging work {work_id} has no planned delta case")
        if decision is not DELTA_CASE_DECISIONS[case]:
            raise SyntheticCycleError(
                f"{work_id}: planned {case.value} but decide_analytical_works_merge "
                f"returned {decision.value}; batch-1 tables do not match this plan"
            )
        rows.append(
            {
                "work_id": work_id,
                "delta_case": case.value,
                "source_activity_state": source["activity_state"],
                "publication_decision": decision.value,
                "accepted": is_accepted_publication_decision(decision),
                "relationship_refresh_eligible": is_relationship_refresh_eligible(
                    decision, source_activity_state=source["activity_state"]
                ),
            }
        )
    return pa.Table.from_pylist(rows, schema=EXPECTED_DECISIONS_SCHEMA)


def compose_delta_staging(
    batch1_works: pa.Table,
    batch1_work_topics: pa.Table,
    *,
    seed: int = DEFAULT_SEED,
    scale: SyntheticScale | str = DEFAULT_SCALE,
    anchor_date: date = DEFAULT_ANCHOR_DATE,
) -> DeltaStaging:
    """Compose batch-2 ``works_staging`` / ``work_topics_staging`` against batch 1.

    ``batch1_works`` is the publication target (the batch-1 ``works`` export);
    ``batch1_work_topics`` supplies the IDENTICAL replays' topic rows. Every
    staged work is classified with ``decide_analytical_works_merge`` and must
    land on its planned outcome, otherwise the inputs do not belong to this
    seed/scale/anchor and ``SyntheticCycleError`` is raised.

    ``work_topics_staging`` holds the topic rows of every staged work, so the
    rows of DELETED tombstones and of non-accepted works are present and must
    be ignored by ``merge_work_topics.sql``.
    """
    validate_canonical_arrow_table("works", batch1_works)
    validate_canonical_arrow_table("work_topics", batch1_work_topics)
    plan = plan_synthetic_cycle(seed=seed, scale=scale, anchor_date=anchor_date)
    raw_assets = build_batch2_raw_assets(plan)
    snapshot, runs = ingest_synthetic_assets(raw_assets, plan=plan, batch=2)

    ingested_works = canonical_table_to_arrow("works", snapshot.works)
    ingested_topics = canonical_table_to_arrow("work_topics", snapshot.work_topics)
    replay_ids = plan.case_work_ids(DeltaCase.IDENTICAL_REPLAY)
    expected_ingested = sorted(
        work_id
        for case in DeltaCase
        if case is not DeltaCase.IDENTICAL_REPLAY
        for work_id in plan.case_work_ids(case)
    )
    if ingested_works.column("work_id").to_pylist() != expected_ingested:
        raise SyntheticCycleError("batch-2 ingest did not produce exactly the planned works")

    replay_set = pa.array(replay_ids, type=pa.string())
    replay_works = batch1_works.filter(pc.is_in(batch1_works["work_id"], value_set=replay_set))
    if replay_works.num_rows != len(replay_ids):
        raise SyntheticCycleError("batch-1 works are missing planned IDENTICAL replays")
    replay_topics = batch1_work_topics.filter(
        pc.is_in(batch1_work_topics["work_id"], value_set=replay_set)
    )

    works_staging = (
        pa.concat_tables(
            [
                _with_canonical_schema("works", ingested_works),
                _with_canonical_schema("works", replay_works),
            ]
        )
        .sort_by("work_id")
        .combine_chunks()
    )
    work_topics_staging = (
        pa.concat_tables(
            [
                _with_canonical_schema("work_topics", ingested_topics),
                _with_canonical_schema("work_topics", replay_topics),
            ]
        )
        .sort_by([("work_id", "ascending"), ("topic_id", "ascending")])
        .combine_chunks()
    )
    validate_canonical_arrow_table("works", works_staging)
    validate_canonical_arrow_table("work_topics", work_topics_staging)
    _require_unique(works_staging, ("work_id",), "works_staging")
    _require_unique(work_topics_staging, ("work_id", "topic_id"), "work_topics_staging")
    staged_ids = set(works_staging.column("work_id").to_pylist())
    if not set(work_topics_staging.column("work_id").to_pylist()) <= staged_ids:
        raise SyntheticCycleError("work_topics_staging has rows for unstaged works")

    expected = _expected_decisions(works_staging, batch1_works, plan.case_by_work_id())
    return DeltaStaging(
        works_staging=works_staging,
        work_topics_staging=work_topics_staging,
        expected_decisions=expected,
        raw_assets=raw_assets,
        ingest_runs=runs,
    )


@dataclass(frozen=True)
class NullTopicInjection:
    """Where the single NULL ``topic_id`` was injected."""

    row_index: int
    work_id: str
    original_topic_id: str
    delta_case: str
    publication_decision: str

    def summary(self) -> dict[str, object]:
        return {
            "row_index": self.row_index,
            "work_id": self.work_id,
            "original_topic_id": self.original_topic_id,
            "delta_case": self.delta_case,
            "publication_decision": self.publication_decision,
        }


def inject_null_topic_id(
    work_topics_staging: pa.Table,
    expected_decisions: pa.Table,
) -> tuple[pa.Table, NullTopicInjection]:
    """Return ``work_topics_staging`` cast all-nullable with one ``topic_id`` set to NULL.

    The row belongs to a relationship-eligible work, preferring an
    APPLY_UPDATE_NEWER work: it already has published topic rows, so the
    transactional DELETE in ``merge_work_topics.sql`` has something to roll
    back when the INSERT hits the REQUIRED ``work_topics.topic_id``.
    """
    validate_canonical_arrow_table("work_topics", work_topics_staging)
    decisions = {
        row["work_id"]: row
        for row in expected_decisions.select(
            ["work_id", "delta_case", "publication_decision", "relationship_refresh_eligible"]
        ).to_pylist()
    }
    work_ids = work_topics_staging.column("work_id").to_pylist()
    preferences = (DeltaCase.APPLY_UPDATE_NEWER.value, DeltaCase.INSERT_NEW.value)
    chosen: int | None = None
    for preferred in preferences:
        for index, work_id in enumerate(work_ids):
            row = decisions.get(work_id)
            if row and row["relationship_refresh_eligible"] and row["delta_case"] == preferred:
                chosen = index
                break
        if chosen is not None:
            break
    if chosen is None:
        raise SyntheticCycleError("no relationship-eligible work has a topic row to inject")

    table = work_topics_staging.cast(WORK_TOPICS_STAGING_INJECTION_SCHEMA)
    topic_ids = table.column("topic_id").to_pylist()
    original = topic_ids[chosen]
    topic_ids[chosen] = None
    position = table.schema.get_field_index("topic_id")
    table = table.set_column(
        position,
        WORK_TOPICS_STAGING_INJECTION_SCHEMA.field("topic_id"),
        pa.array(topic_ids, type=pa.string()),
    )
    decision = decisions[work_ids[chosen]]
    return table, NullTopicInjection(
        row_index=chosen,
        work_id=work_ids[chosen],
        original_topic_id=original,
        delta_case=decision["delta_case"],
        publication_decision=decision["publication_decision"],
    )


# ---------------------------------------------------------------------------
# Expected publication effects and the on-disk cycle.
# ---------------------------------------------------------------------------


def _all_decisions(counts: Mapping[str, int]) -> dict[str, int]:
    return {decision.value: counts.get(decision.value, 0) for decision in PublicationDecision}


def _expected_publication(
    batch1_works: pa.Table,
    batch1_work_topics: pa.Table,
    delta: DeltaStaging,
) -> dict[str, object]:
    """Row effects the BigQuery publish SQL must reproduce for batches 1 and 2.

    Batch 1 loads into empty targets: every staged work INSERTs and only ACTIVE
    works refresh relationships, so DELETED works' topic rows are never
    published. Batch 2 replaces topic rows only for relationship-eligible works;
    tombstones keep theirs (Step 13 Policy A).
    """
    state = dict(
        zip(
            batch1_works.column("work_id").to_pylist(),
            batch1_works.column("activity_state").to_pylist(),
            strict=True,
        )
    )
    batch1_topic_work_ids = batch1_work_topics.column("work_id").to_pylist()
    published_topics = [work_id for work_id in batch1_topic_work_ids if state[work_id] == "ACTIVE"]
    active_b1 = sum(1 for value in state.values() if value == "ACTIVE")

    decisions = delta.expected_decisions.to_pylist()
    eligible = {row["work_id"] for row in decisions if row["relationship_refresh_eligible"]}
    accepted = [row for row in decisions if row["accepted"]]
    counts = delta.decision_counts()
    tombstones = {
        row["work_id"]
        for row in decisions
        if row["delta_case"] == DeltaCase.APPLY_UPDATE_TOMBSTONE.value
    }
    topics_deleted = sum(1 for work_id in published_topics if work_id in eligible)
    topics_inserted = sum(
        1
        for work_id in delta.work_topics_staging.column("work_id").to_pylist()
        if work_id in eligible
    )
    after_state = dict(state)
    for row in accepted:
        after_state[row["work_id"]] = row["source_activity_state"]
    return {
        "batch1": {
            "staging_works": batch1_works.num_rows,
            "staging_work_topics": batch1_work_topics.num_rows,
            "decision_counts": _all_decisions({"INSERT": batch1_works.num_rows}),
            "accepted_work_ids": batch1_works.num_rows,
            "relationship_publish_work_ids": active_b1,
            "merge_works": {"inserted": batch1_works.num_rows, "updated": 0, "deleted": 0},
            "merge_work_topics": {"deleted": 0, "inserted": len(published_topics)},
            "works_rows_after": batch1_works.num_rows,
            "works_deleted_after": batch1_works.num_rows - active_b1,
            "work_topics_rows_after": len(published_topics),
        },
        "batch2": {
            "staging_works": delta.works_staging.num_rows,
            "staging_work_topics": delta.work_topics_staging.num_rows,
            "decision_counts": counts,
            "delta_case_counts": delta.case_counts(),
            "accepted_work_ids": len(accepted),
            "relationship_publish_work_ids": len(eligible),
            "merge_works": {
                "inserted": counts[PublicationDecision.INSERT.value],
                "updated": counts[PublicationDecision.IDENTICAL.value]
                + counts[PublicationDecision.APPLY_UPDATE.value],
                "deleted": 0,
            },
            "merge_work_topics": {"deleted": topics_deleted, "inserted": topics_inserted},
            "tombstone_work_topics_preserved": sum(
                1 for work_id in published_topics if work_id in tombstones
            ),
            "works_rows_after": batch1_works.num_rows + counts[PublicationDecision.INSERT.value],
            "works_deleted_after": sum(1 for value in after_state.values() if value == "DELETED"),
            "work_topics_rows_after": len(published_topics) - topics_deleted + topics_inserted,
        },
    }


@dataclass(frozen=True)
class SyntheticCycleResult:
    """What ``write_synthetic_cycle`` wrote. ``manifest`` holds no absolute paths."""

    output_dir: Path
    manifest: Mapping[str, Any]
    batch1_export: CanonicalParquetExport
    delta: DeltaStaging
    injection: NullTopicInjection

    @property
    def manifest_path(self) -> Path:
        return self.output_dir / MANIFEST_FILE_NAME

    def path_for(self, relative_path: str) -> Path:
        return self.output_dir / relative_path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dump_json(payload: Mapping[str, Any]) -> bytes:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True)
    return f"{text}\n".encode("ascii")


def _file_entry(
    output_dir: Path,
    path: Path,
    *,
    kind: str,
    batch: int | None,
    table_name: str | None = None,
    row_count: int | None = None,
) -> dict[str, object]:
    return {
        "path": path.relative_to(output_dir).as_posix(),
        "kind": kind,
        "batch": batch,
        "table_name": table_name,
        "row_count": row_count,
        "byte_size": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _require_empty_output_dir(output_dir: Path) -> None:
    if output_dir.exists():
        if not output_dir.is_dir():
            raise SyntheticCycleError("output_dir exists and is not a directory")
        if any(output_dir.iterdir()):
            raise SyntheticCycleError(
                "refusing to write a synthetic cycle into a non-empty directory"
            )
    output_dir.mkdir(parents=True, exist_ok=True)


def _write_raw(output_dir: Path, asset: SyntheticRawAsset) -> dict[str, object]:
    path = output_dir / asset.relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(asset.payload)
    kind = "raw_works_asset" if asset.kind == "works" else "raw_deletion_asset"
    return _file_entry(
        output_dir, path, kind=kind, batch=asset.batch, row_count=asset.record_count
    )


def write_synthetic_cycle(
    output_dir: Path | str,
    *,
    scale: SyntheticScale | str = DEFAULT_SCALE,
    seed: int = DEFAULT_SEED,
    anchor_date: date = DEFAULT_ANCHOR_DATE,
) -> SyntheticCycleResult:
    """Generate, ingest, export and compose one full synthetic cycle into ``output_dir``.

    Layout (all paths relative to ``output_dir``)::

        raw/batch1/*.jsonl.gz, raw/batch1/b1_deletions.csv.gz
        raw/batch2/*.jsonl.gz, raw/batch2/b2_deletions.csv.gz
        batch1/<table>.parquet                 (15 canonical tables)
        batch2/works_staging.parquet
        batch2/work_topics_staging.parquet
        injection/work_topics_staging_null_topic_id.parquet   (all-nullable)
        expected_decisions.json                (SEMANTIC_ONLY)
        manifest.json                          (sha256 per file, counts, seed, scale)

    ``output_dir`` must be absent or empty; nothing is overwritten.
    ``manifest.json`` is written last, so a directory without it is an
    incomplete (failed) cycle and must be removed before retrying.
    """
    directory = Path(output_dir)
    plan = plan_synthetic_cycle(seed=seed, scale=scale, anchor_date=anchor_date)
    _require_empty_output_dir(directory)
    entries: list[dict[str, object]] = []

    batch1_raw = build_batch1_raw_assets(plan)
    entries.extend(_write_raw(directory, asset) for asset in batch1_raw)
    snapshot, batch1_runs = ingest_synthetic_assets(batch1_raw, plan=plan, batch=1)
    export = export_snapshot_to_parquet(snapshot, directory / _BATCH1_DIR)
    for item in export.files:
        entries.append(
            _file_entry(
                directory,
                item.path,
                kind="batch1_canonical_parquet",
                batch=1,
                table_name=item.table_name,
                row_count=item.row_count,
            )
        )
    batch1_works = read_canonical_parquet("works", export.file_for("works").path)
    batch1_topics = read_canonical_parquet("work_topics", export.file_for("work_topics").path)

    delta = compose_delta_staging(
        batch1_works, batch1_topics, seed=plan.seed, scale=plan.scale, anchor_date=anchor_date
    )
    entries.extend(_write_raw(directory, asset) for asset in delta.raw_assets)
    for file_name, table_name, table in (
        (_WORKS_STAGING_FILE, "works", delta.works_staging),
        (_WORK_TOPICS_STAGING_FILE, "work_topics", delta.work_topics_staging),
    ):
        written = write_canonical_parquet(table_name, table, directory / _BATCH2_DIR / file_name)
        entries.append(
            _file_entry(
                directory,
                written.path,
                kind="batch2_staging_parquet",
                batch=2,
                table_name=f"{table_name}_staging",
                row_count=written.row_count,
            )
        )

    injected, injection = inject_null_topic_id(delta.work_topics_staging, delta.expected_decisions)
    injection_path = directory / _INJECTION_DIR / _INJECTION_FILE
    injection_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(injected, injection_path, **_INJECTION_WRITE_OPTIONS)
    entries.append(
        _file_entry(
            directory,
            injection_path,
            kind="injection_parquet",
            batch=2,
            table_name="work_topics_staging",
            row_count=injected.num_rows,
        )
    )

    publication = _expected_publication(batch1_works, batch1_topics, delta)
    expected_payload = {
        "schema_version": MANIFEST_VERSION,
        "evidence_label": ValidationLabel.SEMANTIC_ONLY.value,
        "decision_function": (
            "research_platform.analytics.bigquery.contracts.decide_analytical_works_merge"
        ),
        "seed": plan.seed,
        "dataset_scale": plan.scale.dataset_scale,
        "publication": publication,
        "batch2_decisions": delta.expected_decisions.to_pylist(),
        "injection": {
            "path": injection_path.relative_to(directory).as_posix(),
            "null_topic_rows": 1,
            "expectation": (
                "merge_work_topics.sql fails on the REQUIRED work_topics.topic_id; "
                "work_topics row count and fingerprint stay unchanged"
            ),
            **injection.summary(),
        },
    }
    expected_path = directory / EXPECTED_DECISIONS_FILE_NAME
    expected_path.write_bytes(_dump_json(expected_payload))
    entries.append(_file_entry(directory, expected_path, kind="expected_decisions", batch=2))

    manifest: dict[str, Any] = {
        "manifest_version": MANIFEST_VERSION,
        "generator": "research_platform.analytics.bigquery.sandbox.synthetic",
        "generator_version": SYNTHETIC_GENERATOR_VERSION,
        "data_class": "SYNTHETIC",
        "evidence_label": ValidationLabel.SEMANTIC_ONLY.value,
        "seed": plan.seed,
        "anchor_date": plan.anchor_date.isoformat(),
        "scale": plan.scale.model_dump(mode="json"),
        "dataset_scale": plan.scale.dataset_scale,
        "synthetic_id_range": {
            "base": SYNTHETIC_ID_BASE,
            "ceiling_exclusive": _SYNTHETIC_ID_CEILING,
            "prefixes": ["A", "F", "I", "P", "S", "T", "W"],
        },
        "dates": {
            "batch1_updated_dates": [value.isoformat() for value in plan.batch1_dates],
            "batch1_deleted_date": plan.batch1_deleted_date.isoformat(),
            "batch2_newer_date": plan.newer_date.isoformat(),
            "batch2_older_date": plan.older_date.isoformat(),
            "batch2_equal_date": plan.equal_date.isoformat(),
        },
        "environment": {"python": platform.python_version(), "pyarrow": pa.__version__},
        "raw_assets": [asset.summary() for asset in (*batch1_raw, *delta.raw_assets)],
        "ingest_runs": [run.summary() for run in (*batch1_runs, *delta.ingest_runs)],
        "counts": {
            "batch1_tables": export.row_counts(),
            "batch2_staging": {
                "works_staging": delta.works_staging.num_rows,
                "work_topics_staging": delta.work_topics_staging.num_rows,
            },
            "batch2_decisions": delta.decision_counts(),
            "batch2_delta_cases": delta.case_counts(),
        },
        "files": sorted(entries, key=lambda entry: str(entry["path"])),
    }
    (directory / MANIFEST_FILE_NAME).write_bytes(_dump_json(manifest))
    return SyntheticCycleResult(
        output_dir=directory,
        manifest=manifest,
        batch1_export=export,
        delta=delta,
        injection=injection,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m research_platform.analytics.bigquery.sandbox.synthetic OUTPUT_DIR``."""
    parser = argparse.ArgumentParser(
        prog="python -m research_platform.analytics.bigquery.sandbox.synthetic",
        description=(
            "Write one deterministic synthetic OpenAlex cycle (offline; no network, "
            "GCP or BigQuery). Use a directory outside the repository or data/sample/."
        ),
    )
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--scale", choices=sorted(SYNTHETIC_SCALES), default=DEFAULT_SCALE.name)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--anchor-date",
        type=date.fromisoformat,
        default=DEFAULT_ANCHOR_DATE,
        help="YYYY-MM-DD; lineage dates are offsets from it (default %(default)s)",
    )
    arguments = parser.parse_args(argv)
    try:
        result = write_synthetic_cycle(
            arguments.output_dir,
            scale=arguments.scale,
            seed=arguments.seed,
            anchor_date=arguments.anchor_date,
        )
    except SyntheticCycleError as error:
        print(f"synthetic cycle failed: {error}", file=sys.stderr)
        return 2
    manifest = result.manifest
    summary = {
        "dataset_scale": manifest["dataset_scale"],
        "seed": manifest["seed"],
        "anchor_date": manifest["anchor_date"],
        "files": len(manifest["files"]),
        "counts": manifest["counts"],
        "manifest_sha256": _sha256_file(result.manifest_path),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
