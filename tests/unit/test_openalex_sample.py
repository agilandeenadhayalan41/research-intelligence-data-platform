import builtins
import copy
import socket
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from research_platform.config import SampleSelectionConfig, load_config
from research_platform.sources.openalex import (
    OpenAlexAssetMetadata,
    parse_openalex_works_manifest,
    select_openalex_works_sample,
)


def asset(
    name: str,
    *,
    byte_size: int | None = 100,
    snapshot_date: date = date(2025, 1, 15),
    updated_date: date | None = None,
    entity: str = "works",
    content_format: str = "jsonl",
) -> OpenAlexAssetMetadata:
    partition = (
        f"updated_date={updated_date.isoformat()}/" if updated_date is not None else ""
    )
    extension = ".gz" if content_format == "jsonl" else ".parquet"
    uri = (
        f"s3://openalex/data/{content_format}/{entity}/{partition}{name}{extension}"
    )
    return OpenAlexAssetMetadata(
        source="openalex",
        snapshot_date=snapshot_date,
        entity=entity,
        content_format=content_format,  # type: ignore[arg-type]
        file_uri=uri,
        updated_date=updated_date,
        byte_size=byte_size,
    )


def reason_map(report: Any) -> dict[str, str]:
    return {item.asset.file_uri: item.reason for item in report.skipped}


def test_defaults_are_exposed_in_local_settings_and_selector(repo_root: Path) -> None:
    settings = load_config(repo_root / "config/local.yaml", environ={})
    report = select_openalex_works_sample([asset("small")])

    assert (settings.sample_selection.max_files, settings.sample_selection.max_file_size_bytes) == (
        1,
        25_000_000,
    )
    assert (report.max_files, report.max_file_size_bytes) == (1, 25_000_000)
    assert len(report.selected) == 1


@pytest.mark.parametrize(
    ("byte_size", "selected", "reason"),
    [(25_000_000, True, None), (25_000_001, False, "OVERSIZED")],
)
def test_default_size_boundary(
    byte_size: int, selected: bool, reason: str | None
) -> None:
    report = select_openalex_works_sample([asset("boundary", byte_size=byte_size)])

    assert bool(report.selected) is selected
    if reason:
        assert list(reason_map(report).values()) == [reason]
    else:
        assert report.skipped == ()


def test_default_file_limit_and_bounded_override() -> None:
    assets = [asset("a", byte_size=1), asset("b", byte_size=2), asset("c", byte_size=3)]

    default = select_openalex_works_sample(assets)
    override = select_openalex_works_sample(
        assets, SampleSelectionConfig(max_files=2, max_file_size_bytes=3)
    )

    assert [item.file_uri.rsplit("/", 1)[-1] for item in default.selected] == ["a.gz"]
    assert default.eligible_count == 3
    assert set(reason_map(default).values()) == {"FILE_LIMIT_REACHED"}
    assert len(override.selected) == 2
    assert override.eligible_count == 3
    assert list(reason_map(override).values()) == ["FILE_LIMIT_REACHED"]


def test_unknown_and_invalid_sizes_are_excluded() -> None:
    unknown = asset("unknown", byte_size=None)
    invalid = asset("invalid").model_copy(update={"byte_size": -1})

    report = select_openalex_works_sample([unknown, invalid])

    assert report.eligible_count == 0
    assert set(reason_map(report).values()) == {"UNKNOWN_SIZE", "INVALID_SIZE"}


def test_empty_input_and_no_eligible_assets_are_reported() -> None:
    empty = select_openalex_works_sample([])
    ineligible = select_openalex_works_sample([asset("unknown", byte_size=None)])

    assert empty.eligible_count == 0
    assert empty.selected == ()
    assert empty.skipped == ()
    assert ineligible.eligible_count == 0
    assert len(ineligible.skipped) == 1
    assert ineligible.skipped[0].reason == "UNKNOWN_SIZE"


def test_smallest_first_with_deterministic_date_and_uri_tiebreakers() -> None:
    inputs = [
        asset("z", byte_size=5, updated_date=date(2025, 1, 3)),
        asset("b", byte_size=5, updated_date=date(2025, 1, 2)),
        asset("a", byte_size=5, updated_date=date(2025, 1, 2)),
        asset("larger", byte_size=6),
        asset("no-date", byte_size=5),
    ]

    forward = select_openalex_works_sample(
        inputs, SampleSelectionConfig(max_files=5, max_file_size_bytes=10)
    )
    reverse = select_openalex_works_sample(
        list(reversed(inputs)),
        SampleSelectionConfig(max_files=5, max_file_size_bytes=10),
    )

    expected = ["no-date.gz", "a.gz", "b.gz", "z.gz", "larger.gz"]
    assert [item.file_uri.rsplit("/", 1)[-1] for item in forward.selected] == expected
    assert reverse == forward
    limited_forward = select_openalex_works_sample(
        inputs, SampleSelectionConfig(max_files=2, max_file_size_bytes=10)
    )
    limited_reverse = select_openalex_works_sample(
        list(reversed(inputs)),
        SampleSelectionConfig(max_files=2, max_file_size_bytes=10),
    )
    assert limited_reverse == limited_forward
    assert [item.asset.asset_id for item in limited_forward.skipped] == sorted(
        item.asset.asset_id for item in limited_forward.skipped
    )


def test_exact_duplicates_collapse_and_conflicting_duplicates_fail() -> None:
    duplicate = asset("same", byte_size=4)
    report = select_openalex_works_sample([duplicate, duplicate])
    conflicting = duplicate.model_copy(update={"byte_size": 5})

    assert report.eligible_count == 1
    assert report.selected == (duplicate,)
    with pytest.raises(ValueError, match="conflicting metadata"):
        select_openalex_works_sample([duplicate, conflicting])


@pytest.mark.parametrize("invalid_size", [True, 1.0])
@pytest.mark.parametrize("reverse", [False, True])
def test_type_distinct_duplicate_sizes_are_conflicts_in_both_orders(
    invalid_size: bool | float, reverse: bool
) -> None:
    valid = asset("typed-duplicate", byte_size=1)
    invalid = valid.model_copy(update={"byte_size": invalid_size})
    inputs = [invalid, valid] if reverse else [valid, invalid]

    with pytest.raises(ValueError, match="conflicting metadata"):
        select_openalex_works_sample(inputs)


def test_equal_size_date_and_uri_across_snapshots_use_asset_id_tiebreaker() -> None:
    first_snapshot = asset(
        "same-uri", byte_size=1, snapshot_date=date(2025, 1, 15), updated_date=date(2025, 1, 14)
    )
    second_snapshot = asset(
        "same-uri", byte_size=1, snapshot_date=date(2025, 1, 16), updated_date=date(2025, 1, 14)
    )
    expected = min((first_snapshot, second_snapshot), key=lambda item: item.asset_id)

    forward = select_openalex_works_sample(
        [first_snapshot, second_snapshot], SampleSelectionConfig(max_files=1)
    )
    reverse = select_openalex_works_sample(
        [second_snapshot, first_snapshot], SampleSelectionConfig(max_files=1)
    )

    assert forward.selected == reverse.selected == (expected,)


def test_yaml_sample_selection_overrides_are_enforced(tmp_path: Path) -> None:
    path = tmp_path / "local.yaml"
    path.write_text(
        "sample_selection:\n  max_files: 2\n  max_file_size_bytes: 3\n",
        encoding="utf-8",
    )
    config = load_config(path, environ={}).sample_selection
    inputs = [asset("one", byte_size=1), asset("two", byte_size=2),
              asset("three", byte_size=3), asset("four", byte_size=4)]

    report = select_openalex_works_sample(inputs, config)

    assert report.max_files == 2
    assert report.max_file_size_bytes == 3
    assert [item.byte_size for item in report.selected] == [1, 2]
    assert report.eligible_count == 3
    assert set(reason_map(report).values()) == {"FILE_LIMIT_REACHED", "OVERSIZED"}


@pytest.mark.parametrize(
    "field_value",
    ["0", "-1", "true", "1.5", '"2"'],
)
@pytest.mark.parametrize("field", ["max_files", "max_file_size_bytes"])
def test_yaml_rejects_invalid_sample_selection_limits(
    tmp_path: Path, field: str, field_value: str
) -> None:
    path = tmp_path / "invalid.yaml"
    path.write_text(f"sample_selection:\n  {field}: {field_value}\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_config(path, environ={})


@pytest.mark.parametrize("size", [-1, True, 1.0, "1"])
def test_invalid_byte_sizes_are_never_selected(size: object) -> None:
    invalid = asset("invalid-size").model_copy(update={"byte_size": size})

    report = select_openalex_works_sample([invalid])

    assert report.eligible_count == 0
    assert report.selected == ()
    assert report.skipped[0].reason == "INVALID_SIZE"


def test_selector_has_no_io_or_service_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = [asset("one", byte_size=1), asset("two", byte_size=2)]
    original = copy.deepcopy(inputs)

    def reject_side_effect(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("sample selection must not access services or files")

    monkeypatch.setattr(socket, "socket", reject_side_effect)
    monkeypatch.setattr(builtins, "open", reject_side_effect)
    monkeypatch.setattr(Path, "open", reject_side_effect)
    monkeypatch.setattr(Path, "write_bytes", reject_side_effect)
    monkeypatch.setattr(Path, "write_text", reject_side_effect)

    report = select_openalex_works_sample(inputs)

    assert report.selected == (inputs[0],)
    assert inputs == original
    assert list(tmp_path.iterdir()) == []


def test_wrong_entity_and_unsupported_format_are_excluded() -> None:
    wrong_entity = asset("author", entity="authors")
    valid = asset("format")
    unsupported_data = valid.model_dump()
    unsupported_data["content_format"] = "csv"
    unsupported_format = OpenAlexAssetMetadata.model_construct(**unsupported_data)

    report = select_openalex_works_sample([wrong_entity, unsupported_format])

    assert report.eligible_count == 0
    assert set(reason_map(report).values()) == {"WRONG_ENTITY", "UNSUPPORTED_FORMAT"}


def test_snapshot_and_updated_date_partitions_are_applied() -> None:
    assets = [
        asset("match", snapshot_date=date(2025, 1, 15), updated_date=date(2025, 1, 14)),
        asset("snapshot", snapshot_date=date(2025, 1, 16), updated_date=date(2025, 1, 14)),
        asset("updated", snapshot_date=date(2025, 1, 15), updated_date=date(2025, 1, 13)),
        asset("missing-update", snapshot_date=date(2025, 1, 15)),
    ]

    report = select_openalex_works_sample(
        assets,
        SampleSelectionConfig(max_files=4, max_file_size_bytes=100),
        snapshot_date=date(2025, 1, 15),
        updated_date=date(2025, 1, 14),
    )

    assert [item.file_uri.rsplit("/", 1)[-1] for item in report.selected] == ["match.gz"]
    assert set(reason_map(report).values()) == {
        "SNAPSHOT_MISMATCH",
        "UPDATED_DATE_MISMATCH",
    }


def test_skip_reason_precedence_is_stable_for_multiple_failures() -> None:
    wrong_entity_and_oversized = asset(
        "author", entity="authors", byte_size=25_000_001
    )
    snapshot_mismatch_and_oversized = asset(
        "snapshot", snapshot_date=date(2025, 1, 16), byte_size=25_000_001
    )

    report = select_openalex_works_sample(
        [wrong_entity_and_oversized, snapshot_mismatch_and_oversized],
        snapshot_date=date(2025, 1, 15),
    )

    assert reason_map(report) == {
        wrong_entity_and_oversized.file_uri: "WRONG_ENTITY",
        snapshot_mismatch_and_oversized.file_uri: "SNAPSHOT_MISMATCH",
    }


@pytest.mark.parametrize(
    "values",
    [
        {"max_files": 0},
        {"max_files": -1},
        {"max_files": True},
        {"max_files": 1.5},
        {"max_files": "2"},
        {"max_file_size_bytes": 0},
        {"max_file_size_bytes": -1},
        {"max_file_size_bytes": False},
        {"max_file_size_bytes": 25.0},
        {"max_file_size_bytes": "25000000"},
    ],
)
def test_invalid_sample_selection_limits_are_rejected(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        SampleSelectionConfig(**values)


def test_selector_does_not_accept_unvalidated_bounds() -> None:
    invalid_config = SampleSelectionConfig.model_construct(
        max_files=0, max_file_size_bytes=25_000_000
    )

    with pytest.raises(ValidationError):
        select_openalex_works_sample([asset("small")], invalid_config)


def test_manifest_parser_output_is_selector_input() -> None:
    manifest = {
        "date": "2025-01-15",
        "format": "parquet",
        "entity": "works",
        "files": [
            {
                "url": "s3://openalex/data/parquet/works/part_0000.parquet",
                "meta": {"content_length": 7},
            }
        ],
    }

    report = select_openalex_works_sample(parse_openalex_works_manifest(manifest))

    assert report.eligible_count == 1
    assert report.selected[0].byte_size == 7
