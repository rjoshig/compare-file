"""SQLite index for saved configs + full run history (ADR-043).

The filesystem stays the **source of truth**: user configs live under
``user_configs/<name>/`` and each run writes a ``report-*/`` directory with
its own ``summary.json`` (ADR-041). This module maintains a small SQLite
*index* that is dual-written whenever a config is saved or a run completes,
so the second UI (``ui2/``) can show full, searchable run history and
dashboard aggregates that go beyond the filesystem scan's newest-five.

Design rules:

- **Additive and non-fatal.** Every write is best-effort: failures are logged
  and swallowed so the core flow (and the existing Vue ``ui/``) never break if
  the index is unavailable or corrupt.
- **Stdlib only.** Uses :mod:`sqlite3` — no new dependency.
- **Index, not truth.** Rows can always be rebuilt from disk via
  :func:`backfill_from_disk`; nothing here is authoritative.

Database location: ``SEGCMP_DB_PATH`` env var, else ``./segment_compare.db``
next to the repo root (sibling of ``user_configs/``).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from segment_compare.api.models import SaveConfigRequest
from segment_compare.api.storage import REPO_ROOT, UNSAVED_NAME, user_configs_dir

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = REPO_ROOT / "segment_compare.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_dir_name      TEXT NOT NULL,
    run_dir_path      TEXT NOT NULL UNIQUE,
    output_dir        TEXT,
    report_url        TEXT,
    config_name       TEXT,
    file_a            TEXT,
    file_b            TEXT,
    file_a_path       TEXT,
    file_b_path       TEXT,
    created_at        TEXT,
    records_matched   INTEGER NOT NULL DEFAULT 0,
    records_mismatched INTEGER NOT NULL DEFAULT 0,
    keys_in_a_only    INTEGER NOT NULL DEFAULT 0,
    keys_in_b_only    INTEGER NOT NULL DEFAULT 0,
    dups_in_a         INTEGER NOT NULL DEFAULT 0,
    dups_in_b         INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds   REAL NOT NULL DEFAULT 0,
    throughput_rps    REAL NOT NULL DEFAULT 0,
    config_audit_hash TEXT,
    engine_version    TEXT,
    recorded_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_created ON runs (created_at DESC);

CREATE TABLE IF NOT EXISTS run_segments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    segment_name   TEXT NOT NULL,
    match_count    INTEGER NOT NULL DEFAULT 0,
    mismatch_count INTEGER NOT NULL DEFAULT 0,
    total_in_a     INTEGER NOT NULL DEFAULT 0,
    total_in_b     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_run_segments_run ON run_segments (run_id);

CREATE TABLE IF NOT EXISTS configs (
    name         TEXT PRIMARY KEY,
    file_a_path  TEXT,
    file_b_path  TEXT,
    created_at   TEXT,
    updated_at   TEXT,
    payload_json TEXT
);
"""


def db_path() -> Path:
    """Resolve the on-disk SQLite file path.

    Honors ``SEGCMP_DB_PATH`` if set; otherwise defaults to
    ``segment_compare.db`` next to the repo root.
    """
    raw = os.environ.get("SEGCMP_DB_PATH")
    return Path(raw).expanduser().resolve() if raw else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    """Open a connection with row access by name, WAL, and FK enforcement."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables/indexes if absent. Best-effort: logs and swallows errors."""
    try:
        with _connect() as conn:
            conn.executescript(_SCHEMA)
    except sqlite3.Error:
        logger.exception("init_db failed; SQLite index will be unavailable")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Writes (dual-write from routes; best-effort)
# ---------------------------------------------------------------------------


def record_run(
    run_dir_path: Path,
    *,
    config_name: str,
    output_dir: str,
    report_url: str,
) -> None:
    """Index one completed run by reading its on-disk ``summary.json``.

    Called after a successful ``POST /api/runs``. Reading from disk (rather
    than the in-memory ``Summary``) keeps this module decoupled from the
    engine and lets :func:`backfill_from_disk` reuse the same path. Any
    failure is logged and swallowed.

    Args:
        run_dir_path: The ``report-*`` directory the run wrote to.
        config_name: The saved-config name the run used.
        output_dir: The operator-chosen output directory (run dir's parent).
        report_url: The API URL serving the run's HTML report.
    """
    try:
        summary = _read_summary(run_dir_path)
        with _connect() as conn:
            _upsert_run(conn, run_dir_path, config_name, output_dir, report_url, summary)
    except sqlite3.Error:
        logger.exception("record_run failed for %s", run_dir_path)
    except (OSError, ValueError):
        logger.exception("record_run could not read summary for %s", run_dir_path)


def record_config(name: str, body: SaveConfigRequest) -> None:
    """Index one saved config (best-effort). Skips the ``_last_unsaved`` slot."""
    if name == UNSAVED_NAME:
        return
    try:
        now = _now()
        payload = body.model_dump_json()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO configs (name, file_a_path, file_b_path,
                                     created_at, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    file_a_path = excluded.file_a_path,
                    file_b_path = excluded.file_b_path,
                    updated_at  = excluded.updated_at,
                    payload_json = excluded.payload_json
                """,
                (
                    name,
                    body.file_a.file_path,
                    body.file_b.file_path,
                    now,
                    now,
                    payload,
                ),
            )
    except sqlite3.Error:
        logger.exception("record_config failed for %s", name)


def _read_summary(run_dir_path: Path) -> dict[str, Any]:
    """Load ``summary.json`` from a run dir, or return an empty dict."""
    path = run_dir_path / "summary.json"
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _upsert_run(
    conn: sqlite3.Connection,
    run_dir_path: Path,
    config_name: str,
    output_dir: str,
    report_url: str,
    summary: dict[str, Any],
) -> None:
    """Replace any existing row for this run dir, then insert it + its segments."""
    # Idempotent: drop the prior row (cascades to run_segments) before reinsert.
    conn.execute("DELETE FROM runs WHERE run_dir_path = ?", (str(run_dir_path),))
    cur = conn.execute(
        """
        INSERT INTO runs (
            run_dir_name, run_dir_path, output_dir, report_url, config_name,
            file_a, file_b, file_a_path, file_b_path, created_at,
            records_matched, records_mismatched, keys_in_a_only, keys_in_b_only,
            dups_in_a, dups_in_b, elapsed_seconds, throughput_rps,
            config_audit_hash, engine_version, recorded_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_dir_path.name,
            str(run_dir_path),
            output_dir,
            report_url,
            config_name,
            Path(str(summary.get("file_a_path", ""))).name,
            Path(str(summary.get("file_b_path", ""))).name,
            str(summary.get("file_a_path", "")),
            str(summary.get("file_b_path", "")),
            str(summary.get("start_time", "")),
            int(summary.get("records_matched", 0)),
            int(summary.get("records_mismatched", 0)),
            int(summary.get("keys_in_a_only", 0)),
            int(summary.get("keys_in_b_only", 0)),
            int(summary.get("dups_in_a", 0)),
            int(summary.get("dups_in_b", 0)),
            float(summary.get("elapsed_seconds", 0.0)),
            float(summary.get("throughput_records_per_sec", 0.0)),
            str(summary.get("config_audit_hash", "")),
            str(summary.get("engine_version", "")),
            _now(),
        ),
    )
    run_id = cur.lastrowid
    segments = summary.get("per_segment", [])
    if isinstance(segments, list):
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            conn.execute(
                """
                INSERT INTO run_segments (run_id, segment_name, match_count,
                                          mismatch_count, total_in_a, total_in_b)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    str(seg.get("segment_name", "")),
                    int(seg.get("match_count", 0)),
                    int(seg.get("mismatch_count", 0)),
                    int(seg.get("total_in_a", 0)),
                    int(seg.get("total_in_b", 0)),
                ),
            )


# ---------------------------------------------------------------------------
# Reads (serve ui2 dashboard + history)
# ---------------------------------------------------------------------------


def list_runs(
    limit: int = 25, offset: int = 0, search: str | None = None
) -> tuple[list[dict[str, Any]], int]:
    """Return a page of runs (newest first) plus the total matching count.

    Args:
        limit: Page size (clamped to 1..200).
        offset: Row offset for pagination (clamped to >= 0).
        search: Optional case-insensitive substring matched against the
            file names and config name.

    Returns:
        ``(rows, total)`` where ``rows`` is the requested page and ``total``
        is the count of all rows matching ``search``. Empty/zero on error.
    """
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    where, params = _search_clause(search)
    try:
        with _connect() as conn:
            total_row = conn.execute(f"SELECT COUNT(*) AS n FROM runs {where}", params).fetchone()
            total = int(total_row["n"]) if total_row else 0
            rows = conn.execute(
                f"""
                SELECT * FROM runs {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()
            return [dict(r) for r in rows], total
    except sqlite3.Error:
        logger.exception("list_runs failed")
        return [], 0


def _search_clause(search: str | None) -> tuple[str, tuple[Any, ...]]:
    """Build a ``WHERE`` fragment + params for a free-text run search."""
    if not search or not search.strip():
        return "", ()
    like = f"%{search.strip()}%"
    clause = "WHERE file_a LIKE ? OR file_b LIKE ? OR config_name LIKE ?"
    return clause, (like, like, like)


def get_run(run_id: int) -> dict[str, Any] | None:
    """Return one run plus its per-segment rows, or ``None`` if not found."""
    try:
        with _connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                return None
            segs = conn.execute(
                """
                SELECT segment_name, match_count, mismatch_count, total_in_a, total_in_b
                FROM run_segments WHERE run_id = ? ORDER BY id
                """,
                (run_id,),
            ).fetchall()
            run = dict(row)
            run["segments"] = [dict(s) for s in segs]
            return run
    except sqlite3.Error:
        logger.exception("get_run failed for %s", run_id)
        return None


def list_configs() -> list[dict[str, Any]]:
    """Return all indexed configs (newest first). Empty list on error."""
    try:
        with _connect() as conn:
            rows = conn.execute("SELECT * FROM configs ORDER BY updated_at DESC").fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error:
        logger.exception("list_configs failed")
        return []


def dashboard_stats(recent_n: int = 5) -> dict[str, Any]:
    """Aggregate metrics for the ``ui2`` dashboard.

    Returns a dict with the most recent run, the ``recent_n`` newest runs,
    headline totals across all runs, and total mismatches grouped by segment
    (highest first). All sections degrade to empty/zero on error.
    """
    recent_n = max(1, min(int(recent_n), 50))
    recent, _ = list_runs(limit=recent_n, offset=0)
    last_run = recent[0] if recent else None
    try:
        with _connect() as conn:
            totals_row = conn.execute("""
                SELECT COUNT(*) AS total_runs,
                       COALESCE(SUM(records_matched), 0) AS total_matched,
                       COALESCE(SUM(records_mismatched), 0) AS total_mismatched,
                       COALESCE(SUM(keys_in_a_only + keys_in_b_only), 0) AS total_orphans,
                       COALESCE(SUM(dups_in_a + dups_in_b), 0) AS total_dups
                FROM runs
                """).fetchone()
            seg_rows = conn.execute("""
                SELECT segment_name, COALESCE(SUM(mismatch_count), 0) AS mismatch_count
                FROM run_segments
                GROUP BY segment_name
                HAVING mismatch_count > 0
                ORDER BY mismatch_count DESC
                """).fetchall()
    except sqlite3.Error:
        logger.exception("dashboard_stats aggregation failed")
        totals_row = None
        seg_rows = []

    totals = (
        {
            "total_runs": int(totals_row["total_runs"]),
            "total_matched": int(totals_row["total_matched"]),
            "total_mismatched": int(totals_row["total_mismatched"]),
            "total_orphans": int(totals_row["total_orphans"]),
            "total_dups": int(totals_row["total_dups"]),
        }
        if totals_row
        else {
            "total_runs": 0,
            "total_matched": 0,
            "total_mismatched": 0,
            "total_orphans": 0,
            "total_dups": 0,
        }
    )
    return {
        "last_run": last_run,
        "recent_runs": recent,
        "totals": totals,
        "mismatches_by_segment": [dict(r) for r in seg_rows],
    }


# ---------------------------------------------------------------------------
# Backfill (optional, best-effort) — seed the index from what's on disk
# ---------------------------------------------------------------------------


def backfill_from_disk(output_dirs: list[Path] | None = None) -> None:
    """Seed the index from existing on-disk configs and runs.

    Walks ``user_configs/`` for saved configs and any provided ``output_dirs``
    for ``report-*`` run directories, indexing whatever isn't already present.
    Entirely best-effort — intended as a one-time convenience, not a guarantee.
    """
    try:
        _backfill_configs()
    except Exception:  # noqa: BLE001 — backfill must never raise
        logger.exception("config backfill failed")
    for out in output_dirs or []:
        try:
            _backfill_runs(out)
        except Exception:  # noqa: BLE001 — backfill must never raise
            logger.exception("run backfill failed for %s", out)


def _backfill_configs() -> None:
    root = user_configs_dir()
    if not root.exists():
        return
    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name == UNSAVED_NAME:
            continue
        meta_path = sub / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        now = _now()
        with _connect() as conn:
            conn.execute(
                """
                INSERT INTO configs (name, file_a_path, file_b_path,
                                     created_at, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO NOTHING
                """,
                (
                    meta.get("name", sub.name),
                    meta.get("file_a_path", ""),
                    meta.get("file_b_path", ""),
                    meta.get("created_at", now),
                    now,
                    "",
                ),
            )


def _backfill_runs(output_dir: Path) -> None:
    if not output_dir.exists() or not output_dir.is_dir():
        return
    for rd in sorted(output_dir.iterdir()):
        if not rd.is_dir() or not rd.name.startswith("report-"):
            continue
        with _connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM runs WHERE run_dir_path = ?", (str(rd),)
            ).fetchone()
            if existing:
                continue
            summary = _read_summary(rd)
            _upsert_run(conn, rd, "", str(output_dir), "", summary)
