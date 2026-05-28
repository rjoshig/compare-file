"""Per-segment data normalization.

A normalizer turns a segment's raw data bytes into a canonical form
that is comparable across File A and File B. Phase 1 ships only the
position-based form:

1. Drop the file-specific strip ranges (file_a_strip or file_b_strip).
2. Drop the shared exclude ranges (exclude_positions) from the result.
3. Return the resulting bytes — hashed downstream.

Phase 2 will add a field-based normalizer; both implement the same
contract so the comparator and pipeline are agnostic (ADR-007).
"""

from __future__ import annotations

from typing import Literal

from segment_compare.config import NormalizationRule

RecordSource = Literal["A", "B"]


class PositionNormalizer:
    """Position-based normalizer.

    Construct with the per-segment :class:`NormalizationRule` mapping
    from :class:`segment_compare.config.ResolvedConfig.normalization`.
    Segments without a rule pass through unchanged.
    """

    __slots__ = ("_rules",)

    def __init__(self, rules: dict[str, NormalizationRule]) -> None:
        """Initialize with a mapping of segment name to rule.

        Args:
            rules: Per-segment normalization rules. Missing entries
                mean the segment's data is not normalized.
        """
        self._rules = rules

    def normalize(self, segment_name: str, raw_data: bytes, source: RecordSource) -> bytes:
        """Return the canonical bytes for a segment instance.

        Args:
            segment_name: Name of the segment (e.g., ``"NM01"``).
            raw_data: The segment's raw data bytes (header excluded).
            source: ``"A"`` or ``"B"`` to select the per-file strip
                ranges.

        Returns:
            The data bytes with the configured strip and exclude
            ranges removed.

        Raises:
            ValueError: If ``source`` is not ``"A"`` or ``"B"``.
        """
        rule = self._rules.get(segment_name)
        if rule is None:
            return raw_data

        if source == "A":
            strip_ranges = rule.file_a_strip
        elif source == "B":
            strip_ranges = rule.file_b_strip
        else:
            raise ValueError(f"source must be 'A' or 'B', got {source!r}")

        stripped = _remove_ranges(raw_data, strip_ranges)
        return _remove_ranges(stripped, rule.exclude_positions)


def _remove_ranges(data: bytes, ranges: tuple[tuple[int, int], ...]) -> bytes:
    """Return ``data`` with the given ``[start, end)`` byte ranges removed.

    Ranges may be unsorted, overlapping, or extend past ``len(data)``;
    they are clipped to the data bounds and merged before slicing.

    Args:
        data: The source bytes.
        ranges: A tuple of end-exclusive ``[start, end)`` ranges to
            remove.

    Returns:
        The bytes outside any of the (merged) ranges, concatenated in
        order.
    """
    if not ranges:
        return data

    n = len(data)
    clipped: list[tuple[int, int]] = []
    for start, end in ranges:
        s = max(0, start)
        e = min(n, end)
        if s < e:
            clipped.append((s, e))

    if not clipped:
        return data

    clipped.sort()
    merged: list[tuple[int, int]] = [clipped[0]]
    for start, end in clipped[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    parts: list[bytes] = []
    pos = 0
    for start, end in merged:
        if pos < start:
            parts.append(data[pos:start])
        pos = end
    if pos < n:
        parts.append(data[pos:])

    return b"".join(parts)
