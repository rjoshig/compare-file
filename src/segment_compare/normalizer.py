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

from typing import Literal, Protocol

from segment_compare.config import FieldNormalizationRule, NormalizationRule

RecordSource = Literal["A", "B"]

# ASCII Unit Separator. Used in the field-based canonical form between
# successive ``name=value`` field encodings (ADR-029). Chosen because
# fixed-format ASCII data never contains 0x1F in real records.
FIELD_SEPARATOR = b"\x1f"
FIELD_KV_DELIM = b"="


class Normalizer(Protocol):
    """Maps a segment's raw data to its canonical comparable bytes.

    All normalizers (position-based in Phase 1, field-based in Phase 2)
    share this contract so the comparator and pipeline are agnostic.
    """

    def normalize(self, segment_name: str, raw_data: bytes, source: RecordSource) -> bytes:
        """Return the canonical bytes for one segment instance."""
        ...


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


class FieldNormalizer:
    """Field-based normalizer (Phase 2).

    Slices each segment's raw data per the per-source layout, drops
    fields whose ``exclude`` flag is set, and emits a canonical
    ``name=value`` byte string sorted by logical field name. The sort
    makes the canonical form **order-independent** — File A and File B
    can carry the same logical fields in different physical order and
    still compare equal.

    Layout coverage is strict: the sum of field lengths in the chosen
    layout must equal the segment data length at runtime. A mismatch
    (likely a config typo or schema drift) raises ``ValueError`` and
    aborts the comparison.

    Segments not present in the rules map pass through unchanged.
    """

    __slots__ = ("_rules",)

    def __init__(self, rules: dict[str, FieldNormalizationRule]) -> None:
        """Initialize with the per-segment field-rule mapping."""
        self._rules = rules

    def normalize(self, segment_name: str, raw_data: bytes, source: RecordSource) -> bytes:
        """Return the canonical bytes for one segment instance.

        Args:
            segment_name: Name of the segment (e.g., ``"NM01"``).
            raw_data: Segment data bytes (header excluded).
            source: ``"A"`` or ``"B"`` to pick the per-file layout.

        Returns:
            Bytes of the form ``name1=value1\\x1Fname2=value2\\x1F...``
            with field encodings sorted alphabetically by name and
            excluded fields dropped. Empty bytes if every field is
            excluded.

        Raises:
            ValueError: ``source`` is not ``"A"`` or ``"B"``, or the
                chosen layout's total length doesn't match
                ``len(raw_data)``.
        """
        rule = self._rules.get(segment_name)
        if rule is None:
            return raw_data

        if source == "A":
            layout = rule.file_a_layout
        elif source == "B":
            layout = rule.file_b_layout
        else:
            raise ValueError(f"source must be 'A' or 'B', got {source!r}")

        expected = sum(f.length for f in layout)
        if expected != len(raw_data):
            raise ValueError(
                f"FieldNormalizer: segment {segment_name!r} (source {source}) "
                f"data length {len(raw_data)} does not match layout sum "
                f"{expected} ({len(layout)} fields)"
            )

        parts: list[bytes] = []
        pos = 0
        for f in layout:
            value = raw_data[pos : pos + f.length]
            pos += f.length
            if not f.exclude:
                parts.append(f.name.encode("ascii") + FIELD_KV_DELIM + value)

        # Sort by encoded bytes (≡ sort by ASCII name) so A and B with
        # differing physical layouts but the same logical fields produce
        # byte-identical canonical forms.
        parts.sort()
        return FIELD_SEPARATOR.join(parts)


class CompositeNormalizer:
    """Routes each segment to either :class:`PositionNormalizer` or :class:`FieldNormalizer`.

    A single :class:`segment_compare.config.ResolvedConfig` can mix
    both normalization forms; one segment uses position-based and
    another field-based. The pipeline builds **one** normalizer object
    that handles the dispatch internally so the comparator stays
    agnostic.

    Segments absent from both rule maps pass through unchanged.
    """

    __slots__ = ("_position", "_field", "_field_segments")

    def __init__(
        self,
        position_rules: dict[str, NormalizationRule],
        field_rules: dict[str, FieldNormalizationRule],
    ) -> None:
        """Initialize with the two per-segment rule maps.

        Raises:
            ValueError: A segment name appears in both maps. This is
                a programming error — the config loader rejects
                mixed-form entries upstream, so reaching this point
                means the maps were built inconsistently.
        """
        overlap = set(position_rules) & set(field_rules)
        if overlap:
            raise ValueError(
                f"segment(s) {sorted(overlap)} have both position and field rules; "
                "config loader should have rejected this upstream"
            )
        self._position = PositionNormalizer(position_rules)
        self._field = FieldNormalizer(field_rules)
        self._field_segments = frozenset(field_rules)

    def normalize(self, segment_name: str, raw_data: bytes, source: RecordSource) -> bytes:
        """Return canonical bytes via the per-segment chosen normalizer."""
        if segment_name in self._field_segments:
            return self._field.normalize(segment_name, raw_data, source)
        return self._position.normalize(segment_name, raw_data, source)


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
