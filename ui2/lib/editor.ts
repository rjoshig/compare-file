// Editor state for one file side + projection into the backend's FileSideConfig.
// Key rule (per product requirement): every field is COMPARED by default
// (exclude defaults to false). We send explicit exclude_overrides=false for
// every non-key field so any template `exclude:true` default can't silently
// drop a field. The currently selected key field is always included and never
// gets an exclude control or override.

import type { FileSideConfig, TemplateField, TemplateLayout } from "./types";

export interface SideEditorState {
  filePath: string;
  /** Keyed by "<segment>.<field>" -> true when the user opted the field out. */
  excludes: Record<string, boolean>;
  /** Extra fields appended to the key segment, keyed by segment name. */
  addedFields: Record<string, TemplateField[]>;
  keyFieldName: string;
  inputSorted: boolean;
}

export function keySegmentOf(layout: TemplateLayout) {
  return layout.segments.find((s) => s.role === "key") ?? null;
}

export function defaultSideState(layout: TemplateLayout): SideEditorState {
  const keySeg = keySegmentOf(layout);
  const keyField = keySeg?.fields.find((f) => f.key)?.name ?? keySeg?.fields[0]?.name ?? "";
  return { filePath: "", excludes: {}, addedFields: {}, keyFieldName: keyField, inputSorted: true };
}

export function isCurrentKey(
  state: SideEditorState,
  layout: TemplateLayout,
  segName: string,
  fieldName: string,
): boolean {
  const keySeg = keySegmentOf(layout);
  return !!keySeg && keySeg.name === segName && state.keyFieldName === fieldName;
}

export function sideConfigToState(cfg: FileSideConfig): SideEditorState {
  // Inverse of buildSideConfig: repopulate the editor from a saved config so
  // "Open" in Config / "Re-run" in History lands on the exact field choices.
  return {
    filePath: cfg.file_path,
    excludes: { ...cfg.exclude_overrides },
    addedFields: { ...cfg.added_fields },
    keyFieldName: cfg.key_field_name,
    inputSorted: cfg.sort?.input_sorted ?? true,
  };
}

export function buildSideConfig(layout: TemplateLayout, state: SideEditorState): FileSideConfig {
  const overrides: Record<string, boolean> = {};
  for (const seg of layout.segments) {
    for (const f of seg.fields) {
      if (isCurrentKey(state, layout, seg.name, f.name)) continue; // key never excluded
      const k = `${seg.name}.${f.name}`;
      overrides[k] = state.excludes[k] ?? false;
    }
  }
  return {
    file_path: state.filePath,
    strip_leading_bytes: { enabled: false, size: null, encoding: "binary" },
    rdw: { enabled: false, rdw1_bytes: null, rdw2_bytes: null, encoding: "binary_le_uint" },
    sort: { input_sorted: state.inputSorted, order: "ascending", key_type: "alphanumeric" },
    exclude_overrides: overrides,
    added_fields: state.addedFields,
    key_field_name: state.keyFieldName,
    alias_segments: [],
  };
}
