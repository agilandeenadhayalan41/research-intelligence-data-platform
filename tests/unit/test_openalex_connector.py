import json
from collections import deque
from collections.abc import Iterable

import pytest

from research_platform.config import SampleSelectionConfig
from research_platform.sources.base import SourceAsset, SourceConnector
from research_platform.sources.openalex import (
    OpenAlexAccessDeniedError,
    OpenAlexConnector,
    OpenAlexEndpointUnavailableError,
    OpenAlexFormatError,
    OpenAlexManifestError,
    OpenAlexNetworkError,
    OpenAlexSizeLimitError,
    OpenAlexTimeoutError,
    OpenAlexTruncatedError,
    parse_openalex_works_manifest,
)

MANIFEST_URI = "s3://openalex/data/jsonl/works/manifest.json"
FILE_URI = "s3://openalex/data/jsonl/works/part_0000.gz"


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        chunk_size: int = 3,
    ) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {}
        self.chunk_size = chunk_size
        self.closed = False
        self.read_sizes: list[int] = []
        self._position = 0

    def read(self, size: int = -1) -> bytes:
        assert size >= 0, "connector must never make an unbounded transport read"
        self.read_sizes.append(size)
        end = min(self._position + size, self._position + self.chunk_size, len(self.body))
        content = self.body[self._position : end]
        self._position = end
        return content

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, *responses: FakeResponse | Exception) -> None:
        self.responses = deque(responses)
        self.calls: list[tuple[str, float, dict[str, str]]] = []

    def request(
        self,
        uri: str,
        *,
        timeout: float,
        headers: dict[str, str],
    ) -> FakeResponse:
        self.calls.append((uri, timeout, headers))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def manifest(
    *,
    content_format: str = "jsonl",
    file_uri: str = FILE_URI,
    size: int | None = 3,
) -> bytes:
    meta = {} if size is None else {"content_length": size}
    return json.dumps(
        {
            "date": "2026-06-25",
            "format": content_format,
            "entity": "works",
            "files": [{"url": file_uri, "meta": meta}],
        }
    ).encode()


def asset(uri: str = FILE_URI) -> SourceAsset:
    try:
        content_format = "parquet" if uri.endswith(".parquet") else "jsonl"
        return parse_openalex_works_manifest(
            manifest(content_format=content_format, file_uri=uri),
            content_format=content_format,
        )[0].to_source_asset()
    except (ValueError, IndexError):
        return SourceAsset("openalex", "oa-" + "a" * 64, uri)


def connector(
    client: FakeClient,
    *,
    max_file_size_bytes: int = 5,
    content_format: str = "jsonl",
) -> OpenAlexConnector:
    return OpenAlexConnector(
        sample_selection=SampleSelectionConfig(
            max_files=1, max_file_size_bytes=max_file_size_bytes
        ),
        content_format=content_format,
        timeout_seconds=2,
        max_attempts=3,
        retry_backoff_seconds=0,
        client=client,
    )


def test_connector_reuses_source_contract_and_discovery_only_reads_manifest() -> None:
    response = FakeResponse(manifest(), headers={"Content-Type": "application/json"})
    client = FakeClient(response)
    adapter = connector(client)

    discovered: Iterable[SourceAsset] = adapter.discover()

    assert isinstance(adapter, SourceConnector)
    assert list(discovered) == [asset()]
    assert [call[0] for call in client.calls] == [MANIFEST_URI]
    assert max(response.read_sizes) <= 64 * 1024
    assert response.closed


def test_discovery_honors_declared_format_and_bounded_sample_selector() -> None:
    parquet_uri = "s3://openalex/data/parquet/works/part_0000.parquet"
    response = FakeResponse(
        manifest(content_format="parquet", file_uri=parquet_uri, size=4),
        headers={"Content-Type": "application/json"},
    )
    adapter = connector(FakeClient(response), content_format="parquet")

    assert list(adapter.discover()) == [asset(parquet_uri)]
    assert response.closed


@pytest.mark.parametrize(
    "content",
    [
        b"{broken",
        b'{"date":"2026-06-25","format":"jsonl","entity":"works","files":[],"extra":1}',
        b'{"date":"2026-06-25","format":"csv","entity":"works","files":[]}',
    ],
)
def test_discovery_rejects_malformed_manifest_and_unsupported_format(content: bytes) -> None:
    response = FakeResponse(content)
    with pytest.raises((OpenAlexManifestError, OpenAlexFormatError)):
        connector(FakeClient(response)).discover()
    assert response.closed


def test_discovery_rejects_manifest_actual_byte_overflow_and_closes_response() -> None:
    response = FakeResponse(manifest() + b" " * 1_000_001, chunk_size=64 * 1024)
    with pytest.raises(OpenAlexSizeLimitError, match="manifest"):
        OpenAlexConnector(client=FakeClient(response)).discover()
    assert response.closed
    assert max(response.read_sizes) <= 64 * 1024


def test_sample_selector_excludes_unknown_and_over_limit_manifest_assets() -> None:
    unknown = FakeResponse(manifest(size=None))
    oversized = FakeResponse(manifest(size=25_000_001))

    assert list(connector(FakeClient(unknown)).discover()) == []
    assert list(connector(FakeClient(oversized)).discover()) == []


def test_fetch_streams_chunks_and_closes_on_caller_eof() -> None:
    response = FakeResponse(b"abcde", headers={"Content-Length": "5"})
    stream = connector(FakeClient(response)).fetch(asset())

    assert stream.read(2) == b"ab"
    assert not response.closed
    assert stream.read() == b"cde"
    assert response.closed
    assert max(response.read_sizes) <= 64 * 1024
    stream.close()


def test_closing_an_abandoned_stream_releases_the_response() -> None:
    response = FakeResponse(b"unread")
    stream = connector(FakeClient(response)).fetch(asset())

    stream.close()

    assert response.closed


@pytest.mark.parametrize(
    ("headers", "body"),
    [
        ({}, b"abcdef"),
        ({"Content-Length": "2"}, b"abcdef"),
        ({"Content-Length": "malformed"}, b"abcdef"),
    ],
)
def test_actual_payload_bytes_are_limited_despite_missing_or_wrong_metadata(
    headers: dict[str, str], body: bytes
) -> None:
    response = FakeResponse(body, headers=headers)
    stream = connector(FakeClient(response)).fetch(asset())

    with pytest.raises(OpenAlexSizeLimitError):
        stream.read()
    assert response.closed


def test_exact_payload_limit_is_allowed() -> None:
    response = FakeResponse(b"12345")
    stream = connector(FakeClient(response)).fetch(asset())
    assert stream.read() == b"12345"
    assert response.closed


def test_readinto_and_iteration_obey_cumulative_actual_byte_limit() -> None:
    response = FakeResponse(b"abcde")
    stream = connector(FakeClient(response)).fetch(asset())
    buffer = bytearray(3)
    assert stream.readinto(buffer) == 3
    assert buffer == b"abc"
    assert stream.read(2) == b"de"
    assert response.closed

    overflow = FakeResponse(b"abcdef")
    iterable = connector(FakeClient(overflow)).fetch(asset())
    with pytest.raises(OpenAlexSizeLimitError):
        list(iterable)
    assert overflow.closed


def test_fetch_rejects_wrong_scheme_bucket_namespace_and_format_before_request() -> None:
    client = FakeClient()
    adapter = connector(client)
    for uri in (
        "https://openalex.s3.amazonaws.com/data/jsonl/works/part.gz",
        "s3://other/data/jsonl/works/part.gz",
        "s3://openalex/data/parquet/works/part.parquet",
        "s3://openalex/data/jsonl/works/../part.gz",
    ):
        with pytest.raises((OpenAlexEndpointUnavailableError, OpenAlexFormatError)):
            adapter.fetch(asset(uri))
    assert client.calls == []


def test_fetch_rejects_response_with_unexpected_content_type() -> None:
    response = FakeResponse(b"html", headers={"Content-Type": "text/html"})
    with pytest.raises(OpenAlexFormatError):
        connector(FakeClient(response)).fetch(asset())
    assert response.closed


def test_timeout_retry_and_transient_status_retries_are_bounded() -> None:
    retried = FakeResponse(manifest())
    transient = FakeResponse(b"", status=503)
    client = FakeClient(TimeoutError(), transient, retried)
    adapter = connector(client)

    assert list(adapter.discover()) == [asset()]
    assert len(client.calls) == 3
    assert all(call[1] == 2 for call in client.calls)
    assert all(call[2] == {"Accept-Encoding": "identity"} for call in client.calls)
    assert transient.closed
    assert retried.closed


def test_access_denied_and_nonretryable_endpoint_failures_are_explicit() -> None:
    denied = FakeResponse(b"", status=403)
    with pytest.raises(OpenAlexAccessDeniedError, match="anonymous"):
        connector(FakeClient(denied)).discover()
    assert denied.closed

    unavailable = FakeResponse(b"", status=404)
    client = FakeClient(unavailable)
    with pytest.raises(OpenAlexEndpointUnavailableError, match="404"):
        connector(client).discover()
    assert len(client.calls) == 1
    assert unavailable.closed


def test_network_failure_is_explicit_after_bounded_attempts() -> None:
    client = FakeClient(OSError("synthetic network failure"), OSError(), OSError())
    with pytest.raises(OpenAlexNetworkError):
        connector(client).discover()
    assert len(client.calls) == 3


def test_timeout_and_truncated_payload_are_explicit_and_cleaned_up() -> None:
    timeout_response = FakeResponse(b"")
    timeout_response.read = lambda size=-1: (_ for _ in ()).throw(TimeoutError())  # type: ignore[method-assign]
    timeout_stream = connector(FakeClient(timeout_response)).fetch(asset())
    with pytest.raises(OpenAlexTimeoutError):
        timeout_stream.read()
    assert timeout_response.closed

    truncated = FakeResponse(b"abc", headers={"Content-Length": "5"})
    with pytest.raises(OpenAlexTruncatedError):
        connector(FakeClient(truncated)).fetch(asset()).read()
    assert truncated.closed


def test_connectivity_check_is_manifest_only_and_records_expected_and_actual_format() -> None:
    response = FakeResponse(manifest(), headers={"Content-Type": "application/json"})
    client = FakeClient(response)

    evidence = connector(client).check_public_connectivity().as_dict()

    assert evidence == {
        "endpoint": "https://openalex.s3.amazonaws.com/data/jsonl/works/manifest.json",
        "access_mode": "anonymous public HTTPS",
        "expected_format": "jsonl",
        "actual_format": "jsonl",
        "manifest_byte_limit": 1_000_000,
    }
    assert [call[0] for call in client.calls] == [MANIFEST_URI]
    assert response.closed


@pytest.mark.parametrize(
    "settings",
    [
        {"timeout_seconds": 31},
        {"max_attempts": 6},
        {"retry_backoff_seconds": 1.1},
    ],
)
def test_request_configuration_has_hard_upper_bounds(settings: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        OpenAlexConnector(**settings)  # type: ignore[arg-type]
