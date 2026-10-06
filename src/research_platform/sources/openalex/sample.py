"""Deterministic, metadata-only bounded selection of OpenAlex Works assets."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal

from research_platform.config.models import SampleSelectionConfig
from research_platform.sources.openalex.metadata import (
    OpenAlexAssetMetadata,
    unique_assets,
)

SkipReason = Literal[
    "WRONG_ENTITY",
    "UNSUPPORTED_FORMAT",
    "SNAPSHOT_MISMATCH",
    "UPDATED_DATE_MISMATCH",
    "UNKNOWN_SIZE",
    "INVALID_SIZE",
    "OVERSIZED",
    "FILE_LIMIT_REACHED",
]


@dataclass(frozen=True)
class SkippedOpenAlexAsset:
    asset: OpenAlexAssetMetadata
    reason: SkipReason


@dataclass(frozen=True)
class OpenAlexSampleSelection:
    """Selection results contain metadata only; no object is accessed."""

    max_files: int
    max_file_size_bytes: int
    eligible_count: int
    selected: tuple[OpenAlexAssetMetadata, ...]
    skipped: tuple[SkippedOpenAlexAsset, ...]


def select_openalex_works_sample(
    assets: Iterable[OpenAlexAssetMetadata],
    config: SampleSelectionConfig = SampleSelectionConfig(),
    *,
    snapshot_date: date | None = None,
    updated_date: date | None = None,
) -> OpenAlexSampleSelection:
    """Select the smallest eligible assets without reading or writing objects.

    Candidates sort by byte size ascending, then updated date ascending (assets
    without that optional partition sort before dated assets), file URI ascending,
    and stable asset ID ascending. Exact repeated metadata collapses; conflicting
    descriptions of one identity, including values that compare equal but have
    different types, are rejected before the existing duplicate helper is called.
    """
    config = SampleSelectionConfig.model_validate(config.model_dump())
    candidates = tuple(assets)
    _reject_conflicting_descriptions(candidates)
    unique = unique_assets(candidates)
    eligible: list[OpenAlexAssetMetadata] = []
    skipped: list[SkippedOpenAlexAsset] = []

    for asset in unique:
        reason = _skip_reason(
            asset,
            config=config,
            snapshot_date=snapshot_date,
            updated_date=updated_date,
        )
        if reason is None:
            eligible.append(asset)
        else:
            skipped.append(SkippedOpenAlexAsset(asset=asset, reason=reason))

    eligible.sort(
        key=lambda asset: (
            asset.byte_size,
            asset.updated_date is not None,
            asset.updated_date or date.min,
            asset.file_uri,
            asset.asset_id,
        )
    )
    selected = tuple(eligible[: config.max_files])
    skipped.extend(
        SkippedOpenAlexAsset(asset=asset, reason="FILE_LIMIT_REACHED")
        for asset in eligible[config.max_files :]
    )
    skipped.sort(key=lambda item: item.asset.asset_id)

    return OpenAlexSampleSelection(
        max_files=config.max_files,
        max_file_size_bytes=config.max_file_size_bytes,
        eligible_count=len(eligible),
        selected=selected,
        skipped=tuple(skipped),
    )


def _reject_conflicting_descriptions(
    assets: tuple[OpenAlexAssetMetadata, ...],
) -> None:
    signatures_by_identity: dict[str, tuple[tuple[str, type[object], object], ...]] = {}
    for asset in assets:
        if not isinstance(asset, OpenAlexAssetMetadata):
            raise TypeError("sample selection requires OpenAlexAssetMetadata entries")
        signature = tuple(
            (name, type(getattr(asset, name)), getattr(asset, name))
            for name in OpenAlexAssetMetadata.model_fields
        )
        previous = signatures_by_identity.get(asset.asset_id)
        if previous is not None and previous != signature:
            raise ValueError("conflicting metadata for an OpenAlex asset identity")
        signatures_by_identity[asset.asset_id] = signature


def _skip_reason(
    asset: OpenAlexAssetMetadata,
    *,
    config: SampleSelectionConfig,
    snapshot_date: date | None,
    updated_date: date | None,
) -> SkipReason | None:
    if asset.entity != "works":
        return "WRONG_ENTITY"
    if asset.content_format not in {"jsonl", "parquet"}:
        return "UNSUPPORTED_FORMAT"
    if snapshot_date is not None and asset.snapshot_date != snapshot_date:
        return "SNAPSHOT_MISMATCH"
    if updated_date is not None and asset.updated_date != updated_date:
        return "UPDATED_DATE_MISMATCH"
    if asset.byte_size is None:
        return "UNKNOWN_SIZE"
    if type(asset.byte_size) is not int or asset.byte_size < 0:
        return "INVALID_SIZE"
    if asset.byte_size > config.max_file_size_bytes:
        return "OVERSIZED"
    return None
