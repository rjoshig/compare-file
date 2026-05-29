"""Output file writer.

Owns the file handles for all eight Phase 1 outputs (ADR-023) and the
serialization of :class:`Summary` to ``summary.json``. Use as a
context manager so handles are released even on failure.

Output files (in the supplied output directory):

- ``matches.dat`` — File A's raw bytes for every matched record.
- ``mismatches.dat`` — diagnostic side-by-side blocks for each
  mismatched record.
- ``keymismatch_A.dat`` / ``keymismatch_B.dat`` — records whose keys
  appear only in one source file.
- ``dups_A.dat`` / ``dups_B.dat`` — records with duplicate keys,
  pulled before the inner-join (ADR-019).
- ``report.csv`` — one row per mismatched segment-type per record:
  ``key,segment_name,status,a_count,b_count``.
- ``summary.json`` — aggregated metrics and run metadata
  (machine-readable source of truth).
- ``compare_reports.csv`` — the same aggregates rendered as a
  3-column long-format CSV (``section,key,value``) so operators can
  open the run summary in a spreadsheet without flattening JSON by
  hand (ADR-035).
- ``compare_reports.html`` — the same aggregates rendered as a
  self-contained HTML report (inline CSS, no external assets) for
  human review in a browser (ADR-035 / ADR-036).
- ``keys_mismatch_matrix.csv`` — per-key Y/N matrix of which
  segments mismatched (one row per joined-key record where at least
  one segment failed; fully-matched keys omitted). Columns: ``key``
  + one per known segment + ``segment_count_mismatch`` (pipe-
  delimited list of segments whose count differs between A and B).
  See ADR-036.
"""

from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import TracebackType
from typing import IO, Any

from segment_compare.comparator import STATUS_COUNT_DIFF, RecordVerdict
from segment_compare.layout import FileLayout
from segment_compare.parser import Record, SegmentsConfig

MATCHES_FILE = "matches.dat"
MISMATCHES_FILE = "mismatches.dat"
KEYMISMATCH_A_FILE = "keymismatch_A.dat"
KEYMISMATCH_B_FILE = "keymismatch_B.dat"
DUPS_A_FILE = "dups_A.dat"
DUPS_B_FILE = "dups_B.dat"
REPORT_FILE = "report.csv"
SUMMARY_FILE = "summary.json"
COMPARE_REPORTS_CSV_FILE = "compare_reports.csv"
COMPARE_REPORTS_HTML_FILE = "compare_reports.html"
KEY_MATRIX_FILE = "keys_mismatch_matrix.csv"

# Maps each aggregate-count metric to the output file where the
# corresponding records can be found. Used by the HTML report to
# render the counts table with a clickable file column.
METRIC_TO_FILE: dict[str, str] = {
    "records_matched": MATCHES_FILE,
    "records_mismatched": MISMATCHES_FILE,
    "keys_in_a_only": KEYMISMATCH_A_FILE,
    "keys_in_b_only": KEYMISMATCH_B_FILE,
    "dups_in_a": DUPS_A_FILE,
    "dups_in_b": DUPS_B_FILE,
}

REPORT_HEADER = ("key", "segment_name", "status", "a_count", "b_count")

STAMP_FORMAT = "%Y%m%d%H%M"
# Per-run output directory format (ADR-037). Each run lives in its own
# subdirectory under the operator's --output-dir; files inside are written
# with bare names since the directory provides the disambiguation.
RUN_DIR_FORMAT = "report-%Y-%m-%d-%H-%M-%S"

# matches.dat is sampled: only the first N matched records get written
# (ADR-038). The aggregate counts in summary.json continue to reflect
# the true number of matched records; only the on-disk *.dat content is
# truncated. mismatches.dat is unaffected — it keeps the full set.
MATCHES_SAMPLE_SIZE = 10


def stamped_filename(base: str, stamp: str) -> str:
    """Inject a timestamp into a base output filename.

    Args:
        base: The base filename (e.g., ``"matches.dat"``).
        stamp: The stamp to inject (e.g., ``"202605272239"``). Empty
            string leaves the filename unchanged.

    Returns:
        ``base`` if ``stamp`` is empty; otherwise ``<stem>_<stamp>.<ext>``.
        Filenames with no extension get the stamp appended after an
        underscore.
    """
    if not stamp:
        return base
    stem, dot, ext = base.rpartition(".")
    if not dot:
        return f"{base}_{stamp}"
    return f"{stem}_{stamp}.{ext}"


@dataclass(frozen=True, slots=True)
class SegmentSummary:
    """Per-segment-type aggregate over the whole run.

    Attributes:
        segment_name: The segment type.
        match_count: Number of joined records in which this segment
            type matched.
        mismatch_count: Number of joined records in which this segment
            type mismatched.
        total_in_a: Total occurrences across all records in File A.
        total_in_b: Total occurrences across all records in File B.
    """

    segment_name: str
    match_count: int
    mismatch_count: int
    total_in_a: int
    total_in_b: int


@dataclass(frozen=True, slots=True)
class KeyMatrixEntry:
    """One row of the per-key mismatch matrix (ADR-036).

    Built once per joined-key record that didn't fully match.
    Fully-matched records are not represented — the matrix is a
    mismatch-only projection.

    Attributes:
        key: The joined record key.
        segment_status: ``segment_name -> "Y" | "N"``. ``"Y"`` if the
            segment's hash multiset matched between A and B in this
            record; ``"N"`` if it did not. Segments not present in
            either A's or B's record for this key are absent from the
            mapping (the matrix renders them as empty cells).
        segment_count_diffs: Segment names with ``status == count_diff``
            for this record (A and B have a different number of
            instances of that segment type). Pipe-delimited in the CSV
            output.
    """

    key: str
    segment_status: dict[str, str]
    segment_count_diffs: tuple[str, ...]


def build_key_matrix_entry(verdict: RecordVerdict) -> KeyMatrixEntry:
    """Project a :class:`RecordVerdict` into a :class:`KeyMatrixEntry`.

    Returns an entry for any verdict where at least one segment
    mismatched. (For fully-matched verdicts the caller should skip
    creating the entry; this helper does not enforce that, but the
    matrix file omits matched records.)
    """
    status: dict[str, str] = {}
    count_diffs: list[str] = []
    for sv in verdict.segment_verdicts:
        status[sv.segment_name] = "Y" if sv.matched else "N"
        if sv.status == STATUS_COUNT_DIFF:
            count_diffs.append(sv.segment_name)
    return KeyMatrixEntry(
        key=verdict.key,
        segment_status=status,
        segment_count_diffs=tuple(count_diffs),
    )


@dataclass(frozen=True, slots=True)
class Summary:
    """Run summary serialized to ``summary.json``.

    All fields are populated by the pipeline and handed to
    :meth:`OutputWriter.finalize`.
    """

    file_a_path: Path
    file_b_path: Path
    file_a_size_bytes: int
    file_b_size_bytes: int
    file_a_record_count: int
    file_b_record_count: int
    keys_in_a_only: int
    keys_in_b_only: int
    keys_in_both: int
    dups_in_a: int
    dups_in_b: int
    records_matched: int
    records_mismatched: int
    per_segment: tuple[SegmentSummary, ...]
    start_time: str
    end_time: str
    elapsed_seconds: float
    throughput_records_per_sec: float
    config_paths: dict[str, str] = field(default_factory=dict)
    config_audit_hash: str = ""
    engine_version: str = ""
    filename_stamp: str = ""


@dataclass(frozen=True, slots=True)
class CompareReports:
    """Bundle of everything the report-writing functions need (ADR-036).

    Attributes:
        summary: The run :class:`Summary` (machine-readable metrics).
        layout_a: File A's loaded layout — rendered side-by-side in
            the HTML report.
        layout_b: File B's loaded layout.
        key_matrix_entries: Mismatch-only per-key entries (sorted by
            key, mirroring the join order). Used for both
            ``keys_mismatch_matrix.csv`` (full file) and the HTML
            report's "Per-key mismatch sample" section.
        matrix_segments: Union of segment names across both layouts in
            stable order — the column order for the matrix CSV.
        output_dir: Resolved run output directory. The HTML's clickable
            file links resolve relative to this so the HTML works when
            opened directly from disk.
    """

    summary: Summary
    layout_a: FileLayout
    layout_b: FileLayout
    key_matrix_entries: tuple[KeyMatrixEntry, ...]
    matrix_segments: tuple[str, ...]
    output_dir: Path


class OutputWriter:
    """Writes the eight Phase 1 output files.

    Open the writer (preferably as a context manager), call the per-
    record write methods as the pipeline produces verdicts, then call
    :meth:`finalize` with the run :class:`Summary`. The context manager
    closes all handles on exit even if :meth:`finalize` was not called.
    """

    __slots__ = (
        "_output_dir",
        "_delimiter",
        "_handles",
        "_report_writer",
        "_closed",
        "_filename_stamp",
    )

    def __init__(
        self,
        output_dir: Path,
        segments_cfg: SegmentsConfig,
        filename_stamp: str = "",
    ) -> None:
        """Initialize and open all eight output files for writing.

        Args:
            output_dir: Directory to write outputs into. Created if it
                does not exist.
            segments_cfg: Used to learn the record delimiter (the same
                delimiter used in the source files is appended after
                every record written to ``*.dat``).
            filename_stamp: Optional ``YYYYMMDDHHMM`` (or any) suffix
                injected into every output filename so concurrent or
                successive runs don't clobber each other. Empty string
                (the default) uses the bare filenames.
        """
        self._output_dir = output_dir
        self._delimiter = segments_cfg.record_delimiter
        self._filename_stamp = filename_stamp
        self._handles: dict[str, IO[bytes]] = {}
        # csv.writer is a factory function, not a class; the returned object
        # has no public type. Annotate as Any.
        self._report_writer: Any = None
        self._closed = False

        output_dir.mkdir(parents=True, exist_ok=True)

        for name in (
            MATCHES_FILE,
            MISMATCHES_FILE,
            KEYMISMATCH_A_FILE,
            KEYMISMATCH_B_FILE,
            DUPS_A_FILE,
            DUPS_B_FILE,
        ):
            self._handles[name] = (output_dir / self._on_disk(name)).open("wb")

        # csv.writer needs text mode; keep it separate from the binary handles.
        report_path = output_dir / self._on_disk(REPORT_FILE)
        report_handle = report_path.open("w", encoding="utf-8", newline="")
        self._handles[REPORT_FILE] = report_handle  # type: ignore[assignment]
        self._report_writer = csv.writer(report_handle)
        self._report_writer.writerow(REPORT_HEADER)

    def _on_disk(self, base: str) -> str:
        """Resolve a base filename to its on-disk name, honoring the stamp."""
        return stamped_filename(base, self._filename_stamp)

    def path_for(self, base: str) -> Path:
        """Return the resolved on-disk :class:`Path` for one output file.

        Useful for tests and callers that need to read the file after the
        writer is closed without recomputing the stamp.
        """
        return self._output_dir / self._on_disk(base)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "OutputWriter":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Close all open file handles. Safe to call multiple times."""
        if self._closed:
            return
        for handle in self._handles.values():
            handle.close()
        self._closed = True

    # ------------------------------------------------------------------
    # Per-record writes
    # ------------------------------------------------------------------

    def write_match(self, record_a: Record) -> None:
        """Write a matched record to ``matches.dat`` (A's bytes only).

        Per ADR-010, only File A's bytes are emitted because the
        records are equivalent after normalization.
        """
        self._write_record(MATCHES_FILE, record_a)

    def write_mismatch(
        self,
        verdict: RecordVerdict,
        record_a: Record,
        record_b: Record,
    ) -> None:
        """Write a mismatched record (side-by-side) + report.csv rows.

        Args:
            verdict: The :class:`RecordVerdict` from the comparator.
            record_a: The source-A record.
            record_b: The source-B record.
        """
        mismatched = verdict.mismatched_segments
        header = f"=== KEY: {verdict.key} | MISMATCH: {', '.join(mismatched)} ===\n"
        handle = self._handles[MISMATCHES_FILE]
        handle.write(header.encode("ascii"))
        handle.write(b"--- FILE A ---\n")
        handle.write(record_a.raw)
        handle.write(b"\n")
        handle.write(b"--- FILE B ---\n")
        handle.write(record_b.raw)
        handle.write(b"\n\n")

        assert self._report_writer is not None  # set in __init__
        for sv in verdict.segment_verdicts:
            if sv.matched:
                continue
            self._report_writer.writerow(
                (verdict.key, sv.segment_name, sv.status, sv.a_count, sv.b_count)
            )

    def write_key_only_a(self, record_a: Record) -> None:
        """Write a record whose key appears only in File A."""
        self._write_record(KEYMISMATCH_A_FILE, record_a)

    def write_key_only_b(self, record_b: Record) -> None:
        """Write a record whose key appears only in File B."""
        self._write_record(KEYMISMATCH_B_FILE, record_b)

    def write_dup_a(self, record_a: Record) -> None:
        """Write a duplicate-key record pulled from File A (ADR-019)."""
        self._write_record(DUPS_A_FILE, record_a)

    def write_dup_b(self, record_b: Record) -> None:
        """Write a duplicate-key record pulled from File B (ADR-019)."""
        self._write_record(DUPS_B_FILE, record_b)

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    def finalize(self, reports: CompareReports) -> None:
        """Write ``summary.json`` + the three human reports and close all handles.

        Emits, in this order:

        - ``summary.json`` (machine-readable, ADR-023)
        - ``compare_reports.csv`` (3-column long-format, ADR-035)
        - ``compare_reports.html`` (self-contained HTML, ADR-036)
        - ``keys_mismatch_matrix.csv`` (per-key Y/N matrix, ADR-036)

        The four files all carry the same stamp suffix used by the
        binary outputs so a single run lands as a self-contained
        bundle on disk.
        """
        write_summary(reports.summary, self._output_dir / self._on_disk(SUMMARY_FILE))
        write_compare_reports_csv(
            reports, self._output_dir / self._on_disk(COMPARE_REPORTS_CSV_FILE)
        )
        write_compare_reports_html(
            reports, self._output_dir / self._on_disk(COMPARE_REPORTS_HTML_FILE)
        )
        write_keys_mismatch_matrix_csv(reports, self._output_dir / self._on_disk(KEY_MATRIX_FILE))
        self.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_record(self, filename: str, record: Record) -> None:
        handle = self._handles[filename]
        handle.write(record.raw)
        if self._delimiter:
            handle.write(self._delimiter)


def _summary_to_json(summary: Summary) -> dict[str, object]:
    """Convert :class:`Summary` to a JSON-serializable dict.

    Paths become strings, tuples become lists, nested dataclasses
    flatten via ``asdict``.
    """
    out = asdict(summary)
    out["file_a_path"] = str(summary.file_a_path)
    out["file_b_path"] = str(summary.file_b_path)
    out["per_segment"] = [asdict(s) for s in summary.per_segment]
    return out


def write_summary(summary: Summary, path: Path) -> None:
    """Write a :class:`Summary` to ``path`` in the canonical JSON form.

    Used by :meth:`OutputWriter.finalize` and by the parallel
    pipeline's master process (which has no live :class:`OutputWriter`
    by the time it has all the data it needs to build a summary).
    """
    with path.open("w", encoding="utf-8") as fh:
        json.dump(_summary_to_json(summary), fh, indent=2, sort_keys=True)
        fh.write("\n")


# ---------------------------------------------------------------------------
# Human reports (ADR-035 / ADR-036) — CSV + HTML alongside summary.json
# ---------------------------------------------------------------------------

# Order matters: section names group related metrics, and within each
# section the rows appear in this declaration order so the file diffs
# predictably across runs with identical inputs.
_CONFIG_PATH_ORDER = ("layout_a", "layout_b", "runtime")

# How many matrix rows to embed inline in the HTML report. The full
# matrix lives in keys_mismatch_matrix.csv.
_HTML_KEY_MATRIX_SAMPLE_SIZE = 20


def write_compare_reports_csv(reports: CompareReports, path: Path) -> None:
    """Render a :class:`CompareReports` as a 3-column long-format CSV (ADR-035).

    Columns are ``section,key,value``. Sections (in order): ``run``,
    ``inputs``, ``counts``, ``per_segment``, ``output_files``,
    ``timing``, ``config_paths``. Per-segment metrics use
    ``<segment>.<stat>`` style keys so a single segment's four numbers
    stay grouped.

    Opens cleanly in any spreadsheet (Excel, Google Sheets, Numbers)
    and is trivially filterable with ``awk`` / ``grep``.
    """
    summary = reports.summary

    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(("section", "key", "value"))

        # Run identity
        w.writerow(("run", "filename_stamp", summary.filename_stamp))
        w.writerow(("run", "engine_version", summary.engine_version))
        w.writerow(("run", "config_audit_hash", summary.config_audit_hash))

        # Inputs
        w.writerow(("inputs", "file_a_path", str(summary.file_a_path)))
        w.writerow(("inputs", "file_b_path", str(summary.file_b_path)))
        w.writerow(("inputs", "file_a_size_bytes", summary.file_a_size_bytes))
        w.writerow(("inputs", "file_b_size_bytes", summary.file_b_size_bytes))
        w.writerow(("inputs", "file_a_record_count", summary.file_a_record_count))
        w.writerow(("inputs", "file_b_record_count", summary.file_b_record_count))

        # Aggregate counts
        w.writerow(("counts", "records_matched", summary.records_matched))
        w.writerow(("counts", "records_mismatched", summary.records_mismatched))
        w.writerow(("counts", "keys_in_both", summary.keys_in_both))
        w.writerow(("counts", "keys_in_a_only", summary.keys_in_a_only))
        w.writerow(("counts", "keys_in_b_only", summary.keys_in_b_only))
        w.writerow(("counts", "dups_in_a", summary.dups_in_a))
        w.writerow(("counts", "dups_in_b", summary.dups_in_b))

        # Per-segment (alphabetical via the Summary's existing ordering)
        for seg in summary.per_segment:
            w.writerow(("per_segment", f"{seg.segment_name}.match_count", seg.match_count))
            w.writerow(("per_segment", f"{seg.segment_name}.mismatch_count", seg.mismatch_count))
            w.writerow(("per_segment", f"{seg.segment_name}.total_in_a", seg.total_in_a))
            w.writerow(("per_segment", f"{seg.segment_name}.total_in_b", seg.total_in_b))

        # Output files — each count metric paired with the file that
        # holds the corresponding records. Bare names since every output
        # for one run lives inside the per-run subdir (ADR-037).
        for metric, base in METRIC_TO_FILE.items():
            w.writerow(("output_files", metric, base))
        w.writerow(("output_files", "report", REPORT_FILE))
        w.writerow(("output_files", "summary", SUMMARY_FILE))
        w.writerow(("output_files", "keys_mismatch_matrix", KEY_MATRIX_FILE))

        # Timing
        w.writerow(("timing", "start_time", summary.start_time))
        w.writerow(("timing", "end_time", summary.end_time))
        w.writerow(("timing", "elapsed_seconds", summary.elapsed_seconds))
        w.writerow(("timing", "throughput_records_per_sec", summary.throughput_records_per_sec))

        # Config provenance — preserve known-order before any extras
        seen: set[str] = set()
        for kind in _CONFIG_PATH_ORDER:
            if kind in summary.config_paths:
                w.writerow(("config_paths", kind, summary.config_paths[kind]))
                seen.add(kind)
        for kind, p in summary.config_paths.items():
            if kind not in seen:
                w.writerow(("config_paths", kind, p))


def write_keys_mismatch_matrix_csv(reports: CompareReports, path: Path) -> None:
    """Write the per-key mismatch matrix to ``path`` (ADR-036).

    Columns: ``key``, one column per segment in
    ``reports.matrix_segments`` (in declared order), then
    ``segment_count_mismatch``. Cells carry ``"Y"`` (matched in this
    record), ``"N"`` (mismatched), or empty (segment absent from
    both sides for this key). The trailing column is a pipe-delimited
    list of segments where A and B had different counts for this key
    (status=``count_diff``).

    Only mismatched-key rows are written — fully-matched records are
    intentionally omitted to keep the file small and focused.
    """
    columns = list(reports.matrix_segments)
    header = ["key"] + columns + ["segment_count_mismatch"]
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        for entry in reports.key_matrix_entries:
            row: list[str] = [entry.key]
            for col in columns:
                row.append(entry.segment_status.get(col, ""))
            row.append("|".join(entry.segment_count_diffs))
            w.writerow(row)


def write_compare_reports_html(reports: CompareReports, path: Path) -> None:
    """Render a :class:`CompareReports` as a self-contained HTML report (ADR-036).

    All CSS is inline; no external assets are referenced. Sections (in
    order): Layouts (File A and File B side-by-side), Inputs
    (side-by-side columns), Aggregate counts (with clickable
    file-path links), Per-segment breakdown, Per-key mismatch sample
    (first ~20 rows from ``keys_mismatch_matrix.csv``), Timing, and
    Config provenance.
    """
    summary = reports.summary
    stamp = summary.filename_stamp
    e = html.escape

    layouts_html = _render_layouts_side_by_side(reports.layout_a, reports.layout_b, e)
    inputs_html = _render_inputs_side_by_side(summary, e)
    counts_html = _render_aggregate_counts(summary, stamp, e)
    per_segment_html = _render_per_segment(summary, e)
    matrix_sample_html = _render_key_matrix_sample(
        reports.key_matrix_entries, reports.matrix_segments, stamp, e
    )
    timing_html = _render_timing(summary, e)
    config_paths_html = _render_config_paths(summary, e)

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Compare report — {e(stamp)}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    max-width: 1180px; margin: 2em auto; padding: 0 1em; color: #222;
  }}
  h1 {{ font-size: 1.6em; margin-bottom: 0.2em; }}
  .subhead {{ color: #666; font-size: 0.9em; margin-bottom: 1.5em; }}
  h2 {{
    font-size: 1.1em; margin-top: 2.2em; margin-bottom: 0.4em;
    border-bottom: 1px solid #ddd; padding-bottom: 0.25em;
  }}
  h3 {{ font-size: 0.95em; margin: 1em 0 0.4em 0; color: #444; }}
  table {{
    border-collapse: collapse; margin-top: 0.4em; width: 100%;
    font-size: 0.92em;
  }}
  th, td {{
    padding: 0.4em 0.7em; text-align: left;
    border-bottom: 1px solid #eee; vertical-align: top;
  }}
  th {{ background: #f6f6f6; font-weight: 600; }}
  tr:nth-child(even) td {{ background: #fafafa; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .match {{ color: #1f7a1f; font-weight: 600; }}
  .mismatch {{ color: #b04040; font-weight: 600; }}
  .y {{ color: #1f7a1f; font-weight: 600; text-align: center; }}
  .n {{ color: #b04040; font-weight: 600; text-align: center; }}
  .blank {{ color: #ccc; text-align: center; }}
  .code {{
    font-family: ui-monospace, SFMono-Regular, "Menlo", Consolas, monospace;
    font-size: 0.88em; word-break: break-all;
  }}
  .sxs {{ display: flex; gap: 1.2em; }}
  .sxs > div {{ flex: 1; min-width: 0; }}
  .sxs h3 {{
    margin-top: 0; padding-bottom: 0.2em; border-bottom: 1px solid #ddd;
  }}
  .meta dt {{
    font-weight: 600; color: #555; float: left; clear: left;
    width: 9em; margin-right: 0.5em;
  }}
  .meta dd {{ margin-left: 9.5em; margin-bottom: 0.2em; }}
  .field-list {{ font-size: 0.85em; padding-left: 1em; margin: 0.2em 0 0.6em 0; }}
  .field-list li {{ margin: 0.1em 0; list-style: disc; }}
  .field-list .ex {{ color: #999; font-style: italic; }}
  .field-list .key {{ color: #1a5bb8; font-weight: 600; }}
  .sample-note {{ color: #666; font-size: 0.85em; margin-top: 0.5em; }}
  a.fileref {{ color: #1a5bb8; text-decoration: none; }}
  a.fileref:hover {{ text-decoration: underline; }}
  .desc {{ font-size: 0.82em; color: #555; }}
</style>
</head>
<body>
<h1>Compare report</h1>
<div class="subhead">Run <span class="code">{e(stamp)}</span></div>

<h2>Layouts</h2>
{layouts_html}

<h2>Inputs</h2>
{inputs_html}

<h2>Aggregate counts</h2>
{counts_html}

<h2>Per-segment breakdown</h2>
{per_segment_html}

<h2>Per-key mismatch sample</h2>
{matrix_sample_html}

<h2>Timing</h2>
{timing_html}

<h2>Config provenance</h2>
{config_paths_html}

</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")


def _render_layouts_side_by_side(layout_a: FileLayout, layout_b: FileLayout, e: "Any") -> str:
    """Two-column flex layout comparing File A's layout to File B's."""
    return (
        '<div class="sxs">'
        f"<div>{_render_one_layout('File A', layout_a, e)}</div>"
        f"<div>{_render_one_layout('File B', layout_b, e)}</div>"
        "</div>"
    )


def _render_one_layout(title: str, layout: "FileLayout", e: "Any") -> str:
    """One column of the side-by-side layout view."""
    ff = layout.file_format
    key_seg = layout.key_segment
    key_field = layout.key_field
    key_range = layout.key_range
    rdw_desc = (
        f"{layout.rdw.total_bytes} bytes ({layout.rdw.encoding})"
        if layout.rdw is not None
        else "none"
    )
    strip_desc = (
        f"{layout.strip_leading_bytes.size} bytes ({layout.strip_leading_bytes.encoding})"
        if layout.strip_leading_bytes is not None
        else "none"
    )
    aliases_desc = (
        ", ".join(
            f"{e(a.wire_name)}→{e(a.logical_name)} after {e(a.after_segment)}"
            for a in layout.segment_aliases
        )
        or "none"
    )

    meta_html = (
        '<dl class="meta">'
        f"<dt>Layout file</dt><dd class='code'>{e(layout.source_path.name)}</dd>"
        f"<dt>Key segment</dt><dd class='code'>{e(key_seg.name)}</dd>"
        f"<dt>Key field</dt><dd class='code'>{e(key_field.name)}</dd>"
        f"<dt>Key range</dt><dd class='code'>[{key_range[0]}, {key_range[1]})"
        f" ({key_field.length} bytes)</dd>"
        f"<dt>End segment</dt><dd class='code'>{e(layout.end_segment.name)}</dd>"
        f"<dt>Record delim</dt><dd class='code'>{e(repr(ff.record_delimiter))}</dd>"
        f"<dt>Strip prefix</dt><dd>{strip_desc}</dd>"
        f"<dt>RDW</dt><dd>{rdw_desc}</dd>"
        f"<dt>Aliases</dt><dd>{aliases_desc}</dd>"
        f"<dt>Sort</dt><dd>input_sorted={layout.sort.input_sorted}, "
        f"order={e(layout.sort.order)}, key_type={e(layout.sort.key_type)}</dd>"
        "</dl>"
    )

    segments_html_parts = [
        f"<h3>{e(title)} segments</h3><table>",
        "<tr><th>Segment</th><th class='num'>Size</th><th>Fields</th></tr>",
    ]
    for seg in layout.segments:
        field_list_parts = ['<ul class="field-list">']
        for fld in seg.fields:
            extras = []
            if fld.key:
                extras.append('<span class="key">KEY</span>')
            if fld.exclude:
                extras.append('<span class="ex">exclude</span>')
            extras_str = (" — " + ", ".join(extras)) if extras else ""
            field_list_parts.append(
                f"<li><span class='code'>{e(fld.name)}</span>"
                f" ({fld.length} bytes){extras_str}</li>"
            )
        field_list_parts.append("</ul>")
        fields_cell = "".join(field_list_parts)
        segments_html_parts.append(
            f"<tr><td class='code'>{e(seg.name)}</td>"
            f"<td class='num'>{seg.size}</td>"
            f"<td>{fields_cell}</td></tr>"
        )
    segments_html_parts.append("</table>")
    segments_html = "".join(segments_html_parts)

    return f"<h3>{e(title)} — overview</h3>{meta_html}{segments_html}"


def _render_inputs_side_by_side(summary: "Summary", e: "Any") -> str:
    """Inputs section as a side-by-side metric/A/B table."""
    rows = [
        (
            "Path",
            f"<span class='code'>{e(str(summary.file_a_path))}</span>",
            f"<span class='code'>{e(str(summary.file_b_path))}</span>",
        ),
        (
            "Size (bytes)",
            f"<span class='num'>{summary.file_a_size_bytes:,}</span>",
            f"<span class='num'>{summary.file_b_size_bytes:,}</span>",
        ),
        (
            "Record count",
            f"<span class='num'>{summary.file_a_record_count:,}</span>",
            f"<span class='num'>{summary.file_b_record_count:,}</span>",
        ),
    ]
    rows_html = "".join(
        f"<tr><td>{label}</td><td>{a}</td><td>{b}</td></tr>" for label, a, b in rows
    )
    return (
        "<table>" "<tr><th>Metric</th><th>File A</th><th>File B</th></tr>" f"{rows_html}" "</table>"
    )


def _render_aggregate_counts(summary: "Summary", _stamp: str, e: "Any") -> str:
    """Counts table with a description + clickable file-link columns (ADR-036)."""
    rows = (
        (
            "Records matched",
            "Records found in both files with identical content.",
            summary.records_matched,
            "match",
            "records_matched",
        ),
        (
            "Records mismatched",
            "Records found in both files, but the content is different.",
            summary.records_mismatched,
            "mismatch",
            "records_mismatched",
        ),
        (
            "Keys in both",
            "Records found in both files, after duplicates are removed.",
            summary.keys_in_both,
            "",
            None,
        ),
        (
            "Keys only in A",
            "Records found only in File A, not in File B.",
            summary.keys_in_a_only,
            "",
            "keys_in_a_only",
        ),
        (
            "Keys only in B",
            "Records found only in File B, not in File A.",
            summary.keys_in_b_only,
            "",
            "keys_in_b_only",
        ),
        (
            "Duplicate keys in A",
            (
                "Records in File A where the same key appears more "
                "than once. Removed before comparison."
            ),
            summary.dups_in_a,
            "",
            "dups_in_a",
        ),
        (
            "Duplicate keys in B",
            (
                "Records in File B where the same key appears more "
                "than once. Removed before comparison."
            ),
            summary.dups_in_b,
            "",
            "dups_in_b",
        ),
    )

    def _file_cell(metric_key: "str | None") -> str:
        if metric_key is None:
            return "—"
        base = METRIC_TO_FILE.get(metric_key)
        if base is None:
            return "—"
        # Bare filename — HTML lives in the same per-run dir (ADR-037).
        return f"<a class='fileref' href='{e(base)}'>{e(base)}</a>"

    rows_html = "".join(
        f"<tr><td class='{css}'>{label}</td>"
        f"<td class='desc'>{e(desc)}</td>"
        f"<td class='num'>{val:,}</td>"
        f"<td>{_file_cell(metric_key)}</td></tr>"
        for label, desc, val, css, metric_key in rows
    )
    return (
        "<table>"
        "<tr><th>Metric</th><th>Description</th>"
        "<th class='num'>Value</th><th>File</th></tr>"
        f"{rows_html}"
        "</table>"
    )


def _render_per_segment(summary: "Summary", e: "Any") -> str:
    rows_html = "".join(
        f"<tr><td class='code'>{e(seg.segment_name)}</td>"
        f"<td class='num'>{seg.match_count:,}</td>"
        f"<td class='num'>{seg.mismatch_count:,}</td>"
        f"<td class='num'>{seg.total_in_a:,}</td>"
        f"<td class='num'>{seg.total_in_b:,}</td></tr>"
        for seg in summary.per_segment
    )
    note = (
        "<p class='sample-note'>"
        "Match / Mismatch are <strong>record-level</strong>: how many joined records had this "
        "segment type fully agreeing between A and B (Match) vs differing on at least one "
        "instance (Mismatch). Total in A / B count every instance of the segment across all "
        "records in each file (including orphans and duplicates)."
        "</p>"
    )
    return (
        f"{note}"
        "<table>"
        "<tr><th>Segment</th><th class='num'>Match</th><th class='num'>Mismatch</th>"
        "<th class='num'>Total in A</th><th class='num'>Total in B</th></tr>"
        f"{rows_html}"
        "</table>"
    )


def _render_key_matrix_sample(
    entries: tuple[KeyMatrixEntry, ...],
    segments: tuple[str, ...],
    _stamp: str,
    e: "Any",
) -> str:
    """First ~20 rows of the per-key mismatch matrix, with a link to the full file."""
    # Bare filename — HTML lives in the same per-run dir (ADR-037).
    full_link = f"<a class='fileref' href='{e(KEY_MATRIX_FILE)}'>{e(KEY_MATRIX_FILE)}</a>"

    if not entries:
        return (
            f"<p class='sample-note'>No mismatched records — "
            f"{full_link} contains only the header row.</p>"
        )

    cols = list(segments)
    sample = entries[:_HTML_KEY_MATRIX_SAMPLE_SIZE]

    head_cells = "".join(f"<th class='num'>{e(c)}</th>" for c in cols)
    body_rows: list[str] = []
    for entry in sample:
        cells = []
        for col in cols:
            v = entry.segment_status.get(col, "")
            if v == "Y":
                cells.append("<td class='y'>Y</td>")
            elif v == "N":
                cells.append("<td class='n'>N</td>")
            else:
                cells.append("<td class='blank'>·</td>")
        count_diff = "|".join(entry.segment_count_diffs)
        cells.append(f"<td class='code'>{e(count_diff)}</td>")
        body_rows.append(f"<tr><td class='code'>{e(entry.key)}</td>{''.join(cells)}</tr>")
    body = "".join(body_rows)

    note = (
        f"<p class='sample-note'>Showing {len(sample)} of "
        f"{len(entries):,} mismatched keys. "
        f"Full matrix: {full_link}</p>"
    )
    return (
        "<table>"
        f"<tr><th>Key</th>{head_cells}<th>segment_count_mismatch</th></tr>"
        f"{body}"
        "</table>"
        f"{note}"
    )


def _render_timing(summary: "Summary", e: "Any") -> str:
    return (
        "<table>"
        "<tr><th>Metric</th><th>Value</th></tr>"
        f"<tr><td>Start time (UTC)</td><td class='code'>{e(summary.start_time)}</td></tr>"
        f"<tr><td>End time (UTC)</td><td class='code'>{e(summary.end_time)}</td></tr>"
        f"<tr><td>Elapsed</td>"
        f"<td class='num'>{summary.elapsed_seconds:.3f} s</td></tr>"
        f"<tr><td>Throughput</td>"
        f"<td class='num'>{summary.throughput_records_per_sec:,.1f} records/s</td></tr>"
        "</table>"
    )


def _render_config_paths(summary: "Summary", e: "Any") -> str:
    rows = []
    seen_cp: set[str] = set()
    for kind in _CONFIG_PATH_ORDER:
        if kind in summary.config_paths:
            rows.append((kind, summary.config_paths[kind]))
            seen_cp.add(kind)
    for kind, p in summary.config_paths.items():
        if kind not in seen_cp:
            rows.append((kind, p))
    rows_html = "".join(
        f"<tr><td>{e(kind)}</td><td class='code'>{e(p)}</td></tr>" for kind, p in rows
    )
    return f"<table><tr><th>Kind</th><th>Path</th></tr>{rows_html}</table>"
