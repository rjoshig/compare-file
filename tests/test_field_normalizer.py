"""Tests for ``FieldNormalizer`` (ADR-033)."""

from __future__ import annotations

import pytest

from segment_compare.normalizer import (
    FIELD_SEPARATOR,
    FieldDef,
    FieldNormalizationRule,
    FieldNormalizer,
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
    """Cross-system reconciliation: B carries trailing filler that's excluded.

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
