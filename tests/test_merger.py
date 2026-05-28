"""Tests for ``segment_compare.merger``."""

from __future__ import annotations

from pathlib import Path

from segment_compare.merger import fold_partial_summaries, merge_worker_outputs
from segment_compare.worker import WorkerResult


def _make_worker_dir(root: Path, wid: int, matches: bytes, mismatches: bytes, report: str) -> Path:
    wdir = root / f"w{wid}"
    wdir.mkdir(parents=True)
    (wdir / "matches.dat").write_bytes(matches)
    (wdir / "mismatches.dat").write_bytes(mismatches)
    (wdir / "report.csv").write_text(report)
    return wdir


def test_merge_concatenates_matches_in_worker_order(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    w0 = _make_worker_dir(
        tmp_path / "_workers", 0, b"A0\nA1\n", b"", "key,segment_name,status,a_count,b_count\n"
    )
    w1 = _make_worker_dir(
        tmp_path / "_workers", 1, b"A2\nA3\n", b"", "key,segment_name,status,a_count,b_count\n"
    )
    w2 = _make_worker_dir(
        tmp_path / "_workers", 2, b"A4\n", b"", "key,segment_name,status,a_count,b_count\n"
    )

    merge_worker_outputs([w0, w1, w2], out, filename_stamp="202605280000")

    assert (out / "matches_202605280000.dat").read_bytes() == b"A0\nA1\nA2\nA3\nA4\n"


def test_merge_handles_empty_worker_outputs(tmp_path: Path) -> None:
    """A worker whose slice had zero matches still produces an (empty) matches.dat."""
    out = tmp_path / "out"
    out.mkdir()
    w0 = _make_worker_dir(
        tmp_path / "_workers", 0, b"A0\n", b"", "key,segment_name,status,a_count,b_count\n"
    )
    w1 = _make_worker_dir(
        tmp_path / "_workers", 1, b"", b"", "key,segment_name,status,a_count,b_count\n"
    )

    merge_worker_outputs([w0, w1], out, filename_stamp="202605280000")

    assert (out / "matches_202605280000.dat").read_bytes() == b"A0\n"
    assert (out / "mismatches_202605280000.dat").read_bytes() == b""


def test_merge_report_csv_keeps_one_header_then_concatenates_rows(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    header = "key,segment_name,status,a_count,b_count\n"
    w0 = _make_worker_dir(tmp_path / "_workers", 0, b"", b"", header + "K0,NM01,content_diff,1,1\n")
    w1 = _make_worker_dir(tmp_path / "_workers", 1, b"", b"", header + "K1,TR01,count_diff,3,2\n")
    w2 = _make_worker_dir(tmp_path / "_workers", 2, b"", b"", header)  # no rows

    merge_worker_outputs([w0, w1, w2], out, filename_stamp="202605280000")

    merged = (out / "report_202605280000.csv").read_text()
    lines = merged.splitlines()
    assert lines[0] == "key,segment_name,status,a_count,b_count"
    assert lines[1] == "K0,NM01,content_diff,1,1"
    assert lines[2] == "K1,TR01,count_diff,3,2"
    assert len(lines) == 3  # header + 2 rows; w2's no-rows is ignored


def test_merge_mismatches_concatenates_in_order(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    w0 = _make_worker_dir(
        tmp_path / "_workers",
        0,
        b"",
        b"=== KEY: K0 | MISMATCH: NM01 ===\n",
        "key,segment_name,status,a_count,b_count\n",
    )
    w1 = _make_worker_dir(
        tmp_path / "_workers",
        1,
        b"",
        b"=== KEY: K1 | MISMATCH: TR01 ===\n",
        "key,segment_name,status,a_count,b_count\n",
    )
    merge_worker_outputs([w0, w1], out, filename_stamp="202605280000")
    merged = (out / "mismatches_202605280000.dat").read_bytes()
    assert merged.startswith(b"=== KEY: K0")
    assert b"K1" in merged
    # K0 must appear before K1 in the merged stream.
    assert merged.index(b"K0") < merged.index(b"K1")


def test_fold_partial_summaries_sums_counts_and_per_segment() -> None:
    r0 = WorkerResult(
        worker_id=0,
        records_matched=100,
        records_mismatched=5,
        per_segment_match={"NM01": 100, "TR01": 99},
        per_segment_mismatch={"NM01": 5, "TR01": 1},
    )
    r1 = WorkerResult(
        worker_id=1,
        records_matched=80,
        records_mismatched=3,
        per_segment_match={"NM01": 80, "AD01": 78},
        per_segment_mismatch={"AD01": 5},
    )

    matched, mismatched, per_match, per_mismatch = fold_partial_summaries([r0, r1])

    assert matched == 180
    assert mismatched == 8
    assert per_match == {"NM01": 180, "TR01": 99, "AD01": 78}
    assert per_mismatch == {"NM01": 5, "TR01": 1, "AD01": 5}


def test_fold_partial_summaries_empty_input() -> None:
    matched, mismatched, per_match, per_mismatch = fold_partial_summaries([])
    assert matched == 0
    assert mismatched == 0
    assert per_match == {}
    assert per_mismatch == {}
