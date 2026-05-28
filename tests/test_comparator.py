"""Tests for ``segment_compare.comparator``."""

from __future__ import annotations

import pytest

from segment_compare.comparator import (
    STATUS_CONTENT_DIFF,
    STATUS_COUNT_DIFF,
    STATUS_MATCH,
    compare_records,
)
from segment_compare.hasher import Blake2bHasher
from segment_compare.normalizer import FieldNormalizer
from segment_compare.parser import Record, Segment


def _seg(name: str, data: bytes, offset: int = 0) -> Segment:
    return Segment(name=name, size=7 + len(data), data=data, offset=offset)


def _record(key: str, segments: list[Segment]) -> Record:
    raw = b"".join(s.name.encode() + format(s.size, "03d").encode() + s.data for s in segments)
    return Record(key=key, segments=tuple(segments), raw=raw, offset=0, length=len(raw))


# Empty rules map means every segment passes through unchanged (raw bytes
# compared directly), so these tests behave the same as before ADR-033
# replaced PositionNormalizer with the field-based form.
NORMALIZER = FieldNormalizer({})
HASHER = Blake2bHasher()


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_identical_records_match() -> None:
    a = _record("K1", [_seg("TU4R", b"K1"), _seg("NM01", b"alice"), _seg("ENDS", b"")])
    b = _record("K1", [_seg("TU4R", b"K1"), _seg("NM01", b"alice"), _seg("ENDS", b"")])
    verdict = compare_records(a, b, NORMALIZER, HASHER)
    assert verdict.matched is True
    assert verdict.mismatched_segments == ()
    assert {v.segment_name for v in verdict.segment_verdicts} == {"TU4R", "NM01", "ENDS"}
    assert all(v.matched for v in verdict.segment_verdicts)
    assert all(v.status == STATUS_MATCH for v in verdict.segment_verdicts)


def test_reordered_repeating_segments_match() -> None:
    """Three TR01s in different order on each side should match (multiset)."""
    a = _record(
        "K1",
        [
            _seg("TU4R", b"K1"),
            _seg("TR01", b"alpha"),
            _seg("TR01", b"beta"),
            _seg("TR01", b"gamma"),
            _seg("ENDS", b""),
        ],
    )
    b = _record(
        "K1",
        [
            _seg("TU4R", b"K1"),
            _seg("TR01", b"gamma"),
            _seg("TR01", b"alpha"),
            _seg("TR01", b"beta"),
            _seg("ENDS", b""),
        ],
    )
    verdict = compare_records(a, b, NORMALIZER, HASHER)
    assert verdict.matched is True


def test_single_segment_mismatch_isolated() -> None:
    """A mismatch on NM01 should not flag TU4R/ENDS."""
    a = _record("K1", [_seg("TU4R", b"K1"), _seg("NM01", b"alice"), _seg("ENDS", b"")])
    b = _record("K1", [_seg("TU4R", b"K1"), _seg("NM01", b"BOB"), _seg("ENDS", b"")])
    verdict = compare_records(a, b, NORMALIZER, HASHER)
    assert verdict.matched is False
    assert verdict.mismatched_segments == ("NM01",)
    nm = next(v for v in verdict.segment_verdicts if v.segment_name == "NM01")
    assert nm.a_count == 1
    assert nm.b_count == 1
    assert nm.status == STATUS_CONTENT_DIFF


def test_count_mismatch_3_vs_2() -> None:
    a = _record(
        "K1",
        [
            _seg("TU4R", b"K1"),
            _seg("TR01", b"x"),
            _seg("TR01", b"y"),
            _seg("TR01", b"z"),
            _seg("ENDS", b""),
        ],
    )
    b = _record(
        "K1",
        [
            _seg("TU4R", b"K1"),
            _seg("TR01", b"x"),
            _seg("TR01", b"y"),
            _seg("ENDS", b""),
        ],
    )
    verdict = compare_records(a, b, NORMALIZER, HASHER)
    assert verdict.matched is False
    assert verdict.mismatched_segments == ("TR01",)
    tr01 = next(v for v in verdict.segment_verdicts if v.segment_name == "TR01")
    assert tr01.a_count == 3
    assert tr01.b_count == 2
    assert tr01.status == STATUS_COUNT_DIFF


def test_segment_only_in_a_is_mismatch() -> None:
    a = _record(
        "K1",
        [
            _seg("TU4R", b"K1"),
            _seg("AD01", b"address"),
            _seg("ENDS", b""),
        ],
    )
    b = _record("K1", [_seg("TU4R", b"K1"), _seg("ENDS", b"")])
    verdict = compare_records(a, b, NORMALIZER, HASHER)
    assert verdict.matched is False
    assert verdict.mismatched_segments == ("AD01",)
    ad = next(v for v in verdict.segment_verdicts if v.segment_name == "AD01")
    assert ad.a_count == 1
    assert ad.b_count == 0
    assert ad.status == STATUS_COUNT_DIFF


def test_segment_verdicts_are_sorted_by_name() -> None:
    a = _record(
        "K1",
        [
            _seg("TU4R", b"K1"),
            _seg("AD01", b"x"),
            _seg("NM01", b"y"),
            _seg("ENDS", b""),
        ],
    )
    b = _record(
        "K1",
        [
            _seg("TU4R", b"K1"),
            _seg("AD01", b"x"),
            _seg("NM01", b"y"),
            _seg("ENDS", b""),
        ],
    )
    verdict = compare_records(a, b, NORMALIZER, HASHER)
    names = [v.segment_name for v in verdict.segment_verdicts]
    assert names == sorted(names)


def test_compare_uses_normalizer_to_align_a_and_b() -> None:
    """A 5-byte NM01 field compares equal across A and B via field-name keying."""
    from segment_compare.normalizer import FieldDef, FieldNormalizationRule

    rule = FieldNormalizationRule(
        file_a_layout=(FieldDef(name="name", length=5, exclude=False),),
        file_b_layout=(FieldDef(name="name", length=5, exclude=False),),
    )
    norm = FieldNormalizer({"NM01": rule})
    a = _record("K1", [_seg("TU4R", b"K1"), _seg("NM01", b"alice"), _seg("ENDS", b"")])
    b = _record("K1", [_seg("TU4R", b"K1"), _seg("NM01", b"alice"), _seg("ENDS", b"")])
    verdict = compare_records(a, b, norm, HASHER)
    assert verdict.matched is True


def test_compare_rejects_mismatched_keys() -> None:
    a = _record("K1", [_seg("TU4R", b"K1"), _seg("ENDS", b"")])
    b = _record("K2", [_seg("TU4R", b"K2"), _seg("ENDS", b"")])
    with pytest.raises(ValueError) as excinfo:
        compare_records(a, b, NORMALIZER, HASHER)
    assert "equal keys" in str(excinfo.value)


def test_verdict_is_frozen() -> None:
    a = _record("K1", [_seg("TU4R", b"K1"), _seg("ENDS", b"")])
    b = _record("K1", [_seg("TU4R", b"K1"), _seg("ENDS", b"")])
    verdict = compare_records(a, b, NORMALIZER, HASHER)
    with pytest.raises(AttributeError):
        verdict.matched = False  # type: ignore[misc]


def test_record_verdict_with_builtin_hasher() -> None:
    """The Hasher Protocol accepts ints as well as bytes."""
    from segment_compare.hasher import BuiltinHasher

    a = _record("K1", [_seg("TU4R", b"K1"), _seg("NM01", b"alice"), _seg("ENDS", b"")])
    b = _record("K1", [_seg("TU4R", b"K1"), _seg("NM01", b"alice"), _seg("ENDS", b"")])
    verdict = compare_records(a, b, NORMALIZER, BuiltinHasher())
    assert verdict.matched is True
