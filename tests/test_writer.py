"""Tests for ``segment_compare.writer``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from segment_compare.comparator import RecordVerdict, SegmentVerdict
from segment_compare.parser import Record, Segment, SegmentsConfig
from segment_compare.writer import (
    COMPARE_REPORTS_CSV_FILE,
    COMPARE_REPORTS_HTML_FILE,
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
    stamped_filename,
    write_compare_reports_csv,
    write_compare_reports_html,
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


# ---------------------------------------------------------------------------
# Timestamped filename support
# ---------------------------------------------------------------------------


def test_stamped_filename_helper_handles_extensions() -> None:
    assert stamped_filename("matches.dat", "202605272239") == "matches_202605272239.dat"
    assert stamped_filename("report.csv", "202605272239") == "report_202605272239.csv"
    assert stamped_filename("summary.json", "202605272239") == "summary_202605272239.json"


def test_stamped_filename_helper_empty_stamp_is_passthrough() -> None:
    assert stamped_filename("matches.dat", "") == "matches.dat"


def test_stamped_filename_helper_handles_no_extension() -> None:
    assert stamped_filename("noext", "202605272239") == "noext_202605272239"


def test_writer_with_stamp_writes_stamped_filenames(tmp_path: Path) -> None:
    out = tmp_path / "out"
    stamp = "202605272239"
    with OutputWriter(out, SEGMENTS_CFG, filename_stamp=stamp) as w:
        w.write_match(
            Record(
                key="K1",
                segments=(Segment(name="TU4R", size=19, data=b"K1XXXXXXXXXX", offset=0),),
                raw=b"AAAA",
                offset=0,
                length=5,
            )
        )
        w.finalize(_summary(tmp_path))

    # Bare names must NOT exist; stamped names must exist.
    assert not (out / MATCHES_FILE).exists()
    assert not (out / REPORT_FILE).exists()
    assert not (out / SUMMARY_FILE).exists()

    for base in (
        MATCHES_FILE,
        MISMATCHES_FILE,
        KEYMISMATCH_A_FILE,
        KEYMISMATCH_B_FILE,
        DUPS_A_FILE,
        DUPS_B_FILE,
        REPORT_FILE,
        SUMMARY_FILE,
    ):
        assert (out / stamped_filename(base, stamp)).exists()


def test_writer_path_for_helps_callers_resolve_stamped_paths(tmp_path: Path) -> None:
    out = tmp_path / "out"
    stamp = "202605272239"
    w = OutputWriter(out, SEGMENTS_CFG, filename_stamp=stamp)
    try:
        assert w.path_for(MATCHES_FILE) == out / "matches_202605272239.dat"
        assert w.path_for(SUMMARY_FILE) == out / "summary_202605272239.json"
    finally:
        w.close()


# ---------------------------------------------------------------------------
# compare_reports.csv + compare_reports.html (ADR-035)
# ---------------------------------------------------------------------------


def _multi_segment_summary(tmp_path: Path) -> Summary:
    """A Summary with two segments and full config_paths so all sections render."""
    return Summary(
        file_a_path=tmp_path / "a.dat",
        file_b_path=tmp_path / "b.dat",
        file_a_size_bytes=176,
        file_b_size_bytes=180,
        file_a_record_count=4,
        file_b_record_count=5,
        keys_in_a_only=1,
        keys_in_b_only=2,
        keys_in_both=3,
        dups_in_a=0,
        dups_in_b=1,
        records_matched=2,
        records_mismatched=1,
        per_segment=(
            SegmentSummary("AD01", match_count=2, mismatch_count=0, total_in_a=3, total_in_b=3),
            SegmentSummary("NM01", match_count=2, mismatch_count=1, total_in_a=3, total_in_b=3),
        ),
        start_time="2026-05-28T01:00:00+00:00",
        end_time="2026-05-28T01:00:05+00:00",
        elapsed_seconds=5.0,
        throughput_records_per_sec=0.8,
        config_paths={
            "layout_a": "/cfg/layout_file_A.json",
            "layout_b": "/cfg/layout_file_B.json",
            "runtime": "/cfg/runtime.json",
        },
        config_audit_hash="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        engine_version="0.0.1",
        filename_stamp="202605280100",
    )


def test_compare_reports_csv_has_section_key_value_header(tmp_path: Path) -> None:
    path = tmp_path / "reports.csv"
    write_compare_reports_csv(_multi_segment_summary(tmp_path), path)
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first == "section,key,value"


def test_compare_reports_csv_covers_every_summary_section(tmp_path: Path) -> None:
    path = tmp_path / "reports.csv"
    write_compare_reports_csv(_multi_segment_summary(tmp_path), path)
    text = path.read_text(encoding="utf-8")

    # Every documented section must appear.
    for section in ("run", "inputs", "counts", "per_segment", "timing", "config_paths"):
        assert f"\n{section}," in "\n" + text, f"missing section: {section}"

    # Key scalars are pinned with full row content.
    assert "counts,records_matched,2\n" in text
    assert "counts,records_mismatched,1\n" in text
    assert "counts,dups_in_b,1\n" in text
    assert "inputs,file_a_record_count,4\n" in text
    assert "inputs,file_b_record_count,5\n" in text
    assert "run,filename_stamp,202605280100\n" in text

    # Per-segment rows for both segments, all four stats each.
    for seg in ("AD01", "NM01"):
        for stat in ("match_count", "mismatch_count", "total_in_a", "total_in_b"):
            assert f"per_segment,{seg}.{stat}," in text, f"missing per_segment row {seg}.{stat}"

    # Config-paths rows preserve the known layout_a / layout_b / runtime order.
    rows = text.splitlines()
    cp_rows = [r for r in rows if r.startswith("config_paths,")]
    assert cp_rows == [
        "config_paths,layout_a,/cfg/layout_file_A.json",
        "config_paths,layout_b,/cfg/layout_file_B.json",
        "config_paths,runtime,/cfg/runtime.json",
    ]


def test_compare_reports_csv_round_trips_via_csv_module(tmp_path: Path) -> None:
    """The file must parse back via csv.reader without errors and yield the expected row count."""
    import csv

    path = tmp_path / "reports.csv"
    write_compare_reports_csv(_multi_segment_summary(tmp_path), path)
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    header, *body = rows
    assert header == ["section", "key", "value"]
    # 3 run rows + 6 input rows + 7 count rows + 8 per-segment rows (2 segs × 4 stats)
    # + 4 timing rows + 3 config-path rows = 31
    assert len(body) == 31


def test_compare_reports_html_is_self_contained_and_well_formed(tmp_path: Path) -> None:
    path = tmp_path / "reports.html"
    write_compare_reports_html(_multi_segment_summary(tmp_path), path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert "</html>" in text
    # No external CSS or JS — everything must be inline.
    assert "<link " not in text
    assert "<script" not in text
    # Section headings present.
    for heading in (
        "Inputs",
        "Aggregate counts",
        "Per-segment breakdown",
        "Timing",
        "Config provenance",
    ):
        assert heading in text, f"missing heading: {heading}"


def test_compare_reports_html_renders_metric_values(tmp_path: Path) -> None:
    path = tmp_path / "reports.html"
    write_compare_reports_html(_multi_segment_summary(tmp_path), path)
    text = path.read_text(encoding="utf-8")
    # Aggregate counts surface as thousand-separated cell contents.
    assert ">2<" in text  # records_matched
    assert ">1<" in text  # records_mismatched / others
    # Segment names from per_segment table.
    assert "AD01" in text
    assert "NM01" in text
    # Throughput formatted with thousands + 1 decimal.
    assert "0.8 records/s" in text
    # Filename stamp visible in the subhead.
    assert "202605280100" in text


def test_compare_reports_html_escapes_dangerous_characters(tmp_path: Path) -> None:
    """Path-like strings must be HTML-escaped so they cannot break the markup."""
    from dataclasses import replace

    s = replace(
        _multi_segment_summary(tmp_path),
        config_paths={"layout_a": "/cfg/<script>alert(1)</script>"},
    )
    path = tmp_path / "reports.html"
    write_compare_reports_html(s, path)
    text = path.read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>alert(1)</script>" not in text


def test_outputwriter_finalize_emits_csv_and_html_alongside_summary_json(
    tmp_path: Path,
) -> None:
    """finalize must produce summary.json + compare_reports.csv + compare_reports.html."""
    out = tmp_path / "out"
    stamp = "202605280100"
    with OutputWriter(out, SEGMENTS_CFG, filename_stamp=stamp) as w:
        w.finalize(_multi_segment_summary(tmp_path))
    assert (out / stamped_filename(SUMMARY_FILE, stamp)).exists()
    assert (out / stamped_filename(COMPARE_REPORTS_CSV_FILE, stamp)).exists()
    assert (out / stamped_filename(COMPARE_REPORTS_HTML_FILE, stamp)).exists()
