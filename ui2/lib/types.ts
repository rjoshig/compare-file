// TypeScript mirrors of the FastAPI wire models (src/segment_compare/api/models.py).
// Kept deliberately close to the pydantic shapes so the editor and API client
// stay honest about what the backend expects/returns.

export interface TemplateField {
  name: string;
  length: number;
  exclude: boolean;
  key: boolean;
}

export interface TemplateSegment {
  name: string;
  size: number;
  role: string | null; // "key" | "end" | null
  fields: TemplateField[];
  alias_of: string | null;
  alias_after: string | null;
}

export interface TemplateSegmentAlias {
  wire_name: string;
  logical_name: string;
  after_segment: string;
}

export interface TemplateLayout {
  file_label: "A" | "B";
  file_format: Record<string, unknown>;
  strip_leading_bytes: Record<string, unknown> | null;
  rdw: Record<string, unknown> | null;
  sort: Record<string, unknown>;
  segments: TemplateSegment[];
  segment_aliases: TemplateSegmentAlias[];
}

export interface TemplateBundle {
  layout_a: TemplateLayout;
  layout_b: TemplateLayout;
}

// ---- Save config ----

export interface StripBlock {
  enabled: boolean;
  size: number | null;
  encoding: "binary" | "ascii";
}

export interface RdwBlock {
  enabled: boolean;
  rdw1_bytes: number | null;
  rdw2_bytes: number | null;
  encoding: "binary_le_uint" | "ascii_int";
}

export interface SortBlock {
  input_sorted: boolean;
  order: "ascending" | "descending";
  key_type: "alphanumeric" | "numeric" | "string" | "number";
}

export interface FileSideConfig {
  file_path: string;
  strip_leading_bytes: StripBlock;
  rdw: RdwBlock;
  sort: SortBlock;
  exclude_overrides: Record<string, boolean>;
  added_fields: Record<string, TemplateField[]>;
  key_field_name: string;
  alias_segments: never[];
}

export interface SaveConfigRequest {
  name: string | null;
  file_a: FileSideConfig;
  file_b: FileSideConfig;
}

export interface SavedConfigSummary {
  name: string;
  file_a_path: string;
  file_b_path: string;
  created_at: string;
}

// ---- Run ----

export interface RunResponse {
  run_dir_name: string;
  run_dir_path: string;
  report_url: string;
  records_matched: number;
  records_mismatched: number;
  keys_in_a_only: number;
  keys_in_b_only: number;
  dups_in_a: number;
  dups_in_b: number;
}

// ---- SQLite-backed history + dashboard ----

export interface DbRunSegment {
  segment_name: string;
  match_count: number;
  mismatch_count: number;
  total_in_a: number;
  total_in_b: number;
}

export interface DbRunEntry {
  id: number;
  run_dir_name: string;
  run_dir_path: string;
  output_dir: string | null;
  report_url: string | null;
  config_name: string | null;
  file_a: string | null;
  file_b: string | null;
  created_at: string | null;
  records_matched: number;
  records_mismatched: number;
  keys_in_a_only: number;
  keys_in_b_only: number;
  dups_in_a: number;
  dups_in_b: number;
  elapsed_seconds: number;
  throughput_rps: number;
}

export interface RunDetail extends DbRunEntry {
  config_audit_hash: string | null;
  engine_version: string | null;
  segments: DbRunSegment[];
}

export interface HistoryListResponse {
  runs: DbRunEntry[];
  total: number;
  limit: number;
  offset: number;
}

export interface SegmentMismatch {
  segment_name: string;
  mismatch_count: number;
}

export interface DashboardTotals {
  total_runs: number;
  total_matched: number;
  total_mismatched: number;
  total_orphans: number;
  total_dups: number;
}

export interface DashboardResponse {
  last_run: DbRunEntry | null;
  recent_runs: DbRunEntry[];
  totals: DashboardTotals;
  mismatches_by_segment: SegmentMismatch[];
}

// ---- File browse ----

export interface BrowseEntry {
  name: string;
  path: string;
  size?: number;
}

export interface BrowseResponse {
  path: string;
  parent: string | null;
  dirs: BrowseEntry[];
  files: BrowseEntry[];
}
