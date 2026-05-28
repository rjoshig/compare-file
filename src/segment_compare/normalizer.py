"""Per-segment field-based data normalization.

A :class:`FieldNormalizer` turns a segment's raw data bytes into a
canonical comparable form by slicing per the per-source field layout,
dropping fields whose ``exclude`` flag is set, and emitting a sorted
``name=value`` byte string. Field-name-based comparison is the whole
point: File A's ``first_name`` always compares against File B's
``first_name`` regardless of physical position (ADR-029).

ADR-033 moved the schema to per-file layouts and dropped the
position-based normalizer entirely; field-based is the only form.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

RecordSource = Literal["A", "B"]

# ASCII Unit Separator. Used in the field-based canonical form between
# successive ``name=value`` field encodings (ADR-029). Chosen because
# fixed-format ASCII data never contains 0x1F in real records.
FIELD_SEPARATOR = b"\x1f"
FIELD_KV_DELIM = b"="


@dataclass(frozen=True, slots=True)
class FieldDef:
    """One field's definition for a single side of a segment layout.

    A direct projection of :class:`segment_compare.layout.FieldLayout`
    — the normalizer-internal shape so :class:`FieldNormalizer` does
    not depend on the layout module.

    Attributes:
        name: Logical field name. Acts as the comparison anchor across
            File A and File B.
        length: Field width in bytes. Must be > 0.
        exclude: When True the field is dropped from the canonical
            form before hashing.
    """

    name: str
    length: int
    exclude: bool


@dataclass(frozen=True, slots=True)
class FieldNormalizationRule:
    """Per-segment field-based normalization rule.

    Attributes:
        file_a_layout: File A's fields for this segment, in byte order.
        file_b_layout: File B's fields for this segment, in byte order.
    """

    file_a_layout: tuple[FieldDef, ...]
    file_b_layout: tuple[FieldDef, ...]


class Normalizer(Protocol):
    """Maps a segment's raw data to its canonical comparable bytes."""

    def normalize(self, segment_name: str, raw_data: bytes, source: RecordSource) -> bytes:
        """Return the canonical bytes for one segment instance."""
        ...


class FieldNormalizer:
    """Field-based normalizer.

    Slices each segment's raw data per the per-source layout, drops
    fields whose ``exclude`` flag is set, and emits a canonical
    ``name=value`` byte string sorted by logical field name. The sort
    makes the canonical form **order-independent** — File A and File B
    can carry the same logical fields in different physical order and
    still compare equal.

    Layout coverage is strict: the sum of field lengths in the chosen
    layout must equal the segment data length at runtime. A mismatch
    (likely a layout typo or schema drift) raises ``ValueError`` and
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
