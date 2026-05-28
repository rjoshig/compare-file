"""External chunk-and-merge sort for record streams larger than memory.

The Phase 1/2 engine assumes input is sorted by key (so the inner-join
walks both files' indexes in matching order). When an upstream system
delivers unsorted input, this module produces a sorted copy first.

The classic external sort:

1. **Pass 1 — chunk + sort + spill.** Stream the input file. Buffer
   up to ``runtime.chunk_size`` records in memory. When the buffer
   fills (or input ends), sort it by key and write the records out
   to a temp file in ``runtime.sort_temp_dir``.
2. **Pass 2 — merge.** Open every spill file as a record iterator
   and feed them to :func:`heapq.merge` keyed by record key. Write
   the interleaved stream to the output path.

Memory cost: O(``chunk_size`` records). Disk cost: ~2× the input
size (each record is written once during spill and once during
merge). The merge step holds one file descriptor per chunk; for
3M-record inputs at ``chunk_size = 10_000`` that's ~300 fds, well
under typical ulimits.

This module is intentionally independent of the parallel-comparison
path. Sorting is a serial pre-step; the comparison can then run
single-process or multi-worker as usual.
"""

from __future__ import annotations

import heapq
import logging
import tempfile
from pathlib import Path
from typing import Iterator

from segment_compare.config import ResolvedConfig
from segment_compare.parser import iter_records

logger = logging.getLogger(__name__)


def external_sort_file(
    input_path: Path,
    output_path: Path,
    config: ResolvedConfig,
) -> int:
    """Read records from ``input_path``, write sorted-by-key records to ``output_path``.

    Args:
        input_path: Path to a fixed-format segment file. Records may
            be in any order; their byte layout must be valid per the
            config (parser knobs, segment framing).
        output_path: Destination for the sorted record stream. Parent
            directory is created if missing. Existing file is
            overwritten.
        config: Loaded :class:`ResolvedConfig`. ``runtime.chunk_size``
            controls the in-memory buffer size; ``runtime.sort_temp_dir``
            is the directory where spill files land (created if absent
            and deleted after the merge completes).

    Returns:
        The number of records sorted (also the number of records
        written to ``output_path``).

    Side effects:
        Writes ``output_path``. Creates and deletes temp chunk files
        in ``runtime.sort_temp_dir``. On exception the chunks are
        still cleaned up.
    """
    sort_dir = config.runtime.sort_temp_dir
    sort_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    chunk_size = config.runtime.chunk_size
    delimiter = config.segments.record_delimiter

    chunk_paths: list[Path] = []
    record_count = 0
    try:
        # Pass 1: chunk + sort + spill.
        with input_path.open("rb") as fh:
            batch: list[tuple[str, bytes]] = []
            for rec in iter_records(fh, config.parser, config.segments):
                batch.append((rec.key, rec.raw))
                record_count += 1
                if len(batch) >= chunk_size:
                    chunk_paths.append(_spill_sorted_chunk(batch, sort_dir, delimiter))
                    batch.clear()
            if batch:
                chunk_paths.append(_spill_sorted_chunk(batch, sort_dir, delimiter))

        logger.info(
            "external sort: %s → %s (%d records, %d chunks, chunk_size=%d)",
            input_path.name,
            output_path.name,
            record_count,
            len(chunk_paths),
            chunk_size,
        )

        # Pass 2: merge.
        _merge_sorted_chunks(chunk_paths, output_path, config)

    finally:
        for p in chunk_paths:
            p.unlink(missing_ok=True)

    return record_count


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _spill_sorted_chunk(batch: list[tuple[str, bytes]], sort_dir: Path, delimiter: bytes) -> Path:
    """Sort ``batch`` in place by key and write the records to a temp file."""
    batch.sort(key=lambda kr: kr[0])
    fd, path_str = tempfile.mkstemp(dir=sort_dir, prefix="chunk_", suffix=".dat")
    path = Path(path_str)
    with open(fd, "wb") as fh:
        for _key, raw in batch:
            fh.write(raw)
            if delimiter:
                fh.write(delimiter)
    return path


def _merge_sorted_chunks(
    chunk_paths: list[Path], output_path: Path, config: ResolvedConfig
) -> None:
    """Interleave records from all sorted chunk files into ``output_path``.

    ``heapq.merge`` keyed on the record key produces one global sorted
    stream. Each chunk's records are read lazily so memory stays
    O(``len(chunk_paths)``) regardless of total record count.
    """
    if not chunk_paths:
        # No records at all; write an empty output file.
        output_path.write_bytes(b"")
        return

    streams: list[Iterator[tuple[str, bytes]]] = [
        _records_with_delimiter(p, config) for p in chunk_paths
    ]
    try:
        with output_path.open("wb") as out:
            for _key, raw_with_delim in heapq.merge(*streams, key=lambda kr: kr[0]):
                out.write(raw_with_delim)
    finally:
        # Force-close generators so their internal ``with`` blocks
        # run and release their file descriptors promptly. Python
        # would do this on GC eventually, but being explicit avoids
        # surprises in long-running parents like the Phase 4 service.
        for s in streams:
            s.close()  # type: ignore[attr-defined]


def _records_with_delimiter(
    chunk_path: Path, config: ResolvedConfig
) -> Iterator[tuple[str, bytes]]:
    """Yield ``(key, raw_with_delimiter)`` tuples from one chunk file."""
    delimiter = config.segments.record_delimiter
    with chunk_path.open("rb") as fh:
        for rec in iter_records(fh, config.parser, config.segments):
            yield (rec.key, rec.raw + delimiter if delimiter else rec.raw)
