"""Safe TaskMessage / future Airflow XCom contract validation."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from research_platform.orchestration.models import (
    SchedulingClass,
    TaskId,
    TaskMessage,
)

# Keys that must never appear in task messages, XCom, or event details.
FORBIDDEN_MESSAGE_KEYS: frozenset[str] = frozenset(
    {
        "payload",
        "payload_bytes",
        "raw_bytes",
        "raw_payload",
        "arrow",
        "arrow_table",
        "dataframe",
        "df",
        "pandas",
        "duckdb",
        "duckdb_connection",
        "connection",
        "conn",
        "db_connection",
        "dsn",
        "postgres_dsn",
        "password",
        "secret",
        "secrets",
        "credential",
        "credentials",
        "token",
        "access_token",
        "api_key",
        "sql",
        "query",
        "full_sql",
        "records",
        "record_list",
        "canonical_snapshot",
        "gold_rows",
        "gold_table_contents",
        "table_contents",
    }
)


def assert_safe_message_keys(mapping: Mapping[str, Any] | None) -> None:
    """Raise ValueError if forbidden payload/credential/SQL keys are present."""
    if not mapping:
        return
    lowered = {str(k).strip().lower() for k in mapping}
    hits = sorted(lowered & {k.lower() for k in FORBIDDEN_MESSAGE_KEYS})
    if hits:
        raise ValueError(f"forbidden task message keys: {', '.join(hits)}")


def build_task_message(
    *,
    run_id: UUID,
    publication_version: str,
    source: str,
    task_id: TaskId,
    attempt: int,
    environment: str,
    asset_id: str | None = None,
    source_file_id: str | None = None,
    object_key_ref: str | None = None,
    quality_report_ref: str | None = None,
    publication_scope_ref: str | None = None,
    scheduling_class: SchedulingClass = SchedulingClass.MANUAL,
) -> TaskMessage:
    """Construct a validated TaskMessage (identifiers/metadata only)."""
    return TaskMessage.model_validate(
        {
            "run_id": run_id,
            "publication_version": publication_version,
            "source": source,
            "task_id": task_id,
            "attempt": attempt,
            "environment": environment,
            "asset_id": asset_id,
            "source_file_id": source_file_id,
            "object_key_ref": object_key_ref,
            "quality_report_ref": quality_report_ref,
            "publication_scope_ref": publication_scope_ref,
            "scheduling_class": scheduling_class,
        }
    )


def task_message_as_xcom_dict(message: TaskMessage) -> dict[str, Any]:
    """Serialize a TaskMessage for a future small Airflow XCom payload."""
    data = message.model_dump(mode="json")
    assert_safe_message_keys(data)
    return data
