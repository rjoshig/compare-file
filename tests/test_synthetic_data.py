"""Tests for ``tests.synthetic_data``.

The generator is the input side of Phase 2 acceptance:
``ExpectedCounts`` must match what ``pipeline.run`` reports when fed
the generated pair. The big 3M benchmark fixture is exercised by the
Phase 2 benchmark script (not this file) — here we run a small-N
end-to-end check that's fast enough to live in the regular pytest
suite.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from segment_compare.config import load_config
from segment_compare.pipeline import run
from tests.synthetic_data import ExpectedCounts, generate_pair

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# Small-N because this runs inside the regular pytest suite. The benchmark
# fixture (3M) is built and exercised separately.
_N = 1_000
_SEED = 42


def test_generate_pair_is_deterministic(tmp_path: Path) -> None:
    """Same num_records + seed must produce byte-identical files and counts."""
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    a1, b1, c1 = generate_pair(num_records=_N, seed=_SEED, out_dir=out1)
    a2, b2, c2 = generate_pair(num_records=_N, seed=_SEED, out_dir=out2)
    assert a1.read_bytes() == a2.read_bytes()
    assert b1.read_bytes() == b2.read_bytes()
    assert c1 == c2


def test_generate_pair_cache_hit_skips_regeneration(tmp_path: Path) -> None:
    """Second call against the same out_dir reuses the existing artifacts."""
    a1, b1, c1 = generate_pair(num_records=_N, seed=_SEED, out_dir=tmp_path)
    mtime_a = a1.stat().st_mtime_ns
    mtime_b = b1.stat().st_mtime_ns
    a2, b2, c2 = generate_pair(num_records=_N, seed=_SEED, out_dir=tmp_path)
    assert a1 == a2 and b1 == b2
    assert c1 == c2
    # Files were not rewritten on the cache hit.
    assert a2.stat().st_mtime_ns == mtime_a
    assert b2.stat().st_mtime_ns == mtime_b


def test_different_seeds_produce_different_content(tmp_path: Path) -> None:
    a1, b1, _ = generate_pair(num_records=_N, seed=1, out_dir=tmp_path / "s1")
    a2, b2, _ = generate_pair(num_records=_N, seed=2, out_dir=tmp_path / "s2")
    assert a1.read_bytes() != a2.read_bytes()
    assert b1.read_bytes() != b2.read_bytes()


def test_expected_counts_match_engine_actuals(tmp_path: Path) -> None:
    """End-to-end: ExpectedCounts must equal what pipeline.run reports.

    This is the core contract — if it ever drifts, the Phase 2 benchmark
    acceptance test against the 3M fixture will fail in mysterious ways.
    Catch it here at small-N first.
    """
    a, b, expected = generate_pair(num_records=_N, seed=_SEED, out_dir=tmp_path)
    config = load_config(CONFIG_DIR)
    summary = run(
        file_a=a,
        file_b=b,
        config=config,
        output_dir=tmp_path / "results",
        run_timestamp=datetime(2026, 5, 27, 23, 30, tzinfo=timezone.utc),
    )

    assert summary.file_a_record_count == expected.file_a_records
    assert summary.file_b_record_count == expected.file_b_records
    assert summary.records_matched == expected.matches
    assert summary.records_mismatched == expected.mismatches
    assert summary.keys_in_a_only == expected.only_in_a
    assert summary.keys_in_b_only == expected.only_in_b
    assert summary.dups_in_a == expected.dups_in_a
    assert summary.dups_in_b == expected.dups_in_b


def test_expected_counts_is_frozen_dataclass() -> None:
    ec = ExpectedCounts(
        num_keys=10,
        file_a_records=8,
        file_b_records=8,
        matches=7,
        mismatches=1,
        only_in_a=1,
        only_in_b=0,
        dups_in_a=0,
        dups_in_b=0,
        report_rows=1,
    )
    with pytest.raises((AttributeError, TypeError)):
        ec.num_keys = 11  # type: ignore[misc]
