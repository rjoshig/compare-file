"""Unit tests for the SQLite index (``segment_compare.api.db``).

The index is dual-written from the API on config save / run completion; here
we drive the module directly against a temp DB to verify schema creation,
run/config recording, pagination + search, per-segment rollups, dashboard
aggregation, and persistence across reconnect.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")  # api package imports FastAPI at module load

from segment_compare.api import db  # noqa: E402
from segment_compare.api.models import (  # noqa: E402
    FileSideConfig,
    SaveConfigRequest,
)


@pytest.fixture()
def index_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the index at a temp DB file and create the schema."""
    db_file = tmp_path / "index.db"
    monkeypatch.setenv("SEGCMP_DB_PATH", str(db_file))
    db.init_db()
    return db_file


def _write_run(output_dir: Path, stamp: str, **summary: object) -> Path:
    """Create a ``report-<stamp>/`` dir with a ``summary.json`` and return it."""
    run_dir = output_dir / f"report-{stamp}"
    run_dir.mkdir(parents=True)
    base: dict[str, object] = {
        "file_a_path": "/data/sample_a.dat",
        "file_b_path": "/data/sample_b.dat",
        "start_time": f"2026-05-30T10:00:{stamp[-2:]}+00:00",
        "records_matched": 100,
        "records_mismatched": 5,
        "keys_in_a_only": 2,
        "keys_in_b_only": 3,
        "dups_in_a": 1,
        "dups_in_b": 0,
        "elapsed_seconds": 1.5,
        "throughput_records_per_sec": 200.0,
        "config_audit_hash": "abc123",
        "engine_version": "0.0.1",
        "per_segment": [
            {
                "segment_name": "TU4R",
                "match_count": 100,
                "mismatch_count": 0,
                "total_in_a": 105,
                "total_in_b": 105,
            },
            {
                "segment_name": "AD01",
                "match_count": 95,
                "mismatch_count": 5,
                "total_in_a": 100,
                "total_in_b": 100,
            },
        ],
    }
    base.update(summary)
    (run_dir / "summary.json").write_text(json.dumps(base), encoding="utf-8")
    return run_dir


def test_init_db_creates_tables(index_db: Path) -> None:
    """The schema bootstraps the three expected tables."""
    assert index_db.exists()
    runs, total = db.list_runs()
    assert runs == []
    assert total == 0
    assert db.list_configs() == []


def test_record_run_indexes_metrics_and_segments(tmp_path: Path, index_db: Path) -> None:
    """A recorded run reflects summary.json headline metrics + per-segment rows."""
    out = tmp_path / "runs"
    run_dir = _write_run(out, "2026-05-30-10-00-01")
    db.record_run(run_dir, config_name="acct", output_dir=str(out), report_url="/api/x/report")

    runs, total = db.list_runs()
    assert total == 1
    row = runs[0]
    assert row["config_name"] == "acct"
    assert row["records_matched"] == 100
    assert row["records_mismatched"] == 5
    assert row["file_a"] == "sample_a.dat"

    detail = db.get_run(row["id"])
    assert detail is not None
    seg_names = {s["segment_name"]: s["mismatch_count"] for s in detail["segments"]}
    assert seg_names == {"TU4R": 0, "AD01": 5}


def test_record_run_is_idempotent_on_same_dir(tmp_path: Path, index_db: Path) -> None:
    """Re-recording the same run dir replaces (not duplicates) the row + segments."""
    out = tmp_path / "runs"
    run_dir = _write_run(out, "2026-05-30-10-00-01")
    db.record_run(run_dir, config_name="a", output_dir=str(out), report_url="/r1")
    db.record_run(run_dir, config_name="a", output_dir=str(out), report_url="/r2")

    runs, total = db.list_runs()
    assert total == 1
    assert runs[0]["report_url"] == "/r2"
    detail = db.get_run(runs[0]["id"])
    assert detail is not None
    assert len(detail["segments"]) == 2  # not doubled


def test_list_runs_pagination_and_order(tmp_path: Path, index_db: Path) -> None:
    """Runs come back newest-first and respect limit/offset."""
    out = tmp_path / "runs"
    for i in range(1, 6):
        rd = _write_run(out, f"2026-05-30-10-00-0{i}")
        db.record_run(rd, config_name=f"c{i}", output_dir=str(out), report_url=f"/r{i}")

    page1, total = db.list_runs(limit=2, offset=0)
    assert total == 5
    assert [r["config_name"] for r in page1] == ["c5", "c4"]
    page2, _ = db.list_runs(limit=2, offset=2)
    assert [r["config_name"] for r in page2] == ["c3", "c2"]


def test_list_runs_search(tmp_path: Path, index_db: Path) -> None:
    """Search matches the config name (and file names)."""
    out = tmp_path / "runs"
    db.record_run(
        _write_run(out, "2026-05-30-10-00-01", file_a_path="/data/payroll.dat"),
        config_name="payroll",
        output_dir=str(out),
        report_url="/r1",
    )
    db.record_run(
        _write_run(out, "2026-05-30-10-00-02"),
        config_name="accounts",
        output_dir=str(out),
        report_url="/r2",
    )
    hits, total = db.list_runs(search="payroll")
    assert total == 1
    assert hits[0]["config_name"] == "payroll"


def test_dashboard_stats_aggregates(tmp_path: Path, index_db: Path) -> None:
    """Totals sum across runs; mismatches_by_segment rolls up per segment."""
    out = tmp_path / "runs"
    for i in range(1, 3):
        rd = _write_run(out, f"2026-05-30-10-00-0{i}")
        db.record_run(rd, config_name=f"c{i}", output_dir=str(out), report_url=f"/r{i}")

    stats = db.dashboard_stats(recent_n=5)
    assert stats["totals"]["total_runs"] == 2
    assert stats["totals"]["total_matched"] == 200
    assert stats["totals"]["total_mismatched"] == 10
    assert stats["last_run"]["config_name"] == "c2"
    by_seg = {s["segment_name"]: s["mismatch_count"] for s in stats["mismatches_by_segment"]}
    assert by_seg == {"AD01": 10}  # TU4R has zero mismatches → excluded


def test_record_config_upsert(index_db: Path) -> None:
    """Saving the same config name twice updates rather than duplicates."""
    body = SaveConfigRequest(
        name="acct",
        file_a=FileSideConfig(file_path="/data/a.dat", key_field_name="account_nbr"),
        file_b=FileSideConfig(file_path="/data/b.dat", key_field_name="account_nbr"),
    )
    db.record_config("acct", body)
    body.file_a.file_path = "/data/a2.dat"
    db.record_config("acct", body)

    configs = db.list_configs()
    assert len(configs) == 1
    assert configs[0]["file_a_path"] == "/data/a2.dat"


def test_unsaved_config_is_not_indexed(index_db: Path) -> None:
    """The reserved ``_last_unsaved`` slot is never indexed."""
    body = SaveConfigRequest(
        file_a=FileSideConfig(file_path="/a", key_field_name="account_nbr"),
        file_b=FileSideConfig(file_path="/b", key_field_name="account_nbr"),
    )
    db.record_config("_last_unsaved", body)
    assert db.list_configs() == []


def test_persistence_across_reconnect(tmp_path: Path, index_db: Path) -> None:
    """Recorded rows survive a fresh connection (real on-disk persistence)."""
    out = tmp_path / "runs"
    rd = _write_run(out, "2026-05-30-10-00-01")
    db.record_run(rd, config_name="acct", output_dir=str(out), report_url="/r")
    # A brand-new connection (init_db reopens the same file) still sees the row.
    db.init_db()
    runs, total = db.list_runs()
    assert total == 1
    assert runs[0]["config_name"] == "acct"


def test_get_run_missing_returns_none(index_db: Path) -> None:
    """Unknown run id yields None (404 at the route layer)."""
    assert db.get_run(9999) is None
