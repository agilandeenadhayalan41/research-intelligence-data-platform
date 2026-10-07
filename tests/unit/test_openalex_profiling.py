import gzip
import hashlib
import io
import json
from collections import deque
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from research_platform.config import SampleSelectionConfig
from research_platform.sources.openalex import (
    EvidenceType,
    OpenAlexAssetMetadata,
    OpenAlexConnector,
    OpenAlexDecompressionError,
    OpenAlexEmptySourceError,
    OpenAlexEndpointUnavailableError,
    OpenAlexFormatError,
    OpenAlexMalformedJSONLError,
    OpenAlexMalformedParquetError,
    OpenAlexProfileLimitError,
    OpenAlexProfileRun,
    OpenAlexSizeLimitError,
    OpenAlexSizeMismatchError,
    OpenAlexSourceProfile,
    OpenAlexTruncatedError,
    OpenAlexUnsupportedFormatError,
    ProfilingLimits,
)
from research_platform.sources.openalex import profiling
from research_platform.sources.openalex import profile_jsonl as jsonl_module
from research_platform.sources.openalex import profile_models
from research_platform.sources.openalex.profile_jsonl import sample_jsonl
from research_platform.sources.openalex.profile_parquet import inspect_parquet

JSONL_URI = "s3://openalex/data/jsonl/works/updated_date=2026-06-01/part_0000.gz"
PARQUET_URI = "s3://openalex/data/parquet/works/updated_date=2026-06-01/part_0000.parquet"
_UNSET: Any = object()


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        headers: dict[str, str] | None = None,
        chunk_size: int = 7,
    ) -> None:
        self.body = body
        self.status = 200
        self.headers = headers if headers is not None else {"Content-Length": str(len(body))}
        self.chunk_size = chunk_size
        self.closed = False
        self.bytes_served = 0

    def read(self, size: int = -1) -> bytes:
        assert size >= 0, "profiling must never make an unbounded transport read"
        end = min(self.bytes_served + size, self.bytes_served + self.chunk_size, len(self.body))
        content = self.body[self.bytes_served : end]
        self.bytes_served = end
        return content

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = deque(responses)
        self.uris: list[str] = []

    def request(self, uri: str, *, timeout: float, headers: dict[str, str]) -> FakeResponse:
        self.uris.append(uri)
        return self.responses.popleft()


def connector(
    client: FakeClient, content_format: str = "jsonl", max_bytes: int = 25_000_000
) -> OpenAlexConnector:
    return OpenAlexConnector(
        sample_selection=SampleSelectionConfig(max_file_size_bytes=max_bytes),
        content_format=content_format,
        max_attempts=1,
        retry_backoff_seconds=0,
        client=client,
    )


def asset(
    body: bytes | None = None,
    *,
    uri: str = JSONL_URI,
    size: Any = _UNSET,
    record_count: int | None = None,
) -> OpenAlexAssetMetadata:
    return OpenAlexAssetMetadata(
        source="openalex",
        snapshot_date="2026-06-25",
        entity="works",
        file_uri=uri,
        byte_size=len(body or b"") if size is _UNSET else size,
        record_count=record_count,
        updated_date="2026-06-01",
        content_format="parquet" if uri.endswith(".parquet") else "jsonl",
    )


def jsonl(records: list[object]) -> bytes:
    return b"".join(json.dumps(record).encode() + b"\n" for record in records)


def gz(content: bytes) -> bytes:
    return gzip.compress(content, mtime=0)


def profile_jsonl_bytes(
    body: bytes, limits: ProfilingLimits = ProfilingLimits(), **asset_kwargs: Any
) -> tuple[OpenAlexSourceProfile, FakeResponse]:
    response = FakeResponse(body, headers={"Content-Type": "binary/octet-stream"})
    result = profiling.profile_openalex_asset(
        connector(FakeClient(response)), asset(body, **asset_kwargs), limits
    )
    return result, response


def write_parquet(path: Path, table: pa.Table, **kwargs: Any) -> bytes:
    pq.write_table(table, path, **kwargs)
    return path.read_bytes()


def profile_parquet_bytes(
    body: bytes, limits: ProfilingLimits = ProfilingLimits(), **asset_kwargs: Any
) -> tuple[OpenAlexSourceProfile, FakeResponse]:
    response = FakeResponse(body, chunk_size=4096)
    result = profiling.profile_openalex_asset(
        connector(FakeClient(response), "parquet"),
        asset(body, uri=PARQUET_URI, **asset_kwargs),
        limits,
    )
    return result, response


def field(result: OpenAlexSourceProfile, path: str):
    return next(item for item in result.schema_fields if item.path == path)


# Limits


def test_default_limits_match_step_08_bounds() -> None:
    limits = ProfilingLimits()
    assert limits.max_files == 1
    assert limits.max_file_size_bytes == 25_000_000
    assert limits.max_profile_records == 100
    assert limits.sample_selection() == SampleSelectionConfig()


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_files": 0},
        {"max_files": 2},
        {"max_files": -1},
        {"max_file_size_bytes": 0},
        {"max_file_size_bytes": -5},
        {"max_file_size_bytes": "25000000"},
        {"max_file_size_bytes": 1.5},
        {"max_file_size_bytes": None},
        {"max_file_size_bytes": True},
        {"max_profile_records": 0},
        {"max_profile_records": -1},
        {"max_profile_records": None},
        {"max_profile_records": 10_001},
        {"max_decoded_sample_bytes": 0},
        {"unexpected": 1},
    ],
)
def test_invalid_limits_are_rejected_not_unlimited(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ProfilingLimits(**overrides)


# JSONL / JSONL.GZ


def test_gzip_jsonl_reports_sampled_schema_nesting_nulls_and_missing_fields() -> None:
    records = [
        {"id": "W1", "title": "A", "authorships": [{"author": {"id": "A1"}}], "doi": None},
        {"id": "W2", "title": None, "authorships": [], "open_access": {"is_oa": True}},
        {"id": "W3", "authorships": [{"author": {"id": "A2"}}, {"author": None}], "cited_by_count": 3},
    ]
    result, response = profile_jsonl_bytes(gz(jsonl(records)), record_count=3)

    assert result.source_format == "jsonl"
    assert result.compression == "gzip"
    assert result.content_type == "binary/octet-stream"
    assert result.sampled_record_count == 3
    assert result.row_count == 3
    assert result.observed_size_bytes == result.declared_size_bytes == result.bytes_read
    assert response.closed
    assert field(result, "id").data_type == "string"
    assert field(result, "id").nullable is None
    title = field(result, "title")
    assert (title.data_type, title.nullable, title.sampled_null_count) == ("null|string", True, 1)
    assert title.sampled_missing_count == 1
    doi = field(result, "doi")
    assert (doi.sampled_present_count, doi.sampled_missing_count) == (1, 2)
    assert field(result, "authorships").kind == "list"
    element = field(result, "authorships[]")
    assert (element.kind, element.sampled_present_count, element.sampled_missing_count) == (
        "struct",
        3,
        None,
    )
    assert field(result, "authorships[].author").data_type == "null|object"
    assert field(result, "authorships[].author.id").sampled_present_count == 2
    assert field(result, "cited_by_count").data_type == "integer"
    assert {"authorships", "authorships[]", "authorships[].author", "open_access"} <= set(
        result.nested_fields
    )
    assert {"title", "doi", "authorships[].author"} <= set(result.nullable_fields)
    assert all(item.evidence is EvidenceType.SAMPLED_OBSERVATION for item in result.schema_fields)
    assert result.evidence["schema_fields"] is EvidenceType.SAMPLED_OBSERVATION
    assert result.evidence["nullable_fields"] is EvidenceType.SAMPLED_OBSERVATION
    assert result.evidence["row_count"] is EvidenceType.EXACT_FILE_METADATA
    assert result.evidence["declared_size_bytes"] is EvidenceType.VERIFIED_METADATA
    assert result.evidence["row_group_count"] is EvidenceType.UNKNOWN
    assert result.row_group_count is None


def test_jsonl_sampling_stops_at_record_bound_without_reading_to_eof() -> None:
    records = [
        {"id": f"W{index}", "payload": hashlib.sha256(str(index).encode()).hexdigest()}
        for index in range(20_000)
    ]
    body = gz(jsonl(records))
    result, response = profile_jsonl_bytes(body)

    assert result.sampled_record_count == 100
    assert result.row_count is None
    assert result.observed_size_bytes is None
    assert result.evidence["row_count"] is EvidenceType.UNKNOWN
    assert result.evidence["observed_size_bytes"] is EvidenceType.UNKNOWN
    assert result.evidence["sampled_record_count"] is EvidenceType.SAMPLED_OBSERVATION
    assert field(result, "id").sampled_present_count == 100
    assert response.bytes_served < len(body)
    assert result.bytes_read < len(body)
    assert response.closed
    assert any("not read to EOF" in item for item in result.limitations)


def test_jsonl_custom_record_bound_is_respected() -> None:
    result, _ = profile_jsonl_bytes(
        gz(jsonl([{"id": index} for index in range(10)])),
        ProfilingLimits(max_profile_records=3),
    )
    assert result.sampled_record_count == 3


def test_plain_jsonl_stream_is_sampled() -> None:
    sample = sample_jsonl(
        io.BytesIO(jsonl([{"a": 1}, {"a": 2.5, "b": [1, None]}])),
        compression=None,
        max_records=100,
        max_decoded_bytes=1_000,
    )
    assert sample.compression is None
    assert sample.reached_eof
    assert [(item.path, item.data_type) for item in sample.fields] == [
        ("a", "integer|number"),
        ("b", "array"),
        ("b[]", "integer|null"),
    ]


def test_abstract_inverted_index_keys_are_collapsed_not_treated_as_schema() -> None:
    records = [
        {"id": f"W{index}", "abstract_inverted_index": {f"word{index}_{n}": [n] for n in range(200)}}
        for index in range(100)
    ]
    sample = sample_jsonl(
        io.BytesIO(jsonl(records)), compression=None, max_records=100, max_decoded_bytes=10_000_000
    )
    assert [(item.path, item.kind) for item in sample.fields] == [
        ("abstract_inverted_index", "map"),
        ("abstract_inverted_index{value}", "list"),
        ("abstract_inverted_index{value}[]", "scalar"),
        ("id", "scalar"),
    ]
    value = sample.fields[1]
    assert (value.sampled_present_count, value.sampled_missing_count) == (20_000, None)


def test_unusual_keys_are_quoted_and_unambiguous() -> None:
    sample = sample_jsonl(
        io.BytesIO(b'{"": 1, "a.b": 2, "a": {"b": 3, "c[]": 4}}\n'),
        compression=None,
        max_records=100,
        max_decoded_bytes=1_000,
    )
    paths = {item.path: item for item in sample.fields}
    assert set(paths) == {'[""]', '["a.b"]', "a", "a.b", 'a["c[]"]'}
    assert all(item.sampled_missing_count == 0 for item in paths.values())


def test_field_path_limit_is_explicit() -> None:
    record = {f"k{index}": index for index in range(10_001)}
    with pytest.raises(OpenAlexProfileLimitError):
        sample_jsonl(
            io.BytesIO(jsonl([record])),
            compression=None,
            max_records=1,
            max_decoded_bytes=1_000_000,
        )


@pytest.mark.parametrize(
    "body",
    [
        b'{"id": "W1"}\n{"id": \n',
        b'{"id": "W1"}\n[1, 2]\n',
        b'{"id": "W1"}\n\n{"id": "W2"}\n',
        b'{"id": "W1", "id": "W2"}\n',
        b'{"id": NaN}\n',
        b'{"id": "\xff"}\n',
    ],
)
def test_malformed_jsonl_is_explicit(body: bytes) -> None:
    with pytest.raises(OpenAlexMalformedJSONLError):
        profile_jsonl_bytes(gz(body))


def test_truncated_gzip_is_explicit_and_stream_is_closed() -> None:
    body = gz(jsonl([{"id": index} for index in range(5)]))[:-12]
    response = FakeResponse(body)
    with pytest.raises(OpenAlexDecompressionError):
        profiling.profile_openalex_asset(connector(FakeClient(response)), asset(body))
    assert response.closed


def test_corrupt_gzip_is_a_decompression_error() -> None:
    body = bytearray(gz(jsonl([{"id": "W1"}])))
    body[12:16] = b"\x00\xff\x00\xff"
    with pytest.raises(OpenAlexDecompressionError):
        profile_jsonl_bytes(bytes(body))


def test_declared_gzip_without_gzip_header_is_rejected() -> None:
    with pytest.raises(OpenAlexDecompressionError):
        profile_jsonl_bytes(jsonl([{"id": "W1"}]))


@pytest.mark.parametrize("body", [gz(b""), gz(b"\n")])
def test_jsonl_without_records_is_empty_or_malformed(body: bytes) -> None:
    with pytest.raises((OpenAlexEmptySourceError, OpenAlexMalformedJSONLError)):
        profile_jsonl_bytes(body)


def test_empty_metadata_is_rejected_before_retrieval() -> None:
    client = FakeClient()
    with pytest.raises(OpenAlexEmptySourceError):
        profiling.profile_openalex_asset(connector(client), asset(b""))
    assert client.uris == []


def test_zero_byte_payload_is_empty() -> None:
    response = FakeResponse(b"", headers={})
    with pytest.raises(OpenAlexEmptySourceError):
        profiling.profile_openalex_asset(connector(FakeClient(response)), asset(size=10))
    assert response.closed


def test_decompression_expansion_is_bounded() -> None:
    body = gz(jsonl([{"payload": "x" * 100_000}]))
    with pytest.raises(OpenAlexProfileLimitError):
        profile_jsonl_bytes(body, ProfilingLimits(max_decoded_sample_bytes=10_000))


def test_jsonl_record_count_mismatch_is_rejected_when_file_fully_read() -> None:
    with pytest.raises(OpenAlexSizeMismatchError):
        profile_jsonl_bytes(gz(jsonl([{"id": 1}])), record_count=2)


# Byte bounds and metadata checks


def test_oversized_metadata_is_rejected_before_retrieval() -> None:
    client = FakeClient()
    with pytest.raises(OpenAlexProfileLimitError):
        profiling.profile_openalex_asset(
            connector(client), asset(size=25_000_001)
        )
    assert client.uris == []


def test_unknown_size_is_rejected_before_retrieval() -> None:
    client = FakeClient()
    with pytest.raises(OpenAlexProfileLimitError):
        profiling.profile_openalex_asset(connector(client), asset(size=None))
    assert client.uris == []


def test_maximum_metadata_size_does_not_excuse_a_short_payload() -> None:
    body = gz(jsonl([{"id": "W1"}]))
    response = FakeResponse(body, headers={})
    with pytest.raises(OpenAlexSizeMismatchError):
        profiling.profile_openalex_asset(
            connector(FakeClient(response)), asset(size=25_000_000)
        )
    assert response.closed


def test_content_length_mismatch_is_rejected_before_payload_read() -> None:
    body = gz(jsonl([{"id": "W1"}]))
    response = FakeResponse(body, headers={"Content-Length": str(len(body) + 1)})
    with pytest.raises(OpenAlexSizeMismatchError):
        profiling.profile_openalex_asset(connector(FakeClient(response)), asset(body))
    assert response.bytes_served == 0
    assert response.closed


def test_actual_bytes_beyond_declared_size_are_rejected() -> None:
    body = gz(jsonl([{"id": "W1"}]))
    response = FakeResponse(body, headers={})
    with pytest.raises(OpenAlexSizeMismatchError):
        profiling.profile_openalex_asset(
            connector(FakeClient(response)), asset(size=len(body) - 5)
        )
    assert response.closed


def test_actual_byte_overflow_is_rejected_by_profiling_limit() -> None:
    body = jsonl([{"id": "W1"}])
    reader = profiling._CountingReader(io.BytesIO(body), max_bytes=4, declared_bytes=None)
    with pytest.raises(OpenAlexProfileLimitError):
        reader.read(100)


def test_connector_byte_limit_still_applies() -> None:
    body = gz(jsonl([{"id": "W1"}]))
    response = FakeResponse(body)
    with pytest.raises(OpenAlexSizeLimitError):
        profiling.profile_openalex_asset(
            connector(FakeClient(response), max_bytes=10), asset(body)
        )
    assert response.closed


def test_truncated_response_is_reported_by_connector() -> None:
    body = jsonl([{"id": "W1"}])
    gzipped = gz(body)
    response = FakeResponse(gzipped[:-3], headers={"Content-Length": str(len(gzipped))})
    with pytest.raises((OpenAlexTruncatedError, OpenAlexDecompressionError)):
        profiling.profile_openalex_asset(connector(FakeClient(response)), asset(gzipped))
    assert response.closed


@pytest.mark.parametrize(
    ("content_format", "uri"), [("parquet", JSONL_URI), ("jsonl", PARQUET_URI)]
)
def test_unexpected_source_uri_is_rejected_without_request(
    content_format: str, uri: str
) -> None:
    client = FakeClient()
    with pytest.raises(OpenAlexFormatError):
        profiling.profile_openalex_asset(
            connector(client, content_format), asset(b"abc", uri=uri)
        )
    assert client.uris == []


def test_non_metadata_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        profiling.profile_openalex_asset(connector(FakeClient()), object())  # type: ignore[arg-type]


def test_endpoint_failure_is_not_retried_from_profiler() -> None:
    response = FakeResponse(b"")
    response.status = 404
    with pytest.raises(OpenAlexEndpointUnavailableError):
        profiling.profile_openalex_asset(connector(FakeClient(response)), asset(b"abc"))
    assert response.closed


# Parquet


def test_parquet_bytes_declared_as_jsonl_are_not_relabeled(tmp_path: Path) -> None:
    body = write_parquet(tmp_path / "x.parquet", pa.table({"id": ["W1"]}))
    with pytest.raises(OpenAlexUnsupportedFormatError):
        profile_jsonl_bytes(body)


def test_gzip_bytes_declared_as_parquet_are_not_relabeled() -> None:
    with pytest.raises(OpenAlexUnsupportedFormatError):
        profile_parquet_bytes(gz(jsonl([{"id": "W1"}])))


def test_simple_parquet_reports_exact_footer_metadata(tmp_path: Path) -> None:
    table = pa.table({"id": ["W1", "W2", "W3"], "count": pa.array([1, None, 3], pa.int64())})
    body = write_parquet(tmp_path / "simple.parquet", table, compression="snappy")
    result, response = profile_parquet_bytes(body, record_count=3)

    assert result.source_format == "parquet"
    assert result.compression == "SNAPPY"
    assert result.row_count == 3
    assert result.row_group_count == 1
    assert result.column_count == result.physical_column_count == 2
    assert result.statistics_available == "all"
    assert result.parquet_created_by
    assert result.observed_size_bytes == len(body)
    assert result.sampled_record_count == 3
    count = field(result, "count")
    assert (count.data_type, count.nullable, count.sampled_null_count) == ("int64", True, 1)
    assert count.evidence is EvidenceType.EXACT_FILE_METADATA
    assert result.evidence["row_count"] is EvidenceType.EXACT_FILE_METADATA
    assert result.evidence["schema_fields"] is EvidenceType.EXACT_FILE_METADATA
    assert result.evidence["sampled_record_count"] is EvidenceType.SAMPLED_OBSERVATION
    assert result.decoded_bytes_sampled is None
    assert response.closed


def test_nested_parquet_list_struct_map_and_nullability(tmp_path: Path) -> None:
    schema = pa.schema(
        [
            pa.field("id", pa.string(), nullable=False),
            pa.field(
                "authorships",
                pa.list_(
                    pa.struct(
                        [
                            pa.field("author_id", pa.string()),
                            pa.field("institutions", pa.list_(pa.string())),
                        ]
                    )
                ),
            ),
            pa.field("open_access", pa.struct([pa.field("is_oa", pa.bool_())])),
            pa.field("counts", pa.map_(pa.string(), pa.int64())),
        ]
    )
    table = pa.table(
        {
            "id": ["W1", "W2"],
            "authorships": [[{"author_id": "A1", "institutions": ["I1"]}], None],
            "open_access": [{"is_oa": True}, None],
            "counts": [[("2024", 1)], None],
        },
        schema=schema,
    )
    result, _ = profile_parquet_bytes(write_parquet(tmp_path / "nested.parquet", table))

    assert field(result, "id").nullable is False
    assert "id" not in result.nullable_fields
    assert field(result, "authorships").kind == "list"
    assert field(result, "authorships[]").kind == "struct"
    assert field(result, "authorships[].institutions[]").data_type == "string"
    assert field(result, "open_access.is_oa").data_type == "bool"
    assert field(result, "counts").kind == "map"
    assert {"counts{key}", "counts{value}"} <= {item.path for item in result.schema_fields}
    assert {"authorships", "authorships[]", "authorships[].institutions", "open_access", "counts"} <= set(
        result.nested_fields
    )
    assert field(result, "authorships").sampled_null_count == 1
    assert field(result, "authorships[]").sampled_null_count is None
    assert result.physical_column_count > result.column_count


def test_parquet_multiple_row_groups_and_bounded_batch(tmp_path: Path) -> None:
    table = pa.table({"id": [f"W{index}" for index in range(1_000)]})
    body = write_parquet(tmp_path / "groups.parquet", table, row_group_size=100)
    result, _ = profile_parquet_bytes(body, ProfilingLimits(max_profile_records=7))

    assert result.row_count == 1_000
    assert result.row_group_count == 10
    assert result.sampled_record_count == 7
    assert field(result, "id").sampled_present_count == 7


def test_parquet_batch_inspection_never_reads_full_table(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "bounded.parquet"
    pq.write_table(pa.table({"id": list(range(500))}), path, row_group_size=50)
    batch_sizes: list[int] = []
    original = pq.ParquetFile.iter_batches

    def spy(self, batch_size=65536, *args, **kwargs):  # type: ignore[no-untyped-def]
        batch_sizes.append(batch_size)
        return original(self, batch_size, *args, **kwargs)

    def forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("full-table read is forbidden")

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", spy)
    monkeypatch.setattr(pq.ParquetFile, "read", forbidden)
    inspection = inspect_parquet(path, max_records=100)
    assert batch_sizes == [100]
    assert inspection.sampled_record_count == 100


def test_empty_parquet_reports_schema_with_zero_rows(tmp_path: Path) -> None:
    table = pa.table({"id": pa.array([], pa.string())})
    result, _ = profile_parquet_bytes(write_parquet(tmp_path / "empty.parquet", table))
    assert result.row_count == 0
    assert result.sampled_record_count == 0
    assert [item.path for item in result.schema_fields] == ["id"]
    assert any("zero rows" in item for item in result.limitations)


def test_malformed_parquet_is_explicit() -> None:
    body = b"PAR1" + b"\x00" * 32 + b"PAR1"
    with pytest.raises(OpenAlexMalformedParquetError):
        profile_parquet_bytes(body)


def test_non_parquet_bytes_are_malformed_parquet() -> None:
    with pytest.raises(OpenAlexMalformedParquetError):
        profile_parquet_bytes(b"not parquet at all")


def test_truncated_parquet_is_explicit(tmp_path: Path) -> None:
    body = write_parquet(tmp_path / "t.parquet", pa.table({"id": ["W1"]}))[:-10]
    with pytest.raises(OpenAlexMalformedParquetError):
        profile_parquet_bytes(body)


def test_parquet_temporary_file_is_deleted(tmp_path: Path, monkeypatch) -> None:
    body = write_parquet(tmp_path / "s.parquet", pa.table({"id": ["W1"]}))
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setattr(profiling.tempfile, "tempdir", str(spool))
    profile_parquet_bytes(body)
    with pytest.raises(OpenAlexMalformedParquetError):
        profile_parquet_bytes(b"PAR1" + b"\x00" * 32 + b"PAR1")
    assert list(spool.iterdir()) == []


# Resource-budget regressions


class GeneratedStream(io.RawIOBase):
    """Generate boundary-sized input without storing a large fixture."""

    def __init__(self, size: int) -> None:
        super().__init__()
        self.size = size
        self.served = 0
        self.requests: list[int] = []

    def read(self, size: int = -1) -> bytes:
        assert 0 <= size <= 64 * 1024
        self.requests.append(size)
        count = min(size, self.size - self.served)
        self.served += count
        return b"x" * count


@pytest.mark.parametrize("actual_size", [25_000_000, 25_000_001])
def test_actual_source_byte_boundary(actual_size: int) -> None:
    stream = GeneratedStream(actual_size)
    reader = profiling._CountingReader(
        stream, max_bytes=25_000_000, declared_bytes=25_000_000
    )
    if actual_size == 25_000_000:
        assert reader.verify_complete() == 25_000_000
    else:
        with pytest.raises(OpenAlexProfileLimitError):
            reader.verify_complete()
    assert stream.served == actual_size
    assert stream.requests[-1] <= 25_000_000 % (64 * 1024) + 1


@pytest.mark.parametrize(
    ("max_bytes", "declared_bytes", "error"),
    [(4, None, OpenAlexProfileLimitError),
     (4, 10, OpenAlexProfileLimitError),
     (10, 4, OpenAlexSizeMismatchError)],
)
def test_read_requests_allow_only_one_overflow_probe(max_bytes, declared_bytes, error) -> None:
    stream = GeneratedStream(30)
    reader = profiling._CountingReader(
        stream, max_bytes=max_bytes, declared_bytes=declared_bytes
    )
    with pytest.raises(error):
        reader.read(1000)
    assert stream.requests == [5]
    assert stream.served == reader.bytes_read == 5


def test_stricter_profile_limit_closes_supplied_stream(monkeypatch) -> None:
    stream = io.BytesIO(gz(jsonl([{"id": "W1"}])))
    source = connector(FakeClient(), max_bytes=1000)
    monkeypatch.setattr(source, "fetch", lambda unused: stream)
    with pytest.raises(OpenAlexProfileLimitError):
        profiling.profile_openalex_asset(
            source, asset(size=4), ProfilingLimits(max_file_size_bytes=4)
        )
    assert stream.closed


@pytest.mark.parametrize(
    "name",
    ["max_record_bytes", "max_nesting_depth", "max_profile_nodes",
     "max_schema_fields", "max_profile_seconds", "max_parquet_footer_bytes"],
)
@pytest.mark.parametrize("value", [0, -1, True, None, "1", 1.5])
def test_resource_limits_reject_invalid_values(name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ProfilingLimits(**{name: value})


@pytest.mark.parametrize(
    "content",
    [
        b'{"value":"' + b"x" * 1_000_000 + b'"}\n',
        b'{"value":' + b"[" * 65 + b"0" + b"]" * 65 + b"}\n",
        b'{"value":[' + b"0," * 100_001 + b"0]}\n",
    ],
)
def test_default_jsonl_structure_limits_precede_decoding(content: bytes, monkeypatch) -> None:
    def forbidden_decode(line):
        raise AssertionError("record allocation must follow structural preflight")

    monkeypatch.setattr(jsonl_module, "_decode_record", forbidden_decode)
    with pytest.raises(OpenAlexProfileLimitError):
        sample_jsonl(
            io.BytesIO(content), compression=None, max_records=100,
            max_decoded_bytes=25_000_000,
        )


def test_jsonl_node_budget_is_cumulative_across_records() -> None:
    with pytest.raises(OpenAlexProfileLimitError):
        sample_jsonl(
            io.BytesIO(b'{"a":[1,2]}\n' * 2), compression=None,
            max_records=100, max_decoded_bytes=100,
            limits=ProfilingLimits(max_profile_nodes=7),
        )


def test_jsonl_configured_record_budget() -> None:
    with pytest.raises(OpenAlexProfileLimitError):
        sample_jsonl(
            io.BytesIO(b'{"a":"abcdefgh"}\n'), compression=None,
            max_records=100, max_decoded_bytes=100,
            limits=ProfilingLimits(max_record_bytes=8),
        )


def test_jsonl_eof_probe_is_counted_in_decoded_budget() -> None:
    line = b'{"a":1}\n'
    sample = sample_jsonl(
        io.BytesIO(line * 2), compression=None, max_records=1, max_decoded_bytes=100
    )
    assert sample.decoded_bytes == len(line) + 1
    assert not sample.reached_eof
    with pytest.raises(OpenAlexProfileLimitError):
        sample_jsonl(
            io.BytesIO(line * 2), compression=None, max_records=1,
            max_decoded_bytes=len(line),
        )
    assert sample_jsonl(
        io.BytesIO(line), compression=None, max_records=1,
        max_decoded_bytes=len(line),
    ).reached_eof


def test_jsonl_deadline_checked_after_read_and_caller_keeps_stream(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr(profile_models.time, "monotonic", lambda: now[0])

    class SlowStream(io.BytesIO):
        def readline(self, size: int = -1) -> bytes:
            content = super().readline(size)
            now[0] = 11.0
            return content

    stream = SlowStream(b'{"a":1}\n')
    with pytest.raises(OpenAlexProfileLimitError):
        sample_jsonl(stream, compression=None, max_records=100, max_decoded_bytes=100)
    assert not stream.closed


def test_profile_deadline_includes_fetch_and_closes_stream(monkeypatch) -> None:
    now = [0.0]
    monkeypatch.setattr(profile_models.time, "monotonic", lambda: now[0])
    body = gz(jsonl([{"id": 1}]))
    stream = io.BytesIO(body)
    source = connector(FakeClient())

    def slow_fetch(unused):
        now[0] = 11.0
        return stream

    monkeypatch.setattr(source, "fetch", slow_fetch)
    with pytest.raises(OpenAlexProfileLimitError):
        profiling.profile_openalex_asset(source, asset(body))
    assert stream.closed


def test_parquet_expansion_guard_prevents_value_read(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "compressed.parquet"
    pq.write_table(
        pa.table({"value": ["x" * 50_000]}), path,
        compression="gzip", use_dictionary=False, write_statistics=False,
    )
    assert path.stat().st_size < 1000

    def forbidden(*args, **kwargs):
        raise AssertionError("expansion must be checked before iter_batches")

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", forbidden)
    with pytest.raises(OpenAlexProfileLimitError):
        inspect_parquet(
            path, max_records=100, limits=ProfilingLimits(max_decoded_sample_bytes=1000)
        )


def test_parquet_metadata_guard_rejects_before_values_and_closes(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    path = tmp_path / "metadata.parquet"
    pq.write_table(pa.table({"a": [1]}), path)
    column = SimpleNamespace(
        compression="SNAPPY", is_stats_set=False, total_uncompressed_size=1001
    )
    group = SimpleNamespace(num_columns=1, column=lambda index: column)
    metadata = SimpleNamespace(
        num_rows=1, num_columns=1, num_row_groups=1,
        row_group=lambda index: group, created_by=None,
    )
    closed = []

    class MetadataOnly:
        schema_arrow = pa.schema([pa.field("a", pa.int64())])

        def __init__(self, *args, **kwargs):
            self.metadata = metadata

        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append(True)

        def iter_batches(self, *args, **kwargs):
            raise AssertionError("metadata guard must precede value reading")

    monkeypatch.setattr(pq, "ParquetFile", MetadataOnly)
    with pytest.raises(OpenAlexProfileLimitError):
        inspect_parquet(
            path, max_records=100, limits=ProfilingLimits(max_decoded_sample_bytes=1000)
        )
    assert closed == [True]


def test_parquet_footer_limit_precedes_native_reader(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "footer.parquet"
    pq.write_table(pa.table({"a": [1]}), path)

    def forbidden(*args, **kwargs):
        raise AssertionError("footer byte cap must precede native footer decoding")

    monkeypatch.setattr(pq, "ParquetFile", forbidden)
    with pytest.raises(OpenAlexProfileLimitError):
        inspect_parquet(
            path, max_records=100, limits=ProfilingLimits(max_parquet_footer_bytes=8)
        )


def test_parquet_native_footer_has_finite_allocation_limits(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "thrift.parquet"
    pq.write_table(pa.table({"a": [1]}), path)
    original = pq.ParquetFile
    seen = {}

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(pq, "ParquetFile", spy)
    inspect_parquet(path, max_records=100)
    assert seen["thrift_string_size_limit"] == 1_000_000
    assert seen["thrift_container_size_limit"] == 100_000
    assert seen["pre_buffer"] is False


@pytest.mark.parametrize(
    "overrides", [{"max_nesting_depth": 2}, {"max_schema_fields": 2},
                  {"max_profile_nodes": 2}],
)
def test_parquet_schema_work_guard_precedes_values(tmp_path: Path, monkeypatch, overrides) -> None:
    path = tmp_path / "nested-budget.parquet"
    table = pa.table({"a": [[[1]]], "b": [2], "c": [3]})
    pq.write_table(table, path)

    def forbidden(*args, **kwargs):
        raise AssertionError("schema/work guard must precede value reading")

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", forbidden)
    with pytest.raises(OpenAlexProfileLimitError):
        inspect_parquet(path, max_records=100, limits=ProfilingLimits(**overrides))


def test_parquet_native_batch_checks_deadline_on_return(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "time.parquet"
    pq.write_table(pa.table({"a": [1]}), path)
    now = [0.0]
    monkeypatch.setattr(profile_models.time, "monotonic", lambda: now[0])
    original = pq.ParquetFile.iter_batches

    def slow_batch(self, *args, **kwargs):
        batch = next(original(self, *args, **kwargs))
        now[0] = 11.0
        yield batch

    monkeypatch.setattr(pq.ParquetFile, "iter_batches", slow_batch)
    with pytest.raises(OpenAlexProfileLimitError):
        inspect_parquet(path, max_records=100)


def test_parquet_resource_failure_closes_response_and_deletes_spool(tmp_path: Path, monkeypatch) -> None:
    body = write_parquet(tmp_path / "source.parquet", pa.table({"a": ["x" * 1000]}))
    response = FakeResponse(body, chunk_size=4096)
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setattr(profiling.tempfile, "tempdir", str(spool))
    with pytest.raises(OpenAlexProfileLimitError):
        profiling.profile_openalex_asset(
            connector(FakeClient(response), "parquet"),
            asset(body, uri=PARQUET_URI),
            ProfilingLimits(max_decoded_sample_bytes=10),
        )
    assert response.closed
    assert list(spool.iterdir()) == []


def test_parquet_field_schema_and_sample_evidence_are_separate(tmp_path: Path) -> None:
    body = write_parquet(tmp_path / "evidence.parquet", pa.table({"a": [{"b": None}]}))
    result, _ = profile_parquet_bytes(body)
    root = field(result, "a")
    child = field(result, "a.b")
    assert root.evidence is EvidenceType.EXACT_FILE_METADATA
    assert root.sampled_evidence["sampled_present_count"] is EvidenceType.SAMPLED_OBSERVATION
    assert root.sampled_evidence["sampled_missing_count"] is EvidenceType.UNKNOWN
    assert all(value is EvidenceType.UNKNOWN for value in child.sampled_evidence.values())
    data = result.model_dump()
    data["schema_fields"][0]["sampled_evidence"]["sampled_present_count"] = (
        EvidenceType.EXACT_FILE_METADATA
    )
    with pytest.raises(ValidationError):
        OpenAlexSourceProfile.model_validate(data)


# Common report behavior


def test_profile_output_is_deterministic(tmp_path: Path) -> None:
    body = gz(jsonl([{"b": 1, "a": {"y": None, "x": [1]}}, {"a": {"x": []}}]))
    first, _ = profile_jsonl_bytes(body)
    second, _ = profile_jsonl_bytes(body)
    assert first.model_dump_json() == second.model_dump_json()
    assert [item.path for item in first.schema_fields] == sorted(
        item.path for item in first.schema_fields
    )


def test_report_rejects_inconsistent_evidence() -> None:
    result, _ = profile_jsonl_bytes(gz(jsonl([{"id": 1}])))
    data = result.model_dump()
    data["evidence"]["row_group_count"] = EvidenceType.EXACT_FILE_METADATA
    with pytest.raises(ValidationError):
        OpenAlexSourceProfile.model_validate(data)
    data = result.model_dump()
    data["evidence"]["sampled_record_count"] = EvidenceType.EXACT_FILE_METADATA
    with pytest.raises(ValidationError):
        OpenAlexSourceProfile.model_validate(data)
    data = result.model_dump()
    del data["evidence"]["row_count"]
    with pytest.raises(ValidationError):
        OpenAlexSourceProfile.model_validate(data)


# Representation discovery and run


def manifest(content_format: str, files: list[tuple[str, int]]) -> bytes:
    return json.dumps(
        {
            "date": "2026-06-25",
            "format": content_format,
            "entity": "works",
            "files": [
                {"url": uri, "meta": {"content_length": size}} for uri, size in files
            ],
        }
    ).encode()


def test_run_reports_both_representations_and_profiles_one_file(tmp_path: Path) -> None:
    small = gz(jsonl([{"id": "W1"}]))
    jsonl_files = [
        (JSONL_URI, len(small)),
        ("s3://openalex/data/jsonl/works/updated_date=2026-06-02/part_0000.gz", len(small) + 9),
        ("s3://openalex/data/jsonl/works/updated_date=2026-06-03/part_0000.gz", 30_000_000),
    ]
    parquet_files = [(PARQUET_URI, 100)]
    jsonl_client = FakeClient(FakeResponse(manifest("jsonl", jsonl_files)), FakeResponse(small))
    parquet_client = FakeClient(FakeResponse(manifest("parquet", parquet_files)))
    run = profiling.profile_openalex_works(
        {"jsonl": connector(jsonl_client), "parquet": connector(parquet_client, "parquet")}
    )

    assert isinstance(run, OpenAlexProfileRun)
    assert run.error_category is None
    assert [item.source_format for item in run.representations] == ["jsonl", "parquet"]
    assert all(item.status == "VERIFIED" for item in run.representations)
    assert run.representations[0].listed_file_count == 3
    assert run.representations[0].eligible_file_count == 2
    assert run.representations[0].smallest_eligible_size_bytes == len(small)
    assert run.selected_uri == JSONL_URI
    assert len(run.profiles) == 1
    assert jsonl_client.uris[-1] == JSONL_URI
    assert parquet_client.uris == ["s3://openalex/data/parquet/works/manifest.json"]


def test_run_reports_unavailable_representation_without_failing_other() -> None:
    unavailable = FakeResponse(b"")
    unavailable.status = 404
    small = gz(jsonl([{"id": "W1"}]))
    run = profiling.profile_openalex_works(
        {
            "jsonl": connector(
                FakeClient(FakeResponse(manifest("jsonl", [(JSONL_URI, len(small))])), FakeResponse(small))
            ),
            "parquet": connector(FakeClient(unavailable), "parquet"),
        }
    )
    parquet = run.representations[1]
    assert (parquet.status, parquet.evidence, parquet.error_category) == (
        "UNAVAILABLE",
        EvidenceType.UNKNOWN,
        "OpenAlexEndpointUnavailableError",
    )
    assert run.error_category is None
    assert len(run.profiles) == 1


def test_run_without_eligible_file_retrieves_nothing() -> None:
    client = FakeClient(FakeResponse(manifest("jsonl", [(JSONL_URI, 30_000_000)])))
    run = profiling.profile_openalex_works({"jsonl": connector(client)})
    assert run.error_category == "NoEligibleFile"
    assert run.profiles == ()
    assert client.uris == ["s3://openalex/data/jsonl/works/manifest.json"]


def test_run_reports_profile_failure_category() -> None:
    bad = gz(b"not json\n")
    client = FakeClient(FakeResponse(manifest("jsonl", [(JSONL_URI, len(bad))])), FakeResponse(bad))
    run = profiling.profile_openalex_works({"jsonl": connector(client)})
    assert run.error_category == "OpenAlexMalformedJSONLError"
    assert run.profiles == ()


def test_run_rejects_unsupported_profile_format() -> None:
    with pytest.raises(OpenAlexUnsupportedFormatError):
        profiling.profile_openalex_works({"jsonl": connector(FakeClient())}, profile_format="csv")


def test_run_model_enforces_one_file_bound() -> None:
    result, _ = profile_jsonl_bytes(gz(jsonl([{"id": 1}])))
    with pytest.raises(ValidationError):
        OpenAlexProfileRun(
            limits=ProfilingLimits(),
            profile_format="jsonl",
            representations=(),
            selected_uri=JSONL_URI,
            profiles=(result, result),
            error_category=None,
        )


def test_discover_metadata_reuses_selector_and_discover_delegates() -> None:
    files = [(JSONL_URI, 10), ("s3://openalex/data/jsonl/works/part_1.gz", 5)]
    client = FakeClient(FakeResponse(manifest("jsonl", files)), FakeResponse(manifest("jsonl", files)))
    selection = connector(client).discover_metadata()
    assert [item.byte_size for item in selection.selected] == [5]
    assert selection.eligible_count == 2
    second = connector(client)
    assert [item.uri for item in second.discover()] == ["s3://openalex/data/jsonl/works/part_1.gz"]


def test_main_rejects_invalid_limits_without_network(capsys, monkeypatch) -> None:
    monkeypatch.setattr(profiling, "OpenAlexConnector", None)
    assert profiling.main(["--max-profile-records", "0"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "error_category": "InvalidLimits",
        "profile": "failed",
    }


def test_main_prints_safe_structured_summary(capsys, monkeypatch) -> None:
    small = gz(jsonl([{"id": "W1"}]))
    clients = {
        "jsonl": FakeClient(
            FakeResponse(manifest("jsonl", [(JSONL_URI, len(small))])), FakeResponse(small)
        ),
        "parquet": FakeClient(FakeResponse(manifest("parquet", [(PARQUET_URI, 10)]))),
    }

    def fake_connector(**kwargs: Any) -> OpenAlexConnector:
        assert kwargs["sample_selection"] == SampleSelectionConfig()
        return OpenAlexConnector(client=clients[kwargs["content_format"]], **kwargs)

    monkeypatch.setattr(profiling, "OpenAlexConnector", fake_connector)
    assert profiling.main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["profile"] == "profiled"
    assert output["profiles"][0]["source_format"] == "jsonl"
    assert output["profiles"][0]["evidence"]["schema_fields"] == "SAMPLED_OBSERVATION"
    assert output["limits"]["max_files"] == 1
