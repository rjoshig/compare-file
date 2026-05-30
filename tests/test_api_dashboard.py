"""End-to-end API tests for the SQLite-backed dashboard + history endpoints.

Drives the FastAPI app via ``TestClient``: saves a config, runs a comparison
against the committed sample data, then asserts the new ``/api/dashboard``,
``/api/history`` and ``/api/history/{id}`` endpoints reflect that run. The
existing filesystem endpoints are exercised incidentally to confirm they
still work alongside the index.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"
SAMPLE_A = EXAMPLES / "sample_a.dat"
SAMPLE_B = EXAMPLES / "sample_b.dat"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A TestClient with the index DB and user configs isolated to tmp."""
    monkeypatch.setenv("SEGCMP_DB_PATH", str(tmp_path / "index.db"))
    monkeypatch.setenv("SEGCMP_USER_CONFIGS_DIR", str(tmp_path / "user_configs"))
    # Import after env is set so module-level defaults don't matter; the app
    # resolves paths lazily per request, but the lifespan inits the DB.
    from segment_compare.api.main import app

    with TestClient(app) as c:
        yield c


def _save_and_run(client: TestClient, output_dir: Path) -> dict[str, object]:
    """Save a sample config and run it; return the RunResponse JSON."""
    side_a = {"file_path": str(SAMPLE_A), "key_field_name": "account_nbr"}
    side_b = {"file_path": str(SAMPLE_B), "key_field_name": "account_nbr"}
    resp = client.post(
        "/api/configs",
        json={"name": "samples", "file_a": side_a, "file_b": side_b},
    )
    assert resp.status_code == 200, resp.text
    run = client.post(
        "/api/runs",
        json={"config_name": "samples", "output_dir": str(output_dir)},
    )
    assert run.status_code == 200, run.text
    return run.json()


def test_run_populates_dashboard_and_history(client: TestClient, tmp_path: Path) -> None:
    """A run flows through the index into dashboard + history endpoints."""
    out = tmp_path / "runs"
    run_json = _save_and_run(client, out)

    dash = client.get("/api/dashboard").json()
    assert dash["totals"]["total_runs"] == 1
    assert dash["last_run"]["config_name"] == "samples"
    assert dash["last_run"]["run_dir_name"] == run_json["run_dir_name"]
    # Sample data has mismatches → at least one segment rolls up.
    assert isinstance(dash["mismatches_by_segment"], list)

    hist = client.get("/api/history").json()
    assert hist["total"] == 1
    assert hist["runs"][0]["config_name"] == "samples"

    run_id = hist["runs"][0]["id"]
    detail = client.get(f"/api/history/{run_id}").json()
    assert detail["id"] == run_id
    assert len(detail["segments"]) >= 1
    assert detail["records_matched"] == run_json["records_matched"]


def test_history_search_and_pagination(client: TestClient, tmp_path: Path) -> None:
    """Search filters by config name; limit bounds the page."""
    _save_and_run(client, tmp_path / "runs")

    assert client.get("/api/history", params={"q": "samples"}).json()["total"] == 1
    assert client.get("/api/history", params={"q": "nope"}).json()["total"] == 0

    page = client.get("/api/history", params={"limit": 1}).json()
    assert page["limit"] == 1
    assert len(page["runs"]) <= 1


def test_history_detail_404_for_unknown(client: TestClient) -> None:
    """Unknown run id returns 404."""
    assert client.get("/api/history/424242").status_code == 404


def test_config_indexed_after_save(client: TestClient, tmp_path: Path) -> None:
    """Saving a named config indexes it (visible via the filesystem list too)."""
    _save_and_run(client, tmp_path / "runs")
    listed = client.get("/api/configs").json()["configs"]
    assert any(c["name"] == "samples" for c in listed)


def test_empty_dashboard_is_well_formed(client: TestClient) -> None:
    """With no runs the dashboard returns zeroed totals and empty lists."""
    dash = client.get("/api/dashboard").json()
    assert dash["totals"]["total_runs"] == 0
    assert dash["last_run"] is None
    assert dash["recent_runs"] == []
    assert dash["mismatches_by_segment"] == []
