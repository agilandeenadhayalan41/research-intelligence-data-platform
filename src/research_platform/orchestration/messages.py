"""Safe TaskMessage / future Airflow XCom contract validation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from research_platform.orchestration.models import (
    MAX_EVENT_DETAILS_JSON_BYTES,
    MAX_EVENT_DETAILS_KEYS,
    MAX_SAFE_STRUCTURE_DEPTH,
    MAX_SAFE_STRUCTURE_NODES,
    MAX_XCOM_JSON_BYTES,
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
        "traceback",
        "exception_traceback",
        "stack_trace",
    }
)


def assert_safe_message_keys(
    value: object,
    *,
    _depth: int = 0,
    _nodes: list[int] | None = None,
    max_depth: int = MAX_SAFE_STRUCTURE_DEPTH,
    max_nodes: int = MAX_SAFE_STRUCTURE_NODES,
) -> None:
    """Recursively reject forbidden payload/credential/SQL keys.

    Walks mappings and sequences with bounded depth and node count.
    Does not inspect or log secret values — only key names.
    """
    counter = _nodes if _nodes is not None else [0]
    counter[0] += 1
    if counter[0] > max_nodes:
        raise ValueError("safe metadata structure exceeds max node count")
    if _depth > max_depth:
        raise ValueError("safe metadata structure exceeds max nesting depth")

    if isinstance(value, Mapping):
        lowered = {str(k).strip().lower() for k in value}
        hits = sorted(lowered & {k.lower() for k in FORBIDDEN_MESSAGE_KEYS})
        if hits:
            raise ValueError(f"forbidden task message keys: {', '.join(hits)}")
        for nested in value.values():
            assert_safe_message_keys(
                nested,
                _depth=_depth + 1,
                _nodes=counter,
                max_depth=max_depth,
                max_nodes=max_nodes,
            )
        return

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            assert_safe_message_keys(
                nested,
                _depth=_depth + 1,
                _nodes=counter,
                max_depth=max_depth,
                max_nodes=max_nodes,
            )


def assert_bounded_event_details(details: Mapping[str, Any] | None) -> None:
    """Validate event details: key count, recursive deny-list, serialized size."""
    if not details:
        return
    if len(details) > MAX_EVENT_DETAILS_KEYS:
        raise ValueError(
            f"event details exceed max key count ({MAX_EVENT_DETAILS_KEYS})"
        )
    assert_safe_message_keys(details)
    raw = json.dumps(dict(details), sort_keys=True, default=str).encode("utf-8")
    if len(raw) > MAX_EVENT_DETAILS_JSON_BYTES:
        raise ValueError(
            f"event details JSON exceeds {MAX_EVENT_DETAILS_JSON_BYTES} bytes"
        )


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
    """Serialize a TaskMessage for a future small Airflow XCom payload.

    Fails closed when serialized JSON exceeds ``MAX_XCOM_JSON_BYTES``.
    Never truncates.
    """
    data = message.model_dump(mode="json")
    assert_safe_message_keys(data)
    raw = json.dumps(data, sort_keys=True, default=str).encode("utf-8")
    if len(raw) > MAX_XCOM_JSON_BYTES:
        raise ValueError(
            f"TaskMessage XCom JSON exceeds {MAX_XCOM_JSON_BYTES} bytes"
        )
    return data
