"""Tests for ``FieldNormalizer`` + ``CompositeNormalizer``."""

from __future__ import annotations

import pytest

from segment_compare.config import (
    FieldDef,
    FieldNormalizationRule,
    NormalizationRule,
)
from segment_compare.normalizer import (
    FIELD_SEPARATOR,
    CompositeNormalizer,
    FieldNormalizer,
    PositionNormalizer,
)

# Reused layouts ---------------------------------------------------------


def _nm01_layout(
    order_a: tuple[str, str, str], order_b: tuple[str, str, str]
) -> FieldNormalizationRule:
    """Build an NM01 rule with the listed physical field order per side."""
    widths = {"first": 20, "middle": 15, "last": 15}
    excludes = {"first": False, "middle": True, "last": False}

    def _layout(order: tuple[str, str, str]) -> tuple[FieldDef, ...]:
        return tuple(FieldDef(name=n, length=widths[n], exclude=excludes[n]) for n in order)

    return FieldNormalizationRule(
        file_a_layout=_layout(order_a),
        file_b_layout=_layout(order_b),
    )


# FieldNormalizer.normalize -------------------------------------------


def test_field_normalizer_returns_raw_when_segment_not_in_rules() -> None:
    norm = FieldNormalizer({})
    assert norm.normalize("NM01", b"hello", "A") == b"hello"


def test_field_normalizer_drops_excluded_field() -> None:
    """exclude=True fields must not appear in the canonical bytes."""
    rule = FieldNormalizationRule(
        file_a_layout=(
            FieldDef(name="first", length=5, exclude=False),
            FieldDef(name="middle", length=3, exclude=True),
            FieldDef(name="last", length=4, exclude=False),
        ),
        file_b_layout=(
            FieldDef(name="first", length=5, exclude=False),
            FieldDef(name="middle", length=3, exclude=True),
            FieldDef(name="last", length=4, exclude=False),
        ),
    )
    norm = FieldNormalizer({"NM01": rule})
    out = norm.normalize("NM01", b"ALICEABCDOE!", "A")
    # Retained fields sorted alphabetically: first, last
    assert out == b"first=ALICE" + FIELD_SEPARATOR + b"last=DOE!"


def test_field_normalizer_sorts_retained_fields_so_a_and_b_with_different_order_match() -> None:
    """A's physical (first, middle, last) and B's (last, middle, first) must canonicalize equal."""
    rule = _nm01_layout(
        order_a=("first", "middle", "last"),
        order_b=("last", "middle", "first"),
    )
    norm = FieldNormalizer({"NM01": rule})

    # A: first=ALICE..., middle=MARIE..., last=ANDERSON...
    a_data = b"ALICE               " + b"MARIE          " + b"ANDERSON       "
    # B: last=ANDERSON..., middle=MARIE..., first=ALICE...
    b_data = b"ANDERSON       " + b"MARIE          " + b"ALICE               "

    out_a = norm.normalize("NM01", a_data, "A")
    out_b = norm.normalize("NM01", b_data, "B")
    assert out_a == out_b
    # And the canonical form has the two retained fields sorted by name:
    assert out_a.startswith(b"first=ALICE")
    assert b"last=ANDERSON" in out_a
    assert b"middle=" not in out_a


def test_field_normalizer_different_field_counts_a_vs_b_with_filler_excluded_matches() -> None:
    """The user's headline case: B carries trailing filler that's excluded.

    A's NM01 data = 35 bytes (2 fields). B's NM01 data = 40 bytes
    (2 fields + 5-byte filler marked exclude=True). Same logical
    content → same canonical bytes → match.
    """
    rule = FieldNormalizationRule(
        file_a_layout=(
            FieldDef(name="first_name", length=20, exclude=False),
            FieldDef(name="last_name", length=15, exclude=False),
        ),
        file_b_layout=(
            FieldDef(name="first_name", length=20, exclude=False),
            FieldDef(name="last_name", length=15, exclude=False),
            FieldDef(name="trailing_pad", length=5, exclude=True),
        ),
    )
    norm = FieldNormalizer({"NM01": rule})

    a_data = b"ALICE               " + b"ANDERSON       "  # 35 bytes
    b_data = b"ALICE               " + b"ANDERSON       " + b"\x00\x00\x00\x00\x00"  # 40 bytes

    out_a = norm.normalize("NM01", a_data, "A")
    out_b = norm.normalize("NM01", b_data, "B")
    assert out_a == out_b


def test_field_normalizer_layout_sum_mismatch_raises() -> None:
    """Sum of field lengths must equal the segment data length."""
    rule = FieldNormalizationRule(
        file_a_layout=(FieldDef(name="x", length=10, exclude=False),),
        file_b_layout=(FieldDef(name="x", length=10, exclude=False),),
    )
    norm = FieldNormalizer({"NM01": rule})
    with pytest.raises(ValueError, match="data length 5 does not match layout sum 10"):
        norm.normalize("NM01", b"short", "A")


def test_field_normalizer_invalid_source_raises() -> None:
    rule = FieldNormalizationRule(
        file_a_layout=(FieldDef(name="x", length=5, exclude=False),),
        file_b_layout=(FieldDef(name="x", length=5, exclude=False),),
    )
    norm = FieldNormalizer({"NM01": rule})
    with pytest.raises(ValueError, match="source must be 'A' or 'B'"):
        norm.normalize("NM01", b"hello", "C")  # type: ignore[arg-type]


def test_field_normalizer_all_fields_excluded_yields_empty_bytes() -> None:
    rule = FieldNormalizationRule(
        file_a_layout=(FieldDef(name="x", length=5, exclude=True),),
        file_b_layout=(FieldDef(name="x", length=5, exclude=True),),
    )
    norm = FieldNormalizer({"NM01": rule})
    assert norm.normalize("NM01", b"hello", "A") == b""
    assert norm.normalize("NM01", b"world", "B") == b""


def test_field_normalizer_pass_through_for_segments_without_rules_in_mixed_map() -> None:
    """A FieldNormalizer constructed with rules for NM01 must passthrough other segments."""
    rule = FieldNormalizationRule(
        file_a_layout=(FieldDef(name="x", length=3, exclude=False),),
        file_b_layout=(FieldDef(name="x", length=3, exclude=False),),
    )
    norm = FieldNormalizer({"NM01": rule})
    # TR01 not in rules → raw_data returned unchanged.
    assert norm.normalize("TR01", b"abc", "A") == b"abc"


# CompositeNormalizer -------------------------------------------------


def test_composite_routes_position_segment_to_position_normalizer() -> None:
    position = {
        "CL01": NormalizationRule(
            file_a_strip=(),
            file_b_strip=(),
            exclude_positions=((0, 3),),
        ),
    }
    field: dict[str, FieldNormalizationRule] = {}
    composite = CompositeNormalizer(position, field)

    # CL01: exclude first 3 bytes → just "DEF"
    assert composite.normalize("CL01", b"ABCDEF", "A") == b"DEF"


def test_composite_routes_field_segment_to_field_normalizer() -> None:
    position: dict[str, NormalizationRule] = {}
    field = {
        "NM01": FieldNormalizationRule(
            file_a_layout=(
                FieldDef(name="first", length=3, exclude=False),
                FieldDef(name="last", length=3, exclude=False),
            ),
            file_b_layout=(
                FieldDef(name="first", length=3, exclude=False),
                FieldDef(name="last", length=3, exclude=False),
            ),
        ),
    }
    composite = CompositeNormalizer(position, field)

    out = composite.normalize("NM01", b"ABCDEF", "A")
    assert out == b"first=ABC" + FIELD_SEPARATOR + b"last=DEF"


def test_composite_passes_through_segment_with_no_rule_in_either_map() -> None:
    composite = CompositeNormalizer({}, {})
    assert composite.normalize("XYZ1", b"raw bytes", "A") == b"raw bytes"


def test_composite_rejects_segment_in_both_maps() -> None:
    position = {
        "NM01": NormalizationRule(file_a_strip=(), file_b_strip=(), exclude_positions=()),
    }
    field = {
        "NM01": FieldNormalizationRule(
            file_a_layout=(FieldDef(name="x", length=1, exclude=False),),
            file_b_layout=(FieldDef(name="x", length=1, exclude=False),),
        ),
    }
    with pytest.raises(ValueError, match="have both position and field rules"):
        CompositeNormalizer(position, field)


def test_composite_field_and_position_can_coexist_for_different_segments() -> None:
    """Mixing the two forms across segments is the headline Phase 2 capability."""
    position = {
        "ENDS": NormalizationRule(file_a_strip=(), file_b_strip=(), exclude_positions=((0, 3),)),
    }
    field = {
        "NM01": FieldNormalizationRule(
            file_a_layout=(FieldDef(name="first", length=3, exclude=False),),
            file_b_layout=(FieldDef(name="first", length=3, exclude=False),),
        ),
    }
    composite = CompositeNormalizer(position, field)

    # ENDS routes through position-based
    assert composite.normalize("ENDS", b"012XYZ", "A") == b"XYZ"
    # NM01 routes through field-based
    assert composite.normalize("NM01", b"ABC", "A") == b"first=ABC"
    # Unknown segment passes through
    assert composite.normalize("OTHER", b"raw", "A") == b"raw"


def test_composite_satisfies_normalizer_protocol_via_position_normalizer_signature() -> None:
    """Smoke: CompositeNormalizer is duck-compatible with PositionNormalizer."""
    # Build a position-only composite and confirm it produces the same output as
    # a bare PositionNormalizer would. This is the contract the comparator relies on.
    position = {
        "CL01": NormalizationRule(
            file_a_strip=(),
            file_b_strip=(),
            exclude_positions=((1, 3),),
        ),
    }
    composite = CompositeNormalizer(position, {})
    bare = PositionNormalizer(position)
    assert composite.normalize("CL01", b"ABCDE", "A") == bare.normalize("CL01", b"ABCDE", "A")
