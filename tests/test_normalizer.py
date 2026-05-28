"""Tests for ``segment_compare.normalizer``."""

from __future__ import annotations

import pytest

from segment_compare.config import NormalizationRule
from segment_compare.normalizer import PositionNormalizer, _remove_ranges


def _rule(
    file_a_strip: tuple[tuple[int, int], ...] = (),
    file_b_strip: tuple[tuple[int, int], ...] = (),
    exclude_positions: tuple[tuple[int, int], ...] = (),
) -> NormalizationRule:
    return NormalizationRule(
        file_a_strip=file_a_strip,
        file_b_strip=file_b_strip,
        exclude_positions=exclude_positions,
    )


# ---------------------------------------------------------------------------
# _remove_ranges helper
# ---------------------------------------------------------------------------


def test_remove_ranges_empty_ranges_is_identity() -> None:
    assert _remove_ranges(b"hello world", ()) == b"hello world"


def test_remove_ranges_single_range() -> None:
    # Remove "world" → "hello "
    assert _remove_ranges(b"hello world", ((6, 11),)) == b"hello "


def test_remove_ranges_multiple_non_contiguous() -> None:
    # Remove "ell" and "or" → "ho wld"
    assert _remove_ranges(b"hello world", ((1, 4), (7, 9))) == b"ho wld"


def test_remove_ranges_overlapping_ranges_merged() -> None:
    # Overlapping (2, 5) and (4, 8) → merged to (2, 8)
    assert _remove_ranges(b"abcdefghij", ((2, 5), (4, 8))) == b"abij"


def test_remove_ranges_unsorted_input() -> None:
    # Same as test_remove_ranges_multiple_non_contiguous but unsorted
    assert _remove_ranges(b"hello world", ((7, 9), (1, 4))) == b"ho wld"


def test_remove_ranges_adjacent_ranges_merged() -> None:
    # (1, 3) and (3, 5) touch → merged to (1, 5)
    assert _remove_ranges(b"abcdef", ((1, 3), (3, 5))) == b"af"


def test_remove_ranges_clipped_to_data_bounds() -> None:
    # Range extending past end is clipped
    assert _remove_ranges(b"abc", ((1, 100),)) == b"a"
    # Negative start clipped to 0
    assert _remove_ranges(b"abc", ((-5, 2),)) == b"c"


def test_remove_ranges_empty_after_clip_is_identity() -> None:
    # (5, 3) is invalid in the data length → after clip and start<end check, nothing removed
    assert _remove_ranges(b"abc", ((5, 3),)) == b"abc"


def test_remove_ranges_removes_entire_data() -> None:
    assert _remove_ranges(b"abc", ((0, 3),)) == b""


# ---------------------------------------------------------------------------
# PositionNormalizer
# ---------------------------------------------------------------------------


def test_normalize_segment_without_rule_is_identity() -> None:
    norm = PositionNormalizer({})
    assert norm.normalize("NM01", b"hello", "A") == b"hello"
    assert norm.normalize("NM01", b"hello", "B") == b"hello"


def test_normalize_noop_rule_is_identity() -> None:
    norm = PositionNormalizer({"NM01": _rule()})
    assert norm.normalize("NM01", b"hello", "A") == b"hello"


def test_normalize_file_a_strip_only_affects_a() -> None:
    norm = PositionNormalizer({"NM01": _rule(file_a_strip=((0, 3),))})
    assert norm.normalize("NM01", b"hello", "A") == b"lo"
    assert norm.normalize("NM01", b"hello", "B") == b"hello"


def test_normalize_file_b_strip_only_affects_b() -> None:
    norm = PositionNormalizer({"NM01": _rule(file_b_strip=((0, 3),))})
    assert norm.normalize("NM01", b"hello", "A") == b"hello"
    assert norm.normalize("NM01", b"hello", "B") == b"lo"


def test_normalize_exclude_applies_to_both_sources() -> None:
    norm = PositionNormalizer({"NM01": _rule(exclude_positions=((1, 3),))})
    assert norm.normalize("NM01", b"abcde", "A") == b"ade"
    assert norm.normalize("NM01", b"abcde", "B") == b"ade"


def test_normalize_strip_runs_before_exclude() -> None:
    """Exclude indices apply to the POST-STRIP bytes, not the raw bytes."""
    # Raw:        "abcdefghij"  (10 bytes)
    # file_a_strip [0, 3) → "defghij"  (7 bytes)
    # exclude    [1, 3) of "defghij" → "dghij"  (5 bytes)
    norm = PositionNormalizer({"NM01": _rule(file_a_strip=((0, 3),), exclude_positions=((1, 3),))})
    assert norm.normalize("NM01", b"abcdefghij", "A") == b"dghij"


def test_normalize_cross_file_alignment() -> None:
    """Strip A's bytes 0-3, leave B alone; both should land on the same bytes."""
    # A raw:     "XYZhello"  → strip [0,3] → "hello"
    # B raw:     "hello"     → no strip   → "hello"
    norm = PositionNormalizer({"NM01": _rule(file_a_strip=((0, 3),))})
    assert norm.normalize("NM01", b"XYZhello", "A") == norm.normalize("NM01", b"hello", "B")


def test_normalize_invalid_source_raises() -> None:
    norm = PositionNormalizer({"NM01": _rule()})
    with pytest.raises(ValueError):
        norm.normalize("NM01", b"x", "C")  # type: ignore[arg-type]


def test_normalize_multiple_strip_ranges() -> None:
    # Strip ranges (0, 2) and (5, 7) from "abcdefghij" → "cdehij"
    # Wait: "abcdefghij" - remove [0,2) "ab" - remove [5,7) "fg" → "cdehij"
    # Let me verify: a(0)b(1)c(2)d(3)e(4)f(5)g(6)h(7)i(8)j(9)
    # Remove [0,2)=ab and [5,7)=fg → cdehij ✓ (6 bytes)
    norm = PositionNormalizer({"NM01": _rule(file_a_strip=((0, 2), (5, 7)))})
    assert norm.normalize("NM01", b"abcdefghij", "A") == b"cdehij"


def test_normalize_overlapping_strip_ranges_handled() -> None:
    """Overlapping strip ranges must not crash and must merge cleanly."""
    norm = PositionNormalizer({"NM01": _rule(file_a_strip=((0, 4), (2, 6)))})
    # (0,4) ∪ (2,6) = (0,6); "abcdefghij" → "ghij"
    assert norm.normalize("NM01", b"abcdefghij", "A") == b"ghij"
