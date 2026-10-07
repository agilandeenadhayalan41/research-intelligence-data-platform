"""One-asset publication transaction: claim check + canonical + provenance + SUCCESS."""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from research_platform.control.lifecycle import (
    apply_source_file_success,
    assert_claim_owned,
)
from research_platform.persistence.postgres.canonical_store import upsert_work_bundle
from research_platform.persistence.postgres.control_store import PostgresControlStore
from research_platform.persistence.postgres.row_codec import source_file_from_row
from research_platform.persistence.unit_of_work import (
    AssetPublishRequest,
    AssetPublishResult,
)

__all__ = ["AssetPublishRequest", "AssetPublishResult", "publish_claimed_asset"]


def publish_claimed_asset(
    connection: psycopg.Connection, request: AssetPublishRequest
) -> AssetPublishResult:
    """Commit canonical rows, provenance, and SUCCESS in one PostgreSQL transaction.

    Raw ObjectStore landing is intentionally outside this transaction. On failure the
    database rolls back; immutable raw bytes may remain for explicit retry.
    """
    # retrieval_provenance is ObjectStore sidecar metadata; SUCCESS records key/checksum.
    _ = request.retrieval_provenance
    control = PostgresControlStore(connection)
    with connection.transaction():
        with connection.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT * FROM source_files WHERE asset_id = %s FOR UPDATE",
                (request.asset_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise LookupError(f"source file not found: {request.asset_id}")
        existing = source_file_from_row(row)
        assert_claim_owned(
            existing, claim_token=request.claim_token, now=request.now
        )

        outcomes = []
        for bundle in request.bundles:
            outcomes.append(upsert_work_bundle(connection, bundle))

        for provenance in request.provenance_rows:
            control._upsert_provenance(provenance)  # noqa: SLF001 - same txn

        succeeded = apply_source_file_success(
            existing,
            claim_token=request.claim_token,
            processed_at=request.processed_at,
            raw_object_key=request.raw_object_key,
            source_checksum_sha256=request.source_checksum_sha256,
            updated_at=request.processed_at,
            now=request.now,
        )
        stored = control._update_file(succeeded)  # noqa: SLF001 - same txn
        return AssetPublishResult(source_file=stored, outcomes=tuple(outcomes))
