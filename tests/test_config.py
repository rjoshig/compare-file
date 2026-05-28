"""Tests for ``segment_compare.config``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from segment_compare.config import (
    ConfigError,
    NormalizationRule,
    ResolvedConfig,
    RuntimeConfig,
    load_config,
)
from segment_compare.parser import ParserConfig, SegmentsConfig

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _write_configs(
    tmp_path: Path,
    segments: dict | None = None,
    normalization: dict | None = None,
    runtime: dict | None = None,
) -> Path:
    """Write a triple of config files in ``tmp_path`` and return it."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "segments.json").write_text(
        json.dumps(segments if segments is not None else _default_segments())
    )
    (cfg_dir / "normalization.json").write_text(
        json.dumps(normalization if normalization is not None else _default_normalization())
    )
    (cfg_dir / "runtime.json").write_text(
        json.dumps(runtime if runtime is not None else _default_runtime())
    )
    return cfg_dir


def _default_segments() -> dict:
    return {
        "known_segments": ["TU4R", "NM01", "ENDS"],
        "key_segment": "TU4R",
        "end_segment": "ENDS",
        "key_range": [0, 12],
        "record_delimiter": "\n",
        "parser": {
            "segment_name_bytes": 4,
            "size_field_bytes": 3,
            "size_encoding": "ascii_int",
            "size_includes_header": True,
            "data_encoding": "ascii",
        },
    }


def _default_normalization() -> dict:
    return {
        "TU4R": {"file_a_strip": [], "file_b_strip": [], "exclude_positions": []},
        "NM01": {
            "file_a_strip": [[0, 2]],
            "file_b_strip": [],
            "exclude_positions": [[5, 7]],
        },
    }


def _default_runtime() -> dict:
    return {
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


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_load_config_returns_resolved_config(tmp_path: Path) -> None:
    cfg_dir = _write_configs(tmp_path)
    resolved = load_config(cfg_dir)
    assert isinstance(resolved, ResolvedConfig)
    assert isinstance(resolved.parser, ParserConfig)
    assert isinstance(resolved.segments, SegmentsConfig)
    assert isinstance(resolved.runtime, RuntimeConfig)
    assert resolved.segments.key_segment == "TU4R"
    assert resolved.segments.end_segment == "ENDS"
    assert resolved.segments.key_range == (0, 12)
    assert resolved.segments.record_delimiter == b"\n"
    assert resolved.known_segments == ("TU4R", "NM01", "ENDS")
    assert "NM01" in resolved.normalization
    assert resolved.normalization["NM01"].file_a_strip == ((0, 2),)
    assert resolved.normalization["NM01"].exclude_positions == ((5, 7),)
    assert resolved.runtime.hash_method == "blake2b"
    assert resolved.runtime.sort_temp_dir == Path("/tmp/segment_compare")
    assert len(resolved.audit_hash) == 64  # SHA-256 hex


def test_load_config_loads_committed_configs() -> None:
    """The committed config/ directory must load without error."""
    resolved = load_config(CONFIG_DIR)
    assert resolved.parser.segment_name_bytes == 4
    assert resolved.parser.size_field_bytes == 3
    assert resolved.segments.key_segment == "TU4R"
    assert resolved.segments.record_delimiter == b"\n"


def test_audit_hash_is_deterministic(tmp_path: Path) -> None:
    """Loading the same configs twice produces the same hash."""
    cfg_dir = _write_configs(tmp_path)
    h1 = load_config(cfg_dir).audit_hash
    h2 = load_config(cfg_dir).audit_hash
    assert h1 == h2


def test_audit_hash_ignores_dollar_comment_keys(tmp_path: Path) -> None:
    """Editing a $comment key must not change the audit hash."""
    base = _default_segments()
    h1 = load_config(_write_configs(tmp_path / "a", segments=base)).audit_hash
    with_comment = {**base, "$comment": "edited"}
    h2 = load_config(_write_configs(tmp_path / "b", segments=with_comment)).audit_hash
    assert h1 == h2


def test_audit_hash_changes_when_meaningful_field_changes(tmp_path: Path) -> None:
    base = _default_segments()
    h1 = load_config(_write_configs(tmp_path / "a", segments=base)).audit_hash
    changed = {**base, "key_range": [0, 10]}
    h2 = load_config(_write_configs(tmp_path / "b", segments=changed)).audit_hash
    assert h1 != h2


def test_paths_record_source_files(tmp_path: Path) -> None:
    cfg_dir = _write_configs(tmp_path)
    resolved = load_config(cfg_dir)
    assert resolved.paths["segments"] == cfg_dir / "segments.json"
    assert resolved.paths["normalization"] == cfg_dir / "normalization.json"
    assert resolved.paths["runtime"] == cfg_dir / "runtime.json"


# ---------------------------------------------------------------------------
# Missing / malformed files
# ---------------------------------------------------------------------------


def test_missing_segments_file_raises(tmp_path: Path) -> None:
    cfg_dir = _write_configs(tmp_path)
    (cfg_dir / "segments.json").unlink()
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "does not exist" in excinfo.value.message


def test_invalid_json_raises(tmp_path: Path) -> None:
    cfg_dir = _write_configs(tmp_path)
    (cfg_dir / "runtime.json").write_text("{not valid json")
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "invalid JSON" in excinfo.value.message


def test_top_level_must_be_object(tmp_path: Path) -> None:
    cfg_dir = _write_configs(tmp_path)
    (cfg_dir / "segments.json").write_text("[]")
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "object" in excinfo.value.message


# ---------------------------------------------------------------------------
# segments.json validation
# ---------------------------------------------------------------------------


def test_unknown_key_segment_raises(tmp_path: Path) -> None:
    seg = _default_segments()
    seg["key_segment"] = "ZZZZ"
    cfg_dir = _write_configs(tmp_path, segments=seg)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "key_segment" in excinfo.value.field


def test_unknown_end_segment_raises(tmp_path: Path) -> None:
    seg = _default_segments()
    seg["end_segment"] = "ZZZZ"
    cfg_dir = _write_configs(tmp_path, segments=seg)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "end_segment" in excinfo.value.field


def test_key_equals_end_raises(tmp_path: Path) -> None:
    seg = _default_segments()
    seg["end_segment"] = "TU4R"
    cfg_dir = _write_configs(tmp_path, segments=seg)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "differ" in excinfo.value.message


def test_invalid_key_range_raises(tmp_path: Path) -> None:
    seg = _default_segments()
    seg["key_range"] = [5, 5]  # end <= start
    cfg_dir = _write_configs(tmp_path, segments=seg)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "key_range" in excinfo.value.field


def test_negative_key_range_start_raises(tmp_path: Path) -> None:
    seg = _default_segments()
    seg["key_range"] = [-1, 5]
    cfg_dir = _write_configs(tmp_path, segments=seg)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


def test_duplicate_known_segments_raise(tmp_path: Path) -> None:
    seg = _default_segments()
    seg["known_segments"] = ["TU4R", "TU4R", "ENDS"]
    cfg_dir = _write_configs(tmp_path, segments=seg)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "unique" in excinfo.value.message


def test_empty_known_segments_raises(tmp_path: Path) -> None:
    seg = _default_segments()
    seg["known_segments"] = []
    cfg_dir = _write_configs(tmp_path, segments=seg)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


def test_unsupported_parser_knob_raises(tmp_path: Path) -> None:
    seg = _default_segments()
    seg["parser"]["size_encoding"] = "binary_be_uint"
    cfg_dir = _write_configs(tmp_path, segments=seg)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "size_encoding" in excinfo.value.field


def test_nondefault_segment_name_bytes_raises(tmp_path: Path) -> None:
    seg = _default_segments()
    seg["parser"]["segment_name_bytes"] = 5
    cfg_dir = _write_configs(tmp_path, segments=seg)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


def test_non_ascii_delimiter_raises(tmp_path: Path) -> None:
    seg = _default_segments()
    seg["record_delimiter"] = "ÿ"  # Latin-1 only
    cfg_dir = _write_configs(tmp_path, segments=seg)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


# ---------------------------------------------------------------------------
# normalization.json validation
# ---------------------------------------------------------------------------


def test_unknown_segment_in_normalization_raises(tmp_path: Path) -> None:
    norm = {"ZZZZ": {"file_a_strip": [], "file_b_strip": [], "exclude_positions": []}}
    cfg_dir = _write_configs(tmp_path, normalization=norm)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "ZZZZ" in excinfo.value.field


def test_normalization_ignores_dollar_keys(tmp_path: Path) -> None:
    norm = {
        "$comment": "this is fine",
        "TU4R": {"file_a_strip": [], "file_b_strip": [], "exclude_positions": []},
    }
    cfg_dir = _write_configs(tmp_path, normalization=norm)
    resolved = load_config(cfg_dir)
    assert "TU4R" in resolved.normalization
    assert "$comment" not in resolved.normalization


def test_normalization_bad_range_raises(tmp_path: Path) -> None:
    norm = {
        "TU4R": {
            "file_a_strip": [[5, 2]],  # end < start
            "file_b_strip": [],
            "exclude_positions": [],
        }
    }
    cfg_dir = _write_configs(tmp_path, normalization=norm)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "file_a_strip" in excinfo.value.field


def test_normalization_missing_optional_fields_defaults_to_empty(tmp_path: Path) -> None:
    norm = {"TU4R": {}}
    cfg_dir = _write_configs(tmp_path, normalization=norm)
    resolved = load_config(cfg_dir)
    rule = resolved.normalization["TU4R"]
    assert rule.file_a_strip == ()
    assert rule.file_b_strip == ()
    assert rule.exclude_positions == ()


# ---------------------------------------------------------------------------
# runtime.json validation
# ---------------------------------------------------------------------------


def test_invalid_hash_method_raises(tmp_path: Path) -> None:
    rt = _default_runtime()
    rt["hash_method"] = "md5"
    cfg_dir = _write_configs(tmp_path, runtime=rt)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


def test_builtin_hash_method_is_accepted(tmp_path: Path) -> None:
    rt = _default_runtime()
    rt["hash_method"] = "builtin"
    cfg_dir = _write_configs(tmp_path, runtime=rt)
    resolved = load_config(cfg_dir)
    assert resolved.runtime.hash_method == "builtin"


def test_digest_size_out_of_range_raises(tmp_path: Path) -> None:
    rt = _default_runtime()
    rt["blake2b_digest_size"] = 0
    cfg_dir = _write_configs(tmp_path, runtime=rt)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


def test_zero_workers_raises(tmp_path: Path) -> None:
    rt = _default_runtime()
    rt["parallel_workers"] = 0
    cfg_dir = _write_configs(tmp_path, runtime=rt)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


def test_zero_chunk_size_raises(tmp_path: Path) -> None:
    rt = _default_runtime()
    rt["chunk_size"] = 0
    cfg_dir = _write_configs(tmp_path, runtime=rt)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


def test_invalid_partition_strategy_raises(tmp_path: Path) -> None:
    rt = _default_runtime()
    rt["partition_strategy"] = "random"
    cfg_dir = _write_configs(tmp_path, runtime=rt)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


def test_missing_runtime_field_raises(tmp_path: Path) -> None:
    rt = _default_runtime()
    del rt["hash_method"]
    cfg_dir = _write_configs(tmp_path, runtime=rt)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "hash_method" in excinfo.value.field


# ---------------------------------------------------------------------------
# Frozen-ness
# ---------------------------------------------------------------------------


def test_normalization_rule_is_frozen() -> None:
    rule = NormalizationRule(file_a_strip=(), file_b_strip=(), exclude_positions=())
    with pytest.raises(AttributeError):
        rule.file_a_strip = ((0, 1),)  # type: ignore[misc]
