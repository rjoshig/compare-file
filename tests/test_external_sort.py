"""Tests for ``segment_compare.external_sort`` + integration with ``pipeline``."""

from __future__ import annotations

import random
from datetime import datetime, timezone
from pathlib import Path

from segment_compare.config import load_config
from segment_compare.external_sort import external_sort_file
from segment_compare.parser import iter_records
from segment_compare.pipeline import run
from segment_compare.writer import stamped_filename

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
EXAMPLES = REPO_ROOT / "examples"

FIXED_TS = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)
FIXED_STAMP = "202605280000"


def _stamped(out: Path, base: str) -> Path:
    return out / stamped_filename(base, FIXED_STAMP)


def _read_records(path: Path, config) -> list[str]:
    """Return the list of record keys in file order."""
    with path.open("rb") as fh:
        return [r.key for r in iter_records(fh, config.parser, config.segments)]


def _shuffle_records(src: Path, dst: Path, config, seed: int = 42) -> None:
    """Read records from ``src``, shuffle by key, write to ``dst``.

    Preserves the raw bytes; only the record order changes.
    """
    delimiter = config.segments.record_delimiter
    with src.open("rb") as fh:
        records = [r.raw for r in iter_records(fh, config.parser, config.segments)]
    random.Random(seed).shuffle(records)
    with dst.open("wb") as out:
        for raw in records:
            out.write(raw)
            if delimiter:
                out.write(delimiter)


# ---------------------------------------------------------------------------
# external_sort_file unit tests
# ---------------------------------------------------------------------------


def test_external_sort_orders_records_by_key(tmp_path: Path) -> None:
    config = load_config(CONFIG_DIR)
    # Take the realistic sample (already sorted), shuffle to make unsorted input.
    shuffled = tmp_path / "shuffled_a.dat"
    _shuffle_records(EXAMPLES / "sample_a.dat", shuffled, config)
    keys_before = _read_records(shuffled, config)
    assert keys_before != sorted(keys_before), "shuffle should produce unsorted order"

    sorted_out = tmp_path / "sorted_a.dat"
    n = external_sort_file(shuffled, sorted_out, config)

    assert n == len(keys_before)
    keys_after = _read_records(sorted_out, config)
    assert keys_after == sorted(keys_before)
    # Dup keys (KEY...08 appears twice in sample_a) survive intact.
    assert keys_after.count("KEY000000008") == 2


def test_external_sort_is_idempotent_on_sorted_input(tmp_path: Path) -> None:
    """Already-sorted input must come back byte-identical."""
    config = load_config(CONFIG_DIR)
    out = tmp_path / "sorted_a.dat"
    external_sort_file(EXAMPLES / "sample_a.dat", out, config)
    assert out.read_bytes() == (EXAMPLES / "sample_a.dat").read_bytes()


def test_external_sort_empty_input_yields_empty_output(tmp_path: Path) -> None:
    config = load_config(CONFIG_DIR)
    empty_in = tmp_path / "empty.dat"
    empty_in.write_bytes(b"")
    out = tmp_path / "sorted.dat"
    n = external_sort_file(empty_in, out, config)
    assert n == 0
    assert out.read_bytes() == b""


def test_external_sort_chunk_boundary_cases(tmp_path: Path) -> None:
    """Verify correctness when the record count crosses chunk boundaries.

    The realistic sample is 10 records; with a forced chunk_size=3 the
    sort must produce ⌈10/3⌉ = 4 chunks and merge them correctly.
    """
    config = load_config(CONFIG_DIR)
    # Build a config with a tiny chunk_size by mutating the loaded one.
    # ResolvedConfig.runtime is frozen, so reach in via dataclasses.replace.
    from dataclasses import replace as _replace

    small_runtime = _replace(config.runtime, chunk_size=3)
    config = _replace(config, runtime=small_runtime)

    shuffled = tmp_path / "shuffled.dat"
    _shuffle_records(EXAMPLES / "sample_a.dat", shuffled, config, seed=7)
    expected_keys = sorted(_read_records(shuffled, config))

    out = tmp_path / "sorted.dat"
    external_sort_file(shuffled, out, config)
    assert _read_records(out, config) == expected_keys


def test_external_sort_cleans_up_chunk_files(tmp_path: Path) -> None:
    """Temp chunk files must be deleted after the merge."""
    from dataclasses import replace as _replace

    config = load_config(CONFIG_DIR)
    sort_dir = tmp_path / "sort_temp"
    config = _replace(config, runtime=_replace(config.runtime, sort_temp_dir=sort_dir))

    shuffled = tmp_path / "shuffled.dat"
    _shuffle_records(EXAMPLES / "sample_a.dat", shuffled, config)
    out = tmp_path / "sorted.dat"
    external_sort_file(shuffled, out, config)

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

    Output filenames will differ because the timestamp comes from now()
    in the baseline run and we pin run_timestamp here, so we compare
    SUMMARY counts and the keys present in each output file, not raw
    file bytes."""
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
    assert _read_records(shuffled_a, config) != _read_records(EXAMPLES / "sample_a.dat", config)

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


def test_pipeline_run_with_input_sorted_false_in_config_triggers_external_sort(
    tmp_path: Path,
) -> None:
    """If runtime.input_sorted is false, the sort happens even without the flag."""
    from dataclasses import replace as _replace

    config = load_config(CONFIG_DIR)
    config = _replace(config, runtime=_replace(config.runtime, input_sorted=False))

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
        # external_sort flag NOT passed; config flag triggers the sort.
    )

    # Counts should match the canonical sample-file oracle.
    assert summary.records_matched == 4
    assert summary.records_mismatched == 3


def test_summary_records_original_input_paths_not_sorted_temp_paths(
    tmp_path: Path,
) -> None:
    """When the sort runs, summary.json must still cite the original inputs.

    The sorted intermediates live under sort_temp_dir and are an
    implementation detail — auditors care about what was passed in.
    """
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
