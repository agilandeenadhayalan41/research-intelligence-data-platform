"""Anonymous, bounded access to public OpenAlex Works snapshot metadata and files."""

from __future__ import annotations

import http.client
import io
import json
import math
import re
import socket
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import BinaryIO, Protocol
from urllib.parse import urlsplit

from research_platform.config import SampleSelectionConfig
from research_platform.sources.base import SourceAsset, SourceConnector
from research_platform.sources.openalex.manifest import parse_openalex_works_manifest
from research_platform.sources.openalex.metadata import OpenAlexAssetMetadata
from research_platform.sources.openalex.sample import select_openalex_works_sample

_S3_BUCKET = "openalex"
_PUBLIC_HOST = "openalex.s3.amazonaws.com"
_FORMATS = {
    "jsonl": ("/data/jsonl/works/manifest.json", ".gz"),
    "parquet": ("/data/parquet/works/manifest.json", ".parquet"),
}
_MANIFEST_LIMIT = 1_000_000
_CHUNK_SIZE = 64 * 1024
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_TIMEOUT_SECONDS = 30
_MAX_ATTEMPTS = 5
_MAX_RETRY_BACKOFF_SECONDS = 1


class OpenAlexConnectorError(Exception):
    """Safe base exception for OpenAlex connector failures."""


class OpenAlexAccessDeniedError(OpenAlexConnectorError):
    """The public endpoint denied anonymous access."""


class OpenAlexEndpointUnavailableError(OpenAlexConnectorError):
    """The expected public snapshot endpoint is unavailable."""


class OpenAlexManifestError(OpenAlexConnectorError):
    """The Works manifest could not be read or validated."""


class OpenAlexFormatError(OpenAlexConnectorError):
    """The manifest or response declares an unsupported format."""


class OpenAlexTimeoutError(OpenAlexConnectorError):
    """A bounded OpenAlex request or stream timed out."""


class OpenAlexNetworkError(OpenAlexConnectorError):
    """A bounded OpenAlex request or stream failed."""


class OpenAlexSizeLimitError(OpenAlexConnectorError):
    """A manifest or payload exceeded its actual-byte limit."""


class OpenAlexTruncatedError(OpenAlexConnectorError):
    """A response ended before its declared content length."""


class _Response(Protocol):
    status: int
    headers: object

    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


class _Client(Protocol):
    def request(
        self,
        uri: str,
        *,
        timeout: float,
        headers: dict[str, str],
    ) -> _Response: ...


class AnonymousOpenAlexHTTPClient:
    """Minimal HTTPS transport with a fixed public S3 host and no auth handlers."""

    def request(
        self,
        uri: str,
        *,
        timeout: float,
        headers: dict[str, str],
    ) -> _Response:
        path = _public_path(uri)
        deadline = time.monotonic() + timeout
        connection = http.client.HTTPSConnection(_PUBLIC_HOST, timeout=timeout)
        try:
            connection.request("GET", path, headers=headers)
            response = http.client.HTTPResponse(
                _DeadlineSocket(connection.sock, deadline), method="GET"
            )
            response.begin()
        except Exception:
            connection.close()
            raise
        return _HTTPResponse(response, connection)


class _DeadlineSocket:
    def __init__(self, connection_socket: socket.socket, deadline: float):
        self._socket = connection_socket
        self._deadline = deadline

    def makefile(self, mode: str = "rb") -> io.BufferedReader:
        if mode != "rb":
            raise ValueError("OpenAlex transport supports read-only HTTP responses")
        return io.BufferedReader(_DeadlineSocketIO(self))

    def recv_into(self, buffer: memoryview) -> int:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("OpenAlex request deadline expired")
        self._socket.settimeout(remaining)
        return self._socket.recv_into(buffer)


class _DeadlineSocketIO(io.RawIOBase):
    def __init__(self, deadline_socket: _DeadlineSocket):
        super().__init__()
        self._deadline_socket = deadline_socket

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:
        return self._deadline_socket.recv_into(memoryview(buffer))


class _HTTPResponse:
    def __init__(
        self, response: http.client.HTTPResponse, connection: http.client.HTTPSConnection
    ) -> None:
        self._response = response
        self._connection = connection
        self.status = response.status
        self.headers = response.headers

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError("OpenAlex transport requires bounded response reads")
        return self._response.read(size)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


class _BoundedStream(io.RawIOBase):
    def __init__(self, response: _Response, max_bytes: int, expected_bytes: int | None):
        super().__init__()
        self._response = response
        self._max_bytes = max_bytes
        self._expected_bytes = expected_bytes
        self._actual_bytes = 0
        self._eof = False
        self._response_closed = False

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed stream")
        if self._eof:
            return 0
        view = memoryview(buffer).cast("B")
        if not view:
            return 0
        remaining = self._max_bytes - self._actual_bytes
        if remaining == 0:
            self._check_limit_boundary()
            return 0
        try:
            data = self._response.read(min(len(view), remaining, _CHUNK_SIZE))
        except (TimeoutError, socket.timeout):
            self.close()
            raise OpenAlexTimeoutError("OpenAlex payload stream timed out") from None
        except http.client.IncompleteRead:
            self.close()
            raise OpenAlexTruncatedError("OpenAlex payload response ended unexpectedly") from None
        except http.client.HTTPException:
            self.close()
            raise OpenAlexNetworkError("OpenAlex payload stream failed") from None
        except OSError:
            self.close()
            raise OpenAlexNetworkError("OpenAlex payload stream failed") from None
        if not data:
            self._check_declared_length()
            self._finish()
            return 0
        view[: len(data)] = data
        self._actual_bytes += len(data)
        if self._actual_bytes == self._max_bytes:
            self._check_limit_boundary()
        return len(data)

    def _check_limit_boundary(self) -> None:
        try:
            extra = self._response.read(1)
        except (TimeoutError, socket.timeout):
            self.close()
            raise OpenAlexTimeoutError("OpenAlex payload stream timed out") from None
        except http.client.IncompleteRead:
            self.close()
            raise OpenAlexTruncatedError("OpenAlex payload response ended unexpectedly") from None
        except http.client.HTTPException:
            self.close()
            raise OpenAlexNetworkError("OpenAlex payload stream failed") from None
        except OSError:
            self.close()
            raise OpenAlexNetworkError("OpenAlex payload stream failed") from None
        if extra:
            self.close()
            raise OpenAlexSizeLimitError("OpenAlex payload exceeds the configured byte limit")
        self._check_declared_length()
        self._finish()

    def _check_declared_length(self) -> None:
        if self._expected_bytes is not None and self._actual_bytes < self._expected_bytes:
            self.close()
            raise OpenAlexTruncatedError("OpenAlex payload ended before its declared length")

    def close(self) -> None:
        if not self.closed:
            try:
                self._close_response()
            finally:
                super().close()

    def _finish(self) -> None:
        self._eof = True
        self._close_response()

    def _close_response(self) -> None:
        if not self._response_closed:
            self._response_closed = True
            self._response.close()


class OpenAlexConnector(SourceConnector):
    """Discover one bounded Works sample and stream selected public snapshot files."""

    def __init__(
        self,
        *,
        sample_selection: SampleSelectionConfig = SampleSelectionConfig(),
        content_format: str = "jsonl",
        timeout_seconds: float = 10,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.1,
        client: _Client | None = None,
    ) -> None:
        if content_format not in _FORMATS:
            raise OpenAlexFormatError("OpenAlex content format must be jsonl or parquet")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout_seconds must be greater than zero and at most 30")
        if type(max_attempts) is not int or not 1 <= max_attempts <= _MAX_ATTEMPTS:
            raise ValueError("max_attempts must be between 1 and 5")
        if (
            isinstance(retry_backoff_seconds, bool)
            or not isinstance(retry_backoff_seconds, (int, float))
            or not math.isfinite(retry_backoff_seconds)
            or retry_backoff_seconds < 0
            or retry_backoff_seconds > _MAX_RETRY_BACKOFF_SECONDS
        ):
            raise ValueError("retry_backoff_seconds must be between zero and 1")
        self._selection_config = SampleSelectionConfig.model_validate(
            sample_selection.model_dump()
        )
        self._content_format = content_format
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff = retry_backoff_seconds
        self._client = client or AnonymousOpenAlexHTTPClient()

    @property
    def manifest_uri(self) -> str:
        return f"s3://{_S3_BUCKET}{_FORMATS[self._content_format][0]}"

    @property
    def public_manifest_endpoint(self) -> str:
        return f"https://{_PUBLIC_HOST}{_FORMATS[self._content_format][0]}"

    def discover(self) -> Iterable[SourceAsset]:
        assets, _ = self._load_manifest()
        selection = select_openalex_works_sample(assets, self._selection_config)
        return tuple(asset.to_source_asset() for asset in selection.selected)

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        content_format = _validate_source_asset(asset)
        if content_format != self._content_format:
            raise OpenAlexFormatError("OpenAlex source URI format differs from the configured format")
        response = self._request(asset.uri)
        try:
            _raise_for_status(response)
            _validate_payload_content_type(response, content_format)
            content_length = _header(response, "Content-Length")
            expected_bytes = _parse_content_length(content_length)
            limit = self._selection_config.max_file_size_bytes
            if expected_bytes is not None and expected_bytes > limit:
                raise OpenAlexSizeLimitError(
                    "OpenAlex payload Content-Length exceeds the configured byte limit"
                )
            return io.BufferedReader(
                _BoundedStream(response, limit, expected_bytes), buffer_size=_CHUNK_SIZE
            )
        except Exception:
            response.close()
            raise

    def check_public_connectivity(self) -> PublicConnectivityResult:
        """Read only the bounded public Works manifest; never fetch a data object."""
        _, actual_format = self._load_manifest()
        return PublicConnectivityResult(
            endpoint=self.public_manifest_endpoint,
            access_mode="anonymous public HTTPS",
            expected_format=self._content_format,
            actual_format=actual_format,
            manifest_byte_limit=_MANIFEST_LIMIT,
        )

    def _load_manifest(self) -> tuple[tuple[OpenAlexAssetMetadata, ...], str]:
        response = self._request(self.manifest_uri)
        try:
            _raise_for_status(response)
            content_type = _header(response, "Content-Type")
            normalized_type = (
                content_type.split(";", 1)[0].strip().lower() if content_type else ""
            )
            if normalized_type and not _is_json_or_binary_mime(normalized_type):
                raise OpenAlexFormatError("OpenAlex manifest response is not JSON")
            length = _parse_content_length(_header(response, "Content-Length"))
            if length is not None and length > _MANIFEST_LIMIT:
                raise OpenAlexSizeLimitError("OpenAlex Works manifest exceeds its byte limit")
            content = _read_bounded(response, _MANIFEST_LIMIT)
        finally:
            response.close()
        try:
            decoded = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            raise OpenAlexManifestError("OpenAlex Works manifest is malformed") from None
        if not isinstance(decoded, dict):
            raise OpenAlexManifestError("OpenAlex Works manifest is malformed") from None
        if "format" in decoded and (
            not isinstance(decoded["format"], str) or decoded["format"] not in _FORMATS
        ):
            raise OpenAlexFormatError("OpenAlex Works manifest declares an unsupported format")
        if "format" not in decoded:
            raise OpenAlexManifestError("OpenAlex Works manifest is malformed")
        declared_format = decoded["format"]
        if declared_format != self._content_format:
            raise OpenAlexFormatError("OpenAlex Works manifest format differs from the requested format")
        try:
            assets = parse_openalex_works_manifest(
                content, content_format=self._content_format, entity="works"
            )
        except (ValueError, TypeError, RecursionError):
            raise OpenAlexManifestError("OpenAlex Works manifest is malformed") from None
        return assets, declared_format

    def _request(self, uri: str) -> _Response:
        for attempt in range(self._max_attempts):
            try:
                response = self._client.request(
                    uri, timeout=self._timeout, headers={"Accept-Encoding": "identity"}
                )
            except (TimeoutError, socket.timeout):
                if attempt + 1 == self._max_attempts:
                    raise OpenAlexTimeoutError("OpenAlex request timed out") from None
                self._sleep(attempt)
                continue
            except (OSError, http.client.HTTPException):
                if attempt + 1 == self._max_attempts:
                    raise OpenAlexNetworkError("OpenAlex request failed") from None
                self._sleep(attempt)
                continue
            try:
                status = response.status
                if status in {401, 403}:
                    raise OpenAlexAccessDeniedError(
                        "OpenAlex denied anonymous public access"
                    )
                if status in _RETRYABLE_STATUS and attempt + 1 < self._max_attempts:
                    response.close()
                    self._sleep(attempt)
                    continue
                if status != 200:
                    raise OpenAlexEndpointUnavailableError(
                        f"OpenAlex endpoint returned HTTP {status}"
                    )
                return response
            except Exception:
                response.close()
                raise
        raise OpenAlexNetworkError("OpenAlex request failed")

    def _sleep(self, attempt: int) -> None:
        if self._backoff:
            time.sleep(self._backoff * (2**attempt))


@dataclass(frozen=True)
class PublicConnectivityResult:
    endpoint: str
    access_mode: str
    expected_format: str
    actual_format: str
    manifest_byte_limit: int

    def as_dict(self) -> dict[str, str | int]:
        return {
            "endpoint": self.endpoint,
            "access_mode": self.access_mode,
            "expected_format": self.expected_format,
            "actual_format": self.actual_format,
            "manifest_byte_limit": self.manifest_byte_limit,
        }


def _public_path(uri: str) -> str:
    if not isinstance(uri, str) or any(
        ord(character) < 32 or ord(character) == 127 or character.isspace()
        for character in uri
    ):
        raise OpenAlexEndpointUnavailableError("OpenAlex URI contains unsafe characters")
    if not uri.startswith("s3://"):
        raise OpenAlexEndpointUnavailableError("OpenAlex URI is not canonical")
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "s3"
        or parsed.netloc != _S3_BUCKET
        or "?" in uri
        or "#" in uri
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise OpenAlexEndpointUnavailableError("OpenAlex URI is outside the public bucket")
    path = parsed.path
    parts = path.split("/")
    if (
        len(parts) < 5
        or parts[0] != ""
        or parts[1] != "data"
        or parts[2] not in _FORMATS
        or parts[3] != "works"
        or any(part in {"", ".", ".."} for part in parts[1:])
        or "%" in path
        or "\\" in path
        or not path.isascii()
        or any(ord(character) < 32 or character.isspace() for character in path)
    ):
        raise OpenAlexEndpointUnavailableError("OpenAlex URI has an unsafe object path")
    format_name = parts[2]
    if path != _FORMATS[format_name][0] and not parts[-1].endswith(
        _FORMATS[format_name][1]
    ):
        raise OpenAlexFormatError("OpenAlex URI suffix does not match its format")
    return path


def _validate_source_asset(asset: SourceAsset) -> str:
    if asset.source != "openalex" or not re.fullmatch(r"oa-[0-9a-f]{64}", asset.identifier):
        raise OpenAlexEndpointUnavailableError("Source asset is not a validated OpenAlex asset")
    try:
        path = _public_path(asset.uri)
    except (TypeError, ValueError):
        raise OpenAlexEndpointUnavailableError("OpenAlex source URI is invalid") from None
    parts = path.split("/")
    if (
        len(parts) < 5
        or parts[0] != ""
        or parts[1] != "data"
        or parts[2] not in _FORMATS
        or parts[3] != "works"
        or not parts[-1].endswith(_FORMATS[parts[2]][1])
        or any(part in {"", ".", ".."} for part in parts[1:])
        or "%" in path
        or "\\" in path
        or not path.isascii()
        or any(ord(character) < 32 or character.isspace() for character in path)
    ):
        raise OpenAlexFormatError("OpenAlex source URI has an unsupported namespace or format")
    return parts[2]


def _header(response: _Response, name: str) -> str | None:
    headers = response.headers
    if hasattr(headers, "get"):
        value = headers.get(name)  # type: ignore[union-attr]
        return str(value) if value is not None else None
    return None


def _parse_content_length(value: str | None) -> int | None:
    if value is None or not re.fullmatch(r"\d+", value.strip()):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _raise_for_status(response: _Response) -> None:
    if response.status in {401, 403}:
        raise OpenAlexAccessDeniedError("OpenAlex denied anonymous public access")
    if response.status != 200:
        raise OpenAlexEndpointUnavailableError(
            f"OpenAlex endpoint returned HTTP {response.status}"
        )


def _validate_payload_content_type(response: _Response, content_format: str) -> None:
    content_type = _header(response, "Content-Type")
    if not content_type:
        return
    allowed = (
        {"application/gzip", "application/x-gzip", "application/octet-stream", "binary/octet-stream"}
        if content_format == "jsonl"
        else {"application/vnd.apache.parquet", "application/octet-stream", "binary/octet-stream"}
    )
    if content_type.split(";", 1)[0].strip().lower() not in allowed:
        raise OpenAlexFormatError("OpenAlex payload response has an unexpected content type")


def _is_json_or_binary_mime(content_type: str) -> bool:
    return content_type in {
        "application/json",
        "application/x-json",
        "text/json",
        "application/octet-stream",
        "binary/octet-stream",
    } or (
        content_type.startswith("application/")
        and content_type.endswith("+json")
    )


def _read_bounded(response: _Response, max_bytes: int) -> bytes:
    content = bytearray()
    try:
        while True:
            chunk = response.read(min(_CHUNK_SIZE, max_bytes + 1 - len(content)))
            if not chunk:
                expected_length = _parse_content_length(_header(response, "Content-Length"))
                if expected_length is not None and len(content) < expected_length:
                    raise OpenAlexTruncatedError(
                        "OpenAlex Works manifest ended before its declared length"
                    )
                return bytes(content)
            content.extend(chunk)
            if len(content) > max_bytes:
                raise OpenAlexSizeLimitError("OpenAlex Works manifest exceeds its byte limit")
    except (TimeoutError, socket.timeout):
        raise OpenAlexTimeoutError("OpenAlex manifest request timed out") from None
    except http.client.IncompleteRead:
        raise OpenAlexTruncatedError("OpenAlex Works manifest response ended unexpectedly") from None
    except (OSError, http.client.HTTPException):
        raise OpenAlexNetworkError("OpenAlex manifest request failed") from None
