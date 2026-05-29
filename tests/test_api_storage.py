"""Tests for the UI→engine layout projection in ``segment_compare.api.storage``.

Focused on the ADR-039 segment-alias surface: the committed templates expose
``segment_aliases`` to the UI, and both template-baked and operator-declared
aliases project into an engine layout that loads via
``layout.load_file_layout`` without a ``LayoutError``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")  # api package imports FastAPI at module load

from segment_compare.api import storage  # noqa: E402
from segment_compare.api.models import (  # noqa: E402
    AliasSegmentDecl,
    FileSideConfig,
)
from segment_compare.layout import load_file_layout  # noqa: E402


def _side(**kw: object) -> FileSideConfig:
    return FileSideConfig(
        file_path="x", key_field_name="account_nbr", **kw  # type: ignore[arg-type]
    )


def _load_projected(layout: dict, tmp_path: Path):  # type: ignore[type-arg]
    path = tmp_path / "layout_file_A.json"
    path.write_text(json.dumps(layout))
    return load_file_layout(path)


def test_template_exposes_baked_alias() -> None:
    """The committed layout_file_A surfaces the AD01→EMAD rule + UI note metadata."""
    tb = storage.load_template_bundle()
    aliases = tb.layout_a.segment_aliases
    assert [(a.wire_name, a.logical_name, a.after_segment) for a in aliases] == [
        ("AD01", "EMAD", "EM01")
    ]
    emad = next(s for s in tb.layout_a.segments if s.name == "EMAD")
    assert emad.alias_of == "AD01"
    assert emad.alias_after == "EM01"


def test_baked_alias_projects_and_loads(tmp_path: Path) -> None:
    """A template-baked alias round-trips into an engine-loadable layout."""
    tb = storage.load_template_bundle()
    layout = storage._build_engine_layout(_side(), tb.layout_a)

    assert layout["segment_aliases"] == [
        {"wire_name": "AD01", "logical_name": "EMAD", "after_segment": "EM01"}
    ]
    emad = next(s for s in layout["segments"] if s["name"] == "EMAD")
    ad01 = next(s for s in layout["segments"] if s["name"] == "AD01")
    assert emad["size"] == ad01["size"]  # rename reuses the same bytes
    assert all("key" not in f for f in emad["fields"])  # logical segment never holds the key

    loaded = _load_projected(layout, tmp_path)
    assert [(a.wire_name, a.logical_name) for a in loaded.segment_aliases] == [("AD01", "EMAD")]


def test_operator_declared_alias_synthesizes_segment_and_rule(tmp_path: Path) -> None:
    """An operator alias on a template without a baked logical segment works."""
    tb = storage.load_template_bundle()
    # Strip the baked EMAD + rule to simulate a feed that only has AD01/EM01.
    tb.layout_a.segment_aliases = []
    tb.layout_a.segments = [s for s in tb.layout_a.segments if s.name != "EMAD"]
    for s in tb.layout_a.segments:
        s.alias_of = None
        s.alias_after = None

    side = _side(
        alias_segments=[
            AliasSegmentDecl(logical_name="EMAD", wire_name="AD01", after_segment="EM01")
        ]
    )
    layout = storage._build_engine_layout(side, tb.layout_a)

    emad = next(s for s in layout["segments"] if s["name"] == "EMAD")
    ad01 = next(s for s in layout["segments"] if s["name"] == "AD01")
    assert emad["size"] == ad01["size"]
    assert layout["segment_aliases"] == [
        {"wire_name": "AD01", "logical_name": "EMAD", "after_segment": "EM01"}
    ]
    loaded = _load_projected(layout, tmp_path)
    assert [(a.wire_name, a.logical_name) for a in loaded.segment_aliases] == [("AD01", "EMAD")]


def test_operator_alias_deduped_against_template_rule() -> None:
    """One rule per wire: an operator alias for an already-aliased wire is ignored."""
    tb = storage.load_template_bundle()  # baked AD01→EMAD
    side = _side(
        alias_segments=[
            AliasSegmentDecl(logical_name="EMAD2", wire_name="AD01", after_segment="EM01")
        ]
    )
    layout = storage._build_engine_layout(side, tb.layout_a)
    rules = layout["segment_aliases"]
    assert len(rules) == 1
    assert rules[0]["logical_name"] == "EMAD"  # template rule wins


def test_no_aliases_omits_block(tmp_path: Path) -> None:
    """A layout with no aliases doesn't emit a segment_aliases key."""
    tb = storage.load_template_bundle()
    tb.layout_a.segment_aliases = []
    tb.layout_a.segments = [s for s in tb.layout_a.segments if s.name != "EMAD"]
    for s in tb.layout_a.segments:
        s.alias_of = None
        s.alias_after = None
    layout = storage._build_engine_layout(_side(), tb.layout_a)
    assert "segment_aliases" not in layout
    # still engine-loadable
    loaded = _load_projected(layout, tmp_path)
    assert loaded.segment_aliases == ()


def test_operator_alias_unknown_wire_rejected() -> None:
    """Aliasing a wire segment that isn't in the template raises StorageError."""
    tb = storage.load_template_bundle()
    side = _side(
        alias_segments=[
            AliasSegmentDecl(logical_name="ZZZZ", wire_name="NOPE", after_segment="EM01")
        ]
    )
    with pytest.raises(storage.StorageError):
        storage._build_engine_layout(side, tb.layout_a)


# ---------------------------------------------------------------------------
# Run history (ADR-041): directory-derived, newest 5, reads summary.json
# ---------------------------------------------------------------------------


def _make_run_dir(output_dir: Path, stamp: str, matched: int = 4) -> Path:
    """Create a report-<stamp>/ dir with a minimal summary.json."""
    rd = output_dir / f"report-{stamp}"
    rd.mkdir(parents=True)
    (rd / "summary.json").write_text(
        json.dumps(
            {
                "start_time": f"2026-05-29T00:00:{stamp[-2:]}+00:00",
                "file_a_path": "/data/a.dat",
                "file_b_path": "/data/b.dat",
                "records_matched": matched,
                "records_mismatched": 1,
                "keys_in_a_only": 0,
                "keys_in_b_only": 0,
                "dups_in_a": 0,
                "dups_in_b": 0,
            }
        )
    )
    return rd


def test_scan_run_history_returns_newest_five(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    for i in range(7):
        _make_run_dir(out, f"2026-05-29-10-00-0{i}", matched=i)
    (out / "not-a-run").mkdir()  # ignored — wrong prefix

    history = storage.scan_run_history(out)
    assert len(history) == 5  # capped, newest first
    assert [h["run_dir_name"] for h in history] == [
        "report-2026-05-29-10-00-06",
        "report-2026-05-29-10-00-05",
        "report-2026-05-29-10-00-04",
        "report-2026-05-29-10-00-03",
        "report-2026-05-29-10-00-02",
    ]
    # Metrics + file names come from each run's summary.json.
    assert history[0]["records_matched"] == 6
    assert history[0]["file_a"] == "a.dat"
    assert history[0]["records_mismatched"] == 1


def test_scan_run_history_missing_dir_is_empty(tmp_path: Path) -> None:
    assert storage.scan_run_history(tmp_path / "nope") == []


def test_scan_run_history_tolerates_missing_summary(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    rd = out / "report-2026-05-29-10-00-00"
    rd.mkdir(parents=True)  # no summary.json
    history = storage.scan_run_history(out)
    assert len(history) == 1
    assert history[0]["records_matched"] == 0  # zeroed, not dropped
