"""Explicit, read-only public OpenAlex connectivity check."""

import json

from research_platform.sources.openalex.connector import OpenAlexConnector


def main() -> int:
    try:
        result = OpenAlexConnector().check_public_connectivity()
    except Exception as error:
        print(json.dumps({"connectivity": "failed", "error": str(error)}))
        return 1
    print(json.dumps({"connectivity": "verified", **result.as_dict()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
