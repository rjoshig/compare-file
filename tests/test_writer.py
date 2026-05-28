"""Tests for ``segment_compare.writer``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from segment_compare.comparator import RecordVerdict, SegmentVerdict
from segment_compare.parser import Record, Segment, SegmentsConfig
from segment_compare.writer import (
    DUPS_A_FILE,
    DUPS_B_FILE,
    KEYMISMATCH_A_FILE,
    KEYMISMATCH_B_FILE,
    MATCHES_FILE,
    MISMATCHES_FILE,
    REPORT_FILE,
    SUMMARY_FILE,
    OutputWriter,
    SegmentSummary,
    Summary,
)

SEGMENTS_CFG = SegmentsConfig(
    key_segment="TU4R",
    end_segment="ENDS",
    key_range=(0, 12),
    record_delimiter=b"\n",
)


def _record(key: str, raw: bytes) -> Record:
    return Record(
        key=key,
        segments=(Segment(name="TU4R", size=19, data=key.encode().ljust(12)[:12], offset=0),),
        raw=raw,
        offset=0,
        length=len(raw) + 1,
    )


def _summary(tmp_path: Path) -> Summary:
    return Summary(
        file_a_path=tmp_path / "a.dat",
        file_b_path=tmp_path / "b.dat",
        file_a_size_bytes=176,
        file_b_size_bytes=176,
        file_a_record_count=4,
        file_b_record_count=4,
        keys_in_a_only=1,
        keys_in_b_only=1,
        keys_in_both=3,
        dups_in_a=0,
        dups_in_b=0,
        records_matched=2,
        records_mismatched=1,
        per_segment=(
            SegmentSummary("NM01", match_count=2, mismatch_count=1, total_in_a=3, total_in_b=3),
        ),
        start_time="2026-05-28T01:00:00Z",
        end_time="2026-05-28T01:00:05Z",
        elapsed_seconds=5.0,
        throughput_records_per_sec=0.8,
        config_paths={"segments": "/cfg/segments.json"},
        config_audit_hash="deadbeef",
        engine_version="0.0.1",
    )


# ---------------------------------------------------------------------------
# Open / close lifecycle
# ---------------------------------------------------------------------------


def test_open_creates_all_eight_files(tmp_path: Path) -> None:
    out = tmp_path / "out"
    with OutputWriter(out, SEGMENTS_CFG):
        pass
    for name in (
        MATCHES_FILE,
        MISMATCHES_FILE,
        KEYMISMATCH_A_FILE,
        KEYMISMATCH_B_FILE,
        DUPS_A_FILE,
        DUPS_B_FILE,
        REPORT_FILE,
    ):
        assert (out / name).exists()
    # summary.json only exists after finalize


def test_creates_output_dir_if_missing(tmp_path: Path) -> None:
    out = tmp_path / "deep" / "nest" / "out"
    with OutputWriter(out, SEGMENTS_CFG):
        pass
    assert out.is_dir()


def test_close_is_idempotent(tmp_path: Path) -> None:
    w = OutputWriter(tmp_path / "out", SEGMENTS_CFG)
    w.close()
    w.close()  # must not raise


# ---------------------------------------------------------------------------
# Per-record writes
# ---------------------------------------------------------------------------


def test_write_match_appends_a_bytes_plus_delimiter(tmp_path: Path) -> None:
    out = tmp_path / "out"
    rec_a = _record("K1", b"AAAA")
    with OutputWriter(out, SEGMENTS_CFG) as w:
        w.write_match(rec_a)
    assert (out / MATCHES_FILE).read_bytes() == b"AAAA\n"


def test_write_match_writes_multiple_records(tmp_path: Path) -> None:
    out = tmp_path / "out"
    with OutputWriter(out, SEGMENTS_CFG) as w:
        w.write_match(_record("K1", b"AAAA"))
        w.write_match(_record("K2", b"BBBB"))
    assert (out / MATCHES_FILE).read_bytes() == b"AAAA\nBBBB\n"


def test_empty_delimiter_means_back_to_back(tmp_path: Path) -> None:
    out = tmp_path / "out"
    cfg = SegmentsConfig(
        key_segment="TU4R",
        end_segment="ENDS",
        key_range=(0, 12),
        record_delimiter=b"",
    )
    with OutputWriter(out, cfg) as w:
        w.write_match(_record("K1", b"AAAA"))
        w.write_match(_record("K2", b"BBBB"))
    assert (out / MATCHES_FILE).read_bytes() == b"AAAABBBB"


def test_write_mismatch_side_by_side_block(tmp_path: Path) -> None:
    out = tmp_path / "out"
    verdict = RecordVerdict(
        key="K1",
        matched=False,
        segment_verdicts=(SegmentVerdict("NM01", False, 1, 1),),
    )
    rec_a = _record("K1", b"AAAA")
    rec_b = _record("K1", b"BBBB")
    with OutputWriter(out, SEGMENTS_CFG) as w:
        w.write_mismatch(verdict, rec_a, rec_b)
    contents = (out / MISMATCHES_FILE).read_bytes()
    expected = (
        b"=== KEY: K1 | MISMATCH: NM01 ===\n"
        b"--- FILE A ---\n"
        b"AAAA\n"
        b"--- FILE B ---\n"
        b"BBBB\n\n"
    )
    assert contents == expected


def test_write_mismatch_emits_report_rows_per_mismatched_segment(tmp_path: Path) -> None:
    out = tmp_path / "out"
    verdict = RecordVerdict(
        key="K1",
        matched=False,
        segment_verdicts=(
            SegmentVerdict("AD01", True, 1, 1),  # match → no row
            SegmentVerdict("NM01", False, 1, 1),  # content_diff
            SegmentVerdict("TR01", False, 3, 2),  # count_diff
        ),
    )
    rec_a = _record("K1", b"AAAA")
    rec_b = _record("K1", b"BBBB")
    with OutputWriter(out, SEGMENTS_CFG) as w:
        w.write_mismatch(verdict, rec_a, rec_b)
    rows = (out / REPORT_FILE).read_text().splitlines()
    assert rows[0] == "key,segment_name,status,a_count,b_count"
    assert rows[1] == "K1,NM01,content_diff,1,1"
    assert rows[2] == "K1,TR01,count_diff,3,2"
    assert len(rows) == 3  # no row for the matched AD01


def test_report_is_empty_when_no_mismatches_written(tmp_path: Path) -> None:
    out = tmp_path / "out"
    with OutputWriter(out, SEGMENTS_CFG):
        pass
    rows = (out / REPORT_FILE).read_text().splitlines()
    assert rows == ["key,segment_name,status,a_count,b_count"]


def test_write_key_only_a_and_b(tmp_path: Path) -> None:
    out = tmp_path / "out"
    with OutputWriter(out, SEGMENTS_CFG) as w:
        w.write_key_only_a(_record("K1", b"AAAA"))
        w.write_key_only_b(_record("K2", b"BBBB"))
    assert (out / KEYMISMATCH_A_FILE).read_bytes() == b"AAAA\n"
    assert (out / KEYMISMATCH_B_FILE).read_bytes() == b"BBBB\n"


def test_write_dup_a_and_b(tmp_path: Path) -> None:
    out = tmp_path / "out"
    with OutputWriter(out, SEGMENTS_CFG) as w:
        w.write_dup_a(_record("K1", b"AAAA"))
        w.write_dup_b(_record("K2", b"BBBB"))
    assert (out / DUPS_A_FILE).read_bytes() == b"AAAA\n"
    assert (out / DUPS_B_FILE).read_bytes() == b"BBBB\n"


# ---------------------------------------------------------------------------
# Finalize / summary.json
# ---------------------------------------------------------------------------


def test_finalize_writes_summary_json(tmp_path: Path) -> None:
    out = tmp_path / "out"
    summary = _summary(tmp_path)
    with OutputWriter(out, SEGMENTS_CFG) as w:
        w.finalize(summary)
    data = json.loads((out / SUMMARY_FILE).read_text())
    assert data["file_a_record_count"] == 4
    assert data["records_matched"] == 2
    assert data["config_audit_hash"] == "deadbeef"
    assert data["per_segment"][0]["segment_name"] == "NM01"
    # Path fields serialized as strings
    assert isinstance(data["file_a_path"], str)
    assert data["file_a_path"].endswith("a.dat")


def test_finalize_closes_handles(tmp_path: Path) -> None:
    out = tmp_path / "out"
    summary = _summary(tmp_path)
    w = OutputWriter(out, SEGMENTS_CFG)
    w.finalize(summary)
    # After finalize, writing should fail because handles are closed.
    with pytest.raises(ValueError):
        w.write_match(_record("K1", b"AAAA"))


def test_summary_json_is_pretty_printed(tmp_path: Path) -> None:
    out = tmp_path / "out"
    summary = _summary(tmp_path)
    with OutputWriter(out, SEGMENTS_CFG) as w:
        w.finalize(summary)
    text = (out / SUMMARY_FILE).read_text()
    # indent=2 + sort_keys → multi-line, keys in alpha order
    assert "\n" in text
    assert '"config_audit_hash"' in text
    assert text.endswith("\n")
