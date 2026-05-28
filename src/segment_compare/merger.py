"""Per-worker output merger for Phase 2 parallel comparison.

After every worker has finished, the master process must turn N sets
of per-worker files into one set of run-level outputs:

- ``w0/matches.dat`` + ``w1/matches.dat`` + … →
  ``<output_dir>/matches_<stamp>.dat``
- same for ``mismatches.dat``,
- ``w0/report.csv`` (header + rows) + ``w1/report.csv`` (rows only) +
  … → ``<output_dir>/report_<stamp>.csv``.

Concatenating in worker-id order preserves global key order because
the partitioner emits chunks in source order and each worker
processes its chunk sorted. Per-worker per-segment counters are
folded into a single global mapping.

The keymismatch and dups outputs are written by the master directly
(it owns the global key sets and the duplicate map), so this module
only handles the three worker-produced output kinds.
"""

from __future__ import annotations

import logging
import shutil
from collections import defaultdict
from pathlib import Path

from segment_compare.worker import WorkerResult
from segment_compare.writer import (
    MATCHES_FILE,
    MISMATCHES_FILE,
    REPORT_FILE,
    stamped_filename,
)

logger = logging.getLogger(__name__)


# Per-worker filenames (bare, no stamp — the worker subdir disambiguates).
_WORKER_MATCHES = MATCHES_FILE
_WORKER_MISMATCHES = MISMATCHES_FILE
_WORKER_REPORT = REPORT_FILE


def merge_worker_outputs(
    worker_dirs: list[Path],
    output_dir: Path,
    filename_stamp: str,
) -> None:
    """Concatenate per-worker matches / mismatches / report files.

    Args:
        worker_dirs: Per-worker output directories **in worker-id
            order**. Each directory must contain
            ``matches.dat``, ``mismatches.dat``, and ``report.csv``
            (zero-byte files are fine for workers that saw no
            matches or no mismatches in their slice).
        output_dir: The run output directory where the merged
            (stamped) files land.
        filename_stamp: The ``YYYYMMDDHHMM`` stamp to use for the
            merged files.
    """
    _merge_binary(
        worker_dirs,
        _WORKER_MATCHES,
        output_dir / stamped_filename(MATCHES_FILE, filename_stamp),
    )
    _merge_binary(
        worker_dirs,
        _WORKER_MISMATCHES,
        output_dir / stamped_filename(MISMATCHES_FILE, filename_stamp),
    )
    _merge_report_csv(
        worker_dirs,
        output_dir / stamped_filename(REPORT_FILE, filename_stamp),
    )


def fold_partial_summaries(
    results: list[WorkerResult],
) -> tuple[int, int, dict[str, int], dict[str, int]]:
    """Sum per-worker counters into run-level totals.

    Returns:
        ``(records_matched, records_mismatched, per_segment_match,
        per_segment_mismatch)``.
    """
    matched = sum(r.records_matched for r in results)
    mismatched = sum(r.records_mismatched for r in results)

    per_seg_match: dict[str, int] = defaultdict(int)
    per_seg_mismatch: dict[str, int] = defaultdict(int)
    for r in results:
        for name, count in r.per_segment_match.items():
            per_seg_match[name] += count
        for name, count in r.per_segment_mismatch.items():
            per_seg_mismatch[name] += count

    return matched, mismatched, dict(per_seg_match), dict(per_seg_mismatch)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _merge_binary(worker_dirs: list[Path], filename: str, out_path: Path) -> None:
    """Concatenate one per-worker binary file from every worker_dir."""
    with out_path.open("wb") as out:
        for wdir in worker_dirs:
            src = wdir / filename
            if not src.exists():
                logger.warning("missing per-worker file %s; skipping", src)
                continue
            with src.open("rb") as fh:
                shutil.copyfileobj(fh, out)


def _merge_report_csv(worker_dirs: list[Path], out_path: Path) -> None:
    """Concatenate report.csv files, keeping only the first worker's header."""
    with out_path.open("wb") as out:
        header_written = False
        for wdir in worker_dirs:
            src = wdir / _WORKER_REPORT
            if not src.exists():
                logger.warning("missing per-worker file %s; skipping", src)
                continue
            with src.open("rb") as fh:
                header = fh.readline()
                if not header_written:
                    out.write(header)
                    header_written = True
                shutil.copyfileobj(fh, out)
