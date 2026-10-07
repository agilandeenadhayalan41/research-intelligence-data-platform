"""PostgreSQL CanonicalStore for durable Work/relationship upserts."""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg.rows import dict_row

from research_platform.canonical.openalex.models import (
    Author,
    CanonicalWorkBundle,
    Funder,
    Institution,
    Publisher,
    Source,
    Topic,
    Work,
)
from research_platform.canonical.openalex.reconciliation import (
    EntityReconciliation,
    merge_shared_entity,
    reconcile_shared_entity,
)
from research_platform.canonical.openalex.versioning import (
    VersionComparison,
    compare_work_versions,
)
from research_platform.canonical.store import (
    CanonicalConflictError,
    CanonicalStore,
    CanonicalUpsertOutcome,
)
from research_platform.persistence.postgres.row_codec import lineage_columns, lineage_from_row

_Shared = Author | Institution | Source | Publisher | Topic | Funder


class PostgresCanonicalStore(CanonicalStore):
    def __init__(self, connection: psycopg.Connection) -> None:
        self._conn = connection

    def get_work(self, work_id: str) -> Work | None:
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM works WHERE work_id = %s", (work_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return _work_from_row(row)

    def work_count(self) -> int:
        with self._conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM works")
            value = cur.fetchone()
        assert value is not None
        return int(value[0])

    def relationship_counts(self) -> dict[str, int]:
        tables = (
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
        )
        counts: dict[str, int] = {}
        with self._conn.cursor() as cur:
            for table in tables:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                value = cur.fetchone()
                assert value is not None
                counts[table] = int(value[0])
        return counts

    def upsert_work_bundle(self, bundle: CanonicalWorkBundle) -> CanonicalUpsertOutcome:
        with self._conn.transaction():
            return upsert_work_bundle(self._conn, bundle)


def upsert_work_bundle(
    connection: psycopg.Connection, bundle: CanonicalWorkBundle
) -> CanonicalUpsertOutcome:
    """Upsert one bundle using the caller's open transaction."""
    existing = _load_work(connection, bundle.work.work_id)
    if existing is None:
        _apply_bundle(connection, bundle, replace_relationships=True)
        return CanonicalUpsertOutcome.INSERTED
    comparison = compare_work_versions(existing, bundle.work)
    if comparison is VersionComparison.IDENTICAL:
        _upsert_shared_entities(connection, bundle)
        return CanonicalUpsertOutcome.IDENTICAL
    if comparison is VersionComparison.STALE:
        _upsert_shared_entities(connection, bundle)
        return CanonicalUpsertOutcome.STALE
    if comparison is VersionComparison.CONFLICT:
        raise CanonicalConflictError(
            f"conflicting Work versions for {bundle.work.work_id}"
        )
    _apply_bundle(connection, bundle, replace_relationships=True)
    return CanonicalUpsertOutcome.REPLACED


def _load_work(connection: psycopg.Connection, work_id: str) -> Work | None:
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM works WHERE work_id = %s", (work_id,))
        row = cur.fetchone()
    return None if row is None else _work_from_row(row)


def _apply_bundle(
    connection: psycopg.Connection,
    bundle: CanonicalWorkBundle,
    *,
    replace_relationships: bool,
) -> None:
    _upsert_shared_entities(connection, bundle)
    _upsert_work_row(connection, bundle.work)
    work_id = bundle.work.work_id
    if replace_relationships:
        _clear_work_relationships(connection, work_id)
    for row in bundle.work_authors:
        _insert_simple(
            connection,
            "work_authors",
            {
                "work_id": row.work_id,
                "authorship_index": row.authorship_index,
                "author_id": row.author_id,
                "author_position": row.author_position,
                "is_corresponding": row.is_corresponding,
                "raw_author_name": row.raw_author_name,
                **lineage_columns(row.lineage),
            },
        )
    for row in bundle.work_author_institutions:
        _insert_simple(
            connection,
            "work_author_institutions",
            {
                "work_id": row.work_id,
                "authorship_index": row.authorship_index,
                "institution_index": row.institution_index,
                "institution_id": row.institution_id,
                **lineage_columns(row.lineage),
            },
        )
    for row in bundle.work_topics:
        _insert_simple(
            connection,
            "work_topics",
            {
                "work_id": row.work_id,
                "topic_id": row.topic_id,
                "score": row.score,
                **lineage_columns(row.lineage),
            },
        )
    for row in bundle.work_keywords:
        _insert_simple(
            connection,
            "work_keywords",
            {
                "work_id": row.work_id,
                "keyword_id": row.keyword_id,
                "display_name": row.display_name,
                "score": row.score,
                **lineage_columns(row.lineage),
            },
        )
    for row in bundle.work_references:
        _insert_simple(
            connection,
            "work_references",
            {
                "work_id": row.work_id,
                "reference_index": row.reference_index,
                "referenced_work_id": row.referenced_work_id,
                "raw_reference": row.raw_reference,
                "reference_status": row.reference_status.value,
                **lineage_columns(row.lineage),
            },
        )
    for row in bundle.work_mesh:
        _insert_simple(
            connection,
            "work_mesh",
            {
                "work_id": row.work_id,
                "mesh_index": row.mesh_index,
                "descriptor_ui": row.descriptor_ui,
                "descriptor_name": row.descriptor_name,
                "qualifier_ui": row.qualifier_ui,
                "qualifier_name": row.qualifier_name,
                "is_major_topic": row.is_major_topic,
                **lineage_columns(row.lineage),
            },
        )
    for row in bundle.work_locations:
        _insert_simple(
            connection,
            "work_locations",
            {
                "work_id": row.work_id,
                "location_index": row.location_index,
                "location_origin": row.location_origin.value,
                "source_id": row.source_id,
                "is_oa": row.is_oa,
                "landing_page_url": row.landing_page_url,
                "pdf_url": row.pdf_url,
                "license": row.license,
                "version": row.version,
                "is_primary": row.is_primary,
                **lineage_columns(row.lineage),
            },
        )
    for row in bundle.work_grants:
        _insert_simple(
            connection,
            "work_grants",
            {
                "work_id": row.work_id,
                "grant_index": row.grant_index,
                "funder_id": row.funder_id,
                "award_id": row.award_id,
                "funder_display_name": row.funder_display_name,
                **lineage_columns(row.lineage),
            },
        )


def _clear_work_relationships(connection: psycopg.Connection, work_id: str) -> None:
    tables = (
        "work_author_institutions",
        "work_authors",
        "work_topics",
        "work_keywords",
        "work_references",
        "work_mesh",
        "work_locations",
        "work_grants",
    )
    with connection.cursor() as cur:
        for table in tables:
            cur.execute(f"DELETE FROM {table} WHERE work_id = %s", (work_id,))


def _upsert_work_row(connection: psycopg.Connection, work: Work) -> None:
    params = {
        "work_id": work.work_id,
        "work_id_url": work.work_id_url,
        "doi": work.doi,
        "title": work.title,
        "publication_year": work.publication_year,
        "publication_date": work.publication_date,
        "work_type": work.work_type,
        "language": work.language,
        "cited_by_count": work.cited_by_count,
        "referenced_works_count": work.referenced_works_count,
        "is_oa": work.is_oa,
        "oa_status": work.oa_status,
        "primary_source_id": work.primary_source_id,
        "primary_publisher_id": work.primary_publisher_id,
        "source_created_date": work.source_created_date,
        "source_updated_date": work.source_updated_date,
        "authorships_presence": work.authorships_presence.value,
        "topics_presence": work.topics_presence.value,
        "keywords_presence": work.keywords_presence.value,
        "mesh_presence": work.mesh_presence.value,
        "referenced_works_presence": work.referenced_works_presence.value,
        "locations_presence": work.locations_presence.value,
        "grants_presence": work.grants_presence.value,
        **lineage_columns(work.lineage),
    }
    columns = ", ".join(params)
    placeholders = ", ".join(f"%({name})s" for name in params)
    updates = ", ".join(
        f"{name} = EXCLUDED.{name}" for name in params if name != "work_id"
    )
    with connection.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO works ({columns}) VALUES ({placeholders})
            ON CONFLICT (work_id) DO UPDATE SET {updates}
            """,
            params,
        )


def _upsert_shared_entities(
    connection: psycopg.Connection, bundle: CanonicalWorkBundle
) -> None:
    for author in bundle.authors:
        _reconcile_entity(connection, "authors", "author_id", author)
    for institution in bundle.institutions:
        _reconcile_entity(connection, "institutions", "institution_id", institution)
    for source in bundle.sources:
        _reconcile_entity(connection, "sources", "source_id", source)
    for publisher in bundle.publishers:
        _reconcile_entity(connection, "publishers", "publisher_id", publisher)
    for topic in bundle.topics:
        _reconcile_entity(connection, "topics", "topic_id", topic)
    for funder in bundle.funders:
        _reconcile_entity(connection, "funders", "funder_id", funder)


def _reconcile_entity(
    connection: psycopg.Connection,
    table: str,
    id_field: str,
    incoming: _Shared,
) -> None:
    existing = _load_shared(connection, table, id_field, getattr(incoming, id_field))
    if existing is None:
        _write_shared(connection, table, id_field, incoming)
        return
    outcome = reconcile_shared_entity(existing, incoming)
    if outcome is EntityReconciliation.IDENTICAL:
        return
    if outcome is EntityReconciliation.ENRICH:
        _write_shared(connection, table, id_field, merge_shared_entity(existing, incoming))
        return
    if outcome is EntityReconciliation.NEWER:
        _write_shared(connection, table, id_field, incoming)
        return
    if outcome is EntityReconciliation.STALE:
        return
    raise CanonicalConflictError(
        f"conflicting shared entity values for {type(incoming).__name__}"
    )


def _load_shared(
    connection: psycopg.Connection, table: str, id_field: str, entity_id: str
) -> _Shared | None:
    with connection.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT * FROM {table} WHERE {id_field} = %s", (entity_id,))
        row = cur.fetchone()
    if row is None:
        return None
    return _shared_from_row(table, row)


def _write_shared(
    connection: psycopg.Connection, table: str, id_field: str, entity: _Shared
) -> None:
    payload = entity.model_dump(mode="python", exclude={"lineage"})
    params = {**payload, **lineage_columns(entity.lineage)}
    columns = ", ".join(params)
    placeholders = ", ".join(f"%({name})s" for name in params)
    updates = ", ".join(
        f"{name} = EXCLUDED.{name}" for name in params if name != id_field
    )
    with connection.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {table} ({columns}) VALUES ({placeholders})
            ON CONFLICT ({id_field}) DO UPDATE SET {updates}
            """,
            params,
        )


def _insert_simple(
    connection: psycopg.Connection, table: str, params: dict[str, Any]
) -> None:
    columns = ", ".join(params)
    placeholders = ", ".join(f"%({name})s" for name in params)
    with connection.cursor() as cur:
        cur.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
            params,
        )


def _work_from_row(row: dict[str, Any]) -> Work:
    payload = dict(row)
    lineage = lineage_from_row(payload)
    for key in (
        "source_asset_id",
        "source_checksum_sha256",
        "lineage_source_updated_date",
        "run_id",
        "processed_at",
        "activity_state",
        "deleted_at",
    ):
        payload.pop(key, None)
    payload["lineage"] = lineage
    return Work.model_validate(payload)


def _shared_from_row(table: str, row: dict[str, Any]) -> _Shared:
    model = {
        "authors": Author,
        "institutions": Institution,
        "sources": Source,
        "publishers": Publisher,
        "topics": Topic,
        "funders": Funder,
    }[table]
    payload = dict(row)
    lineage = lineage_from_row(payload)
    for key in (
        "source_asset_id",
        "source_checksum_sha256",
        "lineage_source_updated_date",
        "run_id",
        "processed_at",
        "activity_state",
        "deleted_at",
    ):
        payload.pop(key, None)
    # Rename DB columns that differ from model field names where needed.
    if table == "sources" and "source_type" in payload:
        pass
    if table == "institutions" and "institution_type" in payload:
        pass
    payload["lineage"] = lineage
    return model.model_validate(payload)
