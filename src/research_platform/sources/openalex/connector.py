"""Anonymous, bounded access to public OpenAlex Works snapshot metadata and files."""

from __future__ import annotations

import http.client
import io
import json
import re
import socket
import time
from collections.abc import Iterable
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
        connection = http.client.HTTPSConnection(_PUBLIC_HOST, timeout=timeout)
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
        except Exception:
            connection.close()
            raise
        return _HTTPResponse(response, connection)


class _HTTPResponse:
    def __init__(
        self, response: http.client.HTTPResponse, connection: http.client.HTTPSConnection
    ) -> None:
        self._response = response
        self._connection = connection
        self.status = response.status
        self.headers = response.headers

    def read(self, size: int = -1) -> bytes:
        return self._response.read(size)

    def close(self) -> None:
        self._response.close()
        self._connection.close()


class _BoundedStream(io.RawIOBase):
    def __init__(self, response: _Response, max_bytes: int, expected_bytes: int | None):
        super().__init__()
        self._response = response
        self._max_bytes = max_bytes
        self._expected_bytes = expected_bytes
        self._actual_bytes = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: bytearray | memoryview) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed stream")
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
        except OSError:
            self.close()
            raise OpenAlexNetworkError("OpenAlex payload stream failed") from None
        if not data:
            self._check_declared_length()
            self.close()
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
        except OSError:
            self.close()
            raise OpenAlexNetworkError("OpenAlex payload stream failed") from None
        if extra:
            self.close()
            raise OpenAlexSizeLimitError("OpenAlex payload exceeds the configured byte limit")
        self._check_declared_length()
        self.close()

    def _check_declared_length(self) -> None:
        if self._expected_bytes is not None and self._actual_bytes < self._expected_bytes:
            self.close()
            raise OpenAlexTruncatedError("OpenAlex payload ended before its declared length")

    def close(self) -> None:
        if not self.closed:
            try:
                self._response.close()
            finally:
                super().close()


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
        if isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        if isinstance(retry_backoff_seconds, bool) or retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be non-negative")
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

    def discover(self) -> Iterable[SourceAsset]:
        assets, _ = self._load_manifest()
        selection = select_openalex_works_sample(assets, self._selection_config)
        return tuple(asset.to_source_asset() for asset in selection.selected)

    def fetch(self, asset: SourceAsset) -> BinaryIO:
        content_format = _validate_source_asset(asset)
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
            endpoint=f"https://{_PUBLIC_HOST}{_FORMATS[self._content_format][0]}",
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
            if content_type and "json" not in content_type.lower():
                raise OpenAlexFormatError("OpenAlex manifest response is not JSON")
            length = _parse_content_length(_header(response, "Content-Length"))
            if length is not None and length > _MANIFEST_LIMIT:
                raise OpenAlexSizeLimitError("OpenAlex Works manifest exceeds its byte limit")
            content = _read_bounded(response, _MANIFEST_LIMIT)
        finally:
            response.close()
        try:
            decoded = json.loads(content)
            declared_format = decoded.get("format") if isinstance(decoded, dict) else None
            if declared_format not in _FORMATS:
                raise OpenAlexFormatError("OpenAlex Works manifest declares an unsupported format")
            assets = parse_openalex_works_manifest(
                content, content_format=self._content_format, entity="works"
            )
        except OpenAlexFormatError:
            raise
        except ValueError as error:
            if "format" in str(error).lower() or "unsupported" in str(error).lower():
                raise OpenAlexFormatError("OpenAlex Works manifest format is unexpected") from None
            raise OpenAlexManifestError("OpenAlex Works manifest is malformed") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OpenAlexManifestError("OpenAlex Works manifest is malformed") from None
        if declared_format != self._content_format:
            raise OpenAlexFormatError("OpenAlex Works manifest format differs from the requested format")
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
            except OSError:
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
                if status == 404 or status >= 400:
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


class PublicConnectivityResult:
    def __init__(
        self,
        *,
        endpoint: str,
        access_mode: str,
        expected_format: str,
        actual_format: str,
        manifest_byte_limit: int,
    ) -> None:
        self.endpoint = endpoint
        self.access_mode = access_mode
        self.expected_format = expected_format
        self.actual_format = actual_format
        self.manifest_byte_limit = manifest_byte_limit

    def as_dict(self) -> dict[str, str | int]:
        return {
            "endpoint": self.endpoint,
            "access_mode": self.access_mode,
            "expected_format": self.expected_format,
            "actual_format": self.actual_format,
            "manifest_byte_limit": self.manifest_byte_limit,
        }


def _public_path(uri: str) -> str:
    parsed = urlsplit(uri)
    if (
        parsed.scheme != "s3"
        or parsed.netloc != _S3_BUCKET
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise OpenAlexEndpointUnavailableError("OpenAlex URI is outside the public bucket")
    return parsed.path


def _validate_source_asset(asset: SourceAsset) -> str:
    if asset.source != "openalex" or not re.fullmatch(r"oa-[0-9a-f]{64}", asset.identifier):
        raise OpenAlexEndpointUnavailableError("Source asset is not a validated OpenAlex asset")
    try:
        path = _public_path(asset.uri)
    except (TypeError, ValueError):
        raise OpenAlexEndpointUnavailableError("OpenAlex source URI is invalid") from None
    parts = path.split("/")
    if (
        len(parts) < 6
        or parts[0] != ""
        or parts[1] != "data"
        or parts[2] not in _FORMATS
        or parts[3] != "works"
        or parts[-1].endswith(_FORMATS[parts[2]][1]) is False
        or any(part in {"", ".", ".."} for part in parts[1:])
        or "%" in path
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
    return int(value)


def _raise_for_status(response: _Response) -> None:
    if response.status in {401, 403}:
        raise OpenAlexAccessDeniedError("OpenAlex denied anonymous public access")
    if response.status == 404 or response.status >= 400:
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


def _read_bounded(response: _Response, max_bytes: int) -> bytes:
    content = bytearray()
    try:
        while True:
            chunk = response.read(min(_CHUNK_SIZE, max_bytes + 1 - len(content)))
            if not chunk:
                return bytes(content)
            content.extend(chunk)
            if len(content) > max_bytes:
                raise OpenAlexSizeLimitError("OpenAlex Works manifest exceeds its byte limit")
    except (TimeoutError, socket.timeout):
        raise OpenAlexTimeoutError("OpenAlex manifest request timed out") from None
    except OSError:
        raise OpenAlexNetworkError("OpenAlex manifest request failed") from None
