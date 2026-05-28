"""Config-loading tests for the field-based normalization form."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from segment_compare.config import ConfigError, load_config

# Minimum-viable segments + runtime configs we reuse across the field tests.
_SEGMENTS_JSON = {
    "known_segments": ["TU4R", "NM01", "ENDS"],
    "key_segment": "TU4R",
    "key_range": [0, 12],
    "end_segment": "ENDS",
    "record_delimiter": "\n",
    "parser": {
        "segment_name_bytes": 4,
        "size_field_bytes": 3,
        "size_encoding": "ascii_int",
        "size_includes_header": True,
        "data_encoding": "ascii",
    },
}

_RUNTIME_JSON = {
    "hash_method": "blake2b",
    "blake2b_digest_size": 16,
    "input_sorted": True,
    "sort_temp_dir": "/tmp/segment_compare",
    "parallel_workers": 1,
    "chunk_size": 10000,
    "partition_strategy": "equal_count",
    "key_type": "alphanumeric",
    "key_sort_order": "ascending",
}


def _write_cfg(dirpath: Path, normalization: dict) -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "segments.json").write_text(json.dumps(_SEGMENTS_JSON))
    (dirpath / "normalization.json").write_text(json.dumps(normalization))
    (dirpath / "runtime.json").write_text(json.dumps(_RUNTIME_JSON))


def test_field_form_segment_lands_in_field_normalization_map(tmp_path: Path) -> None:
    """An entry with file_a_layout/file_b_layout populates ResolvedConfig.field_normalization."""
    _write_cfg(
        tmp_path,
        {
            "NM01": {
                "file_a_layout": [
                    {"name": "first", "length": 20, "exclude": False},
                    {"name": "middle", "length": 15, "exclude": True},
                    {"name": "last", "length": 15, "exclude": False},
                ],
                "file_b_layout": [
                    {"name": "first", "length": 20, "exclude": False},
                    {"name": "last", "length": 15, "exclude": False},
                    {"name": "middle", "length": 15, "exclude": True},
                ],
            }
        },
    )
    cfg = load_config(tmp_path)
    assert "NM01" not in cfg.normalization
    assert "NM01" in cfg.field_normalization
    rule = cfg.field_normalization["NM01"]
    assert tuple(f.name for f in rule.file_a_layout) == ("first", "middle", "last")
    assert tuple(f.name for f in rule.file_b_layout) == ("first", "last", "middle")
    assert rule.file_a_layout[1].exclude is True


def test_position_form_segment_lands_in_position_normalization_map(tmp_path: Path) -> None:
    """An entry with file_a_strip/exclude_positions populates ResolvedConfig.normalization."""
    _write_cfg(
        tmp_path,
        {
            "NM01": {
                "file_a_strip": [[0, 5]],
                "file_b_strip": [],
                "exclude_positions": [[10, 20]],
            }
        },
    )
    cfg = load_config(tmp_path)
    assert "NM01" in cfg.normalization
    assert "NM01" not in cfg.field_normalization


def test_mixing_position_and_field_keys_in_same_entry_raises(tmp_path: Path) -> None:
    """ADR-029 ban: a single segment cannot use both forms."""
    _write_cfg(
        tmp_path,
        {
            "NM01": {
                "file_a_strip": [[0, 5]],
                "file_a_layout": [{"name": "x", "length": 5, "exclude": False}],
                "file_b_layout": [{"name": "x", "length": 5, "exclude": False}],
            }
        },
    )
    with pytest.raises(ConfigError, match="cannot mix position-based keys"):
        load_config(tmp_path)


def test_field_form_missing_file_b_layout_raises(tmp_path: Path) -> None:
    _write_cfg(
        tmp_path,
        {
            "NM01": {
                "file_a_layout": [{"name": "x", "length": 5, "exclude": False}],
            }
        },
    )
    with pytest.raises(ConfigError, match="file_b_layout"):
        load_config(tmp_path)


def test_field_form_empty_layout_raises(tmp_path: Path) -> None:
    _write_cfg(
        tmp_path,
        {
            "NM01": {
                "file_a_layout": [],
                "file_b_layout": [{"name": "x", "length": 5, "exclude": False}],
            }
        },
    )
    with pytest.raises(ConfigError, match="must declare at least one field"):
        load_config(tmp_path)


def test_field_form_negative_length_raises(tmp_path: Path) -> None:
    _write_cfg(
        tmp_path,
        {
            "NM01": {
                "file_a_layout": [{"name": "x", "length": -1, "exclude": False}],
                "file_b_layout": [{"name": "x", "length": 1, "exclude": False}],
            }
        },
    )
    with pytest.raises(ConfigError, match="must be a positive int"):
        load_config(tmp_path)


def test_field_form_zero_length_raises(tmp_path: Path) -> None:
    _write_cfg(
        tmp_path,
        {
            "NM01": {
                "file_a_layout": [{"name": "x", "length": 0, "exclude": False}],
                "file_b_layout": [{"name": "x", "length": 5, "exclude": False}],
            }
        },
    )
    with pytest.raises(ConfigError, match="must be a positive int"):
        load_config(tmp_path)


def test_field_form_duplicate_field_name_in_one_layout_raises(tmp_path: Path) -> None:
    _write_cfg(
        tmp_path,
        {
            "NM01": {
                "file_a_layout": [
                    {"name": "x", "length": 5, "exclude": False},
                    {"name": "x", "length": 5, "exclude": False},
                ],
                "file_b_layout": [{"name": "x", "length": 5, "exclude": False}],
            }
        },
    )
    with pytest.raises(ConfigError, match="duplicate field name"):
        load_config(tmp_path)


def test_field_form_non_bool_exclude_raises(tmp_path: Path) -> None:
    _write_cfg(
        tmp_path,
        {
            "NM01": {
                "file_a_layout": [{"name": "x", "length": 5, "exclude": "yes"}],
                "file_b_layout": [{"name": "x", "length": 5, "exclude": False}],
            }
        },
    )
    with pytest.raises(ConfigError, match="must be true/false"):
        load_config(tmp_path)


def test_field_form_exclude_defaults_to_false(tmp_path: Path) -> None:
    """Omitting the exclude key should leave the field included (the safer default)."""
    _write_cfg(
        tmp_path,
        {
            "NM01": {
                "file_a_layout": [{"name": "x", "length": 5}],
                "file_b_layout": [{"name": "x", "length": 5}],
            }
        },
    )
    cfg = load_config(tmp_path)
    rule = cfg.field_normalization["NM01"]
    assert rule.file_a_layout[0].exclude is False
    assert rule.file_b_layout[0].exclude is False


def test_field_and_position_segments_coexist_in_one_config(tmp_path: Path) -> None:
    """Different segments may use different forms in the same normalization.json."""
    _write_cfg(
        tmp_path,
        {
            "NM01": {
                "file_a_layout": [{"name": "first", "length": 20, "exclude": False}],
                "file_b_layout": [{"name": "first", "length": 20, "exclude": False}],
            },
            "ENDS": {
                "file_a_strip": [],
                "file_b_strip": [],
                "exclude_positions": [[0, 3]],
            },
        },
    )
    cfg = load_config(tmp_path)
    assert set(cfg.field_normalization) == {"NM01"}
    assert set(cfg.normalization) == {"ENDS"}
