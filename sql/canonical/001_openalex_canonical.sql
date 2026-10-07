-- OpenAlex canonical logical schema specification (Step 11 / issue #15)
--
-- Target dialect: PostgreSQL 16+ for local contract review only.
-- PyArrow schemas in research_platform.canonical.openalex.schemas are the
-- portable source of truth. This SQL does NOT define the future BigQuery
-- physical model (Step 15) and is not auto-applied by tests/CI.
--
-- DuckDB validation of this file would not prove PostgreSQL or BigQuery
-- dialect compatibility.

CREATE TABLE IF NOT EXISTS works (
    work_id TEXT PRIMARY KEY,
    work_id_url TEXT NOT NULL,
    doi TEXT NULL,
    title TEXT NULL,
    publication_year INTEGER NULL,
    publication_date DATE NULL,
    work_type TEXT NULL,
    language TEXT NULL,
    cited_by_count BIGINT NULL,
    referenced_works_count BIGINT NULL,
    is_oa BOOLEAN NULL,
    oa_status TEXT NULL,
    primary_source_id TEXT NULL,
    primary_publisher_id TEXT NULL,
    source_created_date DATE NULL,
    source_updated_date DATE NULL,
    authorships_presence TEXT NOT NULL,
    topics_presence TEXT NOT NULL,
    keywords_presence TEXT NOT NULL,
    mesh_presence TEXT NOT NULL,
    referenced_works_presence TEXT NOT NULL,
    locations_presence TEXT NOT NULL,
    grants_presence TEXT NOT NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL,
    CONSTRAINT works_activity_check CHECK (activity_state IN ('ACTIVE', 'DELETED')),
    CONSTRAINT works_checksum_check CHECK (source_checksum_sha256 ~ '^[a-f0-9]{64}$')
);

CREATE TABLE IF NOT EXISTS authors (
    author_id TEXT PRIMARY KEY,
    author_id_url TEXT NOT NULL,
    display_name TEXT NULL,
    orcid TEXT NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS institutions (
    institution_id TEXT PRIMARY KEY,
    institution_id_url TEXT NOT NULL,
    display_name TEXT NULL,
    ror TEXT NULL,
    country_code TEXT NULL,
    institution_type TEXT NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    source_id_url TEXT NOT NULL,
    display_name TEXT NULL,
    source_type TEXT NULL,
    issn_l TEXT NULL,
    host_publisher_id TEXT NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS publishers (
    publisher_id TEXT PRIMARY KEY,
    publisher_id_url TEXT NOT NULL,
    display_name TEXT NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS topics (
    topic_id TEXT PRIMARY KEY,
    topic_id_url TEXT NOT NULL,
    display_name TEXT NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS funders (
    funder_id TEXT PRIMARY KEY,
    funder_id_url TEXT NOT NULL,
    display_name TEXT NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL
);

CREATE TABLE IF NOT EXISTS work_authors (
    work_id TEXT NOT NULL REFERENCES works (work_id),
    authorship_index INTEGER NOT NULL,
    author_id TEXT NULL,
    author_position TEXT NULL,
    is_corresponding BOOLEAN NULL,
    raw_author_name TEXT NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL,
    PRIMARY KEY (work_id, authorship_index)
);

CREATE TABLE IF NOT EXISTS work_author_institutions (
    work_id TEXT NOT NULL,
    authorship_index INTEGER NOT NULL,
    institution_index INTEGER NOT NULL,
    institution_id TEXT NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL,
    PRIMARY KEY (work_id, authorship_index, institution_index),
    FOREIGN KEY (work_id, authorship_index)
        REFERENCES work_authors (work_id, authorship_index)
);

CREATE TABLE IF NOT EXISTS work_topics (
    work_id TEXT NOT NULL REFERENCES works (work_id),
    topic_id TEXT NOT NULL,
    score DOUBLE PRECISION NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL,
    PRIMARY KEY (work_id, topic_id)
);

CREATE TABLE IF NOT EXISTS work_keywords (
    work_id TEXT NOT NULL REFERENCES works (work_id),
    keyword_id TEXT NOT NULL,
    display_name TEXT NULL,
    score DOUBLE PRECISION NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL,
    PRIMARY KEY (work_id, keyword_id)
);

CREATE TABLE IF NOT EXISTS work_references (
    work_id TEXT NOT NULL REFERENCES works (work_id),
    referenced_work_id TEXT NULL,
    raw_reference TEXT NULL,
    reference_status TEXT NOT NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL,
    CONSTRAINT work_references_status_check
        CHECK (reference_status IN ('RESOLVED_ID', 'MALFORMED', 'MISSING'))
);

CREATE UNIQUE INDEX IF NOT EXISTS work_references_resolved_uidx
    ON work_references (work_id, referenced_work_id)
    WHERE referenced_work_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS work_mesh (
    work_id TEXT NOT NULL REFERENCES works (work_id),
    descriptor_ui TEXT NOT NULL,
    descriptor_name TEXT NULL,
    qualifier_ui TEXT NOT NULL DEFAULT '',
    qualifier_name TEXT NULL,
    is_major_topic BOOLEAN NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL,
    PRIMARY KEY (work_id, descriptor_ui, qualifier_ui)
);

CREATE TABLE IF NOT EXISTS work_locations (
    work_id TEXT NOT NULL REFERENCES works (work_id),
    location_index INTEGER NOT NULL,
    source_id TEXT NULL,
    is_oa BOOLEAN NULL,
    landing_page_url TEXT NULL,
    pdf_url TEXT NULL,
    license TEXT NULL,
    version TEXT NULL,
    is_primary BOOLEAN NOT NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL,
    PRIMARY KEY (work_id, location_index)
);

CREATE TABLE IF NOT EXISTS work_grants (
    work_id TEXT NOT NULL REFERENCES works (work_id),
    funder_id TEXT NOT NULL DEFAULT '',
    award_id TEXT NOT NULL DEFAULT '',
    funder_display_name TEXT NULL,
    source_asset_id TEXT NOT NULL,
    source_checksum_sha256 TEXT NOT NULL,
    lineage_source_updated_date DATE NULL,
    run_id UUID NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    activity_state TEXT NOT NULL,
    deleted_at TIMESTAMPTZ NULL,
    PRIMARY KEY (work_id, funder_id, award_id)
);

-- Notes:
-- 1. Referenced works are not required to exist in works (unresolved external refs).
-- 2. CHECK constraints do not encode full lifecycle transitions.
-- 3. BigQuery partitioning/clustering is out of scope for this file.
