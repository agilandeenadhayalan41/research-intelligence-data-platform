"""CLI entry point for bounded local OpenAlex Works deletion processing."""

from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import BinaryIO, Iterator

from research_platform.ingestion.deletion_pipeline import (
    PersistenceBackend,
    run_openalex_deletions_local_ingest,
)
from research_platform.ingestion.errors import IngestionError
from research_platform.sources.base import SourceAsset
from research_platform.sources.openalex.deletion_metadata import (
    DELETION_PUBLIC_URI,
    OpenAlexDeletionAssetMetadata,
)


class LocalFileConnector:
    """Caller-owned streamed binary fetch from a local path (no preload)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @contextmanager
    def fetch(self, asset: SourceAsset) -> Iterator[BinaryIO]:
        del asset
        with self._path.open("rb") as handle:
            yield handle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply one bounded OpenAlex deleted_ids.csv.gz file. "
            "Choose --backend explicitly: memory is ephemeral; postgres is durable. "
            "Default local bounds remain <= 25 MB compressed; the public ledger is "
            "much larger and is not ingested by this local runner."
        )
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--backend",
        choices=("memory", "postgres"),
        required=True,
    )
    parser.add_argument(
        "--local-file",
        type=Path,
        required=True,
        help="Path to a local deleted_ids.csv.gz payload (bytes landed immutably).",
    )
    parser.add_argument(
        "--file-uri",
        default=DELETION_PUBLIC_URI,
        help=f"Logical source URI (default: {DELETION_PUBLIC_URI})",
    )
    parser.add_argument("--snapshot-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--updated-date", default=None, help="YYYY-MM-DD if known")
    parser.add_argument("--byte-size", type=int, default=None)
    arguments = parser.parse_args(argv)
    backend: PersistenceBackend = arguments.backend
    print(
        json.dumps(
            {
                "persistence": backend,
                "durable": backend == "postgres",
                "pipeline": "openalex-works-deletions",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        asset = OpenAlexDeletionAssetMetadata.model_validate(
            {
                "source": "openalex",
                "snapshot_date": date.fromisoformat(arguments.snapshot_date),
                "entity": "works-deletions",
                "file_uri": arguments.file_uri,
                "byte_size": arguments.byte_size
                if arguments.byte_size is not None
                else arguments.local_file.stat().st_size,
                "updated_date": (
                    None
                    if arguments.updated_date is None
                    else date.fromisoformat(arguments.updated_date)
                ),
                "content_format": "csv",
            }
        )
        result = run_openalex_deletions_local_ingest(
            arguments.config,
            backend=backend,
            deletion_asset=asset,
            connector=LocalFileConnector(arguments.local_file),
        )
    except (IngestionError, OSError, ValueError) as error:
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
            "rows_seen": result.stats.rows_seen,
            "unique_ids": result.stats.unique_ids,
            "duplicates": result.stats.duplicates,
            "deleted": result.stats.deleted,
            "already_deleted": result.stats.already_deleted,
            "unknown": result.stats.unknown,
            "stale": result.stats.stale,
            "conflicts": result.stats.conflicts,
        }
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
