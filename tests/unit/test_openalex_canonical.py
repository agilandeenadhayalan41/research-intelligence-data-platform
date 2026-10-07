"""Offline tests for the OpenAlex canonical logical model (Step 11)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow as pa
import pytest
from pydantic import ValidationError

from research_platform.canonical.openalex import (
    CANONICAL_SCHEMAS,
    ArrayPresence,
    CanonicalActivityState,
    CanonicalLineage,
    IdentifierError,
    MappingError,
    ReferenceStatus,
    VersionComparison,
    compare_work_versions,
    map_openalex_work,
    normalize_doi,
    normalize_openalex_id,
    normalize_orcid,
)
from research_platform.canonical.openalex.models import Work


def _lineage(**overrides: object) -> CanonicalLineage:
    payload: dict[str, object] = {
        "source_asset_id": "oa-" + ("a" * 64),
        "source_checksum_sha256": "ab" * 32,
        "source_updated_date": date(2024, 1, 10),
        "run_id": uuid4(),
        "processed_at": datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
        "activity_state": CanonicalActivityState.ACTIVE,
    }
    payload.update(overrides)
    return CanonicalLineage.model_validate(payload)


def _simple_work() -> dict[str, object]:
    return {
        "id": "https://openalex.org/W100",
        "doi": "https://doi.org/10.1234/example",
        "title": "Simple work",
        "publication_year": 2020,
        "publication_date": "2020-05-01",
        "type": "article",
        "language": "en",
        "cited_by_count": 3,
        "referenced_works_count": 2,
        "open_access": {"is_oa": True, "oa_status": "gold"},
        "created_date": "2020-01-01",
        "updated_date": "2024-01-10",
        "authorships": [],
        "topics": [],
        "keywords": [],
        "mesh": [],
        "referenced_works": [],
        "locations": [],
        "grants": [],
    }


def test_normalize_openalex_and_doi_orcid() -> None:
    assert normalize_openalex_id("https://openalex.org/W1", expected_prefix="W") == "W1"
    assert normalize_doi("https://doi.org/10.1/ABC") == "10.1/abc"
    assert normalize_orcid("https://orcid.org/0000-0002-1825-0097") == "0000-0002-1825-0097"
    with pytest.raises(IdentifierError):
        normalize_openalex_id("not-an-id")
    with pytest.raises(IdentifierError):
        normalize_openalex_id("https://openalex.org/A1", expected_prefix="W")


def test_simple_work_mapping() -> None:
    bundle = map_openalex_work(_simple_work(), lineage=_lineage())
    assert bundle.work.work_id == "W100"
    assert bundle.work.doi == "10.1234/example"
    assert bundle.work.authorships_presence is ArrayPresence.EMPTY
    assert bundle.work_authors == ()
    assert bundle.work.lineage.activity_state is CanonicalActivityState.ACTIVE


def test_cartesian_fanout_prevention() -> None:
    record = {
        "id": "W200",
        "title": "Fanout",
        "authorships": [
            {
                "author_position": "first",
                "is_corresponding": True,
                "author": {"id": "A1", "display_name": "Ada"},
                "institutions": [
                    {"id": "I1", "display_name": "Inst One", "ror": "https://ror.org/02n415q13"},
                    {"id": "I2", "display_name": "Inst Two", "ror": "02m4k8h59"},
                ],
            },
            {
                "author_position": "last",
                "author": {"id": "A2", "display_name": "Alan"},
                "institutions": [{"id": "I3", "display_name": "Inst Three"}],
            },
        ],
        "topics": [
            {"id": "T1", "display_name": "Topic 1", "score": 0.9},
            {"id": "T2", "display_name": "Topic 2", "score": 0.8},
            {"id": "T3", "display_name": "Topic 3", "score": 0.7},
        ],
        "referenced_works": ["W9", "W8"],
    }
    bundle = map_openalex_work(record, lineage=_lineage())
    assert len(bundle.work_authors) == 2
    assert len(bundle.work_author_institutions) == 3
    assert len(bundle.work_topics) == 3
    assert len(bundle.work_references) == 2
    # Critical: not 2 * 3 * 2 * institutions
    assert len(bundle.work_authors) * len(bundle.work_topics) * len(
        bundle.work_references
    ) != len(bundle.work_author_institutions)
    assert {(row.authorship_index, row.institution_index, row.institution_id)
            for row in bundle.work_author_institutions} == {
        (0, 0, "I1"),
        (0, 1, "I2"),
        (1, 0, "I3"),
    }
    assert bundle.work_authors[0].author_id == "A1"
    assert bundle.work_authors[0].is_corresponding is True
    assert bundle.work_authors[1].author_position == "last"


def test_repeated_authors_keep_distinct_authorship_indexes() -> None:
    record = {
        "id": "W201",
        "authorships": [
            {"author": {"id": "A1"}, "author_position": "first", "institutions": []},
            {"author": {"id": "A1"}, "author_position": "last", "institutions": []},
        ],
    }
    bundle = map_openalex_work(record, lineage=_lineage())
    assert len(bundle.authors) == 1
    assert [row.authorship_index for row in bundle.work_authors] == [0, 1]


def test_multi_affiliation_and_topics_keywords_mesh_references() -> None:
    record = {
        "id": "W202",
        "authorships": [
            {
                "author": {"id": "A9", "orcid": "0000-0002-1825-0097"},
                "institutions": [{"id": "I9"}, {"id": "I8"}],
            }
        ],
        "topics": [{"id": "T9", "score": 0.5}],
        "keywords": [
            {
                "id": "https://openalex.org/keywords/machine-learning",
                "display_name": "Machine learning",
                "score": 0.4,
            }
        ],
        "mesh": [
            {
                "descriptor_ui": "D0001",
                "descriptor_name": "Desc",
                "qualifier_ui": "Q1",
                "is_major_topic": True,
            }
        ],
        "referenced_works": [
            "https://openalex.org/W777",
            "not-a-work",
            "",
        ],
    }
    bundle = map_openalex_work(record, lineage=_lineage())
    assert len(bundle.work_author_institutions) == 2
    assert bundle.authors[0].orcid == "0000-0002-1825-0097"
    assert bundle.work_keywords[0].keyword_id == "keywords/machine-learning"
    assert bundle.work_mesh[0].descriptor_ui == "D0001"
    statuses = {row.reference_status for row in bundle.work_references}
    assert ReferenceStatus.RESOLVED_ID in statuses
    assert ReferenceStatus.MALFORMED in statuses
    assert ReferenceStatus.MISSING in statuses


def test_missing_doi_and_empty_arrays() -> None:
    record = {"id": "W203", "title": "No DOI", "authorships": [], "topics": None}
    bundle = map_openalex_work(record, lineage=_lineage())
    assert bundle.work.doi is None
    assert bundle.work.authorships_presence is ArrayPresence.EMPTY
    assert bundle.work.topics_presence is ArrayPresence.NULL


def test_missing_source_arrays() -> None:
    bundle = map_openalex_work({"id": "W204"}, lineage=_lineage())
    assert bundle.work.authorships_presence is ArrayPresence.MISSING
    assert bundle.work_authors == ()


def test_malformed_work_id_fails() -> None:
    with pytest.raises(IdentifierError):
        map_openalex_work({"id": "bad"}, lineage=_lineage())


def test_publisher_source_and_grants() -> None:
    record = {
        "id": "W205",
        "primary_location": {
            "source": {
                "id": "S1",
                "display_name": "Journal",
                "type": "journal",
                "issn_l": "1234-5678",
                "host_organization": "https://openalex.org/P1",
                "host_organization_name": "Pub One",
            },
            "is_oa": True,
            "landing_page_url": "https://example.org/paper",
        },
        "locations": [
            {
                "source": {
                    "id": "S1",
                    "display_name": "Journal",
                    "issn_l": "1234-5678",
                    "host_organization": "P1",
                    "host_organization_name": "Pub One",
                },
                "landing_page_url": "https://example.org/paper",
                "is_oa": True,
            }
        ],
        "grants": [
            {
                "funder": "F1",
                "funder_display_name": "Funder One",
                "award_id": "AWARD-1",
            },
            {"funder": "F1", "award_id": "AWARD-1"},  # duplicate
        ],
    }
    bundle = map_openalex_work(record, lineage=_lineage())
    assert bundle.work.primary_source_id == "S1"
    assert bundle.work.primary_publisher_id == "P1"
    assert len(bundle.sources) == 1
    assert bundle.sources[0].issn_l == "1234-5678"
    assert len(bundle.publishers) == 1
    assert len(bundle.work_locations) == 1
    assert bundle.work_locations[0].is_primary is True
    assert len(bundle.work_grants) == 1
    assert bundle.work_grants[0].award_id == "AWARD-1"


def test_duplicate_topics_deduped() -> None:
    record = {
        "id": "W206",
        "topics": [{"id": "T1"}, {"id": "T1", "score": 0.1}],
    }
    bundle = map_openalex_work(record, lineage=_lineage())
    assert len(bundle.work_topics) == 1


def test_lineage_and_deleted_state() -> None:
    active = _lineage()
    deleted = _lineage(
        activity_state=CanonicalActivityState.DELETED,
        deleted_at=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC),
    )
    bundle = map_openalex_work({"id": "W207"}, lineage=deleted)
    assert bundle.work.lineage.activity_state is CanonicalActivityState.DELETED
    with pytest.raises(ValidationError):
        _lineage(activity_state=CanonicalActivityState.DELETED, deleted_at=None)
    assert active.activity_state is CanonicalActivityState.ACTIVE


def test_version_precedence() -> None:
    base = map_openalex_work(
        {"id": "W208", "updated_date": "2024-01-01"},
        lineage=_lineage(source_checksum_sha256="aa" * 32, source_updated_date=date(2024, 1, 1)),
    ).work
    identical = map_openalex_work(
        {"id": "W208", "updated_date": "2024-01-01"},
        lineage=_lineage(source_checksum_sha256="aa" * 32, source_updated_date=date(2024, 1, 1)),
    ).work
    newer = map_openalex_work(
        {"id": "W208", "updated_date": "2024-02-01"},
        lineage=_lineage(source_checksum_sha256="bb" * 32, source_updated_date=date(2024, 2, 1)),
    ).work
    stale = map_openalex_work(
        {"id": "W208", "updated_date": "2023-01-01"},
        lineage=_lineage(source_checksum_sha256="cc" * 32, source_updated_date=date(2023, 1, 1)),
    ).work
    conflict = map_openalex_work(
        {"id": "W208", "updated_date": "2024-01-01"},
        lineage=_lineage(source_checksum_sha256="dd" * 32, source_updated_date=date(2024, 1, 1)),
    ).work
    assert compare_work_versions(base, identical) is VersionComparison.IDENTICAL
    assert compare_work_versions(base, newer) is VersionComparison.NEWER
    assert compare_work_versions(base, stale) is VersionComparison.STALE
    assert compare_work_versions(base, conflict) is VersionComparison.CONFLICT


def test_pyarrow_schema_contracts() -> None:
    assert set(CANONICAL_SCHEMAS) >= {
        "works",
        "authors",
        "institutions",
        "sources",
        "publishers",
        "topics",
        "funders",
        "work_authors",
        "work_author_institutions",
        "work_topics",
        "work_keywords",
        "work_references",
        "work_mesh",
        "work_locations",
        "work_grants",
    }
    works = CANONICAL_SCHEMAS["works"]
    assert works.field("work_id").type == pa.string()
    assert works.field("work_id").nullable is False
    assert works.field("doi").nullable is True
    assert [field.name for field in works][:3] == ["work_id", "work_id_url", "doi"]
    assert "source_checksum_sha256" in works.names
    assert "run_id" in works.names
    rel = CANONICAL_SCHEMAS["work_author_institutions"]
    assert rel.field("authorship_index").type == pa.int32()
    assert rel.field("institution_index").type == pa.int32()


def test_ddl_contract_present(repo_root: Path) -> None:
    ddl = (repo_root / "sql/canonical/001_openalex_canonical.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS works" in ddl
    assert "work_author_institutions" in ddl
    assert "PostgreSQL 16+" in ddl
    assert "BigQuery" in ddl
    assert "PRIMARY KEY (work_id, authorship_index)" in ddl


def test_invalid_array_type_fails() -> None:
    with pytest.raises(MappingError):
        map_openalex_work({"id": "W209", "authorships": {}}, lineage=_lineage())


def test_work_model_rejects_bad_id() -> None:
    with pytest.raises(ValidationError):
        Work.model_validate(
            {
                "work_id": "bad",
                "work_id_url": "https://openalex.org/bad",
                "authorships_presence": "MISSING",
                "topics_presence": "MISSING",
                "keywords_presence": "MISSING",
                "mesh_presence": "MISSING",
                "referenced_works_presence": "MISSING",
                "locations_presence": "MISSING",
                "grants_presence": "MISSING",
                "lineage": _lineage(),
            }
        )
