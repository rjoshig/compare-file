"""Tests for ``segment_compare.parser``."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from segment_compare.parser import (
    ParseError,
    ParserConfig,
    RdwConfig,
    Record,
    Segment,
    SegmentsConfig,
    iter_records,
    iter_segments,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

DEFAULT_PARSER = ParserConfig()

# Synthetic parser tests below construct their own segments with key at data
# offset [0, 12) for brevity. The committed sample files use the realistic
# format where the key sits at TU4R data offset [4, 16) (after a literal
# "DATA" prefix).
DEFAULT_SEGMENTS = SegmentsConfig(
    key_segment="TU4R",
    end_segment="ENDS",
    key_range=(0, 12),
    record_delimiter=b"\n",
)

REALISTIC_SEGMENTS = SegmentsConfig(
    key_segment="TU4R",
    end_segment="ENDS",
    key_range=(4, 16),
    record_delimiter=b"\n",
)


def _stream(data: bytes) -> io.BytesIO:
    return io.BytesIO(data)


# ---------------------------------------------------------------------------
# iter_segments
# ---------------------------------------------------------------------------


def test_iter_segments_single_segment_round_trip() -> None:
    raw = b"TU4R019KEY000000001A"  # 19 bytes: 7 header + 12 data
    raw = b"TU4R019KEY000000001A"[:19]
    segments = list(iter_segments(_stream(raw), DEFAULT_PARSER))
    assert len(segments) == 1
    seg = segments[0]
    assert seg.name == "TU4R"
    assert seg.size == 19
    assert seg.data == b"KEY000000001"
    assert seg.offset == 0


def test_iter_segments_empty_stream_yields_nothing() -> None:
    assert list(iter_segments(_stream(b""), DEFAULT_PARSER)) == []


def test_iter_segments_multiple_segments_offsets_advance() -> None:
    raw = b"TU4R019KEY000000001NM01017NAME_ALICEENDS007"
    segments = list(iter_segments(_stream(raw), DEFAULT_PARSER))
    assert [s.name for s in segments] == ["TU4R", "NM01", "ENDS"]
    assert [s.size for s in segments] == [19, 17, 7]
    assert [s.offset for s in segments] == [0, 19, 36]
    assert segments[-1].data == b""


def test_iter_segments_truncated_header_raises() -> None:
    raw = b"TU4R01"  # 6 bytes < 7-byte header
    with pytest.raises(ParseError) as excinfo:
        list(iter_segments(_stream(raw), DEFAULT_PARSER))
    assert excinfo.value.offset == 0
    assert "truncated" in excinfo.value.message


def test_iter_segments_size_beyond_eof_raises() -> None:
    raw = b"TU4R099KEY000000001"  # declares 99 bytes but only has 19
    with pytest.raises(ParseError) as excinfo:
        list(iter_segments(_stream(raw), DEFAULT_PARSER))
    assert excinfo.value.offset == 0
    assert "EOF" in excinfo.value.message


def test_iter_segments_bad_ascii_size_raises() -> None:
    raw = b"TU4RABCKEY000000001"
    with pytest.raises(ParseError) as excinfo:
        list(iter_segments(_stream(raw), DEFAULT_PARSER))
    assert excinfo.value.offset == 0
    assert "ASCII integer" in excinfo.value.message


def test_iter_segments_size_smaller_than_header_raises() -> None:
    raw = b"TU4R005"  # size 5 < 7-byte header
    with pytest.raises(ParseError) as excinfo:
        list(iter_segments(_stream(raw), DEFAULT_PARSER))
    assert "smaller than header" in excinfo.value.message


def test_iter_segments_rejects_unsupported_size_encoding() -> None:
    cfg = ParserConfig(size_encoding="binary_be_uint")
    with pytest.raises(ParseError) as excinfo:
        list(iter_segments(_stream(b"TU4R019KEY000000001"), cfg))
    assert "size_encoding" in excinfo.value.message


def test_iter_segments_rejects_size_excludes_header() -> None:
    cfg = ParserConfig(size_includes_header=False)
    with pytest.raises(ParseError) as excinfo:
        list(iter_segments(_stream(b"TU4R012KEY000000001"), cfg))
    assert "size_includes_header" in excinfo.value.message


def test_iter_segments_rejects_non_ascii_name() -> None:
    raw = b"\xff\xff\xff\xff019KEY000000001"
    with pytest.raises(ParseError) as excinfo:
        list(iter_segments(_stream(raw), DEFAULT_PARSER))
    assert "ASCII" in excinfo.value.message


# ---------------------------------------------------------------------------
# iter_records
# ---------------------------------------------------------------------------


def test_iter_records_single_record_round_trip() -> None:
    raw = b"TU4R019KEY000000001NM01017NAME_ALICEENDS007\n"
    records = list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS))
    assert len(records) == 1
    rec = records[0]
    assert rec.key == "KEY000000001"
    assert [s.name for s in rec.segments] == ["TU4R", "NM01", "ENDS"]
    assert rec.raw == b"TU4R019KEY000000001NM01017NAME_ALICEENDS007"
    assert rec.offset == 0
    assert rec.length == len(rec.raw) + 1  # plus delimiter


def test_iter_records_multi_record_stream_offsets() -> None:
    raw = (
        b"TU4R019KEY000000001NM01017NAME_ALICEENDS007\n"
        b"TU4R019KEY000000002NM01017NAME_BOB__ENDS007\n"
    )
    records = list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS))
    assert len(records) == 2
    assert records[0].key == "KEY000000001"
    assert records[1].key == "KEY000000002"
    assert records[0].offset == 0
    assert records[0].length == 44
    assert records[1].offset == 44
    assert records[1].length == 44


def test_iter_records_trailing_record_without_delimiter_ok() -> None:
    raw = b"TU4R019KEY000000001NM01017NAME_ALICEENDS007"  # no trailing \n
    records = list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS))
    assert len(records) == 1
    assert records[0].length == len(records[0].raw)  # no delimiter consumed


def test_iter_records_wrong_starter_raises() -> None:
    raw = b"NM01017NAME_ALICEENDS007\n"
    with pytest.raises(ParseError) as excinfo:
        list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS))
    assert "must start with" in excinfo.value.message


def test_iter_records_missing_terminator_eof_raises() -> None:
    raw = b"TU4R019KEY000000001NM01017NAME_ALICE"  # no ENDS, no delim
    with pytest.raises(ParseError) as excinfo:
        list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS))
    assert "terminator" in excinfo.value.message or "EOF" in excinfo.value.message


def test_iter_records_unexpected_delimiter_raises() -> None:
    raw = b"TU4R019KEY000000001NM01017NAME_ALICEENDS007X"  # X instead of \n
    with pytest.raises(ParseError) as excinfo:
        list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS))
    assert "delimiter" in excinfo.value.message


def test_iter_records_empty_delimiter_records_back_to_back() -> None:
    cfg = SegmentsConfig(
        key_segment="TU4R",
        end_segment="ENDS",
        key_range=(0, 12),
        record_delimiter=b"",
    )
    raw = (
        b"TU4R019KEY000000001NM01017NAME_ALICEENDS007"
        b"TU4R019KEY000000002NM01017NAME_BOB__ENDS007"
    )
    records = list(iter_records(_stream(raw), DEFAULT_PARSER, cfg))
    assert [r.key for r in records] == ["KEY000000001", "KEY000000002"]


def test_iter_records_key_range_beyond_data_raises() -> None:
    cfg = SegmentsConfig(
        key_segment="TU4R",
        end_segment="ENDS",
        key_range=(0, 99),
        record_delimiter=b"\n",
    )
    raw = b"TU4R019KEY000000001ENDS007\n"
    with pytest.raises(ParseError) as excinfo:
        list(iter_records(_stream(raw), DEFAULT_PARSER, cfg))
    assert "key_range" in excinfo.value.message


# ---------------------------------------------------------------------------
# Smoke test against the committed sample files
# ---------------------------------------------------------------------------


def test_sample_a_parses_to_expected_records() -> None:
    with (EXAMPLES / "sample_a.dat").open("rb") as fh:
        records = list(iter_records(fh, DEFAULT_PARSER, REALISTIC_SEGMENTS))
    # File A: 10 records covering all 10 phase-1 scenarios (see examples/README.md).
    # Note: KEY000000008 appears twice (dup-in-A scenario).
    assert [r.key for r in records] == [
        "KEY000000001",
        "KEY000000002",
        "KEY000000003",
        "KEY000000004",
        "KEY000000005",
        "KEY000000006",
        "KEY000000008",
        "KEY000000008",
        "KEY000000010",
        "KEY000000011",
    ]
    for rec in records:
        # Every record has TU4R, SH01, NM01, ≥3×TR01, 2×SC01, CL01, ENDS.
        names = [s.name for s in rec.segments]
        assert names[0] == "TU4R"
        assert names[-1] == "ENDS"
        assert names.count("TR01") in (3, 4)


def test_sample_b_parses_to_expected_records() -> None:
    with (EXAMPLES / "sample_b.dat").open("rb") as fh:
        records = list(iter_records(fh, DEFAULT_PARSER, REALISTIC_SEGMENTS))
    # File B: 11 records. Note: KEY000000009 appears twice (dup-in-B scenario).
    assert [r.key for r in records] == [
        "KEY000000001",
        "KEY000000002",
        "KEY000000003",
        "KEY000000004",
        "KEY000000005",
        "KEY000000007",
        "KEY000000009",
        "KEY000000009",
        "KEY000000010",
        "KEY000000011",
        "KEY000000012",
    ]


def test_sample_record_raw_round_trips() -> None:
    """The raw bytes a parser yields should be byte-identical to the source slice."""
    with (EXAMPLES / "sample_a.dat").open("rb") as fh:
        raw_source = fh.read()
    with (EXAMPLES / "sample_a.dat").open("rb") as fh:
        records = list(iter_records(fh, DEFAULT_PARSER, REALISTIC_SEGMENTS))
    for rec in records:
        assert raw_source[rec.offset : rec.offset + len(rec.raw)] == rec.raw


def test_ends_with_non_zero_data_payload_is_parsed_correctly() -> None:
    """Phase-1 parser verification: ENDS may carry data (segment count etc.).

    The realistic samples use ``ENDS010NNN`` where ``NNN`` is the ASCII
    segment count. The parser must read the declared 3 data bytes and
    treat the segment as a terminator regardless of payload size.
    """
    raw = (
        b"TU4R023DATAKEY000000001TRLR"  # 7 header + 16 data (DATA + 12-byte key)
        b"NM01017NAME_ALICE"  # 7 header + 10 data
        b"ENDS010ABC"  # 7 header + 3 data
        b"\n"
    )
    # Adjust: TRLR is 4 bytes, but TU4R023 declares 23 bytes total = 7 header + 16 data.
    # 4 (DATA) + 12 (key) = 16 data bytes; no room for TRLR. Rebuild minimally.
    raw = (
        b"TU4R023DATAKEY000000001"  # 7 header + 4 "DATA" + 12 key = 23 bytes total
        b"NM01017NAME_ALICE"
        b"ENDS010ABC"
        b"\n"
    )
    records = list(iter_records(_stream(raw), DEFAULT_PARSER, REALISTIC_SEGMENTS))
    assert len(records) == 1
    rec = records[0]
    assert rec.key == "KEY000000001"
    # The ENDS segment carries its payload through to the parsed record.
    ends_seg = rec.segments[-1]
    assert ends_seg.name == "ENDS"
    assert ends_seg.size == 10
    assert ends_seg.data == b"ABC"


# ---------------------------------------------------------------------------
# RDW prefix skip
# ---------------------------------------------------------------------------


def test_iter_records_with_rdw_skips_prefix_and_reports_offsets_after_rdw() -> None:
    """A 4-byte RDW prefix is consumed; record offsets/lengths cover only TU4R..ENDS+delim."""
    rdw = RdwConfig(rdw1_bytes=2, rdw2_bytes=2, encoding="binary_le_uint")
    body = b"TU4R019KEY000000001NM01017NAME_ALICEENDS007"
    raw = b"\x2f\x00\x00\x00" + body + b"\n"
    records = list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS, rdw))
    assert len(records) == 1
    rec = records[0]
    assert rec.key == "KEY000000001"
    assert rec.raw == body
    assert rec.offset == 4  # post-RDW position
    assert rec.length == len(body) + 1  # body + delimiter


def test_iter_records_with_rdw_multi_record_offsets_advance_by_rdw_plus_record() -> None:
    rdw = RdwConfig(rdw1_bytes=2, rdw2_bytes=2, encoding="binary_le_uint")
    body_a = b"TU4R019KEY000000001NM01017NAME_ALICEENDS007"
    body_b = b"TU4R019KEY000000002NM01017NAME_BOB__ENDS007"
    raw = b"\x2c\x00\x00\x00" + body_a + b"\n" + b"\x2c\x00\x00\x00" + body_b + b"\n"
    records = list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS, rdw))
    assert len(records) == 2
    assert records[0].key == "KEY000000001"
    assert records[1].key == "KEY000000002"
    # First record starts at byte 4 (after first RDW).
    assert records[0].offset == 4
    # Second record starts after: 4 (rdw_a) + 44 (rec_a body+delim) + 4 (rdw_b) = 52.
    assert records[1].offset == 52


def test_iter_records_truncated_rdw_raises() -> None:
    rdw = RdwConfig(rdw1_bytes=2, rdw2_bytes=2, encoding="binary_le_uint")
    raw = b"\x01\x00"  # 2 bytes only; need 4
    with pytest.raises(ParseError) as excinfo:
        list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS, rdw))
    assert "RDW" in excinfo.value.message


def test_iter_records_no_rdw_bytes_at_eof_is_clean() -> None:
    """Zero bytes available at the start of a new record (after the previous one) is OK."""
    rdw = RdwConfig(rdw1_bytes=2, rdw2_bytes=2, encoding="binary_le_uint")
    body = b"TU4R019KEY000000001NM01017NAME_ALICEENDS007"
    raw = b"\x2c\x00\x00\x00" + body + b"\n"  # exactly one record, then EOF
    records = list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS, rdw))
    assert len(records) == 1


def test_iter_records_rdw_none_is_identity_with_default() -> None:
    """Passing rdw_cfg=None must behave exactly like omitting it."""
    body = b"TU4R019KEY000000001NM01017NAME_ALICEENDS007"
    raw = body + b"\n"
    a = list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS))
    b = list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS, None))
    assert a == b


def test_rdw_config_total_bytes() -> None:
    assert RdwConfig(rdw1_bytes=2, rdw2_bytes=3, encoding="ascii_int").total_bytes == 5
    assert RdwConfig(rdw1_bytes=1, rdw2_bytes=1, encoding="binary_le_uint").total_bytes == 2


# ---------------------------------------------------------------------------
# strip_leading_bytes prefix skip (ADR-033 / Stage 3)
# ---------------------------------------------------------------------------


def test_iter_records_with_strip_leading_bytes_skips_prefix() -> None:
    body = b"TU4R019KEY000000001NM01017NAME_ALICEENDS007"
    raw = b"XXYYZ" + body + b"\n"  # 5 opaque bytes before TU4R
    records = list(
        iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS, strip_leading_bytes=5)
    )
    assert len(records) == 1
    assert records[0].key == "KEY000000001"
    assert records[0].offset == 5  # post-strip
    assert records[0].length == len(body) + 1


def test_iter_records_strip_then_rdw_then_key() -> None:
    body = b"TU4R019KEY000000001NM01017NAME_ALICEENDS007"
    raw = b"AAAAA" + b"\x2f\x00\x00\x00" + body + b"\n"  # 5 strip + 4 rdw + body + delim
    rdw = RdwConfig(rdw1_bytes=2, rdw2_bytes=2, encoding="binary_le_uint")
    records = list(
        iter_records(
            _stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS, rdw_cfg=rdw, strip_leading_bytes=5
        )
    )
    assert len(records) == 1
    assert records[0].offset == 9  # 5 strip + 4 rdw


def test_iter_records_truncated_strip_raises() -> None:
    raw = b"AAA"  # 3 bytes, asking for 5
    with pytest.raises(ParseError) as excinfo:
        list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS, strip_leading_bytes=5))
    assert "leading-bytes strip" in excinfo.value.message


def test_iter_records_strip_zero_is_identity() -> None:
    body = b"TU4R019KEY000000001NM01017NAME_ALICEENDS007"
    raw = body + b"\n"
    a = list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS))
    b = list(iter_records(_stream(raw), DEFAULT_PARSER, DEFAULT_SEGMENTS, strip_leading_bytes=0))
    assert a == b


# ---------------------------------------------------------------------------
# Dataclass identity / frozen-ness
# ---------------------------------------------------------------------------


def test_segment_is_frozen() -> None:
    seg = Segment(name="TU4R", size=19, data=b"x" * 12, offset=0)
    with pytest.raises(AttributeError):
        seg.name = "ZZZZ"  # type: ignore[misc]


def test_record_is_frozen() -> None:
    rec = Record(
        key="K",
        segments=(),
        raw=b"",
        offset=0,
        length=0,
    )
    with pytest.raises(AttributeError):
        rec.key = "X"  # type: ignore[misc]
