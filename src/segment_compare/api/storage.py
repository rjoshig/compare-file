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
    TemplateSegmentAlias,
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

    aliases = [
        TemplateSegmentAlias(
            wire_name=a["wire_name"],
            logical_name=a["logical_name"],
            after_segment=a["after_segment"],
        )
        for a in raw.get("segment_aliases", [])
    ]
    # Map each logical-target segment to its (wire, trigger) so the UI can
    # render the "EMAD (AD01 segment) · after EM01" note.
    alias_by_logical = {a.logical_name: a for a in aliases}

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
        alias = alias_by_logical.get(seg_raw["name"])
        return TemplateSegment(
            name=seg_raw["name"],
            size=int(seg_raw["size"]),
            role=seg_raw.get("role"),
            fields=fields,
            alias_of=alias.wire_name if alias else None,
            alias_after=alias.after_segment if alias else None,
        )

    return TemplateLayout(
        file_label=label,
        file_format=raw["file_format"],
        strip_leading_bytes=raw.get("strip_leading_bytes"),
        rdw=raw.get("rdw"),
        sort=raw["sort"],
        segments=[_build_segment(s) for s in raw["segments"]],
        segment_aliases=aliases,
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
# Run history — derived from the output directory, nothing stored (ADR-041)
# ---------------------------------------------------------------------------

# Each run lands in a `report-YYYY-MM-DD-HH-MM-SS/` subdir (ADR-037). Run
# history is just the newest N of those in a chosen output directory, read back
# from each run's summary.json. No manifest, no extra state — what you see is
# what's on disk.
RUN_DIR_PREFIX = "report-"
DEFAULT_RUN_HISTORY = 5


def scan_run_history(output_dir: Path, limit: int = DEFAULT_RUN_HISTORY) -> list[dict[str, Any]]:
    """Return the newest ``limit`` runs found in ``output_dir`` (newest first).

    Looks for ``report-*`` subdirectories (their timestamp names sort
    chronologically) and reads each one's ``summary.json`` for the headline
    metrics. Missing/unreadable summaries yield zeroed metrics rather than
    dropping the run. Returns an empty list if ``output_dir`` isn't a directory.
    """
    if not output_dir.exists() or not output_dir.is_dir():
        return []
    run_dirs = sorted(
        (p for p in output_dir.iterdir() if p.is_dir() and p.name.startswith(RUN_DIR_PREFIX)),
        key=lambda p: p.name,
        reverse=True,
    )[:limit]

    out: list[dict[str, Any]] = []
    for rd in run_dirs:
        data: dict[str, Any] = {}
        summary_path = rd / "summary.json"
        if summary_path.exists():
            try:
                loaded = json.loads(summary_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            except (ValueError, OSError):
                data = {}
        out.append(
            {
                "run_dir_name": rd.name,
                "run_dir_path": str(rd),
                "created_at": str(data.get("start_time", "")),
                "file_a": Path(str(data.get("file_a_path", ""))).name,
                "file_b": Path(str(data.get("file_b_path", ""))).name,
                "records_matched": int(data.get("records_matched", 0)),
                "records_mismatched": int(data.get("records_mismatched", 0)),
                "keys_in_a_only": int(data.get("keys_in_a_only", 0)),
                "keys_in_b_only": int(data.get("keys_in_b_only", 0)),
                "dups_in_a": int(data.get("dups_in_a", 0)),
                "dups_in_b": int(data.get("dups_in_b", 0)),
            }
        )
    return out


# ---------------------------------------------------------------------------
# UI-shape → engine-shape projection
# ---------------------------------------------------------------------------


def _resolve_fields(
    seg: TemplateSegment, side: FileSideConfig, *, key_only: bool = False
) -> list[dict[str, Any]]:
    """Resolve one segment's fields: template fields + overrides + added fields.

    ``key_only`` drops the per-field ``key`` flag — used when cloning a wire
    segment's layout into a logical alias segment, which must never carry the
    record key.
    """
    fields_out: list[dict[str, Any]] = []
    for fld in seg.fields:
        override_key = f"{seg.name}.{fld.name}"
        exclude = side.exclude_overrides.get(override_key, fld.exclude)
        entry: dict[str, Any] = {"name": fld.name, "length": fld.length}
        if exclude:
            entry["exclude"] = True
        if fld.key and not key_only:
            entry["key"] = True
        fields_out.append(entry)

    for added in side.added_fields.get(seg.name, []):
        entry = {"name": added.name, "length": added.length}
        if added.exclude:
            entry["exclude"] = True
        if added.key and not key_only:
            entry["key"] = True
        fields_out.append(entry)
    return fields_out


def _build_engine_layout(side: FileSideConfig, template: TemplateLayout) -> dict[str, Any]:
    """Build an engine-shape layout JSON from the UI's per-side config."""
    header_bytes = cast(int, template.file_format["segment_name_bytes"]) + cast(
        int, template.file_format["size_field_bytes"]
    )
    tpl_by_name = {seg.name: seg for seg in template.segments}

    # Effective alias rules: template-baked ∪ operator-declared, one per wire
    # (the engine validator rejects more than one rule per wire_name).
    aliases: list[dict[str, str]] = [
        {"wire_name": a.wire_name, "logical_name": a.logical_name, "after_segment": a.after_segment}
        for a in template.segment_aliases
    ]
    seen_wire = {a["wire_name"] for a in aliases}
    for decl in side.alias_segments:
        if decl.wire_name in seen_wire:
            continue  # one rule per wire_name; template rule wins
        aliases.append(
            {
                "wire_name": decl.wire_name,
                "logical_name": decl.logical_name,
                "after_segment": decl.after_segment,
            }
        )
        seen_wire.add(decl.wire_name)
    # logical segment name -> wire segment it mirrors.
    logical_to_wire = {a["logical_name"]: a["wire_name"] for a in aliases}

    def _segment_entry(name: str, role: str | None, fields: list[dict[str, Any]]) -> dict[str, Any]:
        size = header_bytes + sum(int(f["length"]) for f in fields)
        entry: dict[str, Any] = {"name": name, "size": size, "fields": fields}
        if role:
            entry["role"] = role
        return entry

    segments_out: list[dict[str, Any]] = []
    for seg in template.segments:
        if seg.name in logical_to_wire:
            # Alias-target segment: mirror the wire segment's resolved layout so
            # the two sizes always agree (the rename reuses the same bytes).
            wire = tpl_by_name[logical_to_wire[seg.name]]
            fields_out = _resolve_fields(wire, side, key_only=True)
        else:
            fields_out = _resolve_fields(seg, side)
        segments_out.append(_segment_entry(seg.name, seg.role, fields_out))

    # Operator-declared alias segments whose logical name isn't already a
    # template segment: synthesize one by cloning the wire segment's layout.
    for decl in side.alias_segments:
        if decl.logical_name in tpl_by_name:
            continue
        wire_seg = tpl_by_name.get(decl.wire_name)
        if wire_seg is None:
            raise StorageError(
                f"Alias segment {decl.logical_name!r} mirrors unknown wire segment "
                f"{decl.wire_name!r}; must match a template segment."
            )
        fields_out = _resolve_fields(wire_seg, side, key_only=True)
        segments_out.append(_segment_entry(decl.logical_name, None, fields_out))

    # Promote the chosen key field on TU4R: clear key=true everywhere, then
    # mark the requested one.
    _apply_key_choice(segments_out, side.key_field_name)

    layout: dict[str, Any] = {
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
    if aliases:
        layout["segment_aliases"] = aliases
    return layout


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
