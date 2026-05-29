"""HTTP route handlers for the Phase 3 API."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Path as PathParam
from fastapi.responses import FileResponse

from segment_compare.api import storage
from segment_compare.api.models import (
    RunHistoryEntry,
    RunHistoryListResponse,
    RunRequest,
    RunResponse,
    SaveConfigRequest,
    SavedConfigListResponse,
    TemplateBundle,
)
from segment_compare.config import ConfigError, load_config
from segment_compare.pipeline import InputFileError, run as pipeline_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["compare"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/template-layouts", response_model=TemplateBundle)
def get_template_layouts() -> TemplateBundle:
    """Return the committed ``layout_file_*.json`` templates for the UI to render."""
    try:
        return storage.load_template_bundle()
    except storage.StorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/configs")
def save_config(body: SaveConfigRequest) -> dict[str, str]:
    """Persist a user config and return its on-disk directory name."""
    try:
        name = storage.save_config(body.name, body.file_a, body.file_b)
    except storage.StorageError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"name": name}


@router.get("/configs", response_model=SavedConfigListResponse)
def list_configs() -> SavedConfigListResponse:
    """List saved configs (excludes ``_last_unsaved``)."""
    return SavedConfigListResponse(configs=storage.list_configs())


@router.post("/runs", response_model=RunResponse)
def run_compare(body: RunRequest) -> RunResponse:
    """Invoke the engine for a saved config and return the run summary."""
    try:
        cfg_dir = storage.config_dir_for(body.config_name)
    except storage.StorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        cfg = load_config(cfg_dir)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=f"config invalid: {exc}") from exc

    meta = _read_meta(cfg_dir)
    file_a = Path(meta["file_a_path"]).expanduser().resolve()
    file_b = Path(meta["file_b_path"]).expanduser().resolve()
    output_dir = Path(body.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        summary = pipeline_run(file_a, file_b, cfg, output_dir)
    except InputFileError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface to the UI
        logger.exception("run failed for config %s", body.config_name)
        raise HTTPException(status_code=500, detail=f"run failed: {exc}") from exc

    run_dir_name = summary.filename_stamp
    run_dir_path = output_dir / run_dir_name
    token = _encode_run_token(run_dir_path)
    return RunResponse(
        run_dir_name=run_dir_name,
        run_dir_path=str(run_dir_path),
        report_url=f"/api/runs/{token}/report",
        records_matched=summary.records_matched,
        records_mismatched=summary.records_mismatched,
        keys_in_a_only=summary.keys_in_a_only,
        keys_in_b_only=summary.keys_in_b_only,
        dups_in_a=summary.dups_in_a,
        dups_in_b=summary.dups_in_b,
    )


@router.get("/runs", response_model=RunHistoryListResponse)
def list_runs(output_dir: str | None = None) -> RunHistoryListResponse:
    """Return the newest runs (max 5) found in ``output_dir``.

    Directory-driven: scans for ``report-*`` subdirs and reads each
    ``summary.json``. No server-side state. Empty list when ``output_dir`` is
    missing or not a directory.
    """
    if not output_dir:
        return RunHistoryListResponse(runs=[])
    base = Path(output_dir).expanduser().resolve()
    runs: list[RunHistoryEntry] = []
    for h in storage.scan_run_history(base):
        token = _encode_run_token(Path(h["run_dir_path"]))
        runs.append(RunHistoryEntry(report_url=f"/api/runs/{token}/report", **h))
    return RunHistoryListResponse(runs=runs)


@router.get("/browse")
def browse(path: str | None = None) -> dict[str, object]:
    """List entries in ``path`` for the UI's file-browse dialog.

    Returns a parent-aware listing with directories first, then files
    (only ``.dat`` / ``.csv`` / ``.txt`` extensions). When ``path`` is
    missing or invalid, defaults to the operator's home directory.
    """
    if path:
        target = Path(path).expanduser().resolve()
        if not target.exists() or not target.is_dir():
            target = Path.home()
    else:
        target = Path.home()

    dirs: list[dict[str, str]] = []
    files: list[dict[str, object]] = []
    for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                dirs.append({"name": entry.name, "path": str(entry)})
            elif entry.is_file() and entry.suffix.lower() in (".dat", ".csv", ".txt"):
                files.append(
                    {
                        "name": entry.name,
                        "path": str(entry),
                        "size": entry.stat().st_size,
                    }
                )
        except OSError:
            continue

    return {
        "path": str(target),
        "parent": str(target.parent) if target.parent != target else None,
        "dirs": dirs,
        "files": files,
    }


@router.get("/runs/{token}/report")
def get_report(token: str) -> FileResponse:
    """Serve ``compare_reports.html`` for the run dir encoded in ``token``.

    The report HTML links to sibling files (``matches.dat`` etc.) as
    bare relative paths; serving the report under
    ``/api/runs/{token}/report`` lets the browser resolve those links to
    ``/api/runs/{token}/<name>``, which :func:`get_run_file` serves.
    """
    run_dir = _decode_run_token(token)
    path = run_dir / "compare_reports.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"report not found at {path}")
    return FileResponse(path, media_type="text/html")


@router.get("/runs/{token}/{name}")
def get_run_file(token: str, name: str) -> FileResponse:
    """Serve a sibling file (``matches.dat``, etc.) from a run dir.

    Path-traversal-safe: ``name`` may not contain ``/`` or ``..``, and
    the resolved target must remain strictly inside the run dir.
    """
    if "/" in name or "\\" in name or name in ("..", ".") or name.startswith("."):
        raise HTTPException(status_code=400, detail="invalid filename")
    run_dir = _decode_run_token(token)
    target = (run_dir / name).resolve()
    try:
        target.relative_to(run_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path escapes run dir") from exc
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {name}")
    # text/plain so browsers display .dat / .csv contents inline.
    media_type = "text/plain" if target.suffix.lower() in (".dat", ".csv", ".txt") else None
    return FileResponse(target, media_type=media_type)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_meta(cfg_dir: Path) -> dict[str, str]:
    import json

    path = cfg_dir / "meta.json"
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"meta.json missing in {cfg_dir}")
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _encode_run_token(run_dir: Path) -> str:
    """URL-safe base64 encoding of a run-dir absolute path."""
    raw = str(run_dir.resolve()).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_run_token(token: str) -> Path:
    """Inverse of :func:`_encode_run_token`. Validates the path exists and is a dir."""
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode((token + padding).encode("ascii"))
        run_dir = Path(raw.decode("utf-8")).resolve()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid run token") from exc
    if not run_dir.exists() or not run_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"run dir not found: {run_dir}")
    return run_dir


_ = PathParam  # silence unused-import warning until we add path-param routes
