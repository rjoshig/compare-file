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
- ``summary.json`` — aggregated metrics and run metadata.
"""

from __future__ import annotations

import csv
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

REPORT_HEADER = ("key", "segment_name", "status", "a_count", "b_count")


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


class OutputWriter:
    """Writes the eight Phase 1 output files.

    Open the writer (preferably as a context manager), call the per-
    record write methods as the pipeline produces verdicts, then call
    :meth:`finalize` with the run :class:`Summary`. The context manager
    closes all handles on exit even if :meth:`finalize` was not called.
    """

    __slots__ = ("_output_dir", "_delimiter", "_handles", "_report_writer", "_closed")

    def __init__(self, output_dir: Path, segments_cfg: SegmentsConfig) -> None:
        """Initialize and open all eight output files for writing.

        Args:
            output_dir: Directory to write outputs into. Created if it
                does not exist.
            segments_cfg: Used to learn the record delimiter (the same
                delimiter used in the source files is appended after
                every record written to ``*.dat``).
        """
        self._output_dir = output_dir
        self._delimiter = segments_cfg.record_delimiter
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
            self._handles[name] = (output_dir / name).open("wb")

        # csv.writer needs text mode; keep it separate from the binary handles.
        report_path = output_dir / REPORT_FILE
        report_handle = report_path.open("w", encoding="utf-8", newline="")
        self._handles[REPORT_FILE] = report_handle  # type: ignore[assignment]
        self._report_writer = csv.writer(report_handle)
        self._report_writer.writerow(REPORT_HEADER)

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
        """Write ``summary.json`` and close all handles."""
        summary_path = self._output_dir / SUMMARY_FILE
        with summary_path.open("w", encoding="utf-8") as fh:
            json.dump(_summary_to_json(summary), fh, indent=2, sort_keys=True)
            fh.write("\n")
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
