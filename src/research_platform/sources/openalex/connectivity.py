"""Explicit, read-only public OpenAlex connectivity check."""

import json

from research_platform.sources.openalex.connector import OpenAlexConnector


def main() -> int:
    connector = OpenAlexConnector(timeout_seconds=5, max_attempts=1)
    try:
        result = connector.check_public_connectivity()
    except Exception as error:
        print(
            json.dumps(
                {
                    "connectivity": "failed",
                    "endpoint": connector.public_manifest_endpoint,
                    "access_mode": "anonymous public HTTPS",
                    "expected_format": "jsonl",
                    "actual_format": None,
                    "manifest_byte_limit": 1_000_000,
                    "error": str(error),
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps({"connectivity": "verified", **result.as_dict()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
