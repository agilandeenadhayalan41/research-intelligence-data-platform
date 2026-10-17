"""Offline fake of the public OpenAlex snapshot bucket for the DE-05 inspection script.

Synthetic objects only (no real record values). ``FakeBucket.opener`` stands in for
``connector._open_https`` and ``FakeBucket`` itself is a connector ``_Client``, so
the script runs end to end without a socket. ``load_inspection_script`` loads
``scripts/openalex_snapshot_inspection_126.py`` once per test session by file
location (the repository precedent for script tests).
"""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit
from xml.sax.saxutils import escape

import pyarrow as pa
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "openalex_snapshot_inspection_126.py"
MODULE_NAME = "openalex_snapshot_inspection_126"
SNAPSHOT = "2026-10-14"
PAIR_DAY = "2026-09-28"
LARGE_DAY = "2026-10-14"
NS = "http://s3.amazonaws.com/doc/2006-03-01/"


def load_inspection_script() -> ModuleType:
    module = sys.modules.get(MODULE_NAME)
    if module is None:
        spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[MODULE_NAME] = module
        spec.loader.exec_module(module)
    return module


LICENSE = (
    "Creative Commons Legal Code\n\nCC0 1.0 Universal\n\n    Synthetic licence text for tests.\n\n"
    "4. Limitations and Disclaimers.\n\n"
    " c. Affirmer disclaims responsibility for clearing rights of other persons\n"
    "    that may apply to the Work.\n"
    " d. Affirmer understands something else.\n"
)
README = (
    "OpenAlex snapshot\n=================\nSynthetic files, released once a quarter.\n\n"
    "Data:      data/jsonl/ and data/parquet/\nManifests: written last\nHistory:   RELEASE_NOTES.txt\n"
    "License:   LICENSE.txt (CC0)\nlegacy-data/ is frozen.\n\nDocs: https://help.openalex.org/access/snapshot/\n"
)
RELEASE_NOTES = (
    "SYNTHETIC RELEASE NOTES\n\n"
    "Public releases land on the second Wednesday of January, April, July and October (UTC).\n"
    "Schedule: https://help.openalex.org/access/sync/\n\n"
    "RELEASE 2026-10-14\n- quarterly snapshot\n\nRELEASE 2026-09-23\n- quarterly snapshot\n\n"
    "RELEASE 2025-01-29\n- after this release, the snapshot will be updated quarterly\n"
    "- removed 10 abstracts with junk content\n- delete 5 works: synthetic. (merge into deleted id: W1)\n"
)


def synthetic_work(index: int) -> dict[str, Any]:
    """A synthetic Works record whose string paths are all in the redaction policy."""
    work = f"W{9000 + index}"
    return {
        "id": f"https://openalex.org/{work}",
        "doi": f"https://doi.org/10.0000/test.{index}",
        "title": f"Synthetic title {index}",
        "display_name": f"Synthetic title {index}",
        "publication_date": "2026-01-01",
        "created_date": "2026-01-02T00:00:00.000Z",
        "updated_date": "2026-09-28T01:02:03.000Z",
        "type": "article",
        "language": "en",
        "is_paratext": False,
        "ids": {"openalex": f"https://openalex.org/{work}", "doi": f"https://doi.org/10.0000/test.{index}"},
        "authorships": [
            {
                "author_position": "first",
                "author": {
                    "id": f"https://openalex.org/A59{index:08d}",
                    "display_name": f"Zelda Quorn{index}",
                    "orcid": f"https://orcid.org/0000-0002-{index:04d}-0001",
                },
                "raw_author_name": f"Zelda Quorn{index}",
                "raw_affiliation_strings": [f"Dept {index}, Synthetic Institute"],
                "affiliations": [
                    {"raw_affiliation_string": f"Dept {index}, Synthetic Institute",
                     "institution_ids": ["https://openalex.org/I1"]}
                ],
                "institutions": [{"id": "https://openalex.org/I1", "display_name": "Synthetic Institute",
                                  "ror": "https://ror.org/000000000", "country_code": "GB",
                                  "type": "education", "lineage": ["https://openalex.org/I1"]}],
                "countries": ["GB"],
                "is_corresponding": True,
            }
        ],
        "corresponding_author_ids": [f"https://openalex.org/A59{index:08d}"],
        "primary_location": {
            "id": f"doi:10.0000/test.{index}",
            "landing_page_url": f"https://example.com/landing/{index}",
            "pdf_url": None,
            "is_oa": False,
            "source": {"id": "https://openalex.org/S1", "display_name": "Synthetic Journal",
                       "host_organization": "https://openalex.org/P1", "type": "journal"},
        },
        "open_access": {"is_oa": False, "oa_status": "closed", "oa_url": None},
        "cited_by_count": index,
        "abstract_inverted_index": {"synthetic": [0], "words": [1, 3], "here": [2]},
        "apc_list": {"value": 100, "currency": "USD", "value_usd": 100},
        "topics": [{"id": "https://openalex.org/T1", "display_name": "Topic", "score": 0.652}],
        "referenced_works": [],
        "fwci": 1.5 if index % 2 else 0.0,
    }


ARROW_SCHEMA = pa.schema([
    ("id", pa.string()), ("doi", pa.string()), ("title", pa.string()), ("display_name", pa.string()),
    ("publication_date", pa.date32()), ("created_date", pa.timestamp("us", tz="UTC")),
    ("updated_date", pa.timestamp("us", tz="UTC")), ("type", pa.string()), ("language", pa.string()),
    ("is_paratext", pa.bool_()), ("ids", pa.map_(pa.string(), pa.string())),
    ("authorships", pa.list_(pa.struct([
        ("author_position", pa.string()),
        ("author", pa.struct([("id", pa.string()), ("display_name", pa.string()), ("orcid", pa.string())])),
        ("raw_author_name", pa.string()), ("raw_affiliation_strings", pa.list_(pa.string())),
        ("affiliations", pa.list_(pa.struct([("raw_affiliation_string", pa.string()),
                                             ("institution_ids", pa.list_(pa.string()))]))),
        ("institutions", pa.list_(pa.struct([("id", pa.string()), ("display_name", pa.string()),
                                             ("ror", pa.string()), ("country_code", pa.string()),
                                             ("type", pa.string()), ("lineage", pa.list_(pa.string()))]))),
        ("countries", pa.list_(pa.string())), ("is_corresponding", pa.bool_()),
    ]))),
    ("corresponding_author_ids", pa.list_(pa.string())),
    ("primary_location", pa.struct([
        ("id", pa.string()), ("landing_page_url", pa.string()), ("pdf_url", pa.string()), ("is_oa", pa.bool_()),
        ("source", pa.struct([("id", pa.string()), ("display_name", pa.string()),
                              ("host_organization", pa.string()), ("type", pa.string())])),
    ])),
    ("open_access", pa.struct([("is_oa", pa.bool_()), ("oa_status", pa.string()), ("oa_url", pa.string())])),
    ("cited_by_count", pa.int32()), ("abstract_inverted_index", pa.string()),
    ("apc_list", pa.struct([("value", pa.int32()), ("currency", pa.string()), ("value_usd", pa.int32())])),
    ("topics", pa.list_(pa.struct([("id", pa.string()), ("display_name", pa.string()), ("score", pa.float32())]))),
    ("referenced_works", pa.list_(pa.string())), ("fwci", pa.float64()),
])


def _to_arrow_row(record: dict[str, Any]) -> dict[str, Any]:
    from datetime import date, datetime

    row = dict(record)
    row["publication_date"] = date.fromisoformat(record["publication_date"])
    for key in ("created_date", "updated_date"):
        row[key] = datetime.fromisoformat(record[key].replace("Z", "+00:00"))
    row["ids"] = list(record["ids"].items())
    row["abstract_inverted_index"] = json.dumps(record["abstract_inverted_index"])
    return row


def jsonl_gz(records: list[dict[str, Any]]) -> bytes:
    body = b"".join(json.dumps(r, separators=(",", ":")).encode() + b"\n" for r in records)
    return gzip.compress(body, mtime=0)


def parquet_bytes(records: list[dict[str, Any]], *, row_group_size: int | None = None) -> bytes:
    table = pa.Table.from_pylist([_to_arrow_row(r) for r in records], schema=ARROW_SCHEMA)
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="snappy", row_group_size=row_group_size)
    return sink.getvalue()


def _crc_b64(script: ModuleType, data: bytes) -> str:
    return script.crc64nvme_s3(script.crc64nvme(data))


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.status = status
        self.headers = headers
        self._body = io.BytesIO(body)
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        assert size >= 0, "the script must never make an unbounded read"
        return self._body.read(size)

    def close(self) -> None:
        self.closed = True


class FakeBucket:
    """Synthetic snapshot. ``calls`` records ``(method, path, range)`` of every request."""

    def __init__(
        self,
        *,
        pair_records: int = 12,
        snapshot: str = SNAPSHOT,
        mutate: Any = None,
    ) -> None:
        self.script = load_inspection_script()
        self.calls: list[tuple[str, str, str | None]] = []
        self.overrides: dict[tuple[str, str], FakeResponse] = {}
        self.objects: dict[str, bytes] = {}
        self.etags: dict[str, str] = {}
        records = [synthetic_work(i) for i in range(1, pair_records + 1)]
        if mutate is not None:
            records = [mutate(record) for record in records]
        self.pair_records = records
        large_records = [synthetic_work(i) for i in range(100, 160)]
        self._put("LICENSE.txt", LICENSE.encode())
        self._put("README.txt", README.encode())
        self._put("RELEASE_NOTES.txt", RELEASE_NOTES.encode())
        files = {
            "jsonl": {
                f"data/jsonl/works/updated_date={PAIR_DAY}/part_0000.gz": (jsonl_gz(records), len(records)),
                f"data/jsonl/works/updated_date={LARGE_DAY}/part_0000.gz": (jsonl_gz(large_records), len(large_records)),
            },
            "parquet": {
                f"data/parquet/works/updated_date={PAIR_DAY}/part_0000.parquet": (parquet_bytes(records), len(records)),
                f"data/parquet/works/updated_date={LARGE_DAY}/part_0000.parquet": (
                    parquet_bytes(large_records, row_group_size=20), len(large_records)),
            },
        }
        deletions = gzip.compress(
            b"work_id,deleted_date\n" + b"".join(
                f"https://openalex.org/W{n},2026-08-14\n".encode() for n in range(100, 140)
            ), mtime=0,
        )
        for fmt, parts in files.items():
            for key, (body, _) in parts.items():
                self._put(key, body, multipart=key.endswith("2026-10-14/part_0000.parquet"))
            works_manifest = {
                "date": snapshot, "format": fmt, "entity": "works",
                "record_count": sum(n for _, n in parts.values()),
                "content_length": sum(len(b) for b, _ in parts.values()),
                "files": [{"url": f"s3://openalex/{key}", "meta": {"content_length": len(body), "record_count": n}}
                          for key, (body, n) in sorted(parts.items())],
            }
            authors_part = f"data/{fmt}/authors/updated_date=2026-10-01/part_0000.{'gz' if fmt == 'jsonl' else 'parquet'}"
            self._put(authors_part, b"x" * 50)
            authors_entity = {"entity": "authors", "record_count": 2, "content_length": 50,
                              "files": [{"url": f"s3://openalex/{authors_part}",
                                         "meta": {"content_length": 50, "record_count": 2}}]}
            self._put(f"data/{fmt}/works/manifest.json", json.dumps(works_manifest).encode())
            self._put(f"data/{fmt}/authors/manifest.json", json.dumps({
                "date": snapshot, "format": fmt, "entity": "authors", "record_count": 2,
                "content_length": 50, "files": authors_entity["files"]}).encode())
            combined = {
                "date": snapshot, "format": fmt,
                "meta": {"record_count": works_manifest["record_count"] + 2,
                         "content_length": works_manifest["content_length"] + 50},
                "entities": [authors_entity, {key: works_manifest[key] for key in
                                              ("entity", "record_count", "content_length", "files")}],
            }
            self._put(f"data/{fmt}/manifest.json", json.dumps(combined).encode())
            self._put(f"data/{fmt}/works/deleted_ids.csv.gz", deletions, multipart=True,
                      etag_key="deleted_ids")
        self.legacy_prefixes = ["legacy-data/merged_ids/authors/", "legacy-data/merged_ids/works/"]

    def _put(self, key: str, body: bytes, *, multipart: bool = False, etag_key: str | None = None) -> None:
        self.objects[key] = body
        digest = hashlib.md5((etag_key or "").encode() + body, usedforsecurity=False).hexdigest()
        if etag_key is None:
            digest = hashlib.md5(body, usedforsecurity=False).hexdigest()
        self.etags[key] = f"{digest}-2" if multipart else digest

    # --- transport ----------------------------------------------------------------

    def opener(self, method: str, path: str, *, timeout: float, headers: dict[str, str]) -> FakeResponse:
        byte_range = headers.get("Range", "").removeprefix("bytes=") or None
        self.calls.append((method, path, byte_range))
        override = self.overrides.get((method, path))
        if override is not None:
            return override
        parsed = urlsplit(path)
        if parsed.path == "/" and parsed.query:
            return self._listing(dict(parse_qsl(parsed.query, keep_blank_values=True)), parsed.query)
        key = unquote(parsed.path[1:])
        body = self.objects.get(key)
        if body is None:
            return FakeResponse(404, {"Content-Length": "0"})
        base = {"ETag": f'"{self.etags[key]}"', "Last-Modified": "Wed, 14 Oct 2026 18:21:22 GMT",
                "x-amz-version-id": "v1", "Content-Type": "binary/octet-stream"}
        if method == "HEAD":
            return FakeResponse(200, {**base, "Content-Length": str(len(body)),
                                      "x-amz-checksum-crc64nvme": _crc_b64(self.script, body),
                                      "x-amz-checksum-type": "FULL_OBJECT"})
        if byte_range is not None:
            if headers.get("If-Match") != f'"{self.etags[key]}"':
                return FakeResponse(412, {"Content-Length": "0"})
            start, end = (int(v) for v in byte_range.split("-"))
            chunk = body[start:end + 1]
            return FakeResponse(206, {**base, "Content-Length": str(len(chunk)),
                                      "Content-Range": f"bytes {start}-{end}/{len(body)}"}, chunk)
        return FakeResponse(200, {**base, "Content-Length": str(len(body))}, body)

    def request(self, uri: str, *, timeout: float, headers: dict[str, str]) -> FakeResponse:
        return self.opener("GET", urlsplit(uri).path, timeout=timeout, headers=headers)

    def _listing(self, query: dict[str, str], raw_query: str) -> FakeResponse:
        if "versions" in raw_query.split("&"):
            key = query["prefix"]
            body = self.objects.get(key, b"")
            versions = (
                f"<Version><Key>{escape(key)}</Key><VersionId>v1</VersionId><IsLatest>true</IsLatest>"
                f"<LastModified>2026-10-14T18:27:44.000Z</LastModified><ETag>&quot;{self.etags.get(key, '0' * 32)}&quot;</ETag>"
                f"<Size>{len(body)}</Size></Version>"
                f"<Version><Key>{escape(key)}</Key><VersionId>v0</VersionId><IsLatest>false</IsLatest>"
                f"<LastModified>2026-06-27T02:57:12.000Z</LastModified><ETag>&quot;{'1' * 32}&quot;</ETag>"
                f"<Size>{1_121_660 if key.endswith('/manifest.json') and key.count('/') == 2 else len(body)}</Size></Version>"
            )
            xml = f'<?xml version="1.0" encoding="UTF-8"?><ListVersionsResult xmlns="{NS}">{versions}</ListVersionsResult>'
            return FakeResponse(200, {"Transfer-Encoding": "chunked"}, xml.encode())
        prefix = query.get("prefix", "")
        delimiter = query.get("delimiter")
        max_keys = int(query.get("max-keys", "1000"))
        keys = sorted(k for k in self.objects if k.startswith(prefix))
        contents: list[str] = []
        prefixes: set[str] = set()
        if prefix == "legacy-data/merged_ids/":
            prefixes.update(self.legacy_prefixes)
        for key in keys:
            rest = key[len(prefix):]
            if delimiter and "/" in rest:
                prefixes.add(prefix + rest.split("/", 1)[0] + "/")
            else:
                contents.append(key)
        if prefix == "" and delimiter:
            prefixes.add("legacy-data/")
        start = int(query.get("continuation-token", "0").removeprefix("tok")) if "continuation-token" in query else 0
        page = contents[start:start + max_keys]
        truncated = start + max_keys < len(contents)
        items = "".join(
            f"<Contents><Key>{escape(k)}</Key><LastModified>2026-10-14T18:21:22.000Z</LastModified>"
            f"<ETag>&quot;{self.etags[k]}&quot;</ETag><ChecksumAlgorithm>CRC64NVME</ChecksumAlgorithm>"
            f"<ChecksumType>FULL_OBJECT</ChecksumType><Size>{len(self.objects[k])}</Size></Contents>"
            for k in page
        )
        common = "".join(f"<CommonPrefixes><Prefix>{escape(p)}</Prefix></CommonPrefixes>" for p in sorted(prefixes)) if start == 0 else ""
        token = f"<NextContinuationToken>tok{start + max_keys}</NextContinuationToken>" if truncated else ""
        xml = (
            f'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="{NS}"><Name>openalex</Name>'
            f"<Prefix>{escape(prefix)}</Prefix><IsTruncated>{'true' if truncated else 'false'}</IsTruncated>"
            f"{token}{items}{common}</ListBucketResult>"
        )
        return FakeResponse(200, {"Transfer-Encoding": "chunked"}, xml.encode())
