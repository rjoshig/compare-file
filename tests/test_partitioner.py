"""Tests for ``segment_compare.partitioner``."""

from __future__ import annotations

import pytest

from segment_compare.partitioner import equal_count_partition


def test_partition_preserves_order_and_reconstructs_input() -> None:
    items = [f"K{i:03d}" for i in range(100)]
    chunks = equal_count_partition(items, n_workers=4)
    assert sum(chunks, []) == items


def test_partition_sizes_are_most_even_possible() -> None:
    """The first remainder chunks get one extra; the rest are equal."""
    # 100 // 7 = 14 remainder 2 → sizes [15, 15, 14, 14, 14, 14, 14]
    items = list(range(100))
    chunks = equal_count_partition([str(x) for x in items], n_workers=7)
    sizes = [len(c) for c in chunks]
    assert sizes == [15, 15, 14, 14, 14, 14, 14]
    assert sum(sizes) == 100


def test_partition_one_chunk_is_full_copy() -> None:
    items = ["a", "b", "c"]
    chunks = equal_count_partition(items, n_workers=1)
    assert chunks == [["a", "b", "c"]]
    # And it must be a copy — mutating the chunk must not affect input.
    chunks[0].append("d")
    assert items == ["a", "b", "c"]


def test_partition_more_workers_than_items_produces_empty_tail() -> None:
    items = ["a", "b", "c"]
    chunks = equal_count_partition(items, n_workers=5)
    assert chunks == [["a"], ["b"], ["c"], [], []]


def test_partition_empty_items_returns_n_empty_chunks() -> None:
    chunks = equal_count_partition([], n_workers=4)
    assert chunks == [[], [], [], []]


def test_partition_even_split() -> None:
    items = [str(i) for i in range(12)]
    chunks = equal_count_partition(items, n_workers=4)
    assert [len(c) for c in chunks] == [3, 3, 3, 3]


def test_partition_rejects_zero_workers() -> None:
    with pytest.raises(ValueError, match="n_workers must be >= 1"):
        equal_count_partition(["a"], n_workers=0)


def test_partition_rejects_negative_workers() -> None:
    with pytest.raises(ValueError):
        equal_count_partition(["a"], n_workers=-1)


def test_partition_chunks_are_disjoint_and_complete() -> None:
    """No element appears twice and no element is dropped."""
    items = [f"item{i}" for i in range(50)]
    chunks = equal_count_partition(items, n_workers=6)
    flattened = [x for chunk in chunks for x in chunk]
    assert sorted(flattened) == sorted(items)
    assert len(set(flattened)) == len(items)
