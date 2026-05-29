"""Pydantic request / response schemas for the Phase 3 API.

These models describe the *wire* shape between the Vue UI and FastAPI.
The on-disk shape (``user_configs/<name>/layout_file_A.json`` etc.)
is the engine's existing layout schema (ADR-033) — converted to/from
these wire models in :mod:`segment_compare.api.storage`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Template payload (GET /api/template-layouts)
# ---------------------------------------------------------------------------


class TemplateField(BaseModel):
    """One field as the UI sees it: name + length + per-config exclude default."""

    name: str
    length: int
    exclude: bool = False
    key: bool = False


class TemplateSegment(BaseModel):
    """One segment row in the template; fields stay read-only on name/length."""

    name: str
    size: int
    role: str | None = None  # "key" | "end" | None
    fields: list[TemplateField]


class TemplateLayout(BaseModel):
    """The engine-side layout file rendered for the UI."""

    file_label: Literal["A", "B"]
    file_format: dict[str, object]
    strip_leading_bytes: dict[str, object] | None
    rdw: dict[str, object] | None
    sort: dict[str, object]
    segments: list[TemplateSegment]


class TemplateBundle(BaseModel):
    """What GET /api/template-layouts returns."""

    layout_a: TemplateLayout
    layout_b: TemplateLayout


# ---------------------------------------------------------------------------
# User-config save (POST /api/configs)
# ---------------------------------------------------------------------------


class StripBlock(BaseModel):
    enabled: bool = False
    size: int | None = None
    encoding: Literal["binary", "ascii"] = "binary"


class RdwBlock(BaseModel):
    enabled: bool = False
    rdw1_bytes: int | None = None
    rdw2_bytes: int | None = None
    encoding: Literal["binary_le_uint", "ascii_int"] = "binary_le_uint"


class SortBlock(BaseModel):
    input_sorted: bool = True
    order: Literal["ascending", "descending"] = "ascending"
    key_type: Literal["alphanumeric", "numeric", "string", "number"] = "alphanumeric"


class FileSideConfig(BaseModel):
    """The UI state for one side (File A or File B)."""

    file_path: str
    strip_leading_bytes: StripBlock = Field(default_factory=StripBlock)
    rdw: RdwBlock = Field(default_factory=RdwBlock)
    sort: SortBlock = Field(default_factory=SortBlock)
    # Exclude overrides on template fields, keyed by "<segment>.<field>".
    # Any key absent means "use the template's exclude default".
    exclude_overrides: dict[str, bool] = Field(default_factory=dict)
    # Fields the user added beyond the template, keyed by segment name.
    added_fields: dict[str, list[TemplateField]] = Field(default_factory=dict)
    # The TU4R field marked as the compare key (must exist as either a
    # template field or a user-added field in TU4R).
    key_field_name: str


class SaveConfigRequest(BaseModel):
    """POST /api/configs body."""

    name: str | None = None  # None / blank → _last_unsaved
    file_a: FileSideConfig
    file_b: FileSideConfig


class SavedConfigSummary(BaseModel):
    """One row in GET /api/configs response list."""

    name: str
    file_a_path: str
    file_b_path: str
    created_at: str


class SavedConfigListResponse(BaseModel):
    configs: list[SavedConfigSummary]


# ---------------------------------------------------------------------------
# Run (POST /api/runs)
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    config_name: str
    output_dir: str


class RunResponse(BaseModel):
    run_dir_name: str  # "report-2026-05-29-12-34-56"
    run_dir_path: str  # absolute path
    report_url: str  # "/api/runs/<run_dir_name>/report"
    records_matched: int
    records_mismatched: int
    keys_in_a_only: int
    keys_in_b_only: int
    dups_in_a: int
    dups_in_b: int


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
