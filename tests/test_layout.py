"""Tests for ``segment_compare.layout`` (ADR-033, Stage 2 loader)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from segment_compare.layout import (
    FieldLayout,
    FileFormatConfig,
    FileLayout,
    LayoutError,
    SegmentAlias,
    SegmentLayout,
    SortConfig,
    StripConfig,
    load_file_layout,
)
from segment_compare.parser import RdwConfig

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _minimal_layout() -> dict:
    """Smallest valid layout: TU4R (key + key field) + ENDS (end)."""
    return {
        "file_format": {
            "segment_name_bytes": 4,
            "size_field_bytes": 3,
            "size_encoding": "ascii_int",
            "size_includes_header": True,
            "data_encoding": "ascii",
            "record_delimiter": "\n",
        },
        "strip_leading_bytes": None,
        "rdw": None,
        "sort": {
            "input_sorted": True,
            "order": "ascending",
            "key_type": "alphanumeric",
        },
        "segments": [
            {
                "name": "TU4R",
                "role": "key",
                "size": 19,
                "fields": [
                    {"name": "key_data", "length": 12, "key": True},
                ],
            },
            {
                "name": "ENDS",
                "role": "end",
                "size": 10,
                "fields": [
                    {"name": "segment_count", "length": 3, "exclude": True},
                ],
            },
        ],
    }


def _write(tmp_path: Path, payload: dict, name: str = "layout.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload))
    return p


def _valid_minimal(tmp_path: Path) -> Path:
    return _write(tmp_path, _minimal_layout())


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_load_committed_layout_file_a() -> None:
    """The committed sample layout must load cleanly."""
    layout = load_file_layout(CONFIG_DIR / "layout_file_A.json")
    assert isinstance(layout, FileLayout)
    assert isinstance(layout.file_format, FileFormatConfig)
    assert layout.file_format.record_delimiter == b"\n"
    assert layout.file_format.header_bytes == 7
    assert layout.strip_leading_bytes is None
    assert layout.rdw is None
    assert isinstance(layout.sort, SortConfig)
    assert layout.sort.input_sorted is True
    assert {s.name for s in layout.segments} == {
        "TU4R",
        "SH01",
        "NM01",
        "TR01",
        "SC01",
        "CL01",
        "ENDS",
    }
    assert layout.key_segment.name == "TU4R"
    assert layout.end_segment.name == "ENDS"
    assert layout.key_field.name == "account_nbr"
    # key sits after the 4-byte "DATA" prefix, runs 12 bytes
    assert layout.key_range == (4, 16)


def test_load_committed_layout_file_b() -> None:
    layout = load_file_layout(CONFIG_DIR / "layout_file_B.json")
    assert layout.key_segment.name == "TU4R"
    assert layout.key_range == (4, 16)


def test_minimal_layout_loads(tmp_path: Path) -> None:
    p = _valid_minimal(tmp_path)
    layout = load_file_layout(p)
    assert len(layout.segments) == 2
    assert layout.key_range == (0, 12)


def test_field_defaults_apply_when_omitted(tmp_path: Path) -> None:
    """exclude and key default to False when absent."""
    data = _minimal_layout()
    data["segments"][1] = {
        "name": "ENDS",
        "role": "end",
        "size": 10,
        "fields": [{"name": "padding", "length": 3}],  # no exclude, no key
    }
    p = _write(tmp_path, data)
    layout = load_file_layout(p)
    end_field = layout.segments[1].fields[0]
    assert end_field.exclude is False
    assert end_field.key is False


# ---------------------------------------------------------------------------
# Per-segment size validation (the headline Stage 1 invariant)
# ---------------------------------------------------------------------------


def test_segment_size_mismatch_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"][0]["size"] = 99  # declared 99, fields sum to 12+7=19
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "segments[0].size" in excinfo.value.field
    assert "99" in excinfo.value.message
    assert "19" in excinfo.value.message


def test_segment_zero_size_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"][0]["size"] = 0
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "must be > 0" in excinfo.value.message


# ---------------------------------------------------------------------------
# Role / key invariants
# ---------------------------------------------------------------------------


def test_two_key_segments_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"][1] = {
        "name": "ENDS",
        "role": "key",  # both key, no end
        "size": 10,
        "fields": [{"name": "segment_count", "length": 3, "exclude": True}],
    }
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "role=key" in excinfo.value.message


def test_no_end_segment_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"][1]["role"] = None
    data["segments"][1]["fields"] = [{"name": "x", "length": 3, "exclude": True}]
    data["segments"][1]["size"] = 10
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "role=end" in excinfo.value.message


def test_invalid_role_value_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"][0]["role"] = "primary"
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "segments[0].role" in excinfo.value.field


def test_two_key_fields_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"][1] = {
        "name": "ENDS",
        "role": "end",
        "size": 10,
        "fields": [{"name": "second_key", "length": 3, "key": True}],
    }
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "exactly one" in excinfo.value.message


def test_no_key_field_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"][0]["fields"] = [{"name": "k", "length": 12}]  # no key flag
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "key=true" in excinfo.value.message


def test_key_field_outside_key_segment_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"][0]["fields"] = [{"name": "k", "length": 12}]  # key flag dropped here
    data["segments"][1] = {
        "name": "ENDS",
        "role": "end",
        "size": 10,
        "fields": [{"name": "k_in_end", "length": 3, "key": True}],
    }
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "key segment" in excinfo.value.message


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------


def test_duplicate_field_names_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"][0]["size"] = 22
    data["segments"][0]["fields"] = [
        {"name": "dup", "length": 12, "key": True},
        {"name": "dup", "length": 3},
    ]
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "duplicate field name" in excinfo.value.message


def test_zero_field_length_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"][0]["fields"] = [{"name": "k", "length": 0, "key": True}]
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError):
        load_file_layout(p)


def test_field_non_bool_exclude_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"][1] = {
        "name": "ENDS",
        "role": "end",
        "size": 10,
        "fields": [{"name": "p", "length": 3, "exclude": "yes"}],
    }
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "exclude" in excinfo.value.field


# ---------------------------------------------------------------------------
# strip_leading_bytes + rdw + sort blocks
# ---------------------------------------------------------------------------


def test_strip_leading_bytes_present(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["strip_leading_bytes"] = {"size": 5, "encoding": "binary"}
    data["segments"][1] = {
        "name": "ENDS",
        "role": "end",
        "size": 10,
        "fields": [{"name": "x", "length": 3, "exclude": True}],
    }
    p = _write(tmp_path, data)
    layout = load_file_layout(p)
    assert layout.strip_leading_bytes is not None
    assert layout.strip_leading_bytes.size == 5
    assert layout.strip_leading_bytes.encoding == "binary"


def test_strip_leading_bytes_bad_encoding_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["strip_leading_bytes"] = {"size": 5, "encoding": "ebcdic"}
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "strip_leading_bytes.encoding" in excinfo.value.field


def test_strip_leading_bytes_zero_size_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["strip_leading_bytes"] = {"size": 0, "encoding": "binary"}
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "strip_leading_bytes.size" in excinfo.value.field


def test_rdw_present(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["rdw"] = {"rdw1_bytes": 2, "rdw2_bytes": 2, "encoding": "binary_le_uint"}
    data["segments"][1] = {
        "name": "ENDS",
        "role": "end",
        "size": 10,
        "fields": [{"name": "x", "length": 3, "exclude": True}],
    }
    p = _write(tmp_path, data)
    layout = load_file_layout(p)
    assert isinstance(layout.rdw, RdwConfig)
    assert layout.rdw.total_bytes == 4


def test_rdw_bad_encoding_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["rdw"] = {"rdw1_bytes": 2, "rdw2_bytes": 2, "encoding": "ebcdic"}
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError):
        load_file_layout(p)


def test_sort_bad_order_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["sort"]["order"] = "sideways"
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "sort.order" in excinfo.value.field


def test_sort_bad_key_type_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["sort"]["key_type"] = "binary"
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError):
        load_file_layout(p)


# ---------------------------------------------------------------------------
# segment_aliases (ADR-034)
# ---------------------------------------------------------------------------


def _layout_with_three_segments() -> dict:
    """Layout with TU4R (key), MID (ordinary), ENDS (end) — fertile ground for aliases."""
    data = _minimal_layout()
    # Insert a MID segment between TU4R and ENDS.
    data["segments"].insert(
        1,
        {
            "name": "MID",
            "size": 10,
            "fields": [{"name": "payload", "length": 3}],
        },
    )
    # Insert a second TU4R-shaped segment to alias to.
    data["segments"].append(
        {
            "name": "TU4R_AFTER",
            "size": 19,
            "fields": [{"name": "renamed_data", "length": 12}],
        }
    )
    return data


def test_segment_aliases_default_to_empty_tuple_when_absent(tmp_path: Path) -> None:
    """Layouts that don't declare aliases get an empty tuple, not None."""
    p = _valid_minimal(tmp_path)
    layout = load_file_layout(p)
    assert layout.segment_aliases == ()


def test_segment_aliases_round_trip(tmp_path: Path) -> None:
    data = _layout_with_three_segments()
    data["segment_aliases"] = [
        {"wire_name": "TU4R", "logical_name": "TU4R_AFTER", "after_segment": "MID"}
    ]
    p = _write(tmp_path, data)
    layout = load_file_layout(p)
    assert layout.segment_aliases == (
        SegmentAlias(wire_name="TU4R", logical_name="TU4R_AFTER", after_segment="MID"),
    )


def test_segment_aliases_wire_must_be_declared(tmp_path: Path) -> None:
    data = _layout_with_three_segments()
    data["segment_aliases"] = [
        {"wire_name": "MISSING", "logical_name": "TU4R_AFTER", "after_segment": "MID"}
    ]
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "wire_name" in excinfo.value.field
    assert "MISSING" in excinfo.value.message


def test_segment_aliases_logical_must_be_declared(tmp_path: Path) -> None:
    data = _layout_with_three_segments()
    data["segment_aliases"] = [
        {"wire_name": "TU4R", "logical_name": "MISSING", "after_segment": "MID"}
    ]
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "logical_name" in excinfo.value.field


def test_segment_aliases_after_segment_must_be_declared(tmp_path: Path) -> None:
    data = _layout_with_three_segments()
    data["segment_aliases"] = [
        {"wire_name": "TU4R", "logical_name": "TU4R_AFTER", "after_segment": "NOSUCH"}
    ]
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "after_segment" in excinfo.value.field


def test_segment_aliases_wire_and_logical_must_have_same_size(tmp_path: Path) -> None:
    data = _layout_with_three_segments()
    # Mismatch: TU4R is 19, but make TU4R_AFTER 22.
    data["segments"][-1]["size"] = 22
    data["segments"][-1]["fields"] = [{"name": "renamed_data", "length": 15}]
    data["segment_aliases"] = [
        {"wire_name": "TU4R", "logical_name": "TU4R_AFTER", "after_segment": "MID"}
    ]
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "size mismatch" in excinfo.value.message


def test_segment_aliases_wire_and_logical_must_differ(tmp_path: Path) -> None:
    data = _layout_with_three_segments()
    data["segment_aliases"] = [
        {"wire_name": "TU4R", "logical_name": "TU4R", "after_segment": "MID"}
    ]
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "must differ" in excinfo.value.message


def test_segment_aliases_one_per_wire_name(tmp_path: Path) -> None:
    """Two aliases sharing the same wire_name is a config error."""
    data = _layout_with_three_segments()
    # Add a third segment to alias to.
    data["segments"].append(
        {"name": "TU4R_OTHER", "size": 19, "fields": [{"name": "x", "length": 12}]}
    )
    data["segment_aliases"] = [
        {"wire_name": "TU4R", "logical_name": "TU4R_AFTER", "after_segment": "MID"},
        {"wire_name": "TU4R", "logical_name": "TU4R_OTHER", "after_segment": "ENDS"},
    ]
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "duplicate alias" in excinfo.value.message


def test_segment_aliases_must_be_list(tmp_path: Path) -> None:
    data = _layout_with_three_segments()
    data["segment_aliases"] = "not a list"
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "segment_aliases" in excinfo.value.field


# ---------------------------------------------------------------------------
# File-format validation
# ---------------------------------------------------------------------------


def test_unsupported_size_encoding_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["file_format"]["size_encoding"] = "binary_be_uint"
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "size_encoding" in excinfo.value.field


def test_missing_record_delimiter_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    del data["file_format"]["record_delimiter"]
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "record_delimiter" in excinfo.value.field


# ---------------------------------------------------------------------------
# Top-level / I/O errors
# ---------------------------------------------------------------------------


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(tmp_path / "nope.json")
    assert "does not exist" in excinfo.value.message


def test_invalid_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not valid json")
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "invalid JSON" in excinfo.value.message


def test_top_level_must_be_object(tmp_path: Path) -> None:
    p = tmp_path / "list.json"
    p.write_text("[]")
    with pytest.raises(LayoutError):
        load_file_layout(p)


def test_segments_must_be_non_empty(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"] = []
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError):
        load_file_layout(p)


def test_duplicate_segment_names_raises(tmp_path: Path) -> None:
    data = _minimal_layout()
    data["segments"][1]["name"] = "TU4R"  # duplicate
    p = _write(tmp_path, data)
    with pytest.raises(LayoutError) as excinfo:
        load_file_layout(p)
    assert "duplicate" in excinfo.value.message


# ---------------------------------------------------------------------------
# Frozen-ness
# ---------------------------------------------------------------------------


def test_dataclasses_are_frozen() -> None:
    sc = StripConfig(size=5, encoding="binary")
    with pytest.raises(AttributeError):
        sc.size = 10  # type: ignore[misc]

    fl = FieldLayout(name="k", length=12, exclude=False, key=True)
    with pytest.raises(AttributeError):
        fl.name = "x"  # type: ignore[misc]

    sl = SegmentLayout(name="TU4R", role="key", size=19, fields=(fl,))
    with pytest.raises(AttributeError):
        sl.name = "X"  # type: ignore[misc]
