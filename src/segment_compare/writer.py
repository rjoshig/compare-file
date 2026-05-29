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
  human review in a browser (ADR-035).
"""

from __future__ import annotations

import csv
import html
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import TracebackType
from typing import IO, Any

from segment_compare.comparator import RecordVerdict
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

REPORT_HEADER = ("key", "segment_name", "status", "a_count", "b_count")

STAMP_FORMAT = "%Y%m%d%H%M"


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

    def finalize(self, summary: Summary) -> None:
        """Write ``summary.json`` + the two human reports and close all handles.

        The CSV and HTML report files carry the same metrics as
        ``summary.json`` (ADR-035); JSON remains the machine-readable
        source of truth.
        """
        summary_path = self._output_dir / self._on_disk(SUMMARY_FILE)
        write_summary(summary, summary_path)
        write_compare_reports_csv(
            summary, self._output_dir / self._on_disk(COMPARE_REPORTS_CSV_FILE)
        )
        write_compare_reports_html(
            summary, self._output_dir / self._on_disk(COMPARE_REPORTS_HTML_FILE)
        )
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
# Human reports (ADR-035) — CSV + HTML alongside summary.json
# ---------------------------------------------------------------------------

# Order matters: section names group related metrics, and within each
# section the rows appear in this declaration order so the file diffs
# predictably across runs with identical inputs.
_CONFIG_PATH_ORDER = ("layout_a", "layout_b", "runtime")


def write_compare_reports_csv(summary: Summary, path: Path) -> None:
    """Render ``summary`` as a 3-column long-format CSV (ADR-035).

    Columns are ``section,key,value``. Sections (in order): ``run``,
    ``inputs``, ``counts``, ``per_segment``, ``timing``,
    ``config_paths``. Per-segment metrics use ``<segment>.<stat>``
    style keys so a single segment's four numbers stay grouped.

    Opens cleanly in any spreadsheet (Excel, Google Sheets, Numbers)
    and is trivially filterable with ``awk`` / ``grep``.
    """
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


def write_compare_reports_html(summary: Summary, path: Path) -> None:
    """Render ``summary`` as a self-contained HTML report (ADR-035).

    All CSS is inline; no external assets are referenced, so the file
    can be opened directly from disk or attached to an email. Every
    metric from :class:`Summary` is shown — sectioned into Inputs,
    Aggregate counts, Per-segment breakdown, Timing, and Config
    provenance — with numbers right-aligned and thousand-separated.
    """
    e = html.escape

    rows_inputs = [
        (
            "File A",
            e(str(summary.file_a_path)),
            f"{summary.file_a_size_bytes:,}",
            f"{summary.file_a_record_count:,}",
        ),
        (
            "File B",
            e(str(summary.file_b_path)),
            f"{summary.file_b_size_bytes:,}",
            f"{summary.file_b_record_count:,}",
        ),
    ]
    inputs_rows_html = "".join(
        f"<tr><td>{side}</td><td class='code'>{path_}</td>"
        f"<td class='num'>{size}</td><td class='num'>{n}</td></tr>"
        for side, path_, size, n in rows_inputs
    )

    counts_rows = (
        ("Records matched", f"{summary.records_matched:,}", "match"),
        ("Records mismatched", f"{summary.records_mismatched:,}", "mismatch"),
        ("Keys in both", f"{summary.keys_in_both:,}", ""),
        ("Keys only in A", f"{summary.keys_in_a_only:,}", ""),
        ("Keys only in B", f"{summary.keys_in_b_only:,}", ""),
        ("Duplicate keys in A", f"{summary.dups_in_a:,}", ""),
        ("Duplicate keys in B", f"{summary.dups_in_b:,}", ""),
    )
    counts_rows_html = "".join(
        f"<tr><td class='{css}'>{label}</td><td class='num'>{val}</td></tr>"
        for label, val, css in counts_rows
    )

    per_segment_rows_html = "".join(
        f"<tr><td class='code'>{e(seg.segment_name)}</td>"
        f"<td class='num'>{seg.match_count:,}</td>"
        f"<td class='num'>{seg.mismatch_count:,}</td>"
        f"<td class='num'>{seg.total_in_a:,}</td>"
        f"<td class='num'>{seg.total_in_b:,}</td></tr>"
        for seg in summary.per_segment
    )

    timing_rows_html = (
        f"<tr><td>Start time (UTC)</td><td class='code'>{e(summary.start_time)}</td></tr>"
        f"<tr><td>End time (UTC)</td><td class='code'>{e(summary.end_time)}</td></tr>"
        f"<tr><td>Elapsed</td>"
        f"<td class='num'>{summary.elapsed_seconds:.3f} s</td></tr>"
        f"<tr><td>Throughput</td>"
        f"<td class='num'>{summary.throughput_records_per_sec:,.1f} records/s</td></tr>"
    )

    config_path_rows = []
    seen_cp: set[str] = set()
    for kind in _CONFIG_PATH_ORDER:
        if kind in summary.config_paths:
            config_path_rows.append((kind, summary.config_paths[kind]))
            seen_cp.add(kind)
    for kind, p in summary.config_paths.items():
        if kind not in seen_cp:
            config_path_rows.append((kind, p))
    config_paths_html = "".join(
        f"<tr><td>{e(kind)}</td><td class='code'>{e(p)}</td></tr>" for kind, p in config_path_rows
    )

    audit_short = e(summary.config_audit_hash[:16] + "…") if summary.config_audit_hash else ""

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Compare report — {e(summary.filename_stamp)}</title>
<style>
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                 "Helvetica Neue", Arial, sans-serif;
    max-width: 1040px; margin: 2em auto; padding: 0 1em; color: #222;
  }}
  h1 {{ font-size: 1.6em; margin-bottom: 0.2em; }}
  .subhead {{ color: #666; font-size: 0.9em; margin-bottom: 1.5em; }}
  h2 {{
    font-size: 1.1em; margin-top: 2.2em; margin-bottom: 0.4em;
    border-bottom: 1px solid #ddd; padding-bottom: 0.25em;
  }}
  table {{
    border-collapse: collapse; margin-top: 0.4em; width: 100%;
    font-size: 0.95em;
  }}
  th, td {{
    padding: 0.4em 0.7em; text-align: left;
    border-bottom: 1px solid #eee;
  }}
  th {{ background: #f6f6f6; font-weight: 600; }}
  tr:nth-child(even) td {{ background: #fafafa; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .match {{ color: #1f7a1f; font-weight: 600; }}
  .mismatch {{ color: #b04040; font-weight: 600; }}
  .code {{
    font-family: ui-monospace, SFMono-Regular, "Menlo", Consolas, monospace;
    font-size: 0.88em; word-break: break-all;
  }}
</style>
</head>
<body>
<h1>Compare report</h1>
<div class="subhead">Run <span class="code">{e(summary.filename_stamp)}</span>
  · engine {e(summary.engine_version)}
  · audit <span class="code">{audit_short}</span></div>

<h2>Inputs</h2>
<table>
<tr><th>File</th><th>Path</th><th class="num">Size (bytes)</th><th class="num">Records</th></tr>
{inputs_rows_html}
</table>

<h2>Aggregate counts</h2>
<table>
<tr><th>Metric</th><th class="num">Value</th></tr>
{counts_rows_html}
</table>

<h2>Per-segment breakdown</h2>
<table>
<tr><th>Segment</th><th class="num">Match</th><th class="num">Mismatch</th>
<th class="num">Total in A</th><th class="num">Total in B</th></tr>
{per_segment_rows_html}
</table>

<h2>Timing</h2>
<table>
<tr><th>Metric</th><th>Value</th></tr>
{timing_rows_html}
</table>

<h2>Config provenance</h2>
<table>
<tr><th>Kind</th><th>Path</th></tr>
{config_paths_html}
</table>

</body>
</html>
"""
    path.write_text(doc, encoding="utf-8")
