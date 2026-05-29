"""User-config persistence layer for the Phase 3 API.

One named config = one directory. Each directory contains the three
files the engine's ``load_config(config_dir)`` already expects
(``layout_file_A.json``, ``layout_file_B.json``, ``runtime.json``)
plus a UI-only ``meta.json``. So the engine doesn't learn anything
new — it just gets pointed at the user's per-config directory.

Storage root (``SEGCMP_USER_CONFIGS_DIR`` env var, defaults to
``./user_configs/``):

    user_configs/
    ├── _last_unsaved/      ← auto-overwritten on every blank-name save
    │   ├── layout_file_A.json
    │   ├── layout_file_B.json
    │   ├── runtime.json
    │   └── meta.json
    └── <user-named>/
        ├── layout_file_A.json
        ├── ...
        └── meta.json

The on-disk JSON layouts are the engine's existing schema (ADR-033);
the UI's wire-shape (template overrides + appended fields) is
synthesized into the engine shape inside this module so the engine
never sees the UI-specific structure.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

from segment_compare.api.models import (
    FileSideConfig,
    SavedConfigSummary,
    TemplateBundle,
    TemplateField,
    TemplateLayout,
    TemplateSegment,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_USER_CONFIGS_DIR = REPO_ROOT / "user_configs"
TEMPLATE_CONFIG_DIR = REPO_ROOT / "config"
UNSAVED_NAME = "_last_unsaved"

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class StorageError(Exception):
    """Raised when persistence-layer operations fail with a user-fixable cause."""


def user_configs_dir() -> Path:
    """Resolve the on-disk root for user configs.

    Honors ``SEGCMP_USER_CONFIGS_DIR`` if set; otherwise falls back to
    ``./user_configs/`` next to the repo root.
    """
    raw = os.environ.get("SEGCMP_USER_CONFIGS_DIR")
    return Path(raw).expanduser().resolve() if raw else DEFAULT_USER_CONFIGS_DIR


def _safe_config_name(name: str | None) -> str:
    """Return a filesystem-safe directory name; ``_last_unsaved`` if blank."""
    if not name or not name.strip():
        return UNSAVED_NAME
    cleaned = name.strip()
    if not _SAFE_NAME_RE.match(cleaned):
        raise StorageError(
            "Config name may contain letters, digits, dot, underscore, "
            f"and hyphen only; got {cleaned!r}."
        )
    if cleaned.startswith(".") or cleaned == UNSAVED_NAME:
        raise StorageError(f"Config name {cleaned!r} is reserved.")
    return cleaned


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def load_template_bundle() -> TemplateBundle:
    """Read ``config/layout_file_A.json`` + ``layout_file_B.json`` and project them
    into the UI's :class:`TemplateLayout` shape.
    """
    return TemplateBundle(
        layout_a=_load_one_template("A", TEMPLATE_CONFIG_DIR / "layout_file_A.json"),
        layout_b=_load_one_template("B", TEMPLATE_CONFIG_DIR / "layout_file_B.json"),
    )


def _load_one_template(label: Literal["A", "B"], path: Path) -> TemplateLayout:
    if not path.exists():
        raise StorageError(f"Template layout missing on disk: {path}")
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))

    def _build_segment(seg_raw: dict[str, Any]) -> TemplateSegment:
        fields = [
            TemplateField(
                name=f["name"],
                length=int(f["length"]),
                exclude=bool(f.get("exclude", False)),
                key=bool(f.get("key", False)),
            )
            for f in seg_raw["fields"]
        ]
        return TemplateSegment(
            name=seg_raw["name"],
            size=int(seg_raw["size"]),
            role=seg_raw.get("role"),
            fields=fields,
        )

    return TemplateLayout(
        file_label=label,
        file_format=raw["file_format"],
        strip_leading_bytes=raw.get("strip_leading_bytes"),
        rdw=raw.get("rdw"),
        sort=raw["sort"],
        segments=[_build_segment(s) for s in raw["segments"]],
    )


# ---------------------------------------------------------------------------
# Save / load user configs
# ---------------------------------------------------------------------------


def save_config(name: str | None, file_a: FileSideConfig, file_b: FileSideConfig) -> str:
    """Persist a user config into ``user_configs/<name>/``.

    Returns the resolved on-disk directory name (``_last_unsaved`` when
    ``name`` is blank). Overwrites any prior contents in that directory.
    """
    safe_name = _safe_config_name(name)
    cfg_dir = user_configs_dir() / safe_name
    cfg_dir.mkdir(parents=True, exist_ok=True)

    templates = load_template_bundle()
    layout_a = _build_engine_layout(file_a, templates.layout_a)
    layout_b = _build_engine_layout(file_b, templates.layout_b)
    runtime = _default_runtime()

    (cfg_dir / "layout_file_A.json").write_text(json.dumps(layout_a, indent=2) + "\n")
    (cfg_dir / "layout_file_B.json").write_text(json.dumps(layout_b, indent=2) + "\n")
    (cfg_dir / "runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")

    meta = {
        "name": safe_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "file_a_path": file_a.file_path,
        "file_b_path": file_b.file_path,
    }
    (cfg_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return safe_name


def list_configs() -> list[SavedConfigSummary]:
    """Return summaries of every saved config (skipping ``_last_unsaved``)."""
    root = user_configs_dir()
    if not root.exists():
        return []
    out: list[SavedConfigSummary] = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name == UNSAVED_NAME:
            continue
        meta_path = sub / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        out.append(
            SavedConfigSummary(
                name=meta.get("name", sub.name),
                file_a_path=meta.get("file_a_path", ""),
                file_b_path=meta.get("file_b_path", ""),
                created_at=meta.get("created_at", ""),
            )
        )
    return out


def config_dir_for(name: str) -> Path:
    """Resolve and validate the on-disk directory for ``name``.

    Accepts the reserved ``_last_unsaved`` literal here because it
    points to a real directory created by ``save_config`` for blank-name
    saves; only *new* config names go through the reserved-name guard.
    """
    if name == UNSAVED_NAME:
        safe_name = UNSAVED_NAME
    else:
        safe_name = _safe_config_name(name)
    cfg_dir = user_configs_dir() / safe_name
    if not cfg_dir.exists():
        raise StorageError(f"Config {safe_name!r} does not exist.")
    return cfg_dir


# ---------------------------------------------------------------------------
# UI-shape → engine-shape projection
# ---------------------------------------------------------------------------


def _build_engine_layout(side: FileSideConfig, template: TemplateLayout) -> dict[str, Any]:
    """Build an engine-shape layout JSON from the UI's per-side config."""
    header_bytes = cast(int, template.file_format["segment_name_bytes"]) + cast(
        int, template.file_format["size_field_bytes"]
    )

    segments_out: list[dict[str, Any]] = []
    for seg in template.segments:
        # Start with template fields, applying any per-config exclude override.
        fields_out: list[dict[str, Any]] = []
        for fld in seg.fields:
            override_key = f"{seg.name}.{fld.name}"
            exclude = side.exclude_overrides.get(override_key, fld.exclude)
            entry: dict[str, Any] = {"name": fld.name, "length": fld.length}
            if exclude:
                entry["exclude"] = True
            if fld.key:
                entry["key"] = True
            fields_out.append(entry)

        # Append user-added fields for this segment, if any.
        for added in side.added_fields.get(seg.name, []):
            entry = {"name": added.name, "length": added.length}
            if added.exclude:
                entry["exclude"] = True
            if added.key:
                entry["key"] = True
            fields_out.append(entry)

        size = header_bytes + sum(int(f["length"]) for f in fields_out)
        seg_entry: dict[str, Any] = {"name": seg.name, "size": size, "fields": fields_out}
        if seg.role:
            seg_entry["role"] = seg.role
        segments_out.append(seg_entry)

    # Promote the chosen key field on TU4R: clear key=true everywhere, then
    # mark the requested one.
    _apply_key_choice(segments_out, side.key_field_name)

    return {
        "file_format": dict(template.file_format),
        "strip_leading_bytes": (
            {
                "size": side.strip_leading_bytes.size,
                "encoding": side.strip_leading_bytes.encoding,
            }
            if side.strip_leading_bytes.enabled and side.strip_leading_bytes.size
            else None
        ),
        "rdw": (
            {
                "rdw1_bytes": side.rdw.rdw1_bytes,
                "rdw2_bytes": side.rdw.rdw2_bytes,
                "encoding": side.rdw.encoding,
            }
            if side.rdw.enabled and side.rdw.rdw1_bytes and side.rdw.rdw2_bytes
            else None
        ),
        "sort": {
            "input_sorted": side.sort.input_sorted,
            "order": side.sort.order,
            "key_type": _normalize_key_type(side.sort.key_type),
        },
        "segments": segments_out,
    }


def _normalize_key_type(value: str) -> str:
    """Map the UI's 'number' / 'string' wording to engine values."""
    if value in ("number", "numeric"):
        return "numeric"
    return "alphanumeric"


def _apply_key_choice(segments_out: list[dict[str, Any]], key_field_name: str) -> None:
    """Mark exactly one TU4R field as ``key: true``; clear others."""
    for seg in segments_out:
        if seg.get("role") != "key":
            continue
        for fld in seg["fields"]:
            if fld.get("key"):
                fld.pop("key", None)
        marked = False
        for fld in seg["fields"]:
            if fld["name"] == key_field_name:
                fld["key"] = True
                marked = True
                break
        if not marked:
            raise StorageError(
                f"Key field {key_field_name!r} not found in the key segment "
                f"{seg['name']!r}; must match a template field or an added field."
            )
        return
    raise StorageError("No segment carries role=key in the template; cannot apply key choice.")


# ---------------------------------------------------------------------------
# Runtime defaults
# ---------------------------------------------------------------------------


def _default_runtime() -> dict[str, Any]:
    """Read the committed ``config/runtime.json`` as the per-config default."""
    path = TEMPLATE_CONFIG_DIR / "runtime.json"
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
