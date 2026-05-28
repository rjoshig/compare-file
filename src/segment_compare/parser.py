"""Streaming segment and record parser.

Reads fixed-format binary streams whose records consist of a sequence of
length-prefixed segments framed by a key segment (``TU4R``) and a
terminator segment (``ENDS``). Segments and records are yielded lazily so
the engine never holds an entire file in memory.

Phase 1 supports only the default parser knobs (ASCII names, ASCII
integer size field, header included in declared size, ASCII data). The
:class:`ParserConfig` schema is forward-compatible with other variants
(see ADR-016) but non-default values raise :class:`ParseError` until
they are wired up in a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Iterator


class ParseError(Exception):
    """Raised when input bytes do not match the declared segment format.

    Attributes:
        offset: Byte offset within the source stream where the corruption
            was detected.
        message: Human-readable description of the problem.
    """

    def __init__(self, offset: int, message: str) -> None:
        super().__init__(offset, message)
        self.offset = offset
        self.message = message

    def __str__(self) -> str:
        return f"at offset {self.offset}: {self.message}"


@dataclass(frozen=True, slots=True)
class ParserConfig:
    """Byte-level parsing knobs.

    Phase 1 honors only the default values. The fields exist now so
    real-data variants become config edits, not code changes (ADR-016).

    Attributes:
        segment_name_bytes: Width in bytes of every segment-name header
            field. Default 4 (e.g., ``TU4R``).
        size_field_bytes: Width in bytes of every size header field.
            Default 3 (e.g., ``019``).
        size_encoding: How the size field is encoded. Phase 1 supports
            only ``"ascii_int"``.
        size_includes_header: Whether the declared size includes the
            header bytes. Phase 1 supports only ``True``.
        data_encoding: Text encoding used for segment names and key
            data. Phase 1 supports only ``"ascii"``.
    """

    segment_name_bytes: int = 4
    size_field_bytes: int = 3
    size_encoding: str = "ascii_int"
    size_includes_header: bool = True
    data_encoding: str = "ascii"

    @property
    def header_bytes(self) -> int:
        """Total bytes consumed by a segment header (name + size)."""
        return self.segment_name_bytes + self.size_field_bytes


@dataclass(frozen=True, slots=True)
class SegmentsConfig:
    """Record-framing config used by :func:`iter_records`.

    Attributes:
        key_segment: Name of the segment that starts every record
            (e.g., ``"TU4R"``).
        end_segment: Name of the segment that terminates every record
            (e.g., ``"ENDS"``).
        key_range: ``(start, end)`` byte slice within the key segment's
            data portion that holds the record key. End-exclusive.
        record_delimiter: Bytes that separate consecutive records.
            Empty bytes means records are back-to-back with no
            delimiter.
    """

    key_segment: str
    end_segment: str
    key_range: tuple[int, int]
    record_delimiter: bytes


@dataclass(frozen=True, slots=True)
class RdwConfig:
    """Optional Record Descriptor Word prefix that sits before each record.

    Some upstream systems (mainframe extracts especially) prepend a small
    fixed prefix to every record before the key segment. The engine
    doesn't need to interpret it for comparison — :func:`iter_records`
    just consumes ``total_bytes`` bytes and discards them before reading
    the key segment header.

    The two-field shape (``rdw1`` + ``rdw2``) mirrors the classic
    mainframe RDW layout (length + reserved) but each field's width and
    encoding is configurable. ``encoding`` is recorded for future
    validation/diagnostics; the skip logic itself is encoding-agnostic.

    Attributes:
        rdw1_bytes: Width in bytes of the first RDW field. Must be > 0.
        rdw2_bytes: Width in bytes of the second RDW field. Must be > 0.
        encoding: How the fields are encoded on disk. Either
            ``"ascii_int"`` (zero-padded ASCII decimal) or
            ``"binary_le_uint"`` (unsigned little-endian integer).
            Currently used only for diagnostics; the skip path consumes
            ``total_bytes`` raw bytes regardless of encoding.
    """

    rdw1_bytes: int
    rdw2_bytes: int
    encoding: str

    @property
    def total_bytes(self) -> int:
        """Total bytes consumed by the RDW prefix (``rdw1 + rdw2``)."""
        return self.rdw1_bytes + self.rdw2_bytes


@dataclass(frozen=True, slots=True)
class Segment:
    """A single parsed segment.

    Attributes:
        name: Segment name (e.g., ``"TU4R"``).
        size: Total declared segment length in bytes, including the
            header.
        data: Raw data bytes (``size - header_bytes`` bytes long).
        offset: Byte offset of the segment header in the source stream.
    """

    name: str
    size: int
    data: bytes
    offset: int


@dataclass(frozen=True, slots=True)
class Record:
    """A parsed record framed by the key segment and terminator segment.

    The :attr:`raw` field contains the record's source bytes from the
    start of the key segment header through the end of the terminator
    segment, **excluding** any trailing record delimiter. The writer is
    responsible for appending a delimiter when emitting a record.

    Attributes:
        key: The record key extracted per ``SegmentsConfig.key_range``.
        segments: Ordered tuple of segments in the record, including
            both the key segment and the terminator segment.
        raw: Source bytes of the record without the trailing delimiter.
        offset: Byte offset of the key segment header in the source
            stream.
        length: Total bytes consumed for this record including the
            trailing delimiter (so the next record begins at
            ``offset + length``).
    """

    key: str
    segments: tuple[Segment, ...]
    raw: bytes
    offset: int
    length: int


def _read_segment(stream: BinaryIO, parser_cfg: ParserConfig, offset: int) -> Segment | None:
    """Read one segment from the stream at the current position.

    Args:
        stream: A binary readable stream positioned at a segment header.
        parser_cfg: Parser knobs.
        offset: The byte offset of the upcoming segment in the source
            stream (used for error reporting).

    Returns:
        A :class:`Segment` on success, or ``None`` if the stream is at
        a clean EOF before any header bytes are read.

    Raises:
        ParseError: If the header is truncated, the size field is
            malformed, the declared size is smaller than the header, or
            the stream ends before the declared data is fully read.
    """
    header_size = parser_cfg.header_bytes
    header = stream.read(header_size)
    if not header:
        return None
    if len(header) < header_size:
        raise ParseError(
            offset,
            f"truncated segment header: expected {header_size} bytes, got {len(header)}",
        )

    name_bytes = header[: parser_cfg.segment_name_bytes]
    size_bytes = header[parser_cfg.segment_name_bytes :]

    try:
        name = name_bytes.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ParseError(offset, f"segment name is not ASCII: {name_bytes!r}") from exc

    if parser_cfg.size_encoding != "ascii_int":
        raise ParseError(
            offset,
            f"unsupported size_encoding {parser_cfg.size_encoding!r}; "
            "Phase 1 supports only 'ascii_int'",
        )

    try:
        size = int(size_bytes.decode("ascii"))
    except ValueError as exc:
        raise ParseError(
            offset,
            f"size field is not a valid ASCII integer: {size_bytes!r}",
        ) from exc

    if not parser_cfg.size_includes_header:
        raise ParseError(
            offset,
            "size_includes_header=False is not supported in Phase 1",
        )

    if size < header_size:
        raise ParseError(
            offset,
            f"declared size {size} is smaller than header size {header_size}",
        )

    data_size = size - header_size
    data = stream.read(data_size)
    if len(data) < data_size:
        raise ParseError(
            offset,
            f"segment {name!r} declared {data_size} data bytes but only "
            f"{len(data)} bytes are available before EOF",
        )

    return Segment(name=name, size=size, data=data, offset=offset)


def _segment_bytes(seg: Segment, parser_cfg: ParserConfig) -> bytes:
    """Reconstruct the on-wire bytes of a segment from its parsed form."""
    size_str = format(seg.size, f"0{parser_cfg.size_field_bytes}d")
    return seg.name.encode("ascii") + size_str.encode("ascii") + seg.data


def iter_segments(stream: BinaryIO, parser_cfg: ParserConfig) -> Iterator[Segment]:
    """Yield :class:`Segment` instances from ``stream`` until EOF.

    The stream is consumed sequentially; no seeking is performed. The
    iterator stops cleanly when the stream is exhausted between
    segments. A truncated final segment raises :class:`ParseError`.

    Args:
        stream: A binary readable stream positioned at the start of the
            first segment.
        parser_cfg: Parser knobs.

    Yields:
        Successive segments as they are read from the stream.

    Raises:
        ParseError: On any corruption detected mid-stream.
    """
    offset = 0
    while True:
        seg = _read_segment(stream, parser_cfg, offset)
        if seg is None:
            return
        yield seg
        offset += seg.size


def iter_records(
    stream: BinaryIO,
    parser_cfg: ParserConfig,
    segments_cfg: SegmentsConfig,
    rdw_cfg: RdwConfig | None = None,
    strip_leading_bytes: int = 0,
) -> Iterator[Record]:
    """Yield :class:`Record` instances framed by key + terminator segments.

    Each record must:

    - optionally begin with ``strip_leading_bytes`` raw bytes that are
      consumed and discarded (opaque prefix, applied before RDW),
    - optionally begin with a Record Descriptor Word prefix of
      ``rdw_cfg.total_bytes`` bytes (consumed and discarded; not parsed),
    - begin with a segment whose name equals ``segments_cfg.key_segment``,
    - end with a segment whose name equals ``segments_cfg.end_segment``,
    - be followed by ``segments_cfg.record_delimiter`` (or EOF, for the
      final record).

    Order on the wire is:
    ``[strip_leading_bytes][rdw][key_segment]…[end_segment][delimiter]``.

    Args:
        stream: A binary readable stream positioned at the start of the
            first record.
        parser_cfg: Parser knobs.
        segments_cfg: Record-framing config.
        rdw_cfg: Optional :class:`RdwConfig`. When provided, the parser
            skips ``rdw_cfg.total_bytes`` bytes after the leading-bytes
            strip and before the key segment. ``Record.offset`` and
            ``Record.length`` are reported *relative to the key
            segment*, so seeking back via ``(offset, length)`` reads
            the record without re-skipping either prefix.
        strip_leading_bytes: Number of raw bytes to consume and discard
            at the head of each record, before RDW. Useful for opaque
            block headers the engine doesn't need to interpret. ``0``
            (default) disables the skip.

    Yields:
        Successive records as they are read from the stream.

    Raises:
        ParseError: If a record does not start with the key segment,
            ends in EOF before the terminator, has an unexpected
            delimiter byte sequence, contains a corrupt segment, or has
            a truncated RDW / leading-bytes strip.
    """
    offset = 0
    delimiter = segments_cfg.record_delimiter
    key_start, key_end = segments_cfg.key_range
    delim_len = len(delimiter)
    rdw_total = rdw_cfg.total_bytes if rdw_cfg is not None else 0

    while True:
        if strip_leading_bytes > 0:
            strip_bytes = stream.read(strip_leading_bytes)
            if not strip_bytes:
                return
            if len(strip_bytes) < strip_leading_bytes:
                raise ParseError(
                    offset,
                    f"truncated leading-bytes strip: expected "
                    f"{strip_leading_bytes} bytes, got {len(strip_bytes)}",
                )
            offset += strip_leading_bytes

        if rdw_total > 0:
            rdw_bytes = stream.read(rdw_total)
            if not rdw_bytes:
                return
            if len(rdw_bytes) < rdw_total:
                raise ParseError(
                    offset,
                    f"truncated RDW prefix: expected {rdw_total} bytes, " f"got {len(rdw_bytes)}",
                )
            offset += rdw_total

        record_start = offset
        first_seg = _read_segment(stream, parser_cfg, offset)
        if first_seg is None:
            return
        if first_seg.name != segments_cfg.key_segment:
            raise ParseError(
                offset,
                f"record must start with {segments_cfg.key_segment!r}, " f"got {first_seg.name!r}",
            )
        offset += first_seg.size
        segments_list: list[Segment] = [first_seg]

        while True:
            seg = _read_segment(stream, parser_cfg, offset)
            if seg is None:
                raise ParseError(
                    offset,
                    f"unexpected EOF inside record starting at offset "
                    f"{record_start}; expected terminator "
                    f"{segments_cfg.end_segment!r}",
                )
            segments_list.append(seg)
            offset += seg.size
            if seg.name == segments_cfg.end_segment:
                break

        consumed_delim = 0
        if delim_len > 0:
            delim_bytes = stream.read(delim_len)
            if delim_bytes == delimiter:
                consumed_delim = delim_len
            elif delim_bytes == b"":
                consumed_delim = 0
            else:
                raise ParseError(
                    offset,
                    f"expected record delimiter {delimiter!r} after "
                    f"terminator, got {delim_bytes!r}",
                )

        if key_end > len(first_seg.data):
            raise ParseError(
                first_seg.offset,
                f"key_range [{key_start}:{key_end}] exceeds "
                f"{segments_cfg.key_segment!r} data length "
                f"{len(first_seg.data)}",
            )
        try:
            key = first_seg.data[key_start:key_end].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ParseError(
                first_seg.offset,
                f"key bytes {first_seg.data[key_start:key_end]!r} are " "not ASCII",
            ) from exc

        raw = b"".join(_segment_bytes(s, parser_cfg) for s in segments_list)
        length = len(raw) + consumed_delim
        offset = record_start + length

        yield Record(
            key=key,
            segments=tuple(segments_list),
            raw=raw,
            offset=record_start,
            length=length,
        )
