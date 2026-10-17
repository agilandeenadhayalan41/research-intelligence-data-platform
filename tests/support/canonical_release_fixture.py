"""Synthetic Bronze for DE-07 release tests, landed through the real Step 12/13 wrappers.

Inputs, in application order (LOCAL, synthetic; no OpenAlex data):

- ``a1`` Works, partition 2026-09-01: W1 (every table populated), W2 (every array
  key absent: MISSING presences), W3 (empty arrays, ``topics: null``), W4 (shares
  author A1 and institution I1 with W1).
- ``d1`` deletions, snapshot 2026-09-02: W3 (known: tombstone, relationships kept),
  W9 (unknown: barrier only), and W3 again (a duplicate row).
- ``a2`` Works, partition 2026-09-01: W9 (stale ACTIVE: blocked by the barrier),
  W5 (new).
- ``a3`` Works, partition 2026-09-05: W1 revised (newer: relationships replaced).

Release N lists ``a1, d1, a2``; release N+1 adds ``a3``. One ``StepClock``
continues across every ``land`` call, so each input's ``processed_at`` is unique
and increases in application order, as in a real sequence of runs.
"""

from __future__ import annotations

import gzip
import io
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

import yaml

from research_platform.canonical.memory import InMemoryCanonicalStore
from research_platform.canonical.openalex.rebuild import (
    ReleaseInputSpec,
    release_input_from_source_file,
    release_inputs_from_source_files,
)
from research_platform.control.memory import InMemoryControlStore
from research_platform.control.models import PipelineRunStatus
from research_platform.ingestion import run_openalex_deletions_local_ingest, run_openalex_works_local_ingest
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.deletion_metadata import OpenAlexDeletionAssetMetadata
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import OpenAlexSampleSelection
from research_platform.storage.base import ObjectStore

MAX_BYTES = 1_000_000
CLOCK_START = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
DELETION_URI = "s3://openalex/data/jsonl/works/deleted_ids.csv.gz"


def _source(n: int) -> dict[str, Any]:
    return {
        "id": f"https://openalex.org/S{n}", "display_name": f"Journal {n}", "type": "journal",
        "issn_l": "1234-5678", "host_organization": "https://openalex.org/P1",
        "host_organization_name": "Publisher One",
    }


def rich_work(*, title: str = "A fully populated work", second_author: str = "A2") -> dict[str, Any]:
    return {
        "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1234/Example", "title": title,
        "publication_year": 2020, "publication_date": "2020-05-01", "type": "article", "language": "en",
        "cited_by_count": 3, "referenced_works_count": 3, "open_access": {"is_oa": True, "oa_status": "gold"},
        "created_date": "2020-01-01", "updated_date": "2024-01-10",
        "primary_location": {"source": _source(1), "is_oa": True, "landing_page_url": "https://example.org/w1"},
        "locations": [
            {"source": _source(1), "is_oa": True, "landing_page_url": "https://example.org/w1",
             "pdf_url": "https://example.org/w1.pdf", "license": "cc-by", "version": "publishedVersion"},
            {"source": None, "landing_page_url": "https://repo.example.org/w1"},
        ],
        "authorships": [
            {"author_position": "first", "is_corresponding": True, "raw_author_name": "Ada L.",
             "author": {"id": "https://openalex.org/A1", "display_name": "Ada Lovelace",
                        "orcid": "https://orcid.org/0000-0002-1825-0097"},
             "institutions": [{"id": "https://openalex.org/I1", "display_name": "Inst One",
                               "ror": "https://ror.org/02n415q13", "country_code": "GB", "type": "education"}]},
            {"author_position": "last", "author": {"id": f"https://openalex.org/{second_author}", "display_name": "Alan"},
             "institutions": [{"id": "https://openalex.org/I1", "display_name": "Inst One"},
                              {"id": "https://openalex.org/I2", "display_name": "Inst Two"}]},
        ],
        "topics": [{"id": "https://openalex.org/T1", "display_name": "Topic One", "score": 0.9},
                   {"id": "https://openalex.org/T2", "display_name": "Topic Two", "score": 0.4}],
        "keywords": [{"id": "https://openalex.org/keywords/machine-learning", "display_name": "Machine learning",
                      "score": 0.5}],
        "mesh": [{"descriptor_ui": "D000001", "descriptor_name": "Descriptor", "qualifier_ui": "Q000001",
                  "qualifier_name": "Qualifier", "is_major_topic": True},
                 {"descriptor_ui": "D000002", "descriptor_name": "Other", "qualifier_ui": None}],
        "referenced_works": ["https://openalex.org/W2", "not-a-work", ""],
        "grants": [{"funder": "https://openalex.org/F1", "funder_display_name": "Funder One", "award_id": "AWARD-1"},
                   {"funder": None, "award_id": None}],
    }


def bare_work(n: int) -> dict[str, Any]:
    return {"id": f"https://openalex.org/W{n}"}


def empty_arrays_work(n: int, *, title: str) -> dict[str, Any]:
    return {"id": f"https://openalex.org/W{n}", "title": title, "publication_year": 2019, "authorships": [],
            "topics": None, "referenced_works": ["https://openalex.org/W1"], "keywords": [], "mesh": [],
            "locations": [], "grants": [], "updated_date": "2024-02-01"}


def coauthor_work(n: int) -> dict[str, Any]:
    return {"id": f"https://openalex.org/W{n}", "title": f"Work {n}", "authorships": [
        {"author_position": "first", "author": {"id": "https://openalex.org/A1", "display_name": "Ada Lovelace"},
         "institutions": [{"id": "https://openalex.org/I1", "display_name": "Inst One"}]}]}


@dataclass(frozen=True)
class FixtureAsset:
    name: str
    metadata: OpenAlexAssetMetadata | OpenAlexDeletionAssetMetadata
    payload: bytes


def _gzip(body: bytes) -> bytes:
    """gzip with mtime 0 and header OS byte 255 on every Python (3.12's ``gzip.compress`` keeps zlib's byte)."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as handle:
        handle.write(body)
    return buf.getvalue()


def works_asset(name: str, updated: date, records: Sequence[Mapping[str, Any]]) -> FixtureAsset:
    body = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records).encode()
    payload = _gzip(body)
    metadata = OpenAlexAssetMetadata.model_validate({
        "source": "openalex", "snapshot_date": date(2026, 9, 30), "entity": "works",
        "file_uri": f"s3://openalex/data/jsonl/works/updated_date={updated.isoformat()}/part_{name}.gz",
        "byte_size": len(payload), "record_count": len(records), "updated_date": updated, "content_format": "jsonl",
    })
    return FixtureAsset(name, metadata, payload)


def deletion_asset(name: str, snapshot: date, rows: Sequence[tuple[str, date]]) -> FixtureAsset:
    body = "work_id,deleted_date\n" + "".join(f"{w},{d.isoformat()}\n" for w, d in rows)
    payload = _gzip(body.encode())
    metadata = OpenAlexDeletionAssetMetadata.model_validate({
        "source": "openalex", "snapshot_date": snapshot, "entity": "works-deletions", "file_uri": DELETION_URI,
        "byte_size": len(payload), "updated_date": snapshot, "content_format": "csv",
    })
    return FixtureAsset(name, metadata, payload)


def fixture_assets() -> dict[str, FixtureAsset]:
    return {
        "a1": works_asset("a1", date(2026, 9, 1), [rich_work(), bare_work(2),
                                                   empty_arrays_work(3, title="To be deleted"), coauthor_work(4)]),
        "d1": deletion_asset("d1", date(2026, 9, 2), [("W3", date(2026, 9, 2)), ("W9", date(2026, 9, 2)),
                                                       ("W3", date(2026, 9, 2))]),
        "a2": works_asset("a2", date(2026, 9, 1), [empty_arrays_work(9, title="Stale after barrier"),
                                                   coauthor_work(5)]),
        "a3": works_asset("a3", date(2026, 9, 5), [rich_work(title="Revised work", second_author="A3")]),
    }


class FixtureConnector:
    """Injected source: serves one fixture asset; never reaches OpenAlex."""

    def __init__(self, asset: FixtureAsset) -> None:
        self.asset = asset

    def discover_metadata(self) -> OpenAlexSampleSelection:
        assert isinstance(self.asset.metadata, OpenAlexAssetMetadata)
        return OpenAlexSampleSelection(max_files=1, max_file_size_bytes=MAX_BYTES, eligible_count=1,
                                       selected=(self.asset.metadata,), skipped=())

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        return io.BytesIO(self.asset.payload)


class StepClock:
    def __init__(self, start: datetime = CLOCK_START) -> None:
        self._now = start

    def __call__(self) -> datetime:
        now, self._now = self._now, self._now + timedelta(seconds=1)
        return now


def local_config(directory: Path, landing: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "local.yaml"
    path.write_text(yaml.safe_dump({
        "environment": "local", "log_level": "WARNING", "source": "openalex",
        "storage": {"backend": "local", "landing_path": str(landing)},
        "warehouse": {"transactional": "postgres", "analytical": "duckdb", "postgres_dsn_env": "POSTGRES_DSN",
                      "duckdb_path": ":memory:"},
        "sample_selection": {"max_files": 1, "max_file_size_bytes": MAX_BYTES},
    }), encoding="utf-8")
    return path


@dataclass
class LandedBronze:
    """Control and canonical state of the landing runs, their stats and one continuing clock."""

    control: InMemoryControlStore = field(default_factory=InMemoryControlStore)
    canonical: InMemoryCanonicalStore = field(default_factory=InMemoryCanonicalStore)
    clock: StepClock = field(default_factory=StepClock)
    asset_ids: dict[str, str] = field(default_factory=dict)
    specs: dict[str, ReleaseInputSpec] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)

    def specs_for(self, *names: str) -> tuple[ReleaseInputSpec, ...]:
        """Specs from the SUCCESS control rows, in application order."""
        return release_inputs_from_source_files(
            [self.control.get_source_file(self.asset_ids[name]) for name in names]
        )


def land(objects: ObjectStore, config_path: Path, assets: Sequence[FixtureAsset],
         *, landed: LandedBronze | None = None, **extra: Any) -> LandedBronze:
    """Ingest ``assets`` in order (one run each) through the real local wrappers."""
    state = landed or LandedBronze()
    for asset in assets:
        common: dict[str, Any] = dict(backend="memory", control_store=state.control,
                                      canonical_store=state.canonical, object_store=objects,
                                      connector=FixtureConnector(asset), now=state.clock, **extra)
        if isinstance(asset.metadata, OpenAlexDeletionAssetMetadata):
            result = run_openalex_deletions_local_ingest(config_path, deletion_asset=asset.metadata, **common)
        else:
            result = run_openalex_works_local_ingest(config_path, **common)
        assert result.run.status is PipelineRunStatus.SUCCESS, asset.name
        row = state.control.get_source_file(asset.metadata.asset_id)
        spec = release_input_from_source_file(row)
        assert all(spec.processed_at > prior.processed_at for prior in state.specs.values()), asset.name
        state.asset_ids[asset.name] = asset.metadata.asset_id
        state.specs[asset.name] = spec
        state.stats[asset.name] = result.stats
    return state
