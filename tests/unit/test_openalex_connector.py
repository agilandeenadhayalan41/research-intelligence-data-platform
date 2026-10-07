import io
import json
import socket
from collections import deque
from typing import Any

import pytest

from research_platform.config import SampleSelectionConfig
from research_platform.sources.base import SourceAsset, SourceConnector
from research_platform.sources.openalex import (
    OpenAlexAccessDeniedError,
    OpenAlexConnector,
    OpenAlexConnectorError,
    OpenAlexEndpointUnavailableError,
    OpenAlexFormatError,
    OpenAlexManifestError,
    OpenAlexNetworkError,
    OpenAlexSizeLimitError,
    OpenAlexTimeoutError,
    OpenAlexTruncatedError,
    parse_openalex_works_manifest,
)
from research_platform.sources.openalex import connectivity
from research_platform.sources.openalex import connector as connector_module

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
    files: list[dict[str, Any]] | None = None,
) -> bytes:
    meta = {} if size is None else {"content_length": size}
    manifest_files = files or [{"url": file_uri, "meta": meta}]
    return json.dumps(
        {
            "date": "2026-06-25",
            "format": content_format,
            "entity": "works",
            "files": manifest_files,
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
    max_files: int = 1,
    content_format: str = "jsonl",
) -> OpenAlexConnector:
    return OpenAlexConnector(
        sample_selection=SampleSelectionConfig(
            max_files=max_files, max_file_size_bytes=max_file_size_bytes
        ),
        content_format=content_format,
        timeout_seconds=2,
        max_attempts=3,
        retry_backoff_seconds=0,
        client=client,
    )


@pytest.mark.parametrize(
    ("content_format", "file_uri"),
    [
        ("jsonl", FILE_URI),
        ("parquet", "s3://openalex/data/parquet/works/part_0000.parquet"),
    ],
)
def test_discovery_accepts_observed_binary_mime_for_both_formats(
    content_format: str, file_uri: str
) -> None:
    response = FakeResponse(
        manifest(content_format=content_format, file_uri=file_uri),
        headers={"Content-Type": "binary/octet-stream"},
    )
    client = FakeClient(response)
    adapter = connector(client, content_format=content_format)

    assert isinstance(adapter, SourceConnector)
    assert list(adapter.discover()) == [asset(file_uri)]
    assert [call[0] for call in client.calls] == [
        f"s3://openalex/data/{content_format}/works/manifest.json"
    ]
    assert max(response.read_sizes) <= 64 * 1024
    assert response.closed


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/x-json",
        "text/json",
        "application/vnd.openalex+json",
    ],
)
def test_manifest_accepts_json_mime_types(content_type: str) -> None:
    response = FakeResponse(manifest(), headers={"Content-Type": content_type})
    assert list(connector(FakeClient(response)).discover()) == [asset()]


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


@pytest.mark.parametrize(
    "content",
    [
        b'{"date":"2026-06-25","format":"jsonl","entity":"works","files":[],"extra":1}',
        b'{"date":"2026-06-25","format":"jsonl","entity":"works","entity":"works","files":[]}',
        b'{"date":"2026-06-25","format":"jsonl","entity":"works","files":[],"record_count":' + b"9" * 5000 + b"}",
        b'{"date":"not-a-date","format":"jsonl","entity":"works","files":[]}',
        b"[" + b"[" * 1500 + b"0" + b"]" * 1500 + b"]",
    ],
)
def test_structurally_malformed_manifests_have_exact_safe_error_category(
    content: bytes,
) -> None:
    response = FakeResponse(content, headers={"Content-Type": "binary/octet-stream"})
    with pytest.raises(OpenAlexManifestError):
        connector(FakeClient(response)).discover()
    assert response.closed


@pytest.mark.parametrize("content_type", ["text/html", "application/xml", "text/xml"])
def test_manifest_rejects_html_and_xml_mime_types(content_type: str) -> None:
    response = FakeResponse(manifest(), headers={"Content-Type": content_type})
    with pytest.raises(OpenAlexFormatError):
        connector(FakeClient(response)).discover()
    assert response.closed


def test_manifest_declared_format_error_is_distinct_from_unknown_fields() -> None:
    unknown = FakeResponse(
        b'{"date":"2026-06-25","format":"jsonl","entity":"works","files":[],"unknown":1}',
        headers={"Content-Type": "binary/octet-stream"},
    )
    with pytest.raises(OpenAlexManifestError):
        connector(FakeClient(unknown)).discover()
    assert unknown.closed

    wrong_format = FakeResponse(
        manifest(content_format="parquet"),
        headers={"Content-Type": "binary/octet-stream"},
    )
    with pytest.raises(OpenAlexFormatError):
        connector(FakeClient(wrong_format)).discover()
    assert wrong_format.closed


def test_discovery_rejects_manifest_actual_byte_overflow_and_closes_response() -> None:
    response = FakeResponse(manifest() + b" " * 1_000_001, chunk_size=64 * 1024)
    with pytest.raises(OpenAlexSizeLimitError, match="manifest"):
        OpenAlexConnector(client=FakeClient(response)).discover()
    assert response.closed
    assert max(response.read_sizes) <= 64 * 1024


def test_discovery_rejects_prefix_valid_manifest_truncated_by_content_length() -> None:
    body = manifest()
    response = FakeResponse(
        body,
        headers={
            "Content-Type": "binary/octet-stream",
            "Content-Length": str(len(body) + 10),
        },
    )
    with pytest.raises(OpenAlexTruncatedError):
        connector(FakeClient(response)).discover()
    assert response.closed


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (TimeoutError("synthetic"), OpenAlexTimeoutError),
        (OSError("synthetic"), OpenAlexNetworkError),
    ],
)
def test_manifest_read_failures_are_safe_and_cleanup_responses(
    failure: Exception, expected_error: type[OpenAlexConnectorError]
) -> None:
    class FailingReadResponse(FakeResponse):
        def read(self, size: int = -1) -> bytes:
            raise failure

    response = FailingReadResponse(
        manifest(), headers={"Content-Type": "binary/octet-stream"}
    )
    with pytest.raises(expected_error):
        connector(FakeClient(response)).discover()
    assert response.closed


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


@pytest.mark.parametrize(
    "uri",
    [
        "\x01s3://openalex/data/jsonl/works/part.gz",
        "\x1fs3://openalex/data/jsonl/works/part.gz",
        "s3://openalex/data/jsonl/works/part\n.gz",
        "s3://openalex/data/jsonl/works/part\t.gz",
        "s3://openalex/data/jsonl/works/part\r.gz",
        "s3://openalex/data/jsonl/works/part\x7f.gz",
        "s3://user@openalex/data/jsonl/works/part.gz",
        "s3://openalex:443/data/jsonl/works/part.gz",
        "s3://openalex/data/jsonl/works/part.gz?x=1",
        "s3://openalex/data/jsonl/works/part.gz#fragment",
        "s3://openalex/data/jsonl/works/%2e%2e/part.gz",
        "s3://openalex/data/jsonl/works/../part.gz",
        "s3://openalex/data/jsonl/works\\part.gz",
    ],
)
def test_direct_source_assets_with_noncanonical_uris_are_rejected_raw(
    uri: str,
) -> None:
    client = FakeClient()
    with pytest.raises(OpenAlexConnectorError):
        connector(client).fetch(SourceAsset("openalex", "oa-" + "a" * 64, uri))
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


def test_stream_failure_after_delivery_does_not_retry_or_replay_payload() -> None:
    class FailingAfterFirstByte(FakeResponse):
        def read(self, size: int = -1) -> bytes:
            if self._position:
                raise OSError("synthetic stream failure")
            return super().read(1)

    response = FailingAfterFirstByte(b"ab", chunk_size=1)
    client = FakeClient(response)
    stream = connector(client).fetch(asset())
    assert stream.read1(1) == b"a"
    with pytest.raises(OpenAlexNetworkError):
        stream.read1(1)
    assert len(client.calls) == 1
    assert response.closed


def test_connectivity_check_is_manifest_only_and_records_expected_and_actual_format() -> None:
    response = FakeResponse(manifest(), headers={"Content-Type": "binary/octet-stream"})
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


def test_default_and_explicit_multi_asset_file_bounds() -> None:
    files = [
        {"url": FILE_URI, "meta": {"content_length": 3}},
        {
            "url": "s3://openalex/data/jsonl/works/part_0001.gz",
            "meta": {"content_length": 4},
        },
        {
            "url": "s3://openalex/data/jsonl/works/part_0002.gz",
            "meta": {"content_length": 6},
        },
    ]
    default_response = FakeResponse(
        manifest(files=files), headers={"Content-Type": "binary/octet-stream"}
    )
    default_selected = connector(FakeClient(default_response)).discover()
    assert len(tuple(default_selected)) == 1

    two_files_response = FakeResponse(
        manifest(files=files), headers={"Content-Type": "binary/octet-stream"}
    )
    two_selected = connector(FakeClient(two_files_response), max_files=2).discover()
    assert [asset.uri for asset in two_selected] == [
        FILE_URI,
        "s3://openalex/data/jsonl/works/part_0001.gz",
    ]


def test_generated_payload_exact_default_limit_and_one_byte_overflow() -> None:
    class RepeatingResponse:
        status = 200
        headers: dict[str, str] = {}

        def __init__(self, size: int) -> None:
            self.remaining = size
            self.closed = False

        def read(self, size: int = -1) -> bytes:
            assert size >= 0
            count = min(size, self.remaining)
            self.remaining -= count
            return b"x" * count

        def close(self) -> None:
            self.closed = True

    exact = RepeatingResponse(25_000_000)
    exact_stream = OpenAlexConnector(client=FakeClient(exact)).fetch(asset())
    assert len(exact_stream.read()) == 25_000_000
    assert exact.closed

    too_large = RepeatingResponse(25_000_001)
    too_large_stream = OpenAlexConnector(client=FakeClient(too_large)).fetch(asset())
    with pytest.raises(OpenAlexSizeLimitError):
        too_large_stream.read()
    assert too_large.closed


class WireSocket:
    def __init__(self, wire: bytes, *, max_chunk: int | None = None) -> None:
        self.wire = io.BytesIO(wire)
        self.max_chunk = max_chunk
        self.timeouts: list[float] = []
        self.closed = False

    def recv_into(self, buffer: memoryview) -> int:
        size = len(buffer)
        if self.max_chunk is not None:
            size = min(size, self.max_chunk)
        content = self.wire.read(size)
        buffer[: len(content)] = content
        return len(content)

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def close(self) -> None:
        self.closed = True


class FakeHTTPSConnection:
    instances: list["FakeHTTPSConnection"] = []
    wire = b""
    request_error: Exception | None = None

    def __init__(self, host: str, *, timeout: float) -> None:
        self.host = host
        self.timeout = timeout
        self.sock = WireSocket(type(self).wire)
        self.request_args: tuple[str, str, dict[str, str]] | None = None
        self.closed = False
        type(self).instances.append(self)

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        self.request_args = (method, path, headers)
        if type(self).request_error is not None:
            raise type(self).request_error

    def close(self) -> None:
        self.closed = True
        self.sock.close()


def install_fake_https(
    monkeypatch: pytest.MonkeyPatch,
    wire: bytes,
    *,
    request_error: Exception | None = None,
) -> None:
    FakeHTTPSConnection.instances = []
    FakeHTTPSConnection.wire = wire
    FakeHTTPSConnection.request_error = request_error
    monkeypatch.setattr(
        connector_module.http.client, "HTTPSConnection", FakeHTTPSConnection
    )


def test_anonymous_transport_uses_fixed_host_unsigned_get_and_real_chunk_framing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wire = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
        b"Content-Type: binary/octet-stream\r\n\r\n"
        b"2\r\nhe\r\n3\r\nllo\r\n0\r\n\r\n"
    )
    install_fake_https(monkeypatch, wire)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")

    response = connector_module.AnonymousOpenAlexHTTPClient().request(
        MANIFEST_URI,
        timeout=3,
        headers={"Accept-Encoding": "identity"},
    )

    connection = FakeHTTPSConnection.instances[0]
    assert connection.host == "openalex.s3.amazonaws.com"
    assert connection.timeout == 3
    assert connection.request_args == (
        "GET",
        "/data/jsonl/works/manifest.json",
        {"Accept-Encoding": "identity"},
    )
    assert "Authorization" not in connection.request_args[2]
    assert "Proxy-Authorization" not in connection.request_args[2]
    assert response.read(2) == b"he"
    assert response.read(3) == b"llo"
    response.close()
    assert connection.closed


def test_anonymous_transport_rejects_and_closes_redirect_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_https(
        monkeypatch,
        b"HTTP/1.1 302 Found\r\nContent-Length: 0\r\nLocation: https://evil.invalid/\r\n\r\n",
    )
    adapter = OpenAlexConnector()
    monkeypatch.setattr(adapter, "_client", connector_module.AnonymousOpenAlexHTTPClient())

    with pytest.raises(OpenAlexEndpointUnavailableError):
        adapter.fetch(asset())
    connection = FakeHTTPSConnection.instances[0]
    assert connection.closed


def test_anonymous_transport_closes_connection_when_request_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_https(monkeypatch, b"", request_error=OSError("synthetic"))
    client = connector_module.AnonymousOpenAlexHTTPClient()

    with pytest.raises(OSError):
        client.request(MANIFEST_URI, timeout=3, headers={"Accept-Encoding": "identity"})
    assert FakeHTTPSConnection.instances[0].closed


def test_chunked_http_truncation_is_reported_and_connection_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_https(
        monkeypatch,
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
        b"Content-Type: application/octet-stream\r\n\r\n5\r\nab",
    )
    adapter = OpenAlexConnector()
    monkeypatch.setattr(adapter, "_client", connector_module.AnonymousOpenAlexHTTPClient())

    with pytest.raises(OpenAlexTruncatedError):
        adapter.fetch(asset()).read()
    assert FakeHTTPSConnection.instances[0].closed


def test_http_deadline_expires_during_trickle_without_sleeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [10.0]
    monkeypatch.setattr(connector_module.time, "monotonic", lambda: now[0])

    class DrippingSocket(WireSocket):
        def recv_into(self, buffer: memoryview) -> int:
            size = min(len(buffer), 1)
            content = self.wire.read(size)
            buffer[: len(content)] = content
            now[0] += 0.02
            return len(content)

    wire = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
    FakeHTTPSConnection.instances = []
    FakeHTTPSConnection.wire = wire
    FakeHTTPSConnection.request_error = None

    class DrippingHTTPSConnection(FakeHTTPSConnection):
        def __init__(self, host: str, *, timeout: float) -> None:
            super().__init__(host, timeout=timeout)
            self.sock = DrippingSocket(type(self).wire)

    monkeypatch.setattr(
        connector_module.http.client, "HTTPSConnection", DrippingHTTPSConnection
    )
    with pytest.raises(socket.timeout):
        connector_module.AnonymousOpenAlexHTTPClient().request(
            MANIFEST_URI, timeout=0.05, headers={"Accept-Encoding": "identity"}
        )
    assert DrippingHTTPSConnection.instances[0].closed


def test_connectivity_cli_reports_stable_safe_categories(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Successful:
        def __init__(self, **kwargs: object) -> None:
            pass

        def check_public_connectivity(self) -> object:
            return type(
                "Result",
                (),
                {"as_dict": lambda self: {"actual_format": "jsonl"}},
            )()

    monkeypatch.setattr(connectivity, "OpenAlexConnector", Successful)
    assert connectivity.main() == 0
    assert json.loads(capsys.readouterr().out)["actual_format"] == "jsonl"

    class Failed:
        def __init__(self, **kwargs: object) -> None:
            pass

        def check_public_connectivity(self) -> None:
            raise OpenAlexAccessDeniedError("message must not be emitted")

        @property
        def public_manifest_endpoint(self) -> str:
            return "https://openalex.s3.amazonaws.com/data/jsonl/works/manifest.json"

    monkeypatch.setattr(connectivity, "OpenAlexConnector", Failed)
    assert connectivity.main() == 1
    captured = capsys.readouterr().out
    output = json.loads(captured)
    assert output["error_category"] == "OpenAlexAccessDeniedError"
    assert "message must not be emitted" not in captured


def test_connectivity_cli_does_not_swallow_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Broken:
        def __init__(self, **kwargs: object) -> None:
            pass

        def check_public_connectivity(self) -> None:
            raise RuntimeError("unexpected")

    monkeypatch.setattr(connectivity, "OpenAlexConnector", Broken)
    with pytest.raises(RuntimeError, match="unexpected"):
        connectivity.main()


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
