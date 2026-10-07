-- Pipeline control schema specification (Step 10 / issue #18)
--
-- Target dialect: PostgreSQL 16+ (matches docker-compose local tooling).
-- Versioned contract for review and for the optional local Step 12 durable
-- ingestion path (PostgresControlStore / apply_ingestion_schema).
-- Default offline tests and CI do NOT require applying this DDL.
-- DuckDB or mock validation does NOT prove PostgreSQL dialect compatibility.
--
-- Logical tables:
--   pipeline_runs      mutable run control
--   source_files       mutable per-asset control + claim/lease
--   record_provenance  record-level lineage (no payload)
--
-- Immutable retrieval provenance remains object-store sidecar metadata
-- (IngestionProvenance / provenance.json), not duplicated as authoritative
-- payload storage here.

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id UUID PRIMARY KEY,
    source TEXT NOT NULL,
    pipeline_name TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    started_at TIMESTAMPTZ NULL,
    completed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    failure_category TEXT NULL,
    failure_message TEXT NULL,
    CONSTRAINT pipeline_runs_status_check
        CHECK (status IN ('PENDING', 'PROCESSING', 'SUCCESS', 'FAILED')),
    CONSTRAINT pipeline_runs_attempt_check
        CHECK (attempt >= 1),
    CONSTRAINT pipeline_runs_updated_after_created_check
        CHECK (updated_at >= created_at),
    CONSTRAINT pipeline_runs_temporal_order_check
        CHECK (
            (started_at IS NULL OR (started_at >= created_at AND updated_at >= started_at))
            AND (
                completed_at IS NULL
                OR (
                    started_at IS NOT NULL
                    AND completed_at >= started_at
                    AND updated_at >= completed_at
                )
            )
        ),
    CONSTRAINT pipeline_runs_completed_after_started_check
        CHECK (
            completed_at IS NULL
            OR (started_at IS NOT NULL AND completed_at >= started_at)
        ),
    CONSTRAINT pipeline_runs_pending_shape_check
        CHECK (
            status <> 'PENDING'
            OR (
                started_at IS NULL
                AND completed_at IS NULL
                AND failure_category IS NULL
                AND failure_message IS NULL
            )
        ),
    CONSTRAINT pipeline_runs_processing_shape_check
        CHECK (
            status <> 'PROCESSING'
            OR (
                started_at IS NOT NULL
                AND completed_at IS NULL
                AND failure_category IS NULL
                AND failure_message IS NULL
            )
        ),
    CONSTRAINT pipeline_runs_success_shape_check
        CHECK (
            status <> 'SUCCESS'
            OR (
                started_at IS NOT NULL
                AND completed_at IS NOT NULL
                AND failure_category IS NULL
                AND failure_message IS NULL
            )
        ),
    CONSTRAINT pipeline_runs_failed_shape_check
        CHECK (
            status <> 'FAILED'
            OR (
                started_at IS NOT NULL
                AND completed_at IS NOT NULL
                AND failure_category IS NOT NULL
                AND failure_message IS NOT NULL
            )
        )
);

CREATE INDEX IF NOT EXISTS pipeline_runs_status_updated_idx
    ON pipeline_runs (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS pipeline_runs_source_pipeline_idx
    ON pipeline_runs (source, pipeline_name, created_at DESC);

CREATE TABLE IF NOT EXISTS source_files (
    asset_id TEXT PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES pipeline_runs (run_id),
    source TEXT NOT NULL,
    entity TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    snapshot_date DATE NOT NULL,
    updated_date DATE NULL,
    content_format TEXT NOT NULL,
    declared_size_bytes BIGINT NULL,
    source_checksum_sha256 TEXT NULL,
    raw_object_key TEXT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    claimed_by TEXT NULL,
    claim_token UUID NULL,
    claimed_at TIMESTAMPTZ NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    processed_at TIMESTAMPTZ NULL,
    failure_category TEXT NULL,
    failure_message TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT source_files_status_check
        CHECK (status IN ('DISCOVERED', 'PROCESSING', 'SUCCESS', 'FAILED')),
    CONSTRAINT source_files_format_check
        CHECK (content_format IN ('jsonl', 'parquet', 'csv')),
    CONSTRAINT source_files_attempt_check
        CHECK (attempt_count >= 0),
    CONSTRAINT source_files_size_check
        CHECK (declared_size_bytes IS NULL OR declared_size_bytes >= 0),
    CONSTRAINT source_files_checksum_check
        CHECK (
            source_checksum_sha256 IS NULL
            OR source_checksum_sha256 ~ '^[a-f0-9]{64}$'
        ),
    CONSTRAINT source_files_updated_after_created_check
        CHECK (updated_at >= created_at),
    CONSTRAINT source_files_claim_temporal_check
        CHECK (
            claimed_at IS NULL
            OR (claimed_at >= created_at AND updated_at >= claimed_at)
        ),
    CONSTRAINT source_files_processed_temporal_check
        CHECK (
            processed_at IS NULL
            OR (processed_at >= created_at AND updated_at >= processed_at)
        ),
    CONSTRAINT source_files_raw_object_key_shape_check
        CHECK (
            raw_object_key IS NULL
            OR (
                raw_object_key <> ''
                AND raw_object_key NOT LIKE '/%'
                AND position('..' IN raw_object_key) = 0
                AND position(CHR(92) IN raw_object_key) = 0
                AND position(' ' IN raw_object_key) = 0
                AND position('/' IN raw_object_key) > 0
            )
        ),
    CONSTRAINT source_files_claim_all_or_none_check
        CHECK (
            (
                claimed_by IS NULL
                AND claim_token IS NULL
                AND claimed_at IS NULL
                AND lease_expires_at IS NULL
            )
            OR (
                claimed_by IS NOT NULL
                AND claim_token IS NOT NULL
                AND claimed_at IS NOT NULL
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at > claimed_at
                AND claimed_at >= created_at
            )
        ),
    CONSTRAINT source_files_discovered_shape_check
        CHECK (
            status <> 'DISCOVERED'
            OR (
                attempt_count = 0
                AND claimed_by IS NULL
                AND processed_at IS NULL
                AND raw_object_key IS NULL
                AND failure_category IS NULL
                AND failure_message IS NULL
            )
        ),
    CONSTRAINT source_files_processing_shape_check
        CHECK (
            status <> 'PROCESSING'
            OR (
                attempt_count >= 1
                AND claimed_by IS NOT NULL
                AND claim_token IS NOT NULL
                AND claimed_at IS NOT NULL
                AND lease_expires_at IS NOT NULL
                AND processed_at IS NULL
                AND failure_category IS NULL
                AND failure_message IS NULL
            )
        ),
    CONSTRAINT source_files_success_shape_check
        CHECK (
            status <> 'SUCCESS'
            OR (
                attempt_count >= 1
                AND claimed_by IS NULL
                AND processed_at IS NOT NULL
                AND source_checksum_sha256 IS NOT NULL
                AND raw_object_key IS NOT NULL
                AND failure_category IS NULL
                AND failure_message IS NULL
            )
        ),
    CONSTRAINT source_files_failed_shape_check
        CHECK (
            status <> 'FAILED'
            OR (
                attempt_count >= 1
                AND claimed_by IS NULL
                AND processed_at IS NOT NULL
                AND failure_category IS NOT NULL
                AND failure_message IS NOT NULL
            )
        )
);

-- Stable source identity uniqueness beyond asset_id primary key.
CREATE UNIQUE INDEX IF NOT EXISTS source_files_identity_uidx
    ON source_files (source, entity, source_uri, snapshot_date, COALESCE(updated_date, DATE 'epoch'));

-- At most one active claim token globally while PROCESSING.
CREATE UNIQUE INDEX IF NOT EXISTS source_files_active_claim_token_uidx
    ON source_files (claim_token)
    WHERE status = 'PROCESSING' AND claim_token IS NOT NULL;

CREATE INDEX IF NOT EXISTS source_files_status_lease_idx
    ON source_files (status, lease_expires_at)
    WHERE status = 'PROCESSING';

CREATE INDEX IF NOT EXISTS source_files_run_id_idx
    ON source_files (run_id);

CREATE TABLE IF NOT EXISTS record_provenance (
    record_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    asset_id TEXT NOT NULL REFERENCES source_files (asset_id),
    source_checksum_sha256 TEXT NOT NULL,
    run_id UUID NOT NULL REFERENCES pipeline_runs (run_id),
    source_uri TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    source_updated_date DATE NULL,
    PRIMARY KEY (entity_type, record_id, asset_id, source_checksum_sha256),
    CONSTRAINT record_provenance_checksum_check
        CHECK (source_checksum_sha256 ~ '^[a-f0-9]{64}$')
);

CREATE INDEX IF NOT EXISTS record_provenance_asset_idx
    ON record_provenance (asset_id);

CREATE INDEX IF NOT EXISTS record_provenance_run_idx
    ON record_provenance (run_id);

-- Notes for later adapters (not enforced by this file alone):
-- 1. claim_source_file must use a single conditional UPDATE ... WHERE status IN
--    ('DISCOVERED','FAILED') AND (status <> 'PROCESSING' OR lease expired path
--    recovered first), returning exactly one row.
-- 2. mark_source_file_success/failed must require claim_token match and
--    lease_expires_at > now() (except explicit stale recovery).
-- 3. Do not map these writes through Warehouse.query().
