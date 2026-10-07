-- Deletion event / outcome lineage (Step 13 / issue #21)
--
-- Additive audit of deletion applications. Does not erase ingestion
-- RecordProvenance. Target dialect: PostgreSQL 16+.
--
-- deleted_date is the per-row OpenAlex source deletion calendar date.

CREATE TABLE IF NOT EXISTS deletion_events (
    work_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    run_id UUID NOT NULL,
    source_uri TEXT NOT NULL,
    deleted_date DATE NOT NULL,
    source_updated_date DATE NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    outcome TEXT NOT NULL,
    CONSTRAINT deletion_events_pk
        PRIMARY KEY (work_id, asset_id, source_checksum_sha256),
    CONSTRAINT deletion_events_checksum_check
        CHECK (source_checksum_sha256 ~ '^[a-f0-9]{64}$'),
    CONSTRAINT deletion_events_outcome_check
        CHECK (
            outcome IN (
                'DELETED',
                'ALREADY_DELETED',
                'UNKNOWN_WORK',
                'STALE',
                'CONFLICT',
                'DUPLICATE'
            )
        )
);

CREATE INDEX IF NOT EXISTS deletion_events_asset_idx
    ON deletion_events (asset_id);

CREATE INDEX IF NOT EXISTS deletion_events_work_idx
    ON deletion_events (work_id);

-- deleted_date index is created in apply_ingestion_schema after the
-- ADD COLUMN IF NOT EXISTS migration for databases created before that column.
