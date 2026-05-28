"""End-to-end comparison pipeline.

:func:`run` is the single entry point used by the CLI (Phase 1), the
FastAPI app (Phase 3), and the service runner (Phase 4). It performs:

1. An index-build pass over each input file (key → ``(offset, length)``),
   detecting duplicates and counting per-segment occurrences (ADR-018).
2. Routes duplicate-key records to ``dups_A.dat`` / ``dups_B.dat`` and
   removes them from the join (ADR-019).
3. For each key present in both files, seeks into the source files,
   parses both records, hashes/compares per-segment-type multisets, and
   writes the verdict.
4. Writes orphan-key records (only in A or only in B) to their
   ``keymismatch_*`` files.
5. Aggregates a :class:`Summary` (timings, counts, audit hash) and
   asks the writer to serialize it.

Phase 1 runs everything in one process. The
``Iterator[(key, record_a, record_b)]`` contract between this module
and the writer is what Phase 2 will parallelize without rewriting the
downstream code (ADR-024).
"""

from __future__ import annotations

import io
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from segment_compare import __version__
from segment_compare.comparator import compare_records
from segment_compare.config import ResolvedConfig
from segment_compare.hasher import build_hasher
from segment_compare.normalizer import PositionNormalizer
from segment_compare.parser import Record, iter_records
from segment_compare.writer import STAMP_FORMAT, OutputWriter, SegmentSummary, Summary

logger = logging.getLogger(__name__)


class InputFileError(Exception):
    """Raised when an input file is missing or unreadable."""


@dataclass(frozen=True, slots=True)
class DryRunReport:
    """Per-file counts produced by :func:`dry_run`.

    Attributes:
        file_a_records: Number of records parsed from File A.
        file_b_records: Number of records parsed from File B.
        dups_in_a: Total duplicate-key occurrences in File A.
        dups_in_b: Total duplicate-key occurrences in File B.
    """

    file_a_records: int
    file_b_records: int
    dups_in_a: int
    dups_in_b: int


def dry_run(file_a: Path, file_b: Path, config: ResolvedConfig) -> DryRunReport:
    """Parse both inputs without comparing or writing outputs.

    Surfaces parse errors and duplicate-key counts early so an operator
    can validate inputs before paying for a full comparison.
    """
    for path in (file_a, file_b):
        if not path.exists():
            raise InputFileError(f"input file does not exist: {path}")
    _, dups_a, total_a, _ = _index_file(file_a, config)
    _, dups_b, total_b, _ = _index_file(file_b, config)
    return DryRunReport(
        file_a_records=total_a,
        file_b_records=total_b,
        dups_in_a=sum(len(v) for v in dups_a.values()),
        dups_in_b=sum(len(v) for v in dups_b.values()),
    )


def run(
    file_a: Path,
    file_b: Path,
    config: ResolvedConfig,
    output_dir: Path,
    run_timestamp: datetime | None = None,
) -> Summary:
    """Execute one end-to-end comparison and return its :class:`Summary`.

    Args:
        file_a: Path to File A.
        file_b: Path to File B.
        config: Validated configuration produced by
            :func:`segment_compare.config.load_config`.
        output_dir: Directory to write the eight outputs into. Created
            if absent.
        run_timestamp: Optional UTC timestamp to use as the run's
            identity. Defaults to the moment :func:`run` is called.
            Used both to suffix every output filename (so successive
            runs don't clobber each other) and as the ``start_time``
            field in ``summary.json``. Tests pass a fixed value for
            deterministic output names.

    Returns:
        The :class:`Summary` that was just written to
        ``output_dir / summary_<stamp>.json``.

    Raises:
        InputFileError: If either input file does not exist.
        segment_compare.parser.ParseError: On corrupt input bytes.
    """
    for path in (file_a, file_b):
        if not path.exists():
            raise InputFileError(f"input file does not exist: {path}")

    start_time = run_timestamp or datetime.now(timezone.utc)
    filename_stamp = start_time.strftime(STAMP_FORMAT)
    logger.info("starting comparison: %s vs %s (stamp=%s)", file_a, file_b, filename_stamp)

    index_a, dups_a, total_a, segments_a = _index_file(file_a, config)
    index_b, dups_b, total_b, segments_b = _index_file(file_b, config)
    logger.info(
        "indexed: A=%d records (%d dup keys), B=%d records (%d dup keys)",
        total_a,
        len(dups_a),
        total_b,
        len(dups_b),
    )

    keys_a = set(index_a)
    keys_b = set(index_b)
    only_a_keys = sorted(keys_a - keys_b)
    only_b_keys = sorted(keys_b - keys_a)
    both_keys = sorted(keys_a & keys_b)

    normalizer = PositionNormalizer(config.normalization)
    hasher = build_hasher(config.runtime)

    records_matched = 0
    records_mismatched = 0
    per_segment_match: dict[str, int] = defaultdict(int)
    per_segment_mismatch: dict[str, int] = defaultdict(int)

    with OutputWriter(output_dir, config.segments, filename_stamp=filename_stamp) as writer:
        _write_dups(file_a, dups_a, config, writer.write_dup_a)
        _write_dups(file_b, dups_b, config, writer.write_dup_b)

        with file_a.open("rb") as fh_a, file_b.open("rb") as fh_b:
            for key in both_keys:
                off_a, len_a = index_a[key]
                off_b, len_b = index_b[key]
                rec_a = _read_record_at(fh_a, off_a, len_a, config)
                rec_b = _read_record_at(fh_b, off_b, len_b, config)
                verdict = compare_records(rec_a, rec_b, normalizer, hasher)

                if verdict.matched:
                    writer.write_match(rec_a)
                    records_matched += 1
                else:
                    writer.write_mismatch(verdict, rec_a, rec_b)
                    records_mismatched += 1

                for sv in verdict.segment_verdicts:
                    if sv.matched:
                        per_segment_match[sv.segment_name] += 1
                    else:
                        per_segment_mismatch[sv.segment_name] += 1

        _write_key_only(file_a, only_a_keys, index_a, config, writer.write_key_only_a)
        _write_key_only(file_b, only_b_keys, index_b, config, writer.write_key_only_b)

        end_time = datetime.now(timezone.utc)
        elapsed = (end_time - start_time).total_seconds()
        total_processed = total_a + total_b
        throughput = total_processed / elapsed if elapsed > 0 else 0.0

        per_segment = _build_per_segment_summary(
            per_segment_match, per_segment_mismatch, segments_a, segments_b
        )

        summary = Summary(
            file_a_path=file_a,
            file_b_path=file_b,
            file_a_size_bytes=file_a.stat().st_size,
            file_b_size_bytes=file_b.stat().st_size,
            file_a_record_count=total_a,
            file_b_record_count=total_b,
            keys_in_a_only=len(only_a_keys),
            keys_in_b_only=len(only_b_keys),
            keys_in_both=len(both_keys),
            dups_in_a=sum(len(v) for v in dups_a.values()),
            dups_in_b=sum(len(v) for v in dups_b.values()),
            records_matched=records_matched,
            records_mismatched=records_mismatched,
            per_segment=per_segment,
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            elapsed_seconds=elapsed,
            throughput_records_per_sec=throughput,
            config_paths={k: str(v) for k, v in config.paths.items()},
            config_audit_hash=config.audit_hash,
            engine_version=__version__,
            filename_stamp=filename_stamp,
        )
        writer.finalize(summary)

    logger.info(
        "comparison complete: %d matched, %d mismatched, %d elapsed=%.3fs",
        records_matched,
        records_mismatched,
        len(only_a_keys) + len(only_b_keys),
        elapsed,
    )
    return summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _index_file(path: Path, config: ResolvedConfig) -> tuple[
    dict[str, tuple[int, int]],
    dict[str, list[tuple[int, int]]],
    int,
    Counter[str],
]:
    """Single streaming pass building the key index, dup map, and counts.

    Returns:
        ``(good_index, dup_offsets, total_records, segment_counts)``.

        - ``good_index`` maps non-duplicate keys to
          ``(offset, length)``.
        - ``dup_offsets`` maps duplicate keys to a list of every
          occurrence's ``(offset, length)``.
        - ``total_records`` is the count of all records (including dups).
        - ``segment_counts`` is total occurrences per segment name
          across the entire file.
    """
    good_index: dict[str, tuple[int, int]] = {}
    dup_offsets: dict[str, list[tuple[int, int]]] = {}
    segment_counts: Counter[str] = Counter()
    total_records = 0

    with path.open("rb") as fh:
        for record in iter_records(fh, config.parser, config.segments):
            total_records += 1
            for seg in record.segments:
                segment_counts[seg.name] += 1
            key = record.key
            entry = (record.offset, record.length)
            if key in dup_offsets:
                dup_offsets[key].append(entry)
            elif key in good_index:
                previous = good_index.pop(key)
                dup_offsets[key] = [previous, entry]
            else:
                good_index[key] = entry

    return good_index, dup_offsets, total_records, segment_counts


def _read_record_at(stream: BinaryIO, offset: int, length: int, config: ResolvedConfig) -> Record:
    """Seek to ``offset`` in ``stream`` and parse the record there."""
    stream.seek(offset)
    buf = stream.read(length)
    parsed = list(iter_records(io.BytesIO(buf), config.parser, config.segments))
    if not parsed:
        raise InputFileError(f"no record could be parsed at offset {offset} (length {length})")
    return parsed[0]


def _write_dups(
    path: Path,
    dups: dict[str, list[tuple[int, int]]],
    config: ResolvedConfig,
    write_fn: "object",
) -> None:
    """Write every duplicate-key record's bytes via ``write_fn``."""
    if not dups:
        return
    with path.open("rb") as fh:
        for entries in dups.values():
            for off, length in entries:
                rec = _read_record_at(fh, off, length, config)
                write_fn(rec)  # type: ignore[operator]


def _write_key_only(
    path: Path,
    keys: list[str],
    index: dict[str, tuple[int, int]],
    config: ResolvedConfig,
    write_fn: "object",
) -> None:
    """Write each orphan-key record's bytes via ``write_fn``."""
    if not keys:
        return
    with path.open("rb") as fh:
        for key in keys:
            off, length = index[key]
            rec = _read_record_at(fh, off, length, config)
            write_fn(rec)  # type: ignore[operator]


def _build_per_segment_summary(
    per_segment_match: dict[str, int],
    per_segment_mismatch: dict[str, int],
    segments_a: Counter[str],
    segments_b: Counter[str],
) -> tuple[SegmentSummary, ...]:
    """Merge per-segment counters into a stable, sorted tuple."""
    all_names = (
        set(per_segment_match) | set(per_segment_mismatch) | set(segments_a) | set(segments_b)
    )
    return tuple(
        SegmentSummary(
            segment_name=name,
            match_count=per_segment_match.get(name, 0),
            mismatch_count=per_segment_mismatch.get(name, 0),
            total_in_a=segments_a.get(name, 0),
            total_in_b=segments_b.get(name, 0),
        )
        for name in sorted(all_names)
    )
