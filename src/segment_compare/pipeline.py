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
from concurrent.futures import ProcessPoolExecutor
from segment_compare.comparator import compare_records
from segment_compare.config import EngineConfig
from segment_compare.external_sort import external_sort_file
from segment_compare.hasher import build_hasher
from segment_compare.merger import fold_partial_summaries, merge_worker_outputs
from segment_compare.normalizer import FieldNormalizer
from segment_compare.parser import ParserConfig, Record, RdwConfig, SegmentsConfig, iter_records
from segment_compare.partitioner import equal_count_partition
from segment_compare.worker import WorkerPayload, WorkerResult, run_worker
from segment_compare.writer import (
    STAMP_FORMAT,
    OutputWriter,
    SegmentSummary,
    Summary,
    stamped_filename,
    write_summary,
)

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


def dry_run(file_a: Path, file_b: Path, config: EngineConfig) -> DryRunReport:
    """Parse both inputs without comparing or writing outputs.

    Surfaces parse errors and duplicate-key counts early so an operator
    can validate inputs before paying for a full comparison.
    """
    for path in (file_a, file_b):
        if not path.exists():
            raise InputFileError(f"input file does not exist: {path}")
    _, dups_a, total_a, _ = _index_file(
        file_a,
        config.parser_a,
        config.segments_a,
        config.file_a_rdw,
        config.file_a_strip_size,
    )
    _, dups_b, total_b, _ = _index_file(
        file_b,
        config.parser_b,
        config.segments_b,
        config.file_b_rdw,
        config.file_b_strip_size,
    )
    return DryRunReport(
        file_a_records=total_a,
        file_b_records=total_b,
        dups_in_a=sum(len(v) for v in dups_a.values()),
        dups_in_b=sum(len(v) for v in dups_b.values()),
    )


def run(
    file_a: Path,
    file_b: Path,
    config: EngineConfig,
    output_dir: Path,
    run_timestamp: datetime | None = None,
    external_sort: bool = False,
) -> Summary:
    """Execute one end-to-end comparison and return its :class:`Summary`.

    Args:
        file_a: Path to File A.
        file_b: Path to File B.
        config: Validated configuration produced by
            :func:`segment_compare.config.load_config`.
        output_dir: Directory to write the eight outputs into. Created
            if absent.
        run_timestamp: Optional UTC timestamp that drives the
            ``YYYYMMDDHHMM`` suffix on every output filename. Defaults
            to wall-clock now if omitted. **This does not influence
            the elapsed-time measurement** — ``summary.start_time``
            and ``elapsed_seconds`` always reflect real wall clock so
            throughput numbers are honest even when tests pass a
            fixed stamp for filename determinism.

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

    start_time = datetime.now(timezone.utc)
    filename_stamp = (run_timestamp or start_time).strftime(STAMP_FORMAT)
    logger.info("starting comparison: %s vs %s (stamp=%s)", file_a, file_b, filename_stamp)

    original_file_a, original_file_b = file_a, file_b
    rdw_a: RdwConfig | None = config.file_a_rdw
    rdw_b: RdwConfig | None = config.file_b_rdw
    strip_a = config.file_a_strip_size
    strip_b = config.file_b_strip_size
    parser_a = config.parser_a
    parser_b = config.parser_b
    segments_a_cfg = config.segments_a
    segments_b_cfg = config.segments_b
    if (
        external_sort
        or not config.layout_a.sort.input_sorted
        or not config.layout_b.sort.input_sorted
    ):
        file_a, file_b = _external_sort_inputs(file_a, file_b, config, filename_stamp)
        # The sorted temp copies are written by the engine without RDW or
        # leading-byte strip prefixes, so downstream passes must not try
        # to skip them.
        rdw_a = None
        rdw_b = None
        strip_a = 0
        strip_b = 0

    index_a, dups_a, total_a, segments_a = _index_file(
        file_a, parser_a, segments_a_cfg, rdw_a, strip_a
    )
    index_b, dups_b, total_b, segments_b = _index_file(
        file_b, parser_b, segments_b_cfg, rdw_b, strip_b
    )
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

    normalizer = FieldNormalizer(config.normalization)
    hasher = build_hasher(config.runtime)

    records_matched = 0
    records_mismatched = 0
    per_segment_match: dict[str, int] = defaultdict(int)
    per_segment_mismatch: dict[str, int] = defaultdict(int)

    with OutputWriter(output_dir, segments_a_cfg, filename_stamp=filename_stamp) as writer:
        _write_dups(file_a, dups_a, parser_a, segments_a_cfg, writer.write_dup_a)
        _write_dups(file_b, dups_b, parser_b, segments_b_cfg, writer.write_dup_b)

        with file_a.open("rb") as fh_a, file_b.open("rb") as fh_b:
            for key in both_keys:
                off_a, len_a = index_a[key]
                off_b, len_b = index_b[key]
                rec_a = _read_record_at(fh_a, off_a, len_a, parser_a, segments_a_cfg)
                rec_b = _read_record_at(fh_b, off_b, len_b, parser_b, segments_b_cfg)
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

        _write_key_only(
            file_a, only_a_keys, index_a, parser_a, segments_a_cfg, writer.write_key_only_a
        )
        _write_key_only(
            file_b, only_b_keys, index_b, parser_b, segments_b_cfg, writer.write_key_only_b
        )

        end_time = datetime.now(timezone.utc)
        elapsed = (end_time - start_time).total_seconds()
        total_processed = total_a + total_b
        throughput = total_processed / elapsed if elapsed > 0 else 0.0

        per_segment = _build_per_segment_summary(
            per_segment_match, per_segment_mismatch, segments_a, segments_b
        )

        summary = Summary(
            file_a_path=original_file_a,
            file_b_path=original_file_b,
            file_a_size_bytes=original_file_a.stat().st_size,
            file_b_size_bytes=original_file_b.stat().st_size,
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


def run_parallel(
    file_a: Path,
    file_b: Path,
    config: EngineConfig,
    output_dir: Path,
    workers: int,
    run_timestamp: datetime | None = None,
    external_sort: bool = False,
) -> Summary:
    """Multi-worker variant of :func:`run`.

    Sequence:

    1. Master builds the key→offset index for File A and File B
       (single-process; this stage dominates the pre-join time).
    2. Master partitions the sorted inner-join key set across
       ``workers`` workers (equal-count partitioning, ADR-006).
    3. Master writes orphan-key records and duplicate-key records
       directly to ``keymismatch_*.dat`` / ``dups_*.dat`` (cheap;
       these don't go through the join loop).
    4. Workers each process their key slice in a child process,
       writing per-worker ``matches.dat`` / ``mismatches.dat`` /
       ``report.csv`` under ``<output_dir>/_workers/w<wid>/``.
    5. Master concatenates per-worker outputs into the stamped
       run-level files and folds partial summaries into the global
       :class:`Summary`.

    Produces output byte-identical to :func:`run` when invoked with
    the same inputs (acceptance criterion #2). Differences in
    ``elapsed_seconds`` / ``throughput_records_per_sec`` /
    ``start_time`` / ``end_time`` are expected.

    Args:
        workers: Number of worker processes to spawn. Must be ≥ 1.
            Passing 1 still goes through this parallel path (useful
            for benchmarking the orchestration overhead); use
            :func:`run` directly for the truly single-process path.
        run_timestamp: Optional filename-stamp source. See
            :func:`run` for semantics.

    Raises:
        InputFileError: Either input file does not exist.
        ValueError: ``workers < 1``.
    """
    if workers < 1:
        raise ValueError(f"workers must be >= 1, got {workers}")
    for path in (file_a, file_b):
        if not path.exists():
            raise InputFileError(f"input file does not exist: {path}")

    start_time = datetime.now(timezone.utc)
    filename_stamp = (run_timestamp or start_time).strftime(STAMP_FORMAT)
    logger.info(
        "starting parallel comparison: %s vs %s (workers=%d, stamp=%s)",
        file_a,
        file_b,
        workers,
        filename_stamp,
    )

    original_file_a, original_file_b = file_a, file_b
    rdw_a: RdwConfig | None = config.file_a_rdw
    rdw_b: RdwConfig | None = config.file_b_rdw
    strip_a = config.file_a_strip_size
    strip_b = config.file_b_strip_size
    parser_a = config.parser_a
    parser_b = config.parser_b
    segments_a_cfg = config.segments_a
    segments_b_cfg = config.segments_b
    if (
        external_sort
        or not config.layout_a.sort.input_sorted
        or not config.layout_b.sort.input_sorted
    ):
        file_a, file_b = _external_sort_inputs(file_a, file_b, config, filename_stamp)
        rdw_a = None
        rdw_b = None
        strip_a = 0
        strip_b = 0

    index_a, dups_a, total_a, segments_a = _index_file(
        file_a, parser_a, segments_a_cfg, rdw_a, strip_a
    )
    index_b, dups_b, total_b, segments_b = _index_file(
        file_b, parser_b, segments_b_cfg, rdw_b, strip_b
    )
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

    output_dir.mkdir(parents=True, exist_ok=True)
    workers_root = output_dir / "_workers"

    # Master-owned outputs (orphans + dups). Written single-process via the
    # normal OutputWriter; matches/mismatches/report stay empty in the master
    # writer because those come from workers and are merged in afterwards.
    with OutputWriter(output_dir, segments_a_cfg, filename_stamp=filename_stamp) as master_writer:
        _write_dups(file_a, dups_a, parser_a, segments_a_cfg, master_writer.write_dup_a)
        _write_dups(file_b, dups_b, parser_b, segments_b_cfg, master_writer.write_dup_b)
        _write_key_only(
            file_a, only_a_keys, index_a, parser_a, segments_a_cfg, master_writer.write_key_only_a
        )
        _write_key_only(
            file_b, only_b_keys, index_b, parser_b, segments_b_cfg, master_writer.write_key_only_b
        )
        # Drop the master's empty matches.dat / mismatches.dat / report.csv;
        # the merger overwrites these paths anyway, but deleting now keeps
        # the on-disk state coherent if a worker crashes before merging.
        master_writer.path_for("matches.dat").unlink(missing_ok=True)
        master_writer.path_for("mismatches.dat").unlink(missing_ok=True)
        master_writer.path_for("report.csv").unlink(missing_ok=True)
        # summary.json is written below, after the merge.
        master_writer.path_for("summary.json").unlink(missing_ok=True)

    # Build payloads and spawn workers.
    chunks = equal_count_partition(both_keys, workers)
    payloads: list[WorkerPayload] = []
    for wid, chunk in enumerate(chunks):
        worker_dir = workers_root / f"w{wid}"
        payloads.append(
            WorkerPayload(
                worker_id=wid,
                keys=tuple(chunk),
                offsets_a={k: index_a[k] for k in chunk},
                offsets_b={k: index_b[k] for k in chunk},
                file_a=file_a,
                file_b=file_b,
                config=config,
                worker_output_dir=worker_dir,
            )
        )

    if workers == 1:
        # Run the single worker inline. ProcessPoolExecutor with max_workers=1
        # would also work but adds spawn overhead with no parallelism benefit.
        results: list[WorkerResult] = [run_worker(payloads[0])]
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(run_worker, payloads))

    # Merge per-worker outputs into the stamped run outputs.
    worker_dirs = [workers_root / f"w{wid}" for wid in range(workers)]
    merge_worker_outputs(worker_dirs, output_dir, filename_stamp)

    records_matched, records_mismatched, per_seg_match, per_seg_mismatch = fold_partial_summaries(
        results
    )

    end_time = datetime.now(timezone.utc)
    elapsed = (end_time - start_time).total_seconds()
    total_processed = total_a + total_b
    throughput = total_processed / elapsed if elapsed > 0 else 0.0

    per_segment = _build_per_segment_summary(
        per_seg_match, per_seg_mismatch, segments_a, segments_b
    )

    summary = Summary(
        file_a_path=original_file_a,
        file_b_path=original_file_b,
        file_a_size_bytes=original_file_a.stat().st_size,
        file_b_size_bytes=original_file_b.stat().st_size,
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

    # Write summary.json (under the run output dir, stamped).
    write_summary(summary, output_dir / stamped_filename("summary.json", filename_stamp))

    # Optional: clean up the per-worker scratch dir. Leave it for now so
    # debugging is easier; a future ADR can flip this once the path is
    # stable.

    logger.info(
        "parallel comparison complete: %d matched, %d mismatched, %d orphans, "
        "elapsed=%.3fs across %d workers",
        records_matched,
        records_mismatched,
        len(only_a_keys) + len(only_b_keys),
        elapsed,
        workers,
    )
    return summary


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _index_file(
    path: Path,
    parser_cfg: ParserConfig,
    segments_cfg: SegmentsConfig,
    rdw_cfg: RdwConfig | None,
    strip_size: int,
) -> tuple[
    dict[str, tuple[int, int]],
    dict[str, list[tuple[int, int]]],
    int,
    Counter[str],
]:
    """Single streaming pass building the key index, dup map, and counts.

    Args:
        path: File to scan.
        parser_cfg: This file's byte-level parser knobs.
        segments_cfg: This file's record-framing config (per-file
            key_segment / end_segment / key_range / record_delimiter).
        rdw_cfg: Optional per-file RDW prefix to skip before each record.
            Pass ``None`` when reading an engine-written file (sorted
            temp output) since those never carry an RDW prefix.
        strip_size: Per-record opaque leading-byte strip, 0 if absent.

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
        for record in iter_records(
            fh, parser_cfg, segments_cfg, rdw_cfg, strip_leading_bytes=strip_size
        ):
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


def _read_record_at(
    stream: BinaryIO,
    offset: int,
    length: int,
    parser_cfg: ParserConfig,
    segments_cfg: SegmentsConfig,
) -> Record:
    """Seek to ``offset`` in ``stream`` and parse the record there.

    ``offset`` and ``length`` already point past any RDW or
    leading-byte strip (set during :func:`_index_file`), so no
    additional prefix-skipping is needed here.
    """
    stream.seek(offset)
    buf = stream.read(length)
    parsed = list(iter_records(io.BytesIO(buf), parser_cfg, segments_cfg))
    if not parsed:
        raise InputFileError(f"no record could be parsed at offset {offset} (length {length})")
    return parsed[0]


def _write_dups(
    path: Path,
    dups: dict[str, list[tuple[int, int]]],
    parser_cfg: ParserConfig,
    segments_cfg: SegmentsConfig,
    write_fn: "object",
) -> None:
    """Write every duplicate-key record's bytes via ``write_fn``."""
    if not dups:
        return
    with path.open("rb") as fh:
        for entries in dups.values():
            for off, length in entries:
                rec = _read_record_at(fh, off, length, parser_cfg, segments_cfg)
                write_fn(rec)  # type: ignore[operator]


def _write_key_only(
    path: Path,
    keys: list[str],
    index: dict[str, tuple[int, int]],
    parser_cfg: ParserConfig,
    segments_cfg: SegmentsConfig,
    write_fn: "object",
) -> None:
    """Write each orphan-key record's bytes via ``write_fn``."""
    if not keys:
        return
    with path.open("rb") as fh:
        for key in keys:
            off, length = index[key]
            rec = _read_record_at(fh, off, length, parser_cfg, segments_cfg)
            write_fn(rec)  # type: ignore[operator]


def _external_sort_inputs(
    file_a: Path, file_b: Path, config: EngineConfig, filename_stamp: str
) -> tuple[Path, Path]:
    """Sort both inputs via the external-sort pass and return the sorted paths.

    The sorted copies land in ``config.runtime.sort_temp_dir`` with names
    keyed by ``filename_stamp`` so concurrent runs in the same temp
    directory don't collide. The originals are not modified; callers
    use the returned paths for the rest of the pipeline.
    """
    sort_dir = config.runtime.sort_temp_dir
    sort_dir.mkdir(parents=True, exist_ok=True)
    sorted_a = sort_dir / f"sorted_a_{filename_stamp}.dat"
    sorted_b = sort_dir / f"sorted_b_{filename_stamp}.dat"
    logger.info("external-sort: %s -> %s", file_a, sorted_a)
    external_sort_file(
        file_a,
        sorted_a,
        config.parser_a,
        config.segments_a,
        config.runtime.chunk_size,
        config.runtime.sort_temp_dir,
        rdw_cfg=config.file_a_rdw,
        strip_size=config.file_a_strip_size,
    )
    logger.info("external-sort: %s -> %s", file_b, sorted_b)
    external_sort_file(
        file_b,
        sorted_b,
        config.parser_b,
        config.segments_b,
        config.runtime.chunk_size,
        config.runtime.sort_temp_dir,
        rdw_cfg=config.file_b_rdw,
        strip_size=config.file_b_strip_size,
    )
    return sorted_a, sorted_b


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
