"""Parallelism acceptance tests: N-worker output == single-process output.

These pin Phase 2 acceptance criterion #2: running ``pipeline.run_parallel``
with workers=1, 2, 4 against the same inputs must produce byte-identical
``matches.dat`` / ``mismatches.dat`` / ``keymismatch_*.dat`` / ``dups_*.dat``
to the single-process ``pipeline.run`` path on those inputs.

We don't compare ``summary.json`` byte-for-byte (timestamps and
``elapsed_seconds`` differ between runs) but we do compare every
aggregate count field.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from segment_compare.config import load_config
from segment_compare.pipeline import run, run_parallel
from segment_compare.writer import stamped_filename

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
EXAMPLES = REPO_ROOT / "examples"

# Fixed timestamp so output filenames are deterministic across the
# single-process and parallel runs in the same test.
FIXED_TS = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)
FIXED_STAMP = "202605280000"


def _stamped(out: Path, base: str) -> Path:
    return out / stamped_filename(base, FIXED_STAMP)


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_parallel_output_matches_single_process(tmp_path: Path, workers: int) -> None:
    """Phase 2 acceptance criterion #2."""
    config = load_config(CONFIG_DIR)

    out_single = tmp_path / "single"
    out_parallel = tmp_path / f"parallel_w{workers}"

    single = run(
        file_a=EXAMPLES / "sample_a.dat",
        file_b=EXAMPLES / "sample_b.dat",
        config=config,
        output_dir=out_single,
        run_timestamp=FIXED_TS,
    )
    parallel = run_parallel(
        file_a=EXAMPLES / "sample_a.dat",
        file_b=EXAMPLES / "sample_b.dat",
        config=config,
        output_dir=out_parallel,
        workers=workers,
        run_timestamp=FIXED_TS,
    )

    # Byte-identical *.dat outputs (the four data files + the two dup files).
    for base in (
        "matches.dat",
        "mismatches.dat",
        "keymismatch_A.dat",
        "keymismatch_B.dat",
        "dups_A.dat",
        "dups_B.dat",
    ):
        assert (
            _stamped(out_single, base).read_bytes() == _stamped(out_parallel, base).read_bytes()
        ), f"workers={workers}: {base} differs from single-process baseline"

    # report.csv: identical lines (the merger keeps one header).
    assert (
        _stamped(out_single, "report.csv").read_text()
        == _stamped(out_parallel, "report.csv").read_text()
    ), f"workers={workers}: report.csv differs"

    # Every aggregate count must match exactly.
    assert parallel.records_matched == single.records_matched
    assert parallel.records_mismatched == single.records_mismatched
    assert parallel.keys_in_a_only == single.keys_in_a_only
    assert parallel.keys_in_b_only == single.keys_in_b_only
    assert parallel.keys_in_both == single.keys_in_both
    assert parallel.dups_in_a == single.dups_in_a
    assert parallel.dups_in_b == single.dups_in_b
    assert parallel.file_a_record_count == single.file_a_record_count
    assert parallel.file_b_record_count == single.file_b_record_count
    # Per-segment match/mismatch counts must match across paths.
    parallel_by_name = {s.segment_name: s for s in parallel.per_segment}
    for s in single.per_segment:
        p = parallel_by_name[s.segment_name]
        assert p.match_count == s.match_count, (
            f"workers={workers}: per-segment match for {s.segment_name} differs "
            f"({p.match_count} vs {s.match_count})"
        )
        assert (
            p.mismatch_count == s.mismatch_count
        ), f"workers={workers}: per-segment mismatch for {s.segment_name} differs"


def test_run_parallel_rejects_zero_workers(tmp_path: Path) -> None:
    config = load_config(CONFIG_DIR)
    with pytest.raises(ValueError, match="workers must be >= 1"):
        run_parallel(
            file_a=EXAMPLES / "sample_a.dat",
            file_b=EXAMPLES / "sample_b.dat",
            config=config,
            output_dir=tmp_path / "out",
            workers=0,
        )


def test_run_parallel_writes_summary_json_with_stamp(tmp_path: Path) -> None:
    """summary.json + compare_reports.csv/.html must land at stamped paths
    even though the master writer is closed before the merge happens."""
    config = load_config(CONFIG_DIR)
    out = tmp_path / "out"
    summary = run_parallel(
        file_a=EXAMPLES / "sample_a.dat",
        file_b=EXAMPLES / "sample_b.dat",
        config=config,
        output_dir=out,
        workers=2,
        run_timestamp=FIXED_TS,
    )
    assert (out / stamped_filename("summary.json", FIXED_STAMP)).exists()
    assert (out / stamped_filename("compare_reports.csv", FIXED_STAMP)).exists()
    assert (out / stamped_filename("compare_reports.html", FIXED_STAMP)).exists()
    assert summary.filename_stamp == FIXED_STAMP
