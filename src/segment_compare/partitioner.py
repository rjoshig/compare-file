"""Equal-count key partitioner for Phase 2 parallel comparison.

The inner-join key list (the keys present in both files' good indexes,
sorted) is the unit of work for parallel comparison. Splitting it into
``n_workers`` chunks of (approximately) equal size keeps the workers
load-balanced regardless of how key values are distributed —
alphabetical-range partitioning would skew badly for keys like
``CUST00000001`` … ``CUST09999999`` (all under "C") (ADR-006).

This module owns one pure function. The output is deterministic and
preserves source order within each chunk so that concatenating the
chunks reconstructs the input.
"""

from __future__ import annotations


def equal_count_partition(items: list[str], n_workers: int) -> list[list[str]]:
    """Split ``items`` into ``n_workers`` approximately-equal chunks.

    The first ``len(items) % n_workers`` chunks each carry one extra
    item; remaining chunks all have ``len(items) // n_workers`` items.
    This produces the most even split possible — no chunk exceeds any
    other chunk's size by more than one.

    If ``len(items) < n_workers``, the surplus chunks are returned
    empty. Callers should be prepared to receive empty chunks (a
    no-op worker is the correct response).

    Args:
        items: The sequence to partition. Order is preserved.
        n_workers: Number of chunks to produce. Must be ≥ 1.

    Returns:
        A list of exactly ``n_workers`` lists, concatenating which in
        order reconstructs ``items``.

    Raises:
        ValueError: If ``n_workers < 1``.
    """
    if n_workers < 1:
        raise ValueError(f"n_workers must be >= 1, got {n_workers}")
    if n_workers == 1:
        return [list(items)]

    total = len(items)
    base, remainder = divmod(total, n_workers)

    chunks: list[list[str]] = []
    start = 0
    for i in range(n_workers):
        size = base + (1 if i < remainder else 0)
        chunks.append(list(items[start : start + size]))
        start += size

    return chunks
