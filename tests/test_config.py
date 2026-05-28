"""Tests for ``segment_compare.config`` (the post-ADR-033 ``EngineConfig`` loader)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from segment_compare.config import ConfigError, EngineConfig, RuntimeConfig, load_config
from segment_compare.layout import FileLayout

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _runtime_payload() -> dict:
    return {
        "hash_method": "blake2b",
        "blake2b_digest_size": 16,
        "sort_temp_dir": "/tmp/segment_compare",
        "parallel_workers": 1,
        "chunk_size": 10000,
        "partition_strategy": "equal_count",
    }


def _write_config_dir(
    tmp_path: Path,
    layout_a: dict | None = None,
    layout_b: dict | None = None,
    runtime: dict | None = None,
) -> Path:
    """Stage a config directory with the three required JSON files."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    layout_a_src = (
        json.dumps(layout_a)
        if layout_a is not None
        else (CONFIG_DIR / "layout_file_A.json").read_text(encoding="utf-8")
    )
    layout_b_src = (
        json.dumps(layout_b)
        if layout_b is not None
        else (CONFIG_DIR / "layout_file_B.json").read_text(encoding="utf-8")
    )
    (cfg_dir / "layout_file_A.json").write_text(layout_a_src)
    (cfg_dir / "layout_file_B.json").write_text(layout_b_src)
    (cfg_dir / "runtime.json").write_text(json.dumps(runtime if runtime else _runtime_payload()))
    return cfg_dir


# ---------------------------------------------------------------------------
# Happy path against the committed config/
# ---------------------------------------------------------------------------


def test_load_committed_config() -> None:
    resolved = load_config(CONFIG_DIR)
    assert isinstance(resolved, EngineConfig)
    assert isinstance(resolved.layout_a, FileLayout)
    assert isinstance(resolved.layout_b, FileLayout)
    assert isinstance(resolved.runtime, RuntimeConfig)
    assert len(resolved.audit_hash) == 64
    # Engine-facing accessors
    assert resolved.parser_a.segment_name_bytes == 4
    assert resolved.parser_b.size_field_bytes == 3
    assert resolved.segments_a.key_segment == "TU4R"
    assert resolved.segments_a.end_segment == "ENDS"
    assert resolved.segments_a.key_range == (4, 16)
    assert resolved.segments_b.key_range == (4, 16)
    assert resolved.file_a_rdw is None
    assert resolved.file_b_rdw is None
    assert resolved.file_a_strip_size == 0
    assert resolved.file_b_strip_size == 0
    # Normalization built from layouts: every segment in both layouts gets a rule.
    assert {"TU4R", "NM01", "TR01", "ENDS"}.issubset(set(resolved.normalization))


def test_committed_normalization_pairs_layouts() -> None:
    """Each rule's file_a_layout / file_b_layout reflect the committed JSON."""
    resolved = load_config(CONFIG_DIR)
    nm01 = resolved.normalization["NM01"]
    # The committed fixture has identical layouts for A and B.
    assert nm01.file_a_layout == nm01.file_b_layout
    names = [f.name for f in nm01.file_a_layout]
    assert names == ["first_name", "middle_name", "last_name"]


def test_known_segments_union_order(tmp_path: Path) -> None:
    """A's segment order first, then B's extras."""
    resolved = load_config(CONFIG_DIR)
    # Both committed layouts have the same set; A's order should be preserved.
    a_order = [s.name for s in resolved.layout_a.segments]
    assert resolved.known_segments == tuple(a_order)


# ---------------------------------------------------------------------------
# Audit hash properties
# ---------------------------------------------------------------------------


def test_audit_hash_is_deterministic(tmp_path: Path) -> None:
    cfg_dir = _write_config_dir(tmp_path)
    h1 = load_config(cfg_dir).audit_hash
    h2 = load_config(cfg_dir).audit_hash
    assert h1 == h2


def test_audit_hash_ignores_dollar_comments(tmp_path: Path) -> None:
    """Editing a $comment key must not change the audit hash."""
    layout_a_raw = json.loads((CONFIG_DIR / "layout_file_A.json").read_text(encoding="utf-8"))
    base = _write_config_dir(tmp_path / "base", layout_a=layout_a_raw)
    h1 = load_config(base).audit_hash

    layout_a_raw["$comment"] = "edited but should not affect the hash"
    edited = _write_config_dir(tmp_path / "edited", layout_a=layout_a_raw)
    h2 = load_config(edited).audit_hash
    assert h1 == h2


def test_audit_hash_changes_when_meaningful_field_changes(tmp_path: Path) -> None:
    layout_a_raw = json.loads((CONFIG_DIR / "layout_file_A.json").read_text(encoding="utf-8"))
    base = _write_config_dir(tmp_path / "base", layout_a=layout_a_raw)
    h1 = load_config(base).audit_hash

    # Flip an exclude flag on a real field → different audit hash.
    for seg in layout_a_raw["segments"]:
        if seg["name"] == "NM01":
            seg["fields"][0]["exclude"] = True
            break
    changed = _write_config_dir(tmp_path / "changed", layout_a=layout_a_raw)
    h2 = load_config(changed).audit_hash
    assert h1 != h2


# ---------------------------------------------------------------------------
# Runtime.json validation
# ---------------------------------------------------------------------------


def test_runtime_invalid_hash_method_raises(tmp_path: Path) -> None:
    rt = _runtime_payload()
    rt["hash_method"] = "md5"
    cfg_dir = _write_config_dir(tmp_path, runtime=rt)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "hash_method" in excinfo.value.field


def test_runtime_digest_out_of_range_raises(tmp_path: Path) -> None:
    rt = _runtime_payload()
    rt["blake2b_digest_size"] = 0
    cfg_dir = _write_config_dir(tmp_path, runtime=rt)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


def test_runtime_zero_workers_raises(tmp_path: Path) -> None:
    rt = _runtime_payload()
    rt["parallel_workers"] = 0
    cfg_dir = _write_config_dir(tmp_path, runtime=rt)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


def test_runtime_zero_chunk_size_raises(tmp_path: Path) -> None:
    rt = _runtime_payload()
    rt["chunk_size"] = 0
    cfg_dir = _write_config_dir(tmp_path, runtime=rt)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


def test_runtime_invalid_partition_strategy_raises(tmp_path: Path) -> None:
    rt = _runtime_payload()
    rt["partition_strategy"] = "random"
    cfg_dir = _write_config_dir(tmp_path, runtime=rt)
    with pytest.raises(ConfigError):
        load_config(cfg_dir)


def test_runtime_missing_field_raises(tmp_path: Path) -> None:
    rt = _runtime_payload()
    del rt["hash_method"]
    cfg_dir = _write_config_dir(tmp_path, runtime=rt)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "hash_method" in excinfo.value.field


# ---------------------------------------------------------------------------
# Layout-error → ConfigError wrapping
# ---------------------------------------------------------------------------


def test_layout_error_re_raised_as_config_error(tmp_path: Path) -> None:
    """A bad layout file surfaces as ConfigError for a uniform CLI exit code."""
    layout_a_raw = json.loads((CONFIG_DIR / "layout_file_A.json").read_text(encoding="utf-8"))
    # Wreck the per-segment size invariant.
    layout_a_raw["segments"][0]["size"] = 999
    cfg_dir = _write_config_dir(tmp_path, layout_a=layout_a_raw)
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "segments[0].size" in excinfo.value.field


def test_missing_layout_file_a_raises(tmp_path: Path) -> None:
    cfg_dir = _write_config_dir(tmp_path)
    (cfg_dir / "layout_file_A.json").unlink()
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "does not exist" in excinfo.value.message


def test_missing_runtime_raises(tmp_path: Path) -> None:
    cfg_dir = _write_config_dir(tmp_path)
    (cfg_dir / "runtime.json").unlink()
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "does not exist" in excinfo.value.message


def test_invalid_runtime_json_raises(tmp_path: Path) -> None:
    cfg_dir = _write_config_dir(tmp_path)
    (cfg_dir / "runtime.json").write_text("{not valid json")
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_dir)
    assert "invalid JSON" in excinfo.value.message


def test_paths_recorded(tmp_path: Path) -> None:
    cfg_dir = _write_config_dir(tmp_path)
    resolved = load_config(cfg_dir)
    assert resolved.paths["layout_a"] == cfg_dir / "layout_file_A.json"
    assert resolved.paths["layout_b"] == cfg_dir / "layout_file_B.json"
    assert resolved.paths["runtime"] == cfg_dir / "runtime.json"
