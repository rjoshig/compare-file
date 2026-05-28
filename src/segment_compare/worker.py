"""Worker subprocess entry point for Phase 2 parallel comparison.

A worker is handed:

- a slice of the sorted inner-join key list,
- the ``(offset, length)`` for each of those keys in File A and File B,
- the resolved config,
- paths to the source files,
- a per-worker output directory under ``<run_output_dir>/_workers/w<wid>/``.

It opens both source files, seeks to each key's records, normalizes,
hashes, compares, and writes per-worker ``matches.dat`` /
``mismatches.dat`` / ``report.csv``. It does **not** write
``keymismatch_*.dat`` or ``dups_*.dat`` — those are produced by the
master process which holds the global key sets and the duplicate map.

The worker returns a :class:`WorkerResult` so the master can fold
per-segment counts into the run-global ``summary.json``.

This module is import-clean and pickle-safe: ``run_worker`` is a
top-level function and :class:`WorkerPayload` / :class:`WorkerResult`
are plain frozen dataclasses, so ``concurrent.futures.ProcessPoolExecutor``
can ship them across the process boundary without ceremony.
"""

from __future__ import annotations

import io
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from segment_compare.comparator import compare_records
from segment_compare.config import ResolvedConfig
from segment_compare.hasher import build_hasher
from segment_compare.normalizer import PositionNormalizer
from segment_compare.parser import Record, iter_records
from segment_compare.writer import OutputWriter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerPayload:
    """Everything one worker needs to process its key slice.

    Attributes:
        worker_id: Zero-based identifier; appears in worker subdir
            name and log lines.
        keys: The slice of inner-join keys this worker owns
            (sorted; subset of the global ``both`` set).
        offsets_a: Mapping ``key -> (offset, length)`` in File A for
            every key in ``keys``.
        offsets_b: Same for File B.
        file_a: Path to File A.
        file_b: Path to File B.
        config: The full :class:`ResolvedConfig`. Pickled across the
            process boundary; the per-worker normalizer + hasher are
            rebuilt locally.
        worker_output_dir: Where this worker writes its per-worker
            output files. Created by the master before spawn.
    """

    worker_id: int
    keys: tuple[str, ...]
    offsets_a: dict[str, tuple[int, int]]
    offsets_b: dict[str, tuple[int, int]]
    file_a: Path
    file_b: Path
    config: ResolvedConfig
    worker_output_dir: Path


@dataclass(frozen=True)
class WorkerResult:
    """Summary of one worker's work.

    Per-segment counters cover only the segments this worker saw —
    the master sums them across workers to build the global
    ``per_segment`` block in ``summary.json``.

    Attributes:
        worker_id: Echoes :attr:`WorkerPayload.worker_id`.
        records_matched: Joined records this worker classified as
            fully matching.
        records_mismatched: Joined records this worker classified as
            having at least one mismatched segment type.
        per_segment_match: ``segment_name -> match_count`` over this
            worker's records.
        per_segment_mismatch: ``segment_name -> mismatch_count`` over
            this worker's records.
        elapsed_seconds: Wall time inside the worker, from spawn to
            return. Useful for diagnosing worker imbalance.
    """

    worker_id: int
    records_matched: int
    records_mismatched: int
    per_segment_match: dict[str, int] = field(default_factory=dict)
    per_segment_mismatch: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0


def run_worker(payload: WorkerPayload) -> WorkerResult:
    """Process one key slice and write per-worker outputs.

    Pickle-safe top-level entry point used by
    ``concurrent.futures.ProcessPoolExecutor``. Errors propagate as
    normal exceptions — the master treats any worker exception as a
    fatal run error.
    """
    start = time.perf_counter()
    config = payload.config
    payload.worker_output_dir.mkdir(parents=True, exist_ok=True)

    normalizer = PositionNormalizer(config.normalization)
    hasher = build_hasher(config.runtime)

    matched = 0
    mismatched = 0
    per_segment_match: dict[str, int] = defaultdict(int)
    per_segment_mismatch: dict[str, int] = defaultdict(int)

    # Per-worker outputs use bare filenames (no stamp, no worker suffix);
    # the worker subdir already disambiguates them. The merger concatenates
    # these into the run-level stamped files.
    with (
        OutputWriter(payload.worker_output_dir, config.segments) as writer,
        payload.file_a.open("rb") as fh_a,
        payload.file_b.open("rb") as fh_b,
    ):
        for key in payload.keys:
            off_a, len_a = payload.offsets_a[key]
            off_b, len_b = payload.offsets_b[key]
            rec_a = _read_record_at(fh_a, off_a, len_a, config)
            rec_b = _read_record_at(fh_b, off_b, len_b, config)
            verdict = compare_records(rec_a, rec_b, normalizer, hasher)

            if verdict.matched:
                writer.write_match(rec_a)
                matched += 1
            else:
                writer.write_mismatch(verdict, rec_a, rec_b)
                mismatched += 1

            for sv in verdict.segment_verdicts:
                if sv.matched:
                    per_segment_match[sv.segment_name] += 1
                else:
                    per_segment_mismatch[sv.segment_name] += 1

    elapsed = time.perf_counter() - start
    logger.info(
        "worker %d: %d keys, %d matched, %d mismatched, %.3f s",
        payload.worker_id,
        len(payload.keys),
        matched,
        mismatched,
        elapsed,
    )

    return WorkerResult(
        worker_id=payload.worker_id,
        records_matched=matched,
        records_mismatched=mismatched,
        per_segment_match=dict(per_segment_match),
        per_segment_mismatch=dict(per_segment_mismatch),
        elapsed_seconds=elapsed,
    )


def _read_record_at(stream: BinaryIO, offset: int, length: int, config: ResolvedConfig) -> Record:
    """Seek to ``offset`` and parse the record sitting there.

    Mirrors ``pipeline._read_record_at`` so behavior is identical to
    the single-process path.
    """
    stream.seek(offset)
    buf = stream.read(length)
    parsed = list(iter_records(io.BytesIO(buf), config.parser, config.segments))
    if not parsed:
        raise RuntimeError(f"no record at offset {offset} (length {length}) in worker slice")
    return parsed[0]
