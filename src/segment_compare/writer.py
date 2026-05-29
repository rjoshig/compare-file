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
# Per-key duplicate-count reports (ADR-040): one row per duplicate key with
# its occurrence count in that file. The full set (not a sample), linked from
# the HTML report's "Sample records" dups subsection.
DUPS_A_COUNT_FILE = "dups_A_count_report.csv"
DUPS_B_COUNT_FILE = "dups_B_count_report.csv"

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

# Caps for the "Sample records" section embedded in compare_reports.html
# (ADR-040). These bound how many example rows the report shows per
# category; the aggregate counts in summary.json remain the truth.
MATCH_SAMPLE_SIZE = 5
MISMATCH_SAMPLE_SIZE = 10
DUPS_SAMPLE_SIZE = 10
ORPHANS_SAMPLE_SIZE = 10


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
class RecordSample:
    """One sampled record for the report: its key and decoded raw bytes."""

    key: str
    data: str


@dataclass(frozen=True, slots=True)
class MismatchSample:
    """One sampled mismatched key with both sides' decoded raw records."""

    key: str
    a: str
    b: str


@dataclass(frozen=True, slots=True)
class DupCount:
    """One duplicate key and how many times it occurred in that file."""

    key: str
    count: int


@dataclass(frozen=True, slots=True)
class RunSamples:
    """Capped example rows for the report's "Sample records" section (ADR-040).

    All fields are bounded by the ``*_SAMPLE_SIZE`` constants; they are
    illustrative only and never the source of truth for any count.
    """

    matches: tuple[RecordSample, ...] = ()
    mismatches: tuple[MismatchSample, ...] = ()
    dups_a: tuple[DupCount, ...] = ()
    dups_b: tuple[DupCount, ...] = ()
    orphans_a: tuple[str, ...] = ()
    orphans_b: tuple[str, ...] = ()


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
        samples: Capped example rows (matches / mismatches / dups /
            orphans) for the HTML report's "Sample records" section
            (ADR-040). Defaults to empty.
    """

    summary: Summary
    layout_a: FileLayout
    layout_b: FileLayout
    key_matrix_entries: tuple[KeyMatrixEntry, ...]
    matrix_segments: tuple[str, ...]
    output_dir: Path
    samples: RunSamples = RunSamples()


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


def write_dups_count_report(dup_counts: dict[str, int], path: Path) -> None:
    """Write a per-key duplicate-count CSV (``key,count``) for one file (ADR-040).

    ``dup_counts`` maps each duplicate key to how many times it occurred in
    that source file. Rows are sorted by key for deterministic diffs. The
    header is always written, so the file exists (and the HTML link resolves)
    even when there are no duplicates.
    """
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(("key", "count"))
        for key in sorted(dup_counts):
            w.writerow((key, dup_counts[key]))


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
    samples_html = _render_samples(reports.samples, e)
    timing_html = _render_timing(summary, e)
    config_paths_html = _render_config_paths(summary, e)

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Compare report — {e(stamp)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link
  href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono&display=swap"
  rel="stylesheet">
<style>
  /* Material 3-inspired tokens. Dark mode follows the OS preference
     by default and can be forced via ?theme=light or ?theme=dark
     (handled by the inline script below). */
  :root {{
    --bg: #f7f8fb;
    --surface-1: #ffffff;
    --surface-2: #f3f4f8;
    --surface-3: #e9ebf2;
    --border: #e3e5ea;
    --divider: #ebedf0;
    --text-strong: #1c1f24;
    --text-body: #2d3138;
    --text-muted: #5b626d;
    --primary: #2563eb;
    --primary-soft: rgba(37, 99, 235, 0.10);
    --tone-a: #2563eb;
    --tone-a-soft: rgba(37, 99, 235, 0.10);
    --tone-b: #f59e0b;
    --tone-b-soft: rgba(245, 158, 11, 0.13);
    --match: #16a34a;
    --match-soft: rgba(22, 163, 74, 0.12);
    --mismatch: #dc2626;
    --mismatch-soft: rgba(220, 38, 38, 0.12);
    --warn: #d97706;
    --elev-1: 0 1px 2px rgba(0,0,0,0.06), 0 1px 3px rgba(0,0,0,0.04);
    --elev-2: 0 2px 4px rgba(0,0,0,0.08), 0 4px 8px rgba(0,0,0,0.05);
    --radius: 12px;
    --radius-sm: 8px;
  }}
  html.dark, html[data-theme="dark"] {{
    --bg: #0f1117;
    --surface-1: #181b22;
    --surface-2: #1e222b;
    --surface-3: #232733;
    --border: #2b303d;
    --divider: #262a35;
    --text-strong: #e8ebf2;
    --text-body: #d2d6e0;
    --text-muted: #9098a8;
    --primary: #60a5fa;
    --primary-soft: rgba(96, 165, 250, 0.16);
    --tone-a: #60a5fa;
    --tone-a-soft: rgba(96, 165, 250, 0.16);
    --tone-b: #fbbf24;
    --tone-b-soft: rgba(251, 191, 36, 0.16);
    --match: #22c55e;
    --match-soft: rgba(34, 197, 94, 0.18);
    --mismatch: #ef4444;
    --mismatch-soft: rgba(239, 68, 68, 0.18);
    --warn: #f59e0b;
    --elev-1: 0 1px 2px rgba(0,0,0,0.35), 0 1px 3px rgba(0,0,0,0.20);
    --elev-2: 0 2px 4px rgba(0,0,0,0.40), 0 4px 8px rgba(0,0,0,0.25);
    color-scheme: dark;
  }}
  @media (prefers-color-scheme: dark) {{
    html:not([data-theme="light"]) {{
      --bg: #0f1117;
      --surface-1: #181b22;
      --surface-2: #1e222b;
      --surface-3: #232733;
      --border: #2b303d;
      --divider: #262a35;
      --text-strong: #e8ebf2;
      --text-body: #d2d6e0;
      --text-muted: #9098a8;
      --primary: #60a5fa;
      --primary-soft: rgba(96, 165, 250, 0.16);
      --tone-a: #60a5fa;
      --tone-a-soft: rgba(96, 165, 250, 0.16);
      --tone-b: #fbbf24;
      --tone-b-soft: rgba(251, 191, 36, 0.16);
      --match: #22c55e;
      --match-soft: rgba(34, 197, 94, 0.18);
      --mismatch: #ef4444;
      --mismatch-soft: rgba(239, 68, 68, 0.18);
      --warn: #f59e0b;
      color-scheme: dark;
    }}
  }}

  * {{ box-sizing: border-box; }}
  body {{
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI",
                 Roboto, "Helvetica Neue", Arial, sans-serif;
    font-feature-settings: "cv11", "ss01";
    -webkit-font-smoothing: antialiased;
    background: var(--bg);
    color: var(--text-body);
    margin: 0; padding: 0;
    line-height: 1.5;
  }}
  .page {{ max-width: 1240px; margin: 0 auto; padding: 1.5rem 1.5rem 3rem; }}

  /* Header */
  .topbar {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    padding: 1rem 1.5rem;
    background: var(--surface-1);
    border-bottom: 1px solid var(--border);
    position: sticky; top: 0; z-index: 5;
    backdrop-filter: blur(8px);
  }}
  .brand {{ display: flex; align-items: center; gap: 0.7rem; }}
  .brand-mark {{
    width: 34px; height: 34px;
    display: grid; place-items: center;
    background: linear-gradient(135deg, var(--tone-a) 0%, var(--tone-b) 100%);
    border-radius: 9px;
    color: white; font-weight: 700;
    box-shadow: var(--elev-1);
  }}
  .brand-name {{ font-weight: 600; font-size: 1rem; color: var(--text-strong); }}
  .brand-sub  {{ font-size: 0.78rem; color: var(--text-muted); }}
  .topbar-stamp {{ font-size: 0.85rem; color: var(--text-muted); }}
  .theme-toggle {{
    display: inline-flex; align-items: center; gap: 0.4rem;
    padding: 0.45rem 0.85rem;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: transparent; color: var(--text-body);
    font: inherit; font-size: 0.85rem; cursor: pointer;
  }}
  .theme-toggle:hover {{ background: var(--surface-2); }}

  /* Sections */
  h1 {{
    font-size: 1.6rem; font-weight: 600; letter-spacing: -0.01em;
    color: var(--text-strong); margin: 0;
  }}
  h2 {{
    font-size: 0.8rem; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--text-muted);
    margin: 2.2rem 0 0.7rem;
  }}
  h3 {{ font-size: 0.95rem; font-weight: 600; color: var(--text-strong); margin: 0 0 0.45rem; }}
  p {{ margin: 0 0 0.5rem; }}

  .card {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1.1rem 1.2rem;
    box-shadow: var(--elev-1);
  }}

  .output-banner {{
    display: flex;
    gap: 0.6rem;
    align-items: baseline;
    background: var(--primary-soft);
    border: 1px solid var(--border);
    border-left: 3px solid var(--primary);
    border-radius: var(--radius-sm);
    padding: 0.6rem 0.85rem;
    margin: 0 0 1.1rem;
    font-size: 0.88rem;
  }}
  .output-banner .label {{
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-muted);
    flex-shrink: 0;
  }}
  .output-banner .path {{
    color: var(--text-strong);
    word-break: break-all;
  }}

  table {{
    border-collapse: separate; border-spacing: 0;
    width: 100%; font-size: 0.9rem;
  }}
  th {{
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--text-muted);
    padding: 0.55rem 0.75rem; text-align: left;
    border-bottom: 1px solid var(--divider);
  }}
  td {{
    padding: 0.5rem 0.75rem;
    border-bottom: 1px solid var(--divider);
    vertical-align: top;
    color: var(--text-body);
  }}
  tr:last-child td {{ border-bottom: none; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .code {{
    font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo,
                 Consolas, monospace;
    font-size: 0.86em;
  }}

  /* Side-by-side layout cards */
  .sxs {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}
  .sxs > div {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 1rem 1.1rem;
    box-shadow: var(--elev-1);
  }}
  .sxs h3 {{ padding-bottom: 0.4rem; border-bottom: 1px solid var(--divider); }}

  .meta dt {{
    font-weight: 500; color: var(--text-muted); float: left; clear: left;
    width: 8.5em; margin-right: 0.6rem; font-size: 0.85rem;
  }}
  .meta dd {{ margin-left: 9em; margin-bottom: 0.25rem; font-size: 0.88rem; }}
  /* Per-segment cards (matches the dashboard SegmentEditor look). */
  .seg-stack {{ display: flex; flex-direction: column; gap: 0.55rem; margin-top: 0.4rem; }}
  .seg-card {{
    background: var(--surface-1);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 0.55rem 0.75rem 0.4rem;
  }}
  .seg-head {{
    display: flex; align-items: center; gap: 0.5rem;
    margin-bottom: 0.3rem;
  }}
  .seg-name {{
    font-weight: 600; font-size: 0.9rem; color: var(--text-strong);
  }}
  .seg-size {{
    margin-left: auto; font-size: 0.78rem; color: var(--text-muted);
  }}
  .seg-size strong {{ color: var(--text-strong); font-weight: 600; }}
  .role-pill {{
    font-size: 0.62rem; font-weight: 700;
    letter-spacing: 0.06em; text-transform: uppercase;
    padding: 0.1rem 0.5rem; border-radius: 999px;
  }}
  .role-pill.key {{ background: var(--primary-soft); color: var(--primary); }}
  .role-pill.end {{ background: var(--surface-3); color: var(--text-muted); }}

  table.fields-table {{ font-size: 0.83rem; }}
  table.fields-table th {{
    font-size: 0.62rem; padding: 0.25rem 0.5rem;
  }}
  table.fields-table td {{
    padding: 0.28rem 0.5rem;
    border-bottom: 1px solid var(--divider);
  }}
  table.fields-table tr:last-child td {{ border-bottom: none; }}
  table.fields-table .field-cell {{ display: flex; align-items: center; gap: 0.4rem; }}
  table.fields-table .field-name {{
    font-family: ui-monospace, "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
    font-size: 0.82rem; color: var(--text-strong);
  }}
  .key-badge {{
    background: var(--primary-soft); color: var(--primary);
    padding: 0.05rem 0.4rem; border-radius: 999px;
    font-size: 0.58rem; font-weight: 700; letter-spacing: 0.05em;
  }}
  .excl-yes {{ color: var(--mismatch); font-weight: 600; }}
  .excl-no  {{ color: var(--text-muted); opacity: 0.7; }}

  /* Counts table */
  .desc {{ font-size: 0.82rem; color: var(--text-muted); max-width: 32em; }}
  .match {{ color: var(--match); font-weight: 600; }}
  .mismatch {{ color: var(--mismatch); font-weight: 600; }}

  /* Per-key matrix */
  .y {{
    color: var(--match); font-weight: 700; text-align: center;
    background: var(--match-soft); border-radius: 4px;
  }}
  .n {{
    color: var(--mismatch); font-weight: 700; text-align: center;
    background: var(--mismatch-soft); border-radius: 4px;
  }}
  .blank {{ color: var(--text-muted); opacity: 0.4; text-align: center; }}
  .sample-note {{ color: var(--text-muted); font-size: 0.85rem; margin-top: 0.6rem; }}
  /* Raw copy-pasteable record block: one record per line, no wrap (ADR-042). */
  .sample-block {{
    margin: 0.4rem 0;
    padding: 0.6rem 0.7rem;
    background: var(--surface-2);
    border: 1px solid var(--divider);
    border-radius: var(--radius-sm);
    font-family: ui-monospace, "JetBrains Mono", Menlo, Consolas, monospace;
    font-size: 0.72rem;
    line-height: 1.5;
    white-space: pre;
    overflow-x: auto;
    tab-size: 4;
  }}

  /* File-link styling */
  a.fileref {{
    color: var(--primary); text-decoration: none; font-weight: 500;
    border-bottom: 1px dashed transparent;
  }}
  a.fileref:hover {{ border-bottom-color: var(--primary); }}
</style>
<script>
  // Honor ?theme=dark|light in the URL so the parent UI can preserve
  // its theme when it opens the report in a new tab. The page also
  // respects prefers-color-scheme by default.
  (function () {{
    var params = new URLSearchParams(window.location.search);
    var t = params.get('theme');
    if (t === 'dark' || t === 'light') {{
      document.documentElement.setAttribute('data-theme', t);
    }}
  }})();
  function toggleTheme() {{
    var html = document.documentElement;
    var current = html.getAttribute('data-theme');
    if (!current) {{
      // No explicit override yet — flip relative to OS preference.
      var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      current = prefersDark ? 'dark' : 'light';
    }}
    html.setAttribute('data-theme', current === 'dark' ? 'light' : 'dark');
  }}
</script>
</head>
<body>
<header class="topbar">
  <div class="brand">
    <div class="brand-mark">SC</div>
    <div>
      <div class="brand-name">Segment Compare · Report</div>
      <div class="brand-sub">Run <span class="code">{e(stamp)}</span></div>
    </div>
  </div>
  <button class="theme-toggle" onclick="toggleTheme()" title="Toggle theme">
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"
         xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
      <path d="M12 3a9 9 0 1 0 9 9c-.5 0-.9 0-1.4-.1A7 7 0 1 1
               12.1 4.4 9 9 0 0 0 12 3z"/>
    </svg>
    Theme
  </button>
</header>

<main class="page">
<h1>Compare report</h1>

<div class="output-banner">
  <span class="label">Output dir</span>
  <span class="path code">{e(str(path.parent))}</span>
</div>

<h2>Layouts</h2>
{layouts_html}

<h2>Inputs</h2>
<div class="card">{inputs_html}</div>

<h2>Aggregate counts</h2>
<div class="card">{counts_html}</div>

<h2>Per-segment breakdown</h2>
<div class="card">{per_segment_html}</div>

<h2>Per-key mismatch sample</h2>
<div class="card">{matrix_sample_html}</div>

<h2>Sample records</h2>
<div class="card">{samples_html}</div>

<h2>Timing</h2>
<div class="card">{timing_html}</div>

<h2>Run configs</h2>
<div class="card">{config_paths_html}</div>

</main>
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
    # Segment aliases (ADR-034) are intentionally NOT surfaced in the report —
    # they're an internal backend concern (ADR-040).
    meta_html = (
        '<dl class="meta">'
        f"<dt>Key segment</dt><dd class='code'>{e(key_seg.name)}</dd>"
        f"<dt>Key field</dt><dd class='code'>{e(key_field.name)}</dd>"
        f"<dt>Key range</dt><dd class='code'>[{key_range[0]}, {key_range[1]})"
        f" ({key_field.length} bytes)</dd>"
        f"<dt>End segment</dt><dd class='code'>{e(layout.end_segment.name)}</dd>"
        f"<dt>Record delim</dt><dd class='code'>{e(repr(ff.record_delimiter))}</dd>"
        f"<dt>Strip prefix</dt><dd>{strip_desc}</dd>"
        f"<dt>RDW</dt><dd>{rdw_desc}</dd>"
        f"<dt>Sort</dt><dd>input_sorted={layout.sort.input_sorted}, "
        f"order={e(layout.sort.order)}, key_type={e(layout.sort.key_type)}</dd>"
        "</dl>"
    )

    segments_html_parts = [f"<h3>{e(title)} segments</h3>", '<div class="seg-stack">']
    for seg in layout.segments:
        role_pill = ""
        if seg.role == "key":
            role_pill = '<span class="role-pill key">key</span>'
        elif seg.role == "end":
            role_pill = '<span class="role-pill end">end</span>'

        rows_html_parts = []
        for fld in seg.fields:
            key_badge = '<span class="key-badge">KEY</span>' if fld.key else ""
            excl_html = (
                '<span class="excl-yes">✓</span>'
                if fld.exclude
                else '<span class="excl-no">—</span>'
            )
            rows_html_parts.append(
                "<tr>"
                f"<td><span class='field-cell'>"
                f"<span class='field-name'>{e(fld.name)}</span>{key_badge}"
                f"</span></td>"
                f"<td class='num'>{fld.length}</td>"
                f"<td class='num'>{excl_html}</td>"
                "</tr>"
            )
        fields_table = (
            "<table class='fields-table'>"
            "<tr><th>Field</th><th class='num'>Length</th>"
            "<th class='num'>Exclude</th></tr>"
            f"{''.join(rows_html_parts)}"
            "</table>"
        )
        segments_html_parts.append(
            "<div class='seg-card'>"
            "<div class='seg-head'>"
            f"<span class='seg-name code'>{e(seg.name)}</span>"
            f"{role_pill}"
            f"<span class='seg-size'>Size <strong>{seg.size}</strong></span>"
            "</div>"
            f"{fields_table}"
            "</div>"
        )
    segments_html_parts.append("</div>")
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


def _render_samples(samples: "RunSamples", e: "Any") -> str:
    """Render the "Sample records" section (ADR-040): capped examples per category.

    Matched / mismatched records render as raw monospace code blocks — one
    record per line, no wrapping (horizontal scroll) — so the operator can
    select and paste them straight into a text editor to eyeball differences
    (ADR-042). Each ``<pre>`` line is a full record on a single line.
    """
    if samples.matches:
        lines = "\n".join(f"{e(m.key)}  {e(m.data)}" for m in samples.matches)
        matches_html = (
            f"<pre class='sample-block'>{lines}</pre>"
            f"<p class='sample-note'>Up to {MATCH_SAMPLE_SIZE} matched records, one per line "
            f"(File A shown; File B is identical after normalization). Select &amp; copy.</p>"
        )
    else:
        matches_html = "<p class='sample-note'>No matched records.</p>"

    if samples.mismatches:
        # Two lines per key: File A then File B, so a copied pair diffs cleanly.
        pairs = []
        for m in samples.mismatches:
            pairs.append(f"{e(m.key)} | A | {e(m.a)}")
            pairs.append(f"{e(m.key)} | B | {e(m.b)}")
        lines = "\n".join(pairs)
        link = f"<a class='fileref' href='{e(MISMATCHES_FILE)}'>{e(MISMATCHES_FILE)}</a>"
        mismatches_html = (
            f"<pre class='sample-block'>{lines}</pre>"
            f"<p class='sample-note'>First {len(samples.mismatches)} mismatched records — "
            f"File A then File B per key, one record per line; copy the two lines to diff. "
            f"Full diagnostics: {link}</p>"
        )
    else:
        mismatches_html = "<p class='sample-note'>No mismatched records.</p>"

    def dup_table(dups: tuple[DupCount, ...], fileref: str, count_ref: str) -> str:
        count_link = f"<a class='fileref' href='{e(count_ref)}'>{e(count_ref)}</a>"
        if not dups:
            return f"<p class='sample-note'>No duplicate keys. Per-key counts: {count_link}</p>"
        rows = "".join(
            f"<tr><td class='code'>{e(d.key)}</td><td class='num'>{d.count}</td></tr>" for d in dups
        )
        link = f"<a class='fileref' href='{e(fileref)}'>{e(fileref)}</a>"
        return (
            f"<table><tr><th>Key</th><th>Occurrences</th></tr>{rows}</table>"
            f"<p class='sample-note'>Full records: {link} · "
            f"Per-key counts (all duplicate keys): {count_link}</p>"
        )

    def orphan_block(keys: tuple[str, ...], fileref: str) -> str:
        if not keys:
            return "<p class='sample-note'>No orphan keys.</p>"
        chips = " ".join(f"<code>{e(k)}</code>" for k in keys)
        link = f"<a class='fileref' href='{e(fileref)}'>{e(fileref)}</a>"
        return (
            f"<p style='word-break:break-all'>{chips}</p>"
            f"<p class='sample-note'>Sample of keys. Full records: {link}</p>"
        )

    return (
        f"<h3>Matched (sample)</h3>{matches_html}"
        f"<h3>Mismatched (key · File A · File B)</h3>{mismatches_html}"
        f"<h3>Duplicate keys — File A</h3>"
        f"{dup_table(samples.dups_a, DUPS_A_FILE, DUPS_A_COUNT_FILE)}"
        f"<h3>Duplicate keys — File B</h3>"
        f"{dup_table(samples.dups_b, DUPS_B_FILE, DUPS_B_COUNT_FILE)}"
        f"<h3>Orphan keys — only in File A</h3>"
        f"{orphan_block(samples.orphans_a, KEYMISMATCH_A_FILE)}"
        f"<h3>Orphan keys — only in File B</h3>"
        f"{orphan_block(samples.orphans_b, KEYMISMATCH_B_FILE)}"
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
