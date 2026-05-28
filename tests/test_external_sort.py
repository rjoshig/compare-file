"""Tests for ``segment_compare.external_sort`` + integration with ``pipeline``."""

from __future__ import annotations

import random
from dataclasses import replace as _replace
from datetime import datetime, timezone
from pathlib import Path

from segment_compare.config import EngineConfig, load_config
from segment_compare.external_sort import external_sort_file
from segment_compare.parser import iter_records
from segment_compare.pipeline import run
from segment_compare.writer import stamped_filename

from tests._helpers import (
    make_synthetic_record,
    minimal_layout,
    write_minimal_config_dir,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
EXAMPLES = REPO_ROOT / "examples"

FIXED_TS = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)
FIXED_STAMP = "202605280000"


def _stamped(out: Path, base: str) -> Path:
    return out / stamped_filename(base, FIXED_STAMP)


def _read_keys(path: Path, config: EngineConfig) -> list[str]:
    """Return the list of record keys in file order (assumes path has File A's layout)."""
    with path.open("rb") as fh:
        return [r.key for r in iter_records(fh, config.parser_a, config.segments_a)]


def _shuffle_records(src: Path, dst: Path, config: EngineConfig, seed: int = 42) -> None:
    """Read records from ``src``, shuffle by key, write to ``dst``.

    Preserves the raw bytes; only the record order changes. Assumes
    ``src`` is in File A's layout (which is true for the realistic
    sample fixtures shared between A and B).
    """
    delimiter = config.segments_a.record_delimiter
    with src.open("rb") as fh:
        records = [r.raw for r in iter_records(fh, config.parser_a, config.segments_a)]
    random.Random(seed).shuffle(records)
    with dst.open("wb") as out:
        for raw in records:
            out.write(raw)
            if delimiter:
                out.write(delimiter)


def _sort_a(path: Path, dst: Path, config: EngineConfig) -> int:
    """Call ``external_sort_file`` for File A using the engine config's accessors."""
    return external_sort_file(
        path,
        dst,
        config.parser_a,
        config.segments_a,
        config.runtime.chunk_size,
        config.runtime.sort_temp_dir,
        rdw_cfg=config.file_a_rdw,
        strip_size=config.file_a_strip_size,
    )


def _set_input_sorted(config: EngineConfig, value: bool) -> EngineConfig:
    """Return a config where both layouts' sort.input_sorted is set to ``value``."""
    new_layout_a = _replace(
        config.layout_a, sort=_replace(config.layout_a.sort, input_sorted=value)
    )
    new_layout_b = _replace(
        config.layout_b, sort=_replace(config.layout_b.sort, input_sorted=value)
    )
    return _replace(config, layout_a=new_layout_a, layout_b=new_layout_b)


# ---------------------------------------------------------------------------
# external_sort_file unit tests
# ---------------------------------------------------------------------------


def test_external_sort_orders_records_by_key(tmp_path: Path) -> None:
    config = load_config(CONFIG_DIR)
    # Take the realistic sample (already sorted), shuffle to make unsorted input.
    shuffled = tmp_path / "shuffled_a.dat"
    _shuffle_records(EXAMPLES / "sample_a.dat", shuffled, config)
    keys_before = _read_keys(shuffled, config)
    assert keys_before != sorted(keys_before), "shuffle should produce unsorted order"

    sorted_out = tmp_path / "sorted_a.dat"
    n = _sort_a(shuffled, sorted_out, config)

    assert n == len(keys_before)
    keys_after = _read_keys(sorted_out, config)
    assert keys_after == sorted(keys_before)
    # Dup keys (KEY...08 appears twice in sample_a) survive intact.
    assert keys_after.count("KEY000000008") == 2


def test_external_sort_is_idempotent_on_sorted_input(tmp_path: Path) -> None:
    """Already-sorted input must come back byte-identical."""
    config = load_config(CONFIG_DIR)
    out = tmp_path / "sorted_a.dat"
    _sort_a(EXAMPLES / "sample_a.dat", out, config)
    assert out.read_bytes() == (EXAMPLES / "sample_a.dat").read_bytes()


def test_external_sort_empty_input_yields_empty_output(tmp_path: Path) -> None:
    config = load_config(CONFIG_DIR)
    empty_in = tmp_path / "empty.dat"
    empty_in.write_bytes(b"")
    out = tmp_path / "sorted.dat"
    n = _sort_a(empty_in, out, config)
    assert n == 0
    assert out.read_bytes() == b""


def test_external_sort_chunk_boundary_cases(tmp_path: Path) -> None:
    """Verify correctness when the record count crosses chunk boundaries.

    The realistic sample is 10 records; with a forced chunk_size=3 the
    sort must produce ⌈10/3⌉ = 4 chunks and merge them correctly.
    """
    config = load_config(CONFIG_DIR)
    config = _replace(config, runtime=_replace(config.runtime, chunk_size=3))

    shuffled = tmp_path / "shuffled.dat"
    _shuffle_records(EXAMPLES / "sample_a.dat", shuffled, config, seed=7)
    expected_keys = sorted(_read_keys(shuffled, config))

    out = tmp_path / "sorted.dat"
    _sort_a(shuffled, out, config)
    assert _read_keys(out, config) == expected_keys


def test_external_sort_cleans_up_chunk_files(tmp_path: Path) -> None:
    """Temp chunk files must be deleted after the merge."""
    config = load_config(CONFIG_DIR)
    sort_dir = tmp_path / "sort_temp"
    config = _replace(config, runtime=_replace(config.runtime, sort_temp_dir=sort_dir))

    shuffled = tmp_path / "shuffled.dat"
    _shuffle_records(EXAMPLES / "sample_a.dat", shuffled, config)
    out = tmp_path / "sorted.dat"
    _sort_a(shuffled, out, config)

    # Sort dir may still exist (cheap mkdir) but should contain no chunk_*.dat.
    leftover = list(sort_dir.glob("chunk_*.dat")) if sort_dir.exists() else []
    assert leftover == [], f"unexpected leftover chunks: {leftover}"


# ---------------------------------------------------------------------------
# pipeline integration: unsorted input + external_sort=True gives sorted output
# ---------------------------------------------------------------------------


def test_pipeline_run_with_external_sort_on_unsorted_input_matches_sorted_baseline(
    tmp_path: Path,
) -> None:
    """End-to-end: shuffled inputs + external_sort=True produces the same
    classifications and per-segment counts as the sorted baseline.
    """
    config = load_config(CONFIG_DIR)

    # Sorted-baseline run
    baseline_out = tmp_path / "baseline"
    baseline = run(
        file_a=EXAMPLES / "sample_a.dat",
        file_b=EXAMPLES / "sample_b.dat",
        config=config,
        output_dir=baseline_out,
        run_timestamp=FIXED_TS,
    )

    # Shuffled inputs + external_sort=True
    shuffled_a = tmp_path / "shuffled_a.dat"
    shuffled_b = tmp_path / "shuffled_b.dat"
    _shuffle_records(EXAMPLES / "sample_a.dat", shuffled_a, config, seed=1)
    _shuffle_records(EXAMPLES / "sample_b.dat", shuffled_b, config, seed=2)
    assert _read_keys(shuffled_a, config) != _read_keys(EXAMPLES / "sample_a.dat", config)

    sorted_out = tmp_path / "sorted_run"
    sorted_run = run(
        file_a=shuffled_a,
        file_b=shuffled_b,
        config=config,
        output_dir=sorted_out,
        run_timestamp=FIXED_TS,
        external_sort=True,
    )

    # Aggregate counts must match the sorted baseline exactly.
    assert sorted_run.records_matched == baseline.records_matched
    assert sorted_run.records_mismatched == baseline.records_mismatched
    assert sorted_run.keys_in_a_only == baseline.keys_in_a_only
    assert sorted_run.keys_in_b_only == baseline.keys_in_b_only
    assert sorted_run.keys_in_both == baseline.keys_in_both
    assert sorted_run.dups_in_a == baseline.dups_in_a
    assert sorted_run.dups_in_b == baseline.dups_in_b

    # Every primary .dat output must be byte-identical to the baseline.
    for base in (
        "matches.dat",
        "mismatches.dat",
        "keymismatch_A.dat",
        "keymismatch_B.dat",
        "dups_A.dat",
        "dups_B.dat",
        "report.csv",
    ):
        assert (
            _stamped(baseline_out, base).read_bytes() == _stamped(sorted_out, base).read_bytes()
        ), f"{base} differs between sorted baseline and external-sorted unsorted run"


def test_pipeline_run_with_input_sorted_false_in_layout_triggers_external_sort(
    tmp_path: Path,
) -> None:
    """If either layout's sort.input_sorted is false, the sort happens even without the flag."""
    config = load_config(CONFIG_DIR)
    config = _set_input_sorted(config, False)

    shuffled_a = tmp_path / "shuffled_a.dat"
    shuffled_b = tmp_path / "shuffled_b.dat"
    _shuffle_records(EXAMPLES / "sample_a.dat", shuffled_a, config, seed=3)
    _shuffle_records(EXAMPLES / "sample_b.dat", shuffled_b, config, seed=4)

    out = tmp_path / "out"
    summary = run(
        file_a=shuffled_a,
        file_b=shuffled_b,
        config=config,
        output_dir=out,
        run_timestamp=FIXED_TS,
        # external_sort flag NOT passed; layout flag triggers the sort.
    )

    # Counts should match the canonical sample-file oracle.
    assert summary.records_matched == 4
    assert summary.records_mismatched == 3


def test_summary_records_original_input_paths_not_sorted_temp_paths(
    tmp_path: Path,
) -> None:
    """When the sort runs, summary.json must still cite the original inputs."""
    config = load_config(CONFIG_DIR)
    shuffled_a = tmp_path / "shuffled_a.dat"
    shuffled_b = tmp_path / "shuffled_b.dat"
    _shuffle_records(EXAMPLES / "sample_a.dat", shuffled_a, config, seed=5)
    _shuffle_records(EXAMPLES / "sample_b.dat", shuffled_b, config, seed=6)

    out = tmp_path / "out"
    summary = run(
        file_a=shuffled_a,
        file_b=shuffled_b,
        config=config,
        output_dir=out,
        run_timestamp=FIXED_TS,
        external_sort=True,
    )

    assert summary.file_a_path == shuffled_a
    assert summary.file_b_path == shuffled_b
    assert summary.file_a_size_bytes == shuffled_a.stat().st_size
    assert summary.file_b_size_bytes == shuffled_b.stat().st_size


# ---------------------------------------------------------------------------
# Sort path respects per-record prefixes (RDW + strip_leading_bytes)
#
# These tests prove the engine consumes the configured per-record prefixes
# during the spill pass and produces prefix-less sorted output. Without them,
# the rdw / strip_leading_bytes + sort combination was untested end-to-end —
# the rdw-only test runs in single-process mode and the existing sort tests
# use the realistic fixture which carries no prefixes.
# ---------------------------------------------------------------------------


def _wrap_with_le_rdw(records: list[bytes]) -> bytes:
    """Prepend a 4-byte LE-uint RDW (low 2 bytes = length, high 2 bytes = 0) to each record."""
    out = bytearray()
    for rec in records:
        length = len(rec)
        out += length.to_bytes(2, "little") + b"\x00\x00" + rec + b"\n"
    return bytes(out)


def _wrap_with_strip(records: list[bytes], strip: bytes) -> bytes:
    """Prepend the same opaque strip prefix before every record."""
    out = bytearray()
    for rec in records:
        out += strip + rec + b"\n"
    return bytes(out)


def test_external_sort_respects_rdw_when_input_has_rdw_prefix(tmp_path: Path) -> None:
    """RDW + input_sorted=false: sort must consume the 4-byte RDW from each input record.

    File A's records are shuffled and wrapped with a 4-byte RDW per
    record. The layout declares the RDW and `input_sorted=false` so the
    external-sort path fires. After the sort, all three keys should
    appear as matches against an identically-shuffled File B.
    """
    keys_shuffled = ["KEY000000003", "KEY000000001", "KEY000000002"]
    records = [make_synthetic_record(k) for k in keys_shuffled]
    a_payload = _wrap_with_le_rdw(records)
    b_payload = _wrap_with_le_rdw(records)
    (tmp_path / "a.dat").write_bytes(a_payload)
    (tmp_path / "b.dat").write_bytes(b_payload)

    layout = minimal_layout()
    layout["rdw"] = {"rdw1_bytes": 2, "rdw2_bytes": 2, "encoding": "binary_le_uint"}
    layout["sort"]["input_sorted"] = False  # forces the external sort
    cfg_dir = write_minimal_config_dir(tmp_path, layout_a=layout, layout_b=layout)

    summary = run(
        tmp_path / "a.dat",
        tmp_path / "b.dat",
        load_config(cfg_dir),
        tmp_path / "out",
        run_timestamp=FIXED_TS,
    )
    assert summary.file_a_record_count == 3
    assert summary.file_b_record_count == 3
    assert summary.records_matched == 3
    assert summary.records_mismatched == 0


def test_external_sort_respects_strip_leading_bytes_when_input_has_strip(
    tmp_path: Path,
) -> None:
    """strip + input_sorted=false: sort must consume the opaque strip from each record."""
    keys_shuffled = ["KEY000000003", "KEY000000001", "KEY000000002"]
    records = [make_synthetic_record(k) for k in keys_shuffled]
    a_payload = _wrap_with_strip(records, strip=b"\xff\xee\xdd\xcc\xbb")
    b_payload = _wrap_with_strip(records, strip=b"\xff\xee\xdd\xcc\xbb")
    (tmp_path / "a.dat").write_bytes(a_payload)
    (tmp_path / "b.dat").write_bytes(b_payload)

    layout = minimal_layout()
    layout["strip_leading_bytes"] = {"size": 5, "encoding": "binary"}
    layout["sort"]["input_sorted"] = False
    cfg_dir = write_minimal_config_dir(tmp_path, layout_a=layout, layout_b=layout)

    summary = run(
        tmp_path / "a.dat",
        tmp_path / "b.dat",
        load_config(cfg_dir),
        tmp_path / "out",
        run_timestamp=FIXED_TS,
    )
    assert summary.records_matched == 3
    assert summary.records_mismatched == 0


def test_external_sort_respects_strip_and_rdw_together(tmp_path: Path) -> None:
    """Both per-record prefixes set: sort must consume 5 (strip) + 4 (rdw) = 9 bytes per record."""
    keys_shuffled = ["KEY000000003", "KEY000000001", "KEY000000002"]
    records = [make_synthetic_record(k) for k in keys_shuffled]

    def _wrap_both(records: list[bytes]) -> bytes:
        strip = b"\xaa\xbb\xcc\xdd\xee"
        out = bytearray()
        for rec in records:
            length = len(rec)
            out += strip + length.to_bytes(2, "little") + b"\x00\x00" + rec + b"\n"
        return bytes(out)

    (tmp_path / "a.dat").write_bytes(_wrap_both(records))
    (tmp_path / "b.dat").write_bytes(_wrap_both(records))

    layout = minimal_layout()
    layout["strip_leading_bytes"] = {"size": 5, "encoding": "binary"}
    layout["rdw"] = {"rdw1_bytes": 2, "rdw2_bytes": 2, "encoding": "binary_le_uint"}
    layout["sort"]["input_sorted"] = False
    cfg_dir = write_minimal_config_dir(tmp_path, layout_a=layout, layout_b=layout)

    summary = run(
        tmp_path / "a.dat",
        tmp_path / "b.dat",
        load_config(cfg_dir),
        tmp_path / "out",
        run_timestamp=FIXED_TS,
    )
    assert summary.records_matched == 3
    assert summary.records_mismatched == 0


def test_external_sort_with_rdw_explicit_flag_also_works(tmp_path: Path) -> None:
    """Same as the input_sorted=false case but triggered by external_sort=True instead."""
    records = [make_synthetic_record(k) for k in ("KEY000000002", "KEY000000001")]
    (tmp_path / "a.dat").write_bytes(_wrap_with_le_rdw(records))
    (tmp_path / "b.dat").write_bytes(_wrap_with_le_rdw(records))

    layout = minimal_layout()
    layout["rdw"] = {"rdw1_bytes": 2, "rdw2_bytes": 2, "encoding": "binary_le_uint"}
    cfg_dir = write_minimal_config_dir(tmp_path, layout_a=layout, layout_b=layout)

    summary = run(
        tmp_path / "a.dat",
        tmp_path / "b.dat",
        load_config(cfg_dir),
        tmp_path / "out",
        run_timestamp=FIXED_TS,
        external_sort=True,
    )
    assert summary.records_matched == 2
