"""DE-06 (#127): the streaming gzip completeness check behind ``land_raw_asset``.

Drives ``_GzipCompleteness`` directly with synthetic payloads (LOCAL evidence
only). The pipeline tests in ``test_gcs_bronze_landing.py`` use single-member
payloads that fit one read; these pin multi-member, padded and chunk-split
inputs, every cut inside a member, and the cases that stop without a verdict.
"""

from __future__ import annotations

import gzip
import random

import pytest

from research_platform.ingestion.raw_landing import _GzipCompleteness

LIMIT = 100_000_000


def _member(size: int, seed: int) -> bytes:
    # Incompressible bytes, so a member spans several 64 KiB reads.
    return gzip.compress(random.Random(seed).randbytes(size), mtime=0)


def _check(payload: bytes, *, chunk: int = 65_536, limit: int = LIMIT) -> _GzipCompleteness:
    check = _GzipCompleteness(limit)
    for start in range(0, len(payload), chunk):
        check.update(payload[start : start + chunk])
    return check


FIRST = _member(100_000, 1)
SECOND = _member(30_000, 2)
SMALL = gzip.compress(b'{"id": "https://openalex.org/W1"}\n', mtime=0)


@pytest.mark.parametrize("chunk", [1, 7, 65_535, 65_536])
def test_gzip_check_verifies_single_multi_member_and_zero_padded_payloads_at_any_chunking(chunk: int) -> None:
    for payload in (FIRST, FIRST + SECOND, FIRST + b"\0" * 9 + SECOND + b"\0" * 3):
        check = _check(payload, chunk=chunk)
        assert check.verified_complete and not check.ends_inside_a_member


def test_gzip_check_flags_every_cut_inside_a_member_including_later_members() -> None:
    payload = SMALL + SMALL
    # A lone first byte is not yet known to be gzip; every longer cut is.
    for cut in [*range(2, len(SMALL)), *range(len(SMALL) + 1, len(payload))]:
        check = _check(payload[:cut], chunk=7)
        assert check.ends_inside_a_member and not check.verified_complete, cut
    for cut in (len(FIRST) + 100, len(FIRST) + len(SECOND) - 1):
        assert _check((FIRST + SECOND)[:cut]).ends_inside_a_member, cut


def test_gzip_check_flags_a_cut_inside_a_member_after_zero_padding() -> None:
    payload = SMALL + b"\0" * 5 + SMALL
    for cut in range(len(SMALL) + 6, len(payload)):
        assert _check(payload[:cut], chunk=3).ends_inside_a_member, cut


def test_gzip_check_flags_a_split_magic_left_pending_at_eof() -> None:
    check = _check(SMALL + b"\x1f")
    assert check.ends_inside_a_member and not check.verified_complete
    split = _check(SMALL + SMALL, chunk=len(SMALL) + 1)  # the second magic spans two reads
    assert split.verified_complete


def test_gzip_check_stops_without_verdict_past_the_decompressed_limit() -> None:
    payload = gzip.compress(b"x" * 1_000, mtime=0)
    for data in (payload, payload[:-3]):
        check = _check(data, limit=100)
        assert not check.verified_complete and not check.ends_inside_a_member
    assert _check(payload, limit=1_000).verified_complete


def test_gzip_check_stops_without_verdict_on_corrupt_crc_trailing_garbage_or_plain_bytes() -> None:
    corrupt = bytearray(SMALL)
    corrupt[-5] ^= 0xFF  # the CRC32 in the trailer
    for data in (bytes(corrupt), SMALL + b"garbage", b"plain text, not gzip", b""):
        check = _check(data)
        assert not check.verified_complete and not check.ends_inside_a_member
