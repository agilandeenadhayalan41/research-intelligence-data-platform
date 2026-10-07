"""CLI entry point for bounded local OpenAlex Works ingestion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from research_platform.ingestion.errors import IngestionError
from research_platform.ingestion.pipeline import (
    PersistenceBackend,
    run_openalex_works_local_ingest,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Ingest at most one bounded OpenAlex Works JSONL.GZ file. "
            "Choose --backend explicitly: memory is ephemeral; postgres is durable."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Explicit platform config path (must select environment=local).",
    )
    parser.add_argument(
        "--backend",
        choices=("memory", "postgres"),
        required=True,
        help=(
            "Persistence backend. 'memory' is ephemeral (tests/demo only). "
            "'postgres' uses local PostgreSQL behind the Step 10 write boundary."
        ),
    )
    arguments = parser.parse_args(argv)
    backend: PersistenceBackend = arguments.backend
    print(
        json.dumps(
            {
                "persistence": backend,
                "durable": backend == "postgres",
                "note": (
                    "ephemeral in-process stores; state is lost when the process exits"
                    if backend == "memory"
                    else "durable local PostgreSQL control/canonical write path"
                ),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        result = run_openalex_works_local_ingest(arguments.config, backend=backend)
    except IngestionError as error:
        print(
            json.dumps(
                {
                    "ingest": "failed",
                    "persistence": backend,
                    "error": type(error).__name__,
                    "message": str(error),
                },
                sort_keys=True,
            )
        )
        return 1
    payload: dict[str, object] = {
        "ingest": "ok",
        "persistence": result.persistence_backend,
        "durable": result.persistence_backend == "postgres",
        "run_id": str(result.run.run_id),
        "run_status": result.run.status.value,
        "skipped_reason": result.skipped_reason,
    }
    if result.source_file is not None:
        payload["asset_id"] = result.source_file.asset_id
        payload["file_status"] = result.source_file.status.value
    if result.stats is not None:
        payload["stats"] = {
            "works_inserted": result.stats.works_inserted,
            "works_identical": result.stats.works_identical,
            "works_replaced": result.stats.works_replaced,
            "works_stale": result.stats.works_stale,
            "record_provenance_count": result.stats.record_provenance_count,
            "relationship_counts": dict(result.stats.relationship_counts),
        }
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
