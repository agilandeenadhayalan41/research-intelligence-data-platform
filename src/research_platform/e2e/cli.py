"""CLI for Step-19 bounded end-to-end pipeline (local / offline)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from research_platform.e2e.bounds import parse_bounds
from research_platform.e2e.runner import run_bounded_e2e_pipeline
from research_platform.e2e.summaries import safe_result_summary
from research_platform.sources.openalex.connector import OpenAlexConnector


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-platform-e2e",
        description=(
            "Bounded OpenAlex Works end-to-end pipeline (Step 19). "
            "SEMANTIC_ONLY local evidence — not BigQuery runtime."
        ),
    )
    parser.add_argument("--config", required=True, type=Path, help="Platform config YAML")
    parser.add_argument(
        "--max-files",
        type=int,
        default=1,
        help="Strict max files (default 1; >1 rejected)",
    )
    parser.add_argument(
        "--max-file-size-bytes",
        type=int,
        default=25_000_000,
        help="Strict max file size bytes (default 25000000)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        bounds = parse_bounds(
            max_files=args.max_files,
            max_file_size_bytes=args.max_file_size_bytes,
        )
        from research_platform.config.loader import load_config

        config = load_config(args.config)
        connector = OpenAlexConnector(
            sample_selection=config.sample_selection,
            content_format="jsonl",
        )
        result = run_bounded_e2e_pipeline(
            args.config,
            works_connector=connector,
            bounds=bounds,
        )
        print(json.dumps(safe_result_summary(result), indent=2, sort_keys=True))
        return 0 if result.success else 1
    except Exception as exc:
        print(
            json.dumps(
                {"success": False, "safe_error": type(exc).__name__},
                sort_keys=True,
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
