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
from segment_compare.layout import SegmentAlias
from segment_compare.merger import fold_partial_summaries, merge_worker_outputs
from segment_compare.normalizer import FieldNormalizer
from segment_compare.parser import (
    ParserConfig,
    Record,
    RdwConfig,
    Segment,
    SegmentsConfig,
    iter_records,
)
from segment_compare.partitioner import equal_count_partition
from segment_compare.worker import WorkerPayload, WorkerResult, run_worker
from segment_compare.writer import (
    DUPS_A_COUNT_FILE,
    DUPS_B_COUNT_FILE,
    DUPS_SAMPLE_SIZE,
    MATCH_SAMPLE_SIZE,
    MATCHES_FILE,
    MATCHES_SAMPLE_SIZE,
    MISMATCH_SAMPLE_SIZE,
    MISMATCHES_FILE,
    ORPHANS_SAMPLE_SIZE,
    RUN_DIR_FORMAT,
    STAMP_FORMAT,
    CompareReports,
    DupCount,
    KeyMatrixEntry,
    MismatchSample,
    OutputWriter,
    RecordSample,
    RunSamples,
    SegmentSummary,
    Summary,
    build_key_matrix_entry,
    write_compare_reports_csv,
    write_compare_reports_html,
    write_dups_count_report,
    write_keys_mismatch_matrix_csv,
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
        aliases=config.file_a_aliases,
    )
    _, dups_b, total_b, _ = _index_file(
        file_b,
        config.parser_b,
        config.segments_b,
        config.file_b_rdw,
        config.file_b_strip_size,
        aliases=config.file_b_aliases,
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
    effective_ts = run_timestamp or start_time
    filename_stamp = effective_ts.strftime(STAMP_FORMAT)
    run_dir_name = effective_ts.strftime(RUN_DIR_FORMAT)
    run_output_dir = output_dir / run_dir_name
    logger.info("starting comparison: %s vs %s (run=%s)", file_a, file_b, run_dir_name)

    original_file_a, original_file_b = file_a, file_b
    rdw_a: RdwConfig | None = config.file_a_rdw
    rdw_b: RdwConfig | None = config.file_b_rdw
    strip_a = config.file_a_strip_size
    strip_b = config.file_b_strip_size
    parser_a = config.parser_a
    parser_b = config.parser_b
    segments_a_cfg = config.segments_a
    segments_b_cfg = config.segments_b
    aliases_a = config.file_a_aliases
    aliases_b = config.file_b_aliases
    if (
        external_sort
        or not config.layout_a.sort.input_sorted
        or not config.layout_b.sort.input_sorted
    ):
        file_a, file_b = _external_sort_inputs(file_a, file_b, config, filename_stamp)
        # The sorted temp copies are written by the engine without RDW or
        # leading-byte strip prefixes, so downstream passes must not try
        # to skip them. Aliases stay live since the sorted output still
        # carries on-wire segment names.
        rdw_a = None
        rdw_b = None
        strip_a = 0
        strip_b = 0

    index_a, dups_a, total_a, segments_a = _index_file(
        file_a, parser_a, segments_a_cfg, rdw_a, strip_a, aliases=aliases_a
    )
    index_b, dups_b, total_b, segments_b = _index_file(
        file_b, parser_b, segments_b_cfg, rdw_b, strip_b, aliases=aliases_b
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
    key_matrix_entries: list[KeyMatrixEntry] = []
    match_samples: list[RecordSample] = []
    mismatch_samples: list[MismatchSample] = []

    # Per-run subdir (ADR-037): all 11 outputs land inside run_output_dir
    # with bare filenames since the dir name already disambiguates runs.
    with OutputWriter(run_output_dir, segments_a_cfg) as writer:
        _write_dups(file_a, dups_a, parser_a, segments_a_cfg, writer.write_dup_a)
        _write_dups(file_b, dups_b, parser_b, segments_b_cfg, writer.write_dup_b)

        with file_a.open("rb") as fh_a, file_b.open("rb") as fh_b:
            for key in both_keys:
                off_a, len_a = index_a[key]
                off_b, len_b = index_b[key]
                rec_a = _read_record_at(fh_a, off_a, len_a, parser_a, segments_a_cfg, aliases_a)
                rec_b = _read_record_at(fh_b, off_b, len_b, parser_b, segments_b_cfg, aliases_b)
                verdict = compare_records(rec_a, rec_b, normalizer, hasher)

                if verdict.matched:
                    # matches.dat carries only a sample (ADR-038); the
                    # records_matched counter still reflects every match.
                    if records_matched < MATCHES_SAMPLE_SIZE:
                        writer.write_match(rec_a)
                    if len(match_samples) < MATCH_SAMPLE_SIZE:
                        match_samples.append(RecordSample(key, _decode_raw(rec_a.raw)))
                    records_matched += 1
                else:
                    writer.write_mismatch(verdict, rec_a, rec_b)
                    if len(mismatch_samples) < MISMATCH_SAMPLE_SIZE:
                        mismatch_samples.append(
                            MismatchSample(key, _decode_raw(rec_a.raw), _decode_raw(rec_b.raw))
                        )
                    records_mismatched += 1
                    key_matrix_entries.append(build_key_matrix_entry(verdict))

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
            filename_stamp=run_dir_name,
        )
        dups_a_s, dups_b_s, orphans_a_s, orphans_b_s = _dup_orphan_samples(
            dups_a, dups_b, only_a_keys, only_b_keys
        )
        _write_dup_count_reports(dups_a, dups_b, run_output_dir)
        writer.finalize(
            CompareReports(
                summary=summary,
                layout_a=config.layout_a,
                layout_b=config.layout_b,
                key_matrix_entries=tuple(key_matrix_entries),
                matrix_segments=config.known_segments,
                output_dir=run_output_dir,
                samples=RunSamples(
                    matches=tuple(match_samples),
                    mismatches=tuple(mismatch_samples),
                    dups_a=dups_a_s,
                    dups_b=dups_b_s,
                    orphans_a=orphans_a_s,
                    orphans_b=orphans_b_s,
                ),
            )
        )

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
    effective_ts = run_timestamp or start_time
    filename_stamp = effective_ts.strftime(STAMP_FORMAT)
    run_dir_name = effective_ts.strftime(RUN_DIR_FORMAT)
    run_output_dir = output_dir / run_dir_name
    logger.info(
        "starting parallel comparison: %s vs %s (workers=%d, run=%s)",
        file_a,
        file_b,
        workers,
        run_dir_name,
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
    aliases_a = config.file_a_aliases
    aliases_b = config.file_b_aliases
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
        file_a, parser_a, segments_a_cfg, rdw_a, strip_a, aliases=aliases_a
    )
    index_b, dups_b, total_b, segments_b = _index_file(
        file_b, parser_b, segments_b_cfg, rdw_b, strip_b, aliases=aliases_b
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

    # Per-run subdir (ADR-037): every output for this run lives under
    # run_output_dir; the per-worker scratch dirs sit beside the merged
    # files under that subdir so a single run remains a self-contained
    # bundle on disk.
    run_output_dir.mkdir(parents=True, exist_ok=True)
    workers_root = run_output_dir / "_workers"

    # Master-owned outputs (orphans + dups). Written single-process via the
    # normal OutputWriter; matches/mismatches/report stay empty in the master
    # writer because those come from workers and are merged in afterwards.
    with OutputWriter(run_output_dir, segments_a_cfg) as master_writer:
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
        # summary.json + compare_reports.* + keys_mismatch_matrix.csv are
        # written below, after the merge.
        master_writer.path_for("summary.json").unlink(missing_ok=True)
        master_writer.path_for("compare_reports.csv").unlink(missing_ok=True)
        master_writer.path_for("compare_reports.html").unlink(missing_ok=True)
        master_writer.path_for("keys_mismatch_matrix.csv").unlink(missing_ok=True)

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

    # Merge per-worker outputs into the run-level bare-name outputs.
    worker_dirs = [workers_root / f"w{wid}" for wid in range(workers)]
    merge_worker_outputs(worker_dirs, run_output_dir, "")
    # Workers each emit every matched record into their slice; the master
    # caps the merged matches.dat to MATCHES_SAMPLE_SIZE records so the
    # parallel path matches the single-process behavior (ADR-038).
    _truncate_matches_sample(
        run_output_dir / MATCHES_FILE, MATCHES_SAMPLE_SIZE, segments_a_cfg.record_delimiter
    )

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
        filename_stamp=run_dir_name,
    )

    # Concatenate per-worker matrix entries in worker-id order so the
    # final file mirrors the join's sorted-key order (ADR-036).
    all_matrix_entries = tuple(entry for r in results for entry in r.key_matrix_entries)

    # Report samples (ADR-040). dups/orphans come from master memory; match/
    # mismatch are read back from the merged files (workers wrote them, the
    # master has no in-memory copy). The merged files are complete + closed by
    # now, so the read-back is safe in both the inline and pooled worker modes.
    dups_a_s, dups_b_s, orphans_a_s, orphans_b_s = _dup_orphan_samples(
        dups_a, dups_b, only_a_keys, only_b_keys
    )
    samples = RunSamples(
        matches=_read_match_samples(
            run_output_dir / MATCHES_FILE, parser_a, segments_a_cfg, MATCH_SAMPLE_SIZE
        ),
        mismatches=_parse_mismatch_samples(run_output_dir / MISMATCHES_FILE, MISMATCH_SAMPLE_SIZE),
        dups_a=dups_a_s,
        dups_b=dups_b_s,
        orphans_a=orphans_a_s,
        orphans_b=orphans_b_s,
    )
    reports = CompareReports(
        summary=summary,
        layout_a=config.layout_a,
        layout_b=config.layout_b,
        key_matrix_entries=all_matrix_entries,
        matrix_segments=config.known_segments,
        output_dir=run_output_dir,
        samples=samples,
    )

    # Write summary.json + the three human reports (ADR-035, ADR-036,
    # ADR-037), bare-named, all inside the per-run subdir.
    write_summary(summary, run_output_dir / "summary.json")
    write_compare_reports_csv(reports, run_output_dir / "compare_reports.csv")
    write_compare_reports_html(reports, run_output_dir / "compare_reports.html")
    write_keys_mismatch_matrix_csv(reports, run_output_dir / "keys_mismatch_matrix.csv")
    _write_dup_count_reports(dups_a, dups_b, run_output_dir)

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


def _apply_aliases(record: Record, aliases: tuple[SegmentAlias, ...]) -> Record:
    """Apply context-sensitive segment renames to a parsed record (ADR-034).

    Walks the record's segments in order. The instant a segment named
    ``alias.after_segment`` is seen, the alias is "armed" — every
    subsequent segment named ``alias.wire_name`` in this record gets
    renamed to ``alias.logical_name`` (raw bytes unchanged; only the
    in-memory :attr:`Segment.name` differs).

    Returns the original record unchanged when ``aliases`` is empty
    or when no segment matched any rename rule — preserving the
    no-aliases fast path's identity behavior.
    """
    if not aliases:
        return record
    armed: set[str] = set()
    triggers = {a.after_segment for a in aliases}
    wire_to_alias = {a.wire_name: a for a in aliases}
    new_segments: list[Segment] = []
    changed = False
    for seg in record.segments:
        if seg.name in triggers:
            armed.add(seg.name)
        alias = wire_to_alias.get(seg.name)
        if alias is not None and alias.after_segment in armed:
            new_segments.append(
                Segment(name=alias.logical_name, size=seg.size, data=seg.data, offset=seg.offset)
            )
            changed = True
        else:
            new_segments.append(seg)
    if not changed:
        return record
    return Record(
        key=record.key,
        segments=tuple(new_segments),
        raw=record.raw,
        offset=record.offset,
        length=record.length,
    )


def _index_file(
    path: Path,
    parser_cfg: ParserConfig,
    segments_cfg: SegmentsConfig,
    rdw_cfg: RdwConfig | None,
    strip_size: int,
    aliases: tuple[SegmentAlias, ...] = (),
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
            record = _apply_aliases(record, aliases)
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
    aliases: tuple[SegmentAlias, ...] = (),
) -> Record:
    """Seek to ``offset`` in ``stream`` and parse the record there.

    ``offset`` and ``length`` already point past any RDW or
    leading-byte strip (set during :func:`_index_file`), so no
    additional prefix-skipping is needed here. The configured
    segment aliases are applied so the returned record carries the
    same logical segment names as the records that the index pass saw.
    """
    stream.seek(offset)
    buf = stream.read(length)
    parsed = list(iter_records(io.BytesIO(buf), parser_cfg, segments_cfg))
    if not parsed:
        raise InputFileError(f"no record could be parsed at offset {offset} (length {length})")
    return _apply_aliases(parsed[0], aliases)


def _decode_raw(raw: bytes) -> str:
    """Decode a record's raw bytes for display in the HTML report (ADR-040)."""
    return raw.decode("ascii", errors="replace").rstrip("\n")


def _dup_orphan_samples(
    dups_a: dict[str, list[tuple[int, int]]],
    dups_b: dict[str, list[tuple[int, int]]],
    only_a_keys: list[str],
    only_b_keys: list[str],
) -> tuple[tuple[DupCount, ...], tuple[DupCount, ...], tuple[str, ...], tuple[str, ...]]:
    """Build the dup-count + orphan-key samples (same in both pipeline paths)."""
    dca = tuple(DupCount(k, len(dups_a[k])) for k in sorted(dups_a)[:DUPS_SAMPLE_SIZE])
    dcb = tuple(DupCount(k, len(dups_b[k])) for k in sorted(dups_b)[:DUPS_SAMPLE_SIZE])
    return (
        dca,
        dcb,
        tuple(only_a_keys[:ORPHANS_SAMPLE_SIZE]),
        tuple(only_b_keys[:ORPHANS_SAMPLE_SIZE]),
    )


def _write_dup_count_reports(
    dups_a: dict[str, list[tuple[int, int]]],
    dups_b: dict[str, list[tuple[int, int]]],
    run_output_dir: Path,
) -> None:
    """Write the full per-key dup-count CSVs for both files (ADR-040)."""
    write_dups_count_report(
        {k: len(v) for k, v in dups_a.items()}, run_output_dir / DUPS_A_COUNT_FILE
    )
    write_dups_count_report(
        {k: len(v) for k, v in dups_b.items()}, run_output_dir / DUPS_B_COUNT_FILE
    )


def _read_match_samples(
    path: Path, parser_cfg: ParserConfig, segments_cfg: SegmentsConfig, limit: int
) -> tuple[RecordSample, ...]:
    """Read up to ``limit`` matched records back from the merged matches.dat."""
    if not path.exists():
        return ()
    out: list[RecordSample] = []
    with path.open("rb") as fh:
        for rec in iter_records(fh, parser_cfg, segments_cfg):
            out.append(RecordSample(rec.key, _decode_raw(rec.raw)))
            if len(out) >= limit:
                break
    return tuple(out)


def _parse_mismatch_samples(path: Path, limit: int) -> tuple[MismatchSample, ...]:
    """Parse up to ``limit`` side-by-side blocks back from the merged mismatches.dat.

    Blocks are written by :meth:`OutputWriter.write_mismatch` as::

        === KEY: <key> | MISMATCH: <segs> ===
        --- FILE A ---
        <raw bytes>
        --- FILE B ---
        <raw bytes>
    """
    if not path.exists():
        return ()
    text = path.read_text(encoding="ascii", errors="replace")
    out: list[MismatchSample] = []
    for block in text.split("=== KEY: ")[1:]:
        if len(out) >= limit:
            break
        header, _, rest = block.partition("\n")
        key = header.split(" | ", 1)[0].strip()
        if "--- FILE A ---\n" not in rest or "--- FILE B ---\n" not in rest:
            continue
        _, _, after_a = rest.partition("--- FILE A ---\n")
        a_part, _, after_b = after_a.partition("--- FILE B ---\n")
        out.append(MismatchSample(key, a_part.strip("\n"), after_b.strip("\n")))
    return tuple(out)


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


def _truncate_matches_sample(path: Path, sample_size: int, delimiter: bytes) -> None:
    """Cap ``matches.dat`` at the first ``sample_size`` records (ADR-038).

    The post-merge file is read once, scanned for record boundaries
    (the configured per-record delimiter), and rewritten with at most
    ``sample_size`` records. No-op when ``path`` doesn't exist or is
    already at or below the cap.
    """
    if sample_size <= 0 or not path.exists():
        return
    if not delimiter:
        # Records are back-to-back with no separator — the engine can't
        # find boundaries without parsing. Skip the truncation in that
        # mode; the operator can opt back in by setting a delimiter.
        return
    raw = path.read_bytes()
    if not raw:
        return
    kept: list[bytes] = []
    pos = 0
    count = 0
    while count < sample_size:
        idx = raw.find(delimiter, pos)
        if idx == -1:
            # Last record without a trailing delimiter — keep it whole.
            kept.append(raw[pos:])
            count += 1
            pos = len(raw)
            break
        kept.append(raw[pos : idx + len(delimiter)])
        pos = idx + len(delimiter)
        count += 1
    path.write_bytes(b"".join(kept))


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
