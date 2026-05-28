"""Per-record multiset comparator.

For each joined ``(record_a, record_b)`` pair:

1. Hash every normalized segment in each record.
2. Group hashes per segment-type into a ``collections.Counter``
   (a multiset).
3. Compare A's Counter and B's Counter per segment type. Equal Counters
   mean the segment type matches; unequal means it mismatches.
4. The record matches overall iff every segment type matches (ADR-001).

The verdict carries per-segment-type counts and an aggregate matched
flag; downstream writers decide what to emit from it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from segment_compare.hasher import Hasher, HashValue
from segment_compare.normalizer import Normalizer
from segment_compare.parser import Record

STATUS_MATCH = "match"
STATUS_COUNT_DIFF = "count_diff"
STATUS_CONTENT_DIFF = "content_diff"


@dataclass(frozen=True, slots=True)
class SegmentVerdict:
    """The comparison outcome for one segment type within a record.

    Attributes:
        segment_name: The segment type (e.g., ``"NM01"``).
        matched: True iff A's hash multiset equals B's hash multiset
            for this segment type.
        a_count: Number of instances of this segment in record A.
        b_count: Number of instances of this segment in record B.
    """

    segment_name: str
    matched: bool
    a_count: int
    b_count: int

    @property
    def status(self) -> str:
        """Human-readable status for ``report.csv``.

        Returns ``"match"``, ``"count_diff"``, or ``"content_diff"``.
        """
        if self.matched:
            return STATUS_MATCH
        if self.a_count != self.b_count:
            return STATUS_COUNT_DIFF
        return STATUS_CONTENT_DIFF


@dataclass(frozen=True, slots=True)
class RecordVerdict:
    """The comparison outcome for one joined record pair.

    Attributes:
        key: The shared record key.
        matched: True iff every segment-type's hash multiset matched.
        segment_verdicts: Per-segment-type verdicts, sorted by segment
            name for stable output ordering.
    """

    key: str
    matched: bool
    segment_verdicts: tuple[SegmentVerdict, ...]

    @property
    def mismatched_segments(self) -> tuple[str, ...]:
        """Names of segment types whose multisets did not match."""
        return tuple(v.segment_name for v in self.segment_verdicts if not v.matched)


def compare_records(
    record_a: Record,
    record_b: Record,
    normalizer: Normalizer,
    hasher: Hasher,
) -> RecordVerdict:
    """Compare two records via per-segment-type hash multisets.

    Args:
        record_a: Record from File A.
        record_b: Record from File B. Must have the same key as
            ``record_a``.
        normalizer: Normalizer applied to each segment's data before
            hashing.
        hasher: Hasher applied to the normalized data.

    Returns:
        A :class:`RecordVerdict` summarizing the comparison.

    Raises:
        ValueError: If the two records have different keys.
    """
    if record_a.key != record_b.key:
        raise ValueError(
            f"compare_records requires equal keys, got "
            f"{record_a.key!r} (A) vs {record_b.key!r} (B)"
        )

    a_counters: dict[str, Counter[HashValue]] = defaultdict(Counter)
    b_counters: dict[str, Counter[HashValue]] = defaultdict(Counter)

    for seg in record_a.segments:
        canonical = normalizer.normalize(seg.name, seg.data, "A")
        a_counters[seg.name][hasher.hash(canonical)] += 1

    for seg in record_b.segments:
        canonical = normalizer.normalize(seg.name, seg.data, "B")
        b_counters[seg.name][hasher.hash(canonical)] += 1

    segment_names = sorted(set(a_counters) | set(b_counters))
    verdicts: list[SegmentVerdict] = []
    overall_match = True

    for name in segment_names:
        a_c = a_counters.get(name, Counter())
        b_c = b_counters.get(name, Counter())
        matched = a_c == b_c
        overall_match = overall_match and matched
        verdicts.append(
            SegmentVerdict(
                segment_name=name,
                matched=matched,
                a_count=sum(a_c.values()),
                b_count=sum(b_c.values()),
            )
        )

    return RecordVerdict(
        key=record_a.key,
        matched=overall_match,
        segment_verdicts=tuple(verdicts),
    )
