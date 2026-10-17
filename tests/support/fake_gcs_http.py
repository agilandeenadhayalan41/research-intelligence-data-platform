"""In-memory fake of the GCS JSON API for a REAL ``google.cloud.storage.Client``.

Pass it as ``_http`` with ``AnonymousCredentials`` (as the BigQuery fake does in
``tests/unit/test_bigquery_sandbox_103_script.py``): nothing leaves the process.
It speaks the resumable-upload protocol that the pinned google-cloud-storage
3.16.0 uses for every ``upload_from_file`` without ``size``: a POST that starts a
session (recording ``ifGenerationMatch``), chunk PUTs with ``Content-Range``,
``bytes */N`` status queries, object metadata GETs, generation-pinned media GETs
and DELETE (always refused with 403, and logged).

Modelled GCS behaviour (not verified against real GCS here): the create-only
precondition holds until finalization, so a 412 can come at initiation or at the
final chunk (``precondition_at``); a finalized session answers any later PUT or
status query with 200 and the object resource.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urlsplit

import google_crc32c
import requests
from requests.structures import CaseInsensitiveDict

BUCKET = "synthetic-bucket"
CALLER = "synthetic-caller@example.invalid"
_UPLOAD = f"/upload/storage/v1/b/{BUCKET}/o"
_OBJECT = f"/storage/v1/b/{BUCKET}/o/"
_MEDIA = f"/download/storage/v1/b/{BUCKET}/o/"


class _Raw(io.BytesIO):
    def __init__(self, body: bytes, headers: CaseInsensitiveDict) -> None:
        super().__init__(body)
        self.headers = headers


def _response(
    status: int, body: bytes | dict[str, Any] = b"", headers: dict[str, str] | None = None
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body if isinstance(body, bytes) else json.dumps(body).encode()
    response.headers = CaseInsensitiveDict(headers or {"content-type": "application/json"})
    response.encoding = "utf-8"
    response._content_consumed = True
    response.raw = _Raw(response._content, response.headers)
    return response


def _error(status: int, message: str) -> requests.Response:
    return _response(status, {"error": {"code": status, "message": message}})


@dataclass
class FakeGcsHttp:
    """Fault modes; one-shot unless marked sticky.

    - ``drop_next_chunk``: ``requests.ConnectionError`` before the chunk is stored
      (the library retries and resends it).
    - ``lose_final_response_after_commit``: the final chunk commits, then
      ``requests.ConnectionError`` (the library's status query gets 200).
    - ``reject_next_chunk``: 400 (not retried; the session never finalizes).
    - ``always_503`` (sticky): every chunk PUT answers 503 until retries run out.
    - ``racer``: ``(bytes, metadata)`` another writer commits just before our
      final chunk; its generation is lower than any we assign afterwards.
    - ``forbid_create`` (sticky): 403 on the resumable POST, with the request URL
      (naming the bucket) and the caller in the text, like the real error.
    - ``corrupt_reported_digests`` (sticky): object resources report a wrong
      ``crc32c`` and ``md5Hash``.
    """

    precondition_at: Literal["initiation", "finalization", "both"] = "both"
    drop_next_chunk: bool = False
    lose_final_response_after_commit: bool = False
    reject_next_chunk: bool = False
    always_503: bool = False
    racer: tuple[bytes, dict[str, str]] | None = None
    forbid_create: bool = False
    corrupt_reported_digests: bool = False
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    log: list[tuple[str, str, dict[str, str]]] = field(default_factory=list)
    precondition_failed_at: list[str] = field(default_factory=list)
    is_mtls: bool = False
    _sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    _generation: int = 0

    def request(
        self,
        method: str,
        url: str,
        data: Any = None,
        headers: dict[str, str] | None = None,
        timeout: Any = None,
        **_: Any,
    ) -> requests.Response:
        response = self._handle(method, url, data, {k.lower(): v for k, v in (headers or {}).items()})
        response.request = requests.Request(method, url).prepare()
        return response

    def store(self, name: str, data: bytes, metadata: dict[str, str]) -> None:
        self._generation += 1
        self.objects[name] = {"data": data, "metadata": dict(metadata), "generation": self._generation}

    def resource(self, name: str) -> dict[str, Any]:
        stored = self.objects[name]
        data = stored["data"]
        crc = google_crc32c.value(data)
        md5 = hashlib.md5(data, usedforsecurity=False).digest()
        if self.corrupt_reported_digests:
            crc ^= 1
            md5 = bytes([md5[0] ^ 1]) + md5[1:]
        return {
            "kind": "storage#object", "name": name, "bucket": BUCKET,
            "generation": str(stored["generation"]), "metageneration": "1",
            "size": str(len(data)), "metadata": stored["metadata"],
            "md5Hash": base64.b64encode(md5).decode(),
            "crc32c": base64.b64encode(crc.to_bytes(4, "big")).decode(),
        }

    def _handle(self, method: str, url: str, data: Any, headers: dict[str, str]) -> requests.Response:
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        logged = {k: headers[k] for k in ("content-range", "x-goog-hash") if k in headers}
        self.log.append((method, parts.path, {**query, **logged}))
        if method == "POST" and parts.path == _UPLOAD and query.get("uploadType") == "resumable":
            return self._initiate(url, json.loads(data), query)
        if method == "PUT" and "upload_id" in query:
            return self._chunk(self._sessions[query["upload_id"]], data, headers)
        if method == "GET" and parts.path.startswith(_OBJECT):
            name = unquote(parts.path.removeprefix(_OBJECT))
            if not self._exists(name, query):
                return _error(404, "No such object")
            return _response(200, self.resource(name))
        if method == "GET" and parts.path.startswith(_MEDIA):
            return self._media(unquote(parts.path.removeprefix(_MEDIA)), query, headers)
        if method == "DELETE":
            return _error(403, f"{CALLER} does not have storage.objects.delete access")
        raise AssertionError(f"unexpected request {method} {parts.path}")

    def _exists(self, name: str, query: dict[str, str]) -> bool:
        stored = self.objects.get(name)
        if stored is None:
            return False
        generation = query.get("generation")
        return generation is None or generation == str(stored["generation"])

    def _initiate(self, url: str, body: dict[str, Any], query: dict[str, str]) -> requests.Response:
        if self.forbid_create:
            return _error(
                403,
                f"{CALLER} does not have storage.objects.create access to the Google Cloud "
                f"Storage object. Permission 'storage.objects.create' denied on resource "
                f"(or it may not exist). Request: {url}",
            )
        create_only = query.get("ifGenerationMatch") == "0"
        if create_only and self.precondition_at != "finalization" and body["name"] in self.objects:
            self.precondition_failed_at.append("initiation")
            return _error(412, "At least one of the pre-conditions you specified did not hold.")
        session_id = f"s{len(self._sessions)}"
        self._sessions[session_id] = {
            "name": body["name"], "metadata": body.get("metadata", {}), "buf": b"",
            "create_only": create_only, "finalized": False,
        }
        location = f"https://storage.googleapis.com{_UPLOAD}?uploadType=resumable&upload_id={session_id}"
        return _response(200, b"", {"location": location})

    def _chunk(self, session: dict[str, Any], data: Any, headers: dict[str, str]) -> requests.Response:
        if session["finalized"]:
            return _response(200, self.resource(session["name"]))
        content_range = headers["content-range"]
        if content_range.startswith("bytes */"):
            size = len(session["buf"])
            return _response(308, b"", {"range": f"bytes=0-{size - 1}"} if size else {})
        if self.always_503:
            return _error(503, "Backend Error")
        if self.drop_next_chunk:
            self.drop_next_chunk = False
            raise requests.exceptions.ConnectionError("connection reset mid-chunk")
        if self.reject_next_chunk:
            self.reject_next_chunk = False
            return _error(400, "Invalid request")
        match = re.match(r"bytes (\d+)-(\d+)/(\d+|\*)", content_range)
        assert match is not None and int(match[1]) == len(session["buf"])
        buf = session["buf"] + bytes(data)
        final = match[3] != "*" and len(buf) == int(match[3])
        if not final:
            session["buf"] = buf
            return _response(308, b"", {"range": f"bytes=0-{len(buf) - 1}"})
        if self.racer is not None:
            racer_data, racer_metadata = self.racer
            self.racer = None
            self.store(session["name"], racer_data, racer_metadata)
        if (
            session["create_only"]
            and self.precondition_at != "initiation"
            and session["name"] in self.objects
        ):
            self.precondition_failed_at.append("finalization")
            return _error(412, "At least one of the pre-conditions you specified did not hold.")
        session["buf"] = buf
        self.store(session["name"], buf, session["metadata"])
        session["finalized"] = True
        if self.lose_final_response_after_commit:
            self.lose_final_response_after_commit = False
            raise requests.exceptions.ConnectionError("connection reset after commit")
        return _response(200, self.resource(session["name"]))

    def _media(self, name: str, query: dict[str, str], headers: dict[str, str]) -> requests.Response:
        if not self._exists(name, query):
            return _error(404, "No such object")
        stored = self.objects[name]
        data = stored["data"]
        match = re.match(r"bytes=(\d+)-(\d*)", headers.get("range", ""))
        low = int(match[1]) if match else 0
        high = int(match[2]) if match and match[2] else len(data) - 1
        chunk = data[low:high + 1]
        return _response(206 if match else 200, chunk, {
            "content-range": f"bytes {low}-{low + len(chunk) - 1}/{len(data)}",
            "content-length": str(len(chunk)),
            "x-goog-generation": str(stored["generation"]),
        })
