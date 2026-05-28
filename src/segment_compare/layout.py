"""Per-file layout config loader (ADR-033, Stage 2).

A *layout file* is a single JSON document that describes everything
specific to one of the two inputs of a comparison run: byte-level
parser knobs, optional leading-byte strip, optional RDW prefix, sort
order, and an ordered list of segments with per-segment ``size`` and
per-field ``name`` / ``length`` / ``exclude`` / ``key`` declarations.

Two layout files (one per input) plus ``runtime.json`` are intended to
replace the legacy ``segments.json`` + ``normalization.json`` pair in
Stage 3. Stage 2 (this module) ships the loader and dataclasses
additively — nothing in the engine consumes :class:`FileLayout` yet,
so the legacy code path keeps working untouched.

Every load-time invariant listed in ADR-033 is enforced here and raises
:class:`LayoutError` with a precise field path so a bad layout fails
loudly at startup, not deep inside a 3M-record run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from segment_compare.parser import RdwConfig

SUPPORTED_SIZE_ENCODINGS = ("ascii_int",)
SUPPORTED_DATA_ENCODINGS = ("ascii",)
SUPPORTED_STRIP_ENCODINGS = ("binary", "ascii")
SUPPORTED_RDW_ENCODINGS = ("ascii_int", "binary_le_uint")
SUPPORTED_SORT_ORDERS = ("ascending", "descending")
SUPPORTED_KEY_TYPES = ("alphanumeric", "numeric")
SUPPORTED_ROLES = ("key", "end")

DEFAULT_SEGMENT_NAME_BYTES = 4
DEFAULT_SIZE_FIELD_BYTES = 3


class LayoutError(Exception):
    """Raised when a layout file is missing, malformed, or invalid.

    Attributes:
        field: Path-like identifier of the offending field (e.g.,
            ``"layout_file_A.json::segments[2].fields[1].length"``).
        message: Human-readable description of the problem.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(field, message)
        self.field = field
        self.message = message

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


@dataclass(frozen=True, slots=True)
class FileFormatConfig:
    """Per-file byte-level parser knobs.

    Mirrors :class:`segment_compare.parser.ParserConfig` but lives on
    :class:`FileLayout` so each input declares its own (real-world
    feeds may diverge on segment-name width, size-field width, etc.).

    Attributes:
        segment_name_bytes: Width in bytes of every segment-name header
            field. Currently 4 is the only supported value.
        size_field_bytes: Width in bytes of every size header field.
            Currently 3 is the only supported value.
        size_encoding: How the size field is encoded. Currently
            ``"ascii_int"`` is the only supported value.
        size_includes_header: Whether the declared size includes the
            header bytes. Currently ``True`` is the only supported value.
        data_encoding: Text encoding for segment names and key data.
            Currently ``"ascii"`` is the only supported value.
        record_delimiter: Bytes between consecutive records on disk.
            Empty bytes means records are back-to-back.
    """

    segment_name_bytes: int
    size_field_bytes: int
    size_encoding: str
    size_includes_header: bool
    data_encoding: str
    record_delimiter: bytes

    @property
    def header_bytes(self) -> int:
        """Total bytes consumed by a segment header (name + size)."""
        return self.segment_name_bytes + self.size_field_bytes


@dataclass(frozen=True, slots=True)
class StripConfig:
    """Opaque leading-byte strip applied before RDW / key segment.

    Consumed per record (same lifecycle as :class:`RdwConfig`); the
    bytes are discarded without interpretation. ``encoding`` is
    recorded for documentation and future validation; the skip logic
    is encoding-agnostic.

    Attributes:
        size: Number of bytes to skip before each record. Must be > 0.
        encoding: ``"binary"`` or ``"ascii"`` — informational only.
    """

    size: int
    encoding: str


@dataclass(frozen=True, slots=True)
class SortConfig:
    """Per-file sort metadata.

    Attributes:
        input_sorted: Whether this file is already sorted by its key.
            When ``False`` the engine runs the external-sort pass on
            this side before the index-build pass.
        order: ``"ascending"`` or ``"descending"`` — direction of sort
            when ``input_sorted`` is true (and the sort order the engine
            produces when it sorts a file itself).
        key_type: ``"alphanumeric"`` or ``"numeric"`` — drives the
            sort comparator. Today only alphanumeric is exercised.
    """

    input_sorted: bool
    order: str
    key_type: str


@dataclass(frozen=True, slots=True)
class FieldLayout:
    """One field within a segment's per-file layout.

    Attributes:
        name: Logical field name. Acts as the comparison anchor across
            File A and File B: fields with the same name compare,
            fields named in only one side drop from that segment's
            comparison.
        length: Field width in bytes. Must be > 0.
        exclude: When ``True`` the field is dropped before hashing.
            Use for fillers, timestamps, segment counts, etc. Defaults
            to ``False`` — every field is compared unless flagged.
        key: When ``True`` the field's value is the record key. Exactly
            one field across the whole layout must carry ``key=True``,
            and it must live inside the segment with ``role=key``.
    """

    name: str
    length: int
    exclude: bool
    key: bool


@dataclass(frozen=True, slots=True)
class SegmentLayout:
    """Per-segment layout: role, declared size, and ordered fields.

    Attributes:
        name: Segment name (e.g., ``"TU4R"``). Must be unique across
            the layout.
        role: ``"key"``, ``"end"``, or ``None`` for ordinary segments.
            Exactly one segment in the layout has ``role="key"`` and
            exactly one has ``role="end"``.
        size: Total on-wire segment bytes (header + data). Must equal
            ``file_format.header_bytes + sum(field.length for field in
            fields)``; load-time check raises :class:`LayoutError` on
            mismatch.
        fields: Ordered tuple of :class:`FieldLayout` covering the data
            area of the segment.
    """

    name: str
    role: str | None
    size: int
    fields: tuple[FieldLayout, ...]


@dataclass(frozen=True, slots=True)
class FileLayout:
    """Full layout description for one input file.

    Attributes:
        file_format: Byte-level parser knobs declared per file.
        strip_leading_bytes: Optional opaque per-record skip applied
            before the RDW (or, if no RDW, before the key segment).
        rdw: Optional Record Descriptor Word prefix consumed per
            record between ``strip_leading_bytes`` and the key segment.
        sort: Per-file sort metadata.
        segments: Ordered tuple of segments. Order is documentation
            only; the parser is order-agnostic for non-role segments.
        source_path: Path the layout was loaded from. Recorded for
            inclusion in audit logs and error messages.
    """

    file_format: FileFormatConfig
    strip_leading_bytes: StripConfig | None
    rdw: RdwConfig | None
    sort: SortConfig
    segments: tuple[SegmentLayout, ...]
    source_path: Path

    @property
    def key_segment(self) -> SegmentLayout:
        """Return the segment whose role is ``"key"``."""
        for seg in self.segments:
            if seg.role == "key":
                return seg
        raise LayoutError(  # pragma: no cover — load-time invariant
            f"{self.source_path.name}::segments",
            "no segment has role=key (load-time invariant should have caught this)",
        )

    @property
    def end_segment(self) -> SegmentLayout:
        """Return the segment whose role is ``"end"``."""
        for seg in self.segments:
            if seg.role == "end":
                return seg
        raise LayoutError(  # pragma: no cover — load-time invariant
            f"{self.source_path.name}::segments",
            "no segment has role=end (load-time invariant should have caught this)",
        )

    @property
    def key_field(self) -> FieldLayout:
        """Return the single field across the whole layout with ``key=True``."""
        for field in self.key_segment.fields:
            if field.key:
                return field
        raise LayoutError(  # pragma: no cover — load-time invariant
            f"{self.source_path.name}::segments",
            "no field has key=true inside the key segment (load-time invariant)",
        )

    @property
    def key_range(self) -> tuple[int, int]:
        """``(start, end)`` byte range of the key inside the key segment's data area.

        Computed by accumulating preceding field lengths. End-exclusive,
        matches the legacy ``SegmentsConfig.key_range`` convention so
        callers can swap shapes without changing slicing logic.
        """
        offset = 0
        for field in self.key_segment.fields:
            if field.key:
                return (offset, offset + field.length)
            offset += field.length
        raise LayoutError(  # pragma: no cover — load-time invariant
            f"{self.source_path.name}::segments",
            "key field not located while computing key_range",
        )


def load_file_layout(path: Path) -> FileLayout:
    """Load and validate one layout JSON file.

    Args:
        path: Path to a layout file (typically ``config/layout_file_A.json``
            or ``config/layout_file_B.json``).

    Returns:
        A fully validated :class:`FileLayout`.

    Raises:
        LayoutError: If the file is missing, malformed, or violates any
            of the invariants documented in ADR-033.
    """
    raw = _read_json(path)
    file_format = _build_file_format(raw, path)
    strip = _build_strip(raw, path)
    rdw = _build_rdw(raw, path)
    sort = _build_sort(raw, path)
    segments = _build_segments(raw, path, file_format)
    return FileLayout(
        file_format=file_format,
        strip_leading_bytes=strip,
        rdw=rdw,
        sort=sort,
        segments=segments,
        source_path=path,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise LayoutError(str(path), "layout file does not exist")
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LayoutError(str(path), f"could not read file: {exc}") from exc
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise LayoutError(str(path), f"invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LayoutError(str(path), "top-level JSON value must be an object")
    return parsed


def _require_field(obj: dict[str, Any], key: str, path: Path, field_path: str) -> Any:
    if key not in obj:
        raise LayoutError(f"{path.name}::{field_path}", "required field is missing")
    return obj[key]


def _require_type(value: Any, expected: type, path: Path, field_path: str) -> Any:
    if not isinstance(value, expected):
        raise LayoutError(
            f"{path.name}::{field_path}",
            f"expected {expected.__name__}, got {type(value).__name__}",
        )
    return value


def _build_file_format(raw: dict[str, Any], path: Path) -> FileFormatConfig:
    block = _require_field(raw, "file_format", path, "file_format")
    _require_type(block, dict, path, "file_format")

    snb = block.get("segment_name_bytes", DEFAULT_SEGMENT_NAME_BYTES)
    sfb = block.get("size_field_bytes", DEFAULT_SIZE_FIELD_BYTES)
    enc = block.get("size_encoding", "ascii_int")
    inc = block.get("size_includes_header", True)
    data_enc = block.get("data_encoding", "ascii")
    delim_raw = _require_field(block, "record_delimiter", path, "file_format.record_delimiter")
    _require_type(delim_raw, str, path, "file_format.record_delimiter")

    if snb != DEFAULT_SEGMENT_NAME_BYTES:
        raise LayoutError(
            f"{path.name}::file_format.segment_name_bytes",
            f"supports only {DEFAULT_SEGMENT_NAME_BYTES}, got {snb!r}",
        )
    if sfb != DEFAULT_SIZE_FIELD_BYTES:
        raise LayoutError(
            f"{path.name}::file_format.size_field_bytes",
            f"supports only {DEFAULT_SIZE_FIELD_BYTES}, got {sfb!r}",
        )
    if enc not in SUPPORTED_SIZE_ENCODINGS:
        raise LayoutError(
            f"{path.name}::file_format.size_encoding",
            f"must be one of {list(SUPPORTED_SIZE_ENCODINGS)}, got {enc!r}",
        )
    if inc is not True:
        raise LayoutError(
            f"{path.name}::file_format.size_includes_header",
            f"only True is supported, got {inc!r}",
        )
    if data_enc not in SUPPORTED_DATA_ENCODINGS:
        raise LayoutError(
            f"{path.name}::file_format.data_encoding",
            f"must be one of {list(SUPPORTED_DATA_ENCODINGS)}, got {data_enc!r}",
        )
    try:
        delim_bytes = delim_raw.encode("ascii")
    except UnicodeEncodeError as exc:
        raise LayoutError(
            f"{path.name}::file_format.record_delimiter",
            f"must be ASCII-encodable, got {delim_raw!r}",
        ) from exc

    return FileFormatConfig(
        segment_name_bytes=snb,
        size_field_bytes=sfb,
        size_encoding=enc,
        size_includes_header=inc,
        data_encoding=data_enc,
        record_delimiter=delim_bytes,
    )


def _build_strip(raw: dict[str, Any], path: Path) -> StripConfig | None:
    block = raw.get("strip_leading_bytes")
    if block is None:
        return None
    _require_type(block, dict, path, "strip_leading_bytes")
    size = _require_field(block, "size", path, "strip_leading_bytes.size")
    encoding = _require_field(block, "encoding", path, "strip_leading_bytes.encoding")
    _require_type(size, int, path, "strip_leading_bytes.size")
    _require_type(encoding, str, path, "strip_leading_bytes.encoding")
    if size <= 0:
        raise LayoutError(
            f"{path.name}::strip_leading_bytes.size",
            f"must be > 0, got {size}",
        )
    if encoding not in SUPPORTED_STRIP_ENCODINGS:
        raise LayoutError(
            f"{path.name}::strip_leading_bytes.encoding",
            f"must be one of {list(SUPPORTED_STRIP_ENCODINGS)}, got {encoding!r}",
        )
    return StripConfig(size=size, encoding=encoding)


def _build_rdw(raw: dict[str, Any], path: Path) -> RdwConfig | None:
    block = raw.get("rdw")
    if block is None:
        return None
    _require_type(block, dict, path, "rdw")
    rdw1 = _require_field(block, "rdw1_bytes", path, "rdw.rdw1_bytes")
    rdw2 = _require_field(block, "rdw2_bytes", path, "rdw.rdw2_bytes")
    encoding = _require_field(block, "encoding", path, "rdw.encoding")
    _require_type(rdw1, int, path, "rdw.rdw1_bytes")
    _require_type(rdw2, int, path, "rdw.rdw2_bytes")
    _require_type(encoding, str, path, "rdw.encoding")
    if rdw1 <= 0:
        raise LayoutError(f"{path.name}::rdw.rdw1_bytes", f"must be > 0, got {rdw1}")
    if rdw2 <= 0:
        raise LayoutError(f"{path.name}::rdw.rdw2_bytes", f"must be > 0, got {rdw2}")
    if encoding not in SUPPORTED_RDW_ENCODINGS:
        raise LayoutError(
            f"{path.name}::rdw.encoding",
            f"must be one of {list(SUPPORTED_RDW_ENCODINGS)}, got {encoding!r}",
        )
    return RdwConfig(rdw1_bytes=rdw1, rdw2_bytes=rdw2, encoding=encoding)


def _build_sort(raw: dict[str, Any], path: Path) -> SortConfig:
    block = _require_field(raw, "sort", path, "sort")
    _require_type(block, dict, path, "sort")
    input_sorted = _require_field(block, "input_sorted", path, "sort.input_sorted")
    order = _require_field(block, "order", path, "sort.order")
    key_type = _require_field(block, "key_type", path, "sort.key_type")
    _require_type(input_sorted, bool, path, "sort.input_sorted")
    _require_type(order, str, path, "sort.order")
    _require_type(key_type, str, path, "sort.key_type")
    if order not in SUPPORTED_SORT_ORDERS:
        raise LayoutError(
            f"{path.name}::sort.order",
            f"must be one of {list(SUPPORTED_SORT_ORDERS)}, got {order!r}",
        )
    if key_type not in SUPPORTED_KEY_TYPES:
        raise LayoutError(
            f"{path.name}::sort.key_type",
            f"must be one of {list(SUPPORTED_KEY_TYPES)}, got {key_type!r}",
        )
    return SortConfig(input_sorted=input_sorted, order=order, key_type=key_type)


def _build_segments(
    raw: dict[str, Any], path: Path, file_format: FileFormatConfig
) -> tuple[SegmentLayout, ...]:
    seg_raw = _require_field(raw, "segments", path, "segments")
    _require_type(seg_raw, list, path, "segments")
    if not seg_raw:
        raise LayoutError(f"{path.name}::segments", "must declare at least one segment")

    segments: list[SegmentLayout] = []
    seen_names: set[str] = set()
    key_segment_count = 0
    end_segment_count = 0
    total_key_fields = 0
    key_field_segment: str | None = None

    for i, item in enumerate(seg_raw):
        seg = _build_one_segment(item, path, file_format, i)
        if seg.name in seen_names:
            raise LayoutError(
                f"{path.name}::segments[{i}].name",
                f"duplicate segment name {seg.name!r}",
            )
        seen_names.add(seg.name)
        if seg.role == "key":
            key_segment_count += 1
        elif seg.role == "end":
            end_segment_count += 1
        for field in seg.fields:
            if field.key:
                total_key_fields += 1
                key_field_segment = seg.name
        segments.append(seg)

    if key_segment_count != 1:
        raise LayoutError(
            f"{path.name}::segments",
            f"exactly one segment must have role=key, found {key_segment_count}",
        )
    if end_segment_count != 1:
        raise LayoutError(
            f"{path.name}::segments",
            f"exactly one segment must have role=end, found {end_segment_count}",
        )
    if total_key_fields != 1:
        raise LayoutError(
            f"{path.name}::segments",
            f"exactly one field across all segments must have key=true, found {total_key_fields}",
        )
    # Key field must live inside the role:key segment.
    for seg in segments:
        if seg.role == "key":
            assert key_field_segment is not None  # invariant: counted exactly one above
            if key_field_segment != seg.name:
                raise LayoutError(
                    f"{path.name}::segments",
                    f"key=true field lives in segment {key_field_segment!r} but role=key is on "
                    f"{seg.name!r}; the key field must live inside the key segment",
                )
            break

    return tuple(segments)


def _build_one_segment(
    raw: Any, path: Path, file_format: FileFormatConfig, index: int
) -> SegmentLayout:
    base_path = f"segments[{index}]"
    if not isinstance(raw, dict):
        raise LayoutError(
            f"{path.name}::{base_path}",
            f"must be an object, got {type(raw).__name__}",
        )
    name = _require_field(raw, "name", path, f"{base_path}.name")
    _require_type(name, str, path, f"{base_path}.name")
    if not name:
        raise LayoutError(f"{path.name}::{base_path}.name", "must be non-empty")
    size = _require_field(raw, "size", path, f"{base_path}.size")
    _require_type(size, int, path, f"{base_path}.size")
    if size <= 0:
        raise LayoutError(
            f"{path.name}::{base_path}.size",
            f"must be > 0, got {size}",
        )
    role = raw.get("role")
    if role is not None:
        if not isinstance(role, str) or role not in SUPPORTED_ROLES:
            raise LayoutError(
                f"{path.name}::{base_path}.role",
                f"must be one of {list(SUPPORTED_ROLES)} or absent, got {role!r}",
            )

    fields_raw = _require_field(raw, "fields", path, f"{base_path}.fields")
    _require_type(fields_raw, list, path, f"{base_path}.fields")
    if not fields_raw:
        raise LayoutError(
            f"{path.name}::{base_path}.fields",
            "must declare at least one field",
        )
    fields = _build_fields(fields_raw, path, f"{base_path}.fields")

    expected = file_format.header_bytes + sum(f.length for f in fields)
    if size != expected:
        raise LayoutError(
            f"{path.name}::{base_path}.size",
            f"size {size} does not match header_bytes ({file_format.header_bytes}) + sum of "
            f"field lengths ({expected - file_format.header_bytes}) = {expected}",
        )

    return SegmentLayout(name=name, role=role, size=size, fields=fields)


def _build_fields(raw: list[Any], path: Path, base_path: str) -> tuple[FieldLayout, ...]:
    seen_names: set[str] = set()
    out: list[FieldLayout] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise LayoutError(
                f"{path.name}::{base_path}[{i}]",
                f"must be an object, got {type(item).__name__}",
            )
        name = _require_field(item, "name", path, f"{base_path}[{i}].name")
        length = _require_field(item, "length", path, f"{base_path}[{i}].length")
        exclude = item.get("exclude", False)
        key = item.get("key", False)
        if not isinstance(name, str) or not name:
            raise LayoutError(
                f"{path.name}::{base_path}[{i}].name",
                "must be a non-empty string",
            )
        if not isinstance(length, int) or length <= 0:
            raise LayoutError(
                f"{path.name}::{base_path}[{i}].length",
                f"must be a positive int, got {length!r}",
            )
        if not isinstance(exclude, bool):
            raise LayoutError(
                f"{path.name}::{base_path}[{i}].exclude",
                f"must be true/false, got {type(exclude).__name__}",
            )
        if not isinstance(key, bool):
            raise LayoutError(
                f"{path.name}::{base_path}[{i}].key",
                f"must be true/false, got {type(key).__name__}",
            )
        if name in seen_names:
            raise LayoutError(
                f"{path.name}::{base_path}[{i}].name",
                f"duplicate field name {name!r} within this segment",
            )
        seen_names.add(name)
        out.append(FieldLayout(name=name, length=length, exclude=exclude, key=key))
    return tuple(out)
