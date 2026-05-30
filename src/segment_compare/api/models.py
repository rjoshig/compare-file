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
    """One segment row in the template; fields stay read-only on name/length.

    When this segment is the ``logical_name`` target of a segment alias
    (e.g. ``EMAD`` mirroring ``AD01`` after ``EM01``), ``alias_of`` and
    ``alias_after`` carry the wire/trigger segment names so the UI can
    render the "EMAD (AD01 segment) · after EM01" note. Both are ``None``
    for ordinary segments.
    """

    name: str
    size: int
    role: str | None = None  # "key" | "end" | None
    fields: list[TemplateField]
    alias_of: str | None = None  # wire segment this one mirrors (e.g. "AD01")
    alias_after: str | None = None  # trigger segment (e.g. "EM01")


class TemplateSegmentAlias(BaseModel):
    """One context-sensitive rename rule (ADR-034) as the UI sees it."""

    wire_name: str  # segment on the wire, e.g. "AD01"
    logical_name: str  # comparison bucket once triggered, e.g. "EMAD"
    after_segment: str  # trigger segment, e.g. "EM01"


class TemplateLayout(BaseModel):
    """The engine-side layout file rendered for the UI."""

    file_label: Literal["A", "B"]
    file_format: dict[str, object]
    strip_leading_bytes: dict[str, object] | None
    rdw: dict[str, object] | None
    sort: dict[str, object]
    segments: list[TemplateSegment]
    segment_aliases: list[TemplateSegmentAlias] = Field(default_factory=list)


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


class AliasSegmentDecl(BaseModel):
    """An operator-declared alias segment (ADR-034 / ADR-039).

    The operator places ``logical_name`` (e.g. ``EMAD``) in the segment
    list; it mirrors ``wire_name``'s layout and is applied to every
    ``wire_name`` instance appearing after ``after_segment`` in a record.
    """

    logical_name: str  # e.g. "EMAD"
    wire_name: str  # segment whose layout it reuses, e.g. "AD01"
    after_segment: str  # trigger segment, e.g. "EM01"


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
    # Operator-declared alias segments beyond any baked into the template.
    alias_segments: list[AliasSegmentDecl] = Field(default_factory=list)


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


class RunHistoryEntry(BaseModel):
    """One past run, read from a `report-*` dir's summary.json (ADR-041)."""

    run_dir_name: str
    run_dir_path: str
    report_url: str
    created_at: str
    file_a: str
    file_b: str
    records_matched: int
    records_mismatched: int
    keys_in_a_only: int
    keys_in_b_only: int
    dups_in_a: int
    dups_in_b: int


class RunHistoryListResponse(BaseModel):
    runs: list[RunHistoryEntry]


# ---------------------------------------------------------------------------
# SQLite-backed history + dashboard (ADR-043) — consumed by ui2
# ---------------------------------------------------------------------------


class DbRunSegment(BaseModel):
    """One per-segment rollup row for a run, from the SQLite index."""

    segment_name: str
    match_count: int
    mismatch_count: int
    total_in_a: int
    total_in_b: int


class DbRunEntry(BaseModel):
    """One run as stored in the SQLite index (full history, queryable)."""

    id: int
    run_dir_name: str
    run_dir_path: str
    output_dir: str | None = None
    report_url: str | None = None
    config_name: str | None = None
    file_a: str | None = None
    file_b: str | None = None
    created_at: str | None = None
    records_matched: int = 0
    records_mismatched: int = 0
    keys_in_a_only: int = 0
    keys_in_b_only: int = 0
    dups_in_a: int = 0
    dups_in_b: int = 0
    elapsed_seconds: float = 0.0
    throughput_rps: float = 0.0


class HistoryListResponse(BaseModel):
    """GET /api/history — a page of runs plus total/limit/offset for paging."""

    runs: list[DbRunEntry]
    total: int
    limit: int
    offset: int


class RunDetailResponse(DbRunEntry):
    """GET /api/history/{id} — a run plus its per-segment rollup."""

    config_audit_hash: str | None = None
    engine_version: str | None = None
    segments: list[DbRunSegment] = Field(default_factory=list)


class SegmentMismatch(BaseModel):
    """Total mismatches for one segment across all indexed runs."""

    segment_name: str
    mismatch_count: int


class DashboardTotals(BaseModel):
    """Headline counts aggregated across every indexed run."""

    total_runs: int = 0
    total_matched: int = 0
    total_mismatched: int = 0
    total_orphans: int = 0
    total_dups: int = 0


class DashboardResponse(BaseModel):
    """GET /api/dashboard — aggregates powering the ui2 dashboard."""

    last_run: DbRunEntry | None = None
    recent_runs: list[DbRunEntry] = Field(default_factory=list)
    totals: DashboardTotals = Field(default_factory=DashboardTotals)
    mismatches_by_segment: list[SegmentMismatch] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Error envelope
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
