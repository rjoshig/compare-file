"""Tests for ``segment_compare.writer``."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from segment_compare.comparator import RecordVerdict, SegmentVerdict
from segment_compare.parser import Record, Segment, SegmentsConfig
from segment_compare.layout import load_file_layout
from segment_compare.writer import (
    COMPARE_REPORTS_CSV_FILE,
    COMPARE_REPORTS_HTML_FILE,
    DUPS_A_FILE,
    DUPS_B_FILE,
    KEY_MATRIX_FILE,
    KEYMISMATCH_A_FILE,
    KEYMISMATCH_B_FILE,
    MATCHES_FILE,
    MISMATCHES_FILE,
    REPORT_FILE,
    SUMMARY_FILE,
    CompareReports,
    KeyMatrixEntry,
    OutputWriter,
    SegmentSummary,
    Summary,
    stamped_filename,
    write_compare_reports_csv,
    write_compare_reports_html,
    write_keys_mismatch_matrix_csv,
)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

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
        w.finalize(_reports(summary, out))
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
    w.finalize(_reports(summary, out))
    # After finalize, writing should fail because handles are closed.
    with pytest.raises(ValueError):
        w.write_match(_record("K1", b"AAAA"))


def test_summary_json_is_pretty_printed(tmp_path: Path) -> None:
    out = tmp_path / "out"
    summary = _summary(tmp_path)
    with OutputWriter(out, SEGMENTS_CFG) as w:
        w.finalize(_reports(summary, out))
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
        w.finalize(_reports(_summary(tmp_path), out))

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


def _reports(
    summary: Summary,
    output_dir: Path,
    key_matrix_entries: tuple[KeyMatrixEntry, ...] = (),
    matrix_segments: tuple[str, ...] = ("TU4R", "NM01", "ENDS"),
) -> CompareReports:
    """Wrap a Summary into a CompareReports using the committed layouts."""
    return CompareReports(
        summary=summary,
        layout_a=load_file_layout(CONFIG_DIR / "layout_file_A.json"),
        layout_b=load_file_layout(CONFIG_DIR / "layout_file_B.json"),
        key_matrix_entries=key_matrix_entries,
        matrix_segments=matrix_segments,
        output_dir=output_dir,
    )


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


def _sample_matrix_entries() -> tuple[KeyMatrixEntry, ...]:
    return (
        KeyMatrixEntry(
            key="KEY000000003",
            segment_status={"TU4R": "Y", "NM01": "N", "AD01": "Y", "ENDS": "Y"},
            segment_count_diffs=(),
        ),
        KeyMatrixEntry(
            key="KEY000000005",
            segment_status={"TU4R": "Y", "TR01": "N", "ENDS": "Y"},
            segment_count_diffs=("TR01",),
        ),
    )


def test_compare_reports_csv_has_section_key_value_header(tmp_path: Path) -> None:
    path = tmp_path / "reports.csv"
    summary = _multi_segment_summary(tmp_path)
    write_compare_reports_csv(_reports(summary, tmp_path), path)
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first == "section,key,value"


def test_compare_reports_csv_covers_every_summary_section(tmp_path: Path) -> None:
    path = tmp_path / "reports.csv"
    summary = _multi_segment_summary(tmp_path)
    write_compare_reports_csv(_reports(summary, tmp_path), path)
    text = path.read_text(encoding="utf-8")

    # Every documented section must appear (output_files is new in ADR-036).
    for section in (
        "run",
        "inputs",
        "counts",
        "per_segment",
        "output_files",
        "timing",
        "config_paths",
    ):
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

    # New ADR-036 rows: every count metric paired with its stamped output file.
    # ADR-037: output_files rows carry bare filenames; every run's files
    # live inside its own per-run subdirectory so disambiguation by stamp
    # in the filenames is no longer needed.
    assert "output_files,records_matched,matches.dat\n" in text
    assert "output_files,records_mismatched,mismatches.dat\n" in text
    assert "output_files,dups_in_a,dups_A.dat\n" in text
    assert "output_files,keys_mismatch_matrix,keys_mismatch_matrix.csv\n" in text

    # Config-paths rows preserve the known layout_a / layout_b / runtime order.
    rows = text.splitlines()
    cp_rows = [r for r in rows if r.startswith("config_paths,")]
    assert cp_rows == [
        "config_paths,layout_a,/cfg/layout_file_A.json",
        "config_paths,layout_b,/cfg/layout_file_B.json",
        "config_paths,runtime,/cfg/runtime.json",
    ]


def test_compare_reports_csv_round_trips_via_csv_module(tmp_path: Path) -> None:
    """The file must parse back via csv.reader and yield the expected row count."""
    import csv

    path = tmp_path / "reports.csv"
    summary = _multi_segment_summary(tmp_path)
    write_compare_reports_csv(_reports(summary, tmp_path), path)
    with path.open("r", encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    header, *body = rows
    assert header == ["section", "key", "value"]
    # 3 run + 6 inputs + 7 counts + 8 per_segment + 9 output_files
    # (6 metric→file + report + summary + key_matrix) + 4 timing + 3 config_paths = 40
    assert len(body) == 40


def test_compare_reports_html_is_self_contained_and_well_formed(tmp_path: Path) -> None:
    path = tmp_path / "reports.html"
    summary = _multi_segment_summary(tmp_path)
    write_compare_reports_html(_reports(summary, tmp_path), path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<!DOCTYPE html>")
    assert "</html>" in text
    # Inline CSS only; any <link> is the Google Fonts stylesheet (progressive
    # enhancement — the file still renders fine offline with system fonts).
    # Any <script> is the inline theme-toggle helper (~25 lines). No external
    # JS bundles must be loaded.
    for link in re.findall(r"<link [^>]*>", text):
        assert (
            "fonts.googleapis.com" in link or "fonts.gstatic.com" in link
        ), f"unexpected external link: {link}"
    assert "<script src=" not in text
    # Section headings present (Layouts and Per-key mismatch sample are new).
    for heading in (
        "Layouts",
        "Inputs",
        "Aggregate counts",
        "Per-segment breakdown",
        "Per-key mismatch sample",
        "Timing",
        "Config provenance",
    ):
        assert heading in text, f"missing heading: {heading}"


def test_compare_reports_html_layouts_section_is_side_by_side(tmp_path: Path) -> None:
    """The Layouts section should render File A and File B in two flex columns."""
    path = tmp_path / "reports.html"
    summary = _multi_segment_summary(tmp_path)
    write_compare_reports_html(_reports(summary, tmp_path), path)
    text = path.read_text(encoding="utf-8")
    # Side-by-side container present.
    assert 'class="sxs"' in text
    # Both layouts addressed by name.
    assert "File A — overview" in text
    assert "File B — overview" in text
    # Committed layouts both target TU4R as the key segment and ENDS as end.
    assert text.count("TU4R") >= 4  # appears in each layout meta + segments table
    assert text.count("ENDS") >= 4


def test_compare_reports_html_aggregate_counts_has_description_column(
    tmp_path: Path,
) -> None:
    """Aggregate counts row carries a small-font description column."""
    path = tmp_path / "reports.html"
    summary = _multi_segment_summary(tmp_path)
    write_compare_reports_html(_reports(summary, tmp_path), path)
    text = path.read_text(encoding="utf-8")

    aggregate_section = text.split("<h2>Aggregate counts</h2>")[1].split("</table>")[0]
    # Description renders as a dedicated column with the small-font .desc class.
    assert "<th>Description</th>" in aggregate_section
    assert "class='desc'>Records found in both files with identical content." in aggregate_section
    assert "class='desc'>Records found only in File A, not in File B." in aggregate_section
    # No jargon leak in description text.
    for jargon in ("hash", "multiset", "inner-join", "ADR-019"):
        assert (
            jargon not in aggregate_section
        ), f"jargon {jargon!r} leaked into Aggregate counts descriptions"


def test_compare_reports_html_aggregate_counts_link_to_output_files(tmp_path: Path) -> None:
    """Each count metric's row must include a clickable link to its bare output file."""
    path = tmp_path / "reports.html"
    summary = _multi_segment_summary(tmp_path)
    write_compare_reports_html(_reports(summary, tmp_path), path)
    text = path.read_text(encoding="utf-8")
    # ADR-037: links carry bare names since the HTML lives in the per-run dir.
    for fragment in (
        "matches.dat",
        "mismatches.dat",
        "keymismatch_A.dat",
        "keymismatch_B.dat",
        "dups_A.dat",
        "dups_B.dat",
    ):
        assert f"href='{fragment}'" in text, f"missing link to {fragment}"


def test_compare_reports_html_per_key_sample_renders_and_links_to_full_matrix(
    tmp_path: Path,
) -> None:
    path = tmp_path / "reports.html"
    summary = _multi_segment_summary(tmp_path)
    reports = _reports(
        summary,
        tmp_path,
        key_matrix_entries=_sample_matrix_entries(),
        matrix_segments=("TU4R", "NM01", "AD01", "TR01", "ENDS"),
    )
    write_compare_reports_html(reports, path)
    text = path.read_text(encoding="utf-8")
    # Both sample keys present.
    assert "KEY000000003" in text
    assert "KEY000000005" in text
    # Y/N cells render with their CSS classes.
    assert "class='y'>Y<" in text
    assert "class='n'>N<" in text
    # segment_count_mismatch pipe-delimited entry.
    assert ">TR01<" in text
    # Sample-note link points at the bare matrix file (ADR-037).
    assert "href='keys_mismatch_matrix.csv'" in text


def test_compare_reports_html_renders_metric_values(tmp_path: Path) -> None:
    path = tmp_path / "reports.html"
    summary = _multi_segment_summary(tmp_path)
    write_compare_reports_html(_reports(summary, tmp_path), path)
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

    summary = replace(
        _multi_segment_summary(tmp_path),
        config_paths={"layout_a": "/cfg/<script>alert(1)</script>"},
    )
    path = tmp_path / "reports.html"
    write_compare_reports_html(_reports(summary, tmp_path), path)
    text = path.read_text(encoding="utf-8")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "<script>alert(1)</script>" not in text


def test_outputwriter_finalize_emits_all_four_report_files(tmp_path: Path) -> None:
    """finalize must produce summary.json + compare_reports.csv + compare_reports.html + matrix."""
    out = tmp_path / "out"
    stamp = "202605280100"
    summary = _multi_segment_summary(tmp_path)
    with OutputWriter(out, SEGMENTS_CFG, filename_stamp=stamp) as w:
        w.finalize(_reports(summary, out))
    assert (out / stamped_filename(SUMMARY_FILE, stamp)).exists()
    assert (out / stamped_filename(COMPARE_REPORTS_CSV_FILE, stamp)).exists()
    assert (out / stamped_filename(COMPARE_REPORTS_HTML_FILE, stamp)).exists()
    assert (out / stamped_filename(KEY_MATRIX_FILE, stamp)).exists()


# ---------------------------------------------------------------------------
# keys_mismatch_matrix.csv (ADR-036)
# ---------------------------------------------------------------------------


def test_keys_mismatch_matrix_has_expected_header(tmp_path: Path) -> None:
    summary = _multi_segment_summary(tmp_path)
    reports = _reports(summary, tmp_path, matrix_segments=("TU4R", "NM01", "ENDS"))
    path = tmp_path / "matrix.csv"
    write_keys_mismatch_matrix_csv(reports, path)
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header == "key,TU4R,NM01,ENDS,segment_count_mismatch"


def test_keys_mismatch_matrix_renders_each_entry(tmp_path: Path) -> None:
    summary = _multi_segment_summary(tmp_path)
    reports = _reports(
        summary,
        tmp_path,
        key_matrix_entries=_sample_matrix_entries(),
        matrix_segments=("TU4R", "NM01", "AD01", "TR01", "ENDS"),
    )
    path = tmp_path / "matrix.csv"
    write_keys_mismatch_matrix_csv(reports, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "KEY000000003,Y,N,Y,,Y,"
    assert lines[2] == "KEY000000005,Y,,,N,Y,TR01"


def test_keys_mismatch_matrix_empty_when_no_mismatches(tmp_path: Path) -> None:
    summary = _multi_segment_summary(tmp_path)
    reports = _reports(summary, tmp_path)
    path = tmp_path / "matrix.csv"
    write_keys_mismatch_matrix_csv(reports, path)
    # Only the header row.
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
