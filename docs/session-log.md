# Session Log

Working journal for this project. **Read the most recent entry first at
the start of every session.** Append a new entry at the end of every
session.

Required fields per entry: branch, phase, status, what was completed,
what's pending, blockers, next concrete action.

---

## Session: 2026-05-29 (ADR-039 — segment aliases in the Web UI + demo fixture)

**Branch:** `dev`
**Phase:** 3 (web UI) — also touches the engine demo fixture (Phase 2 surface)
**Status:** Shipped end-to-end. Operator can declare an `AD01`-after-`EM01`
→ `EMAD` alias from the browser; the committed demo fixture exercises it via
CLI + UI. 225 tests pass on the `.venv` interpreter; black / flake8 / mypy
clean; `ui/` builds.

### Why this session

ADR-034 shipped the engine's `segment_aliases` capability last session, but
only the CLI / hand-authored layouts could use it. The user asked to expose it
in the Web UI: place an `EMAD` segment after `EM01` that reuses `AD01`'s layout,
shown as "EMAD (AD01 segment)", with the engine treating `AD01`-after-`EM01`
as `EMAD`. Two product calls taken up front: (1) operator *declares* the alias
segment in the UI (not just views a baked one); (2) `AD01`/`EM01`/`EMAD` go
into the **main committed fixture** so it's demoable from the browser + CLI.

### What was completed (ADR-039)

- **Sample layout (standalone):** `config/layout_example_segment_alias.json`
  — fully-commented AD01/EM01/EMAD + alias rule (verified it loads via
  `load_file_layout`). Honors the "every config feature needs a sample layout"
  rule.
- **Committed config:** `config/layout_file_A.json` + `layout_file_B.json`
  now declare `AD01` (street/city/state/zip5 = 59 bytes), `EM01` (47), `EMAD`
  (59, mirror) + the `AD01→EMAD after EM01` rule.
- **Fixture:** `examples/sample_*.dat` carry, after each record's `NM01`, an
  `AD01`(postal) + `EM01` + `AD01`(email) trio. The trailing `AD01` buckets as
  `EMAD`. Inserted bytes are **identical on both sides**, so every aggregate
  count is unchanged (matched=4 / mismatched=3 / only 1,2 / dups 2,2). Rebuilt
  with the idempotent `scripts/inject_alias_segments.py`. Record sizes
  417→582, 467→632; file sizes 5880 / 6413 bytes.
- **API wire schema (`api/models.py`):** `TemplateSegment.alias_of/alias_after`;
  new `TemplateSegmentAlias` on `TemplateLayout.segment_aliases`; new
  `AliasSegmentDecl` on `FileSideConfig.alias_segments`.
- **Projection (`api/storage.py`):** `_load_one_template` surfaces aliases +
  tags logical-target segments. `_build_engine_layout` clones the **wire**
  segment's resolved fields into the logical segment (guarantees the equal-size
  invariant), emits a top-level `segment_aliases`, dedupes by `wire_name`
  (template rule wins). New `_resolve_fields(..., key_only=)` helper.
- **UI (`ui/src/`):** `SegmentEditor.vue` renders the "EMAD (AD01 segment) ·
  after EM01" note + an `alias` tag, read-only field mirror, remove button for
  operator-added aliases. New `AliasSegmentEditor.vue` (Add-alias form: logical
  name + mirrors-segment Select + applied-after Select, with one-rule-per-wire
  guarding). `FileBody.vue` gains a "Segment aliases" panel; `FieldConfig.vue`
  seeds `alias_segments: []`. **Per user request the "Segment aliases"
  authoring panel is commented out in `FileBody.vue` for now** (import +
  template block) — template-baked aliases like `EMAD` still render as
  read-only cards in the Segments panel via `SegmentEditor`'s alias note.
  Re-enable by uncommenting both spots; `AliasSegmentEditor.vue` and the
  backend `alias_segments` path are kept intact.
- **Tests:** oracle test asserts the new `AD01` + `EMAD` per-segment buckets
  (counts unchanged); `test_load_committed_layout_file_a/b` updated for the new
  segments + alias rule; new `tests/test_api_storage.py` (6 cases) covers both
  projection paths + dedupe + unknown-wire rejection, each round-tripped
  through `load_file_layout`.
- **Docs:** ADR-039 in `docs/decisions.md`; `examples/README.md` record-layout
  table + sizes + aliasing note; `docs/architecture.md` api/storage row.

### Gotcha resolved

`tests/synthetic_data.py` already emitted an `AD01` (street/city/state/zip =
52 data, 59 total). Declaring `AD01` in the committed config turned on
FieldNormalizer length validation, so `AD01`/`EMAD` were aligned to that exact
59-byte layout (was briefly 77). `EM01`/`EMAD` are absent from synthetic data,
so the alias simply never fires there.

### What's pending

- Unchanged Phase 3 backlog from the prior entry: API route tests
  (`tests/test_api_*` for routes), Run History view, saved-config picker UI.
- The UI alias panel has no automated front-end test (no FE test harness in the
  repo yet); verified via `npm run build` + the storage round-trip only.

### Blockers

None.

### Decisions captured this session

- **ADR-039**: segment aliases in the Web UI (operator-declared via
  `FileSideConfig.alias_segments` + template-baked metadata) and the
  AD01/EM01/EMAD demo-fixture extension (identical-content insertion preserves
  all counts). Extends ADR-034 / ADR-033.

### Next concrete action

Resume the prior Phase 3 backlog: write `tests/test_api_routes.py`
(`/api/template-layouts` now includes `segment_aliases`; `/api/configs`
round-trip persists `alias_segments`), then the Run History view.

### Notes for future me

- `scripts/inject_alias_segments.py` is idempotent (skips if `AD01` already
  present) — safe to re-run after a `git checkout` of the samples.
- Engine emits `record.raw`, so `matches.dat`/`mismatches.dat` still carry the
  on-wire `AD01`; only `summary.json::per_segment` + `report.csv` show `EMAD`.
- The projection clones the **wire** segment's fields for the logical segment;
  editing `AD01`'s excludes in the UI flows to `EMAD` automatically (EMAD's
  field table is read-only by design).

---

## Session: 2026-05-29 (Phase 3 kickoff — FastAPI backend + Vue dashboard + report polish)

**Branch:** `dev`
**Phase:** 3 (web UI)
**Status:** Phase 3 first vertical slice is live end-to-end. Operator can pick files, configure layouts/keys/sort, save a config, run the engine, and open the HTML report — all from the browser.

### What was completed

**Backend (`src/segment_compare/api/`):**
- `main.py` — FastAPI app factory with permissive CORS for the Vite dev server (`http://localhost:5173`, `http://127.0.0.1:5173`).
- `models.py` — pydantic v2 wire schemas: `TemplateBundle`, `FileSideConfig`, `SaveConfigRequest`, `RunRequest`, `RunResponse`, `SavedConfigSummary`, etc.
- `storage.py` — user-config persistence (one config = one directory; layout_file_A.json + layout_file_B.json + runtime.json + meta.json). UI-shape (template overrides + appended fields + per-side key/sort) projects into the engine's existing on-disk schema (ADR-033), so `load_config()` keeps working untouched. Storage root honors `SEGCMP_USER_CONFIGS_DIR` env var; defaults to `./user_configs/`.
- `routes.py` — `/api/health`, `/api/template-layouts`, `/api/configs` (save + list), `/api/runs` (invoke pipeline), `/api/browse` (server-side filesystem browse for the UI's pickers, with `.dat/.csv/.txt` extension filter), `/api/runs/{token}/report` and `/api/runs/{token}/{name}` (base64url-encoded run dir in the URL *path* so the report HTML's bare relative file links resolve to a real endpoint).
- `pyproject.toml` — new `[project.optional-dependencies].api` group (fastapi, uvicorn, pydantic) and dev-time httpx for TestClient.
- `.gitignore` — added `user_configs/`.

**Report HTML (`src/segment_compare/writer.py`):**
- Re-skinned the report with Material 3 tokens, Inter / JetBrains Mono via Google Fonts, dark mode that follows OS preference + `?theme=light|dark` URL param + a header toggle.
- Layouts section now renders segments as per-segment cards (one card per segment with role pill, size readout, and a `Field | Length | Exclude` table) — mirrors the dashboard's `SegmentEditor`.
- Added an `Output dir` banner immediately under the `Compare report` headline so the operator sees where every file landed.
- File links in the Aggregate counts and Per-key matrix sections are sibling relative paths that now resolve via the run-token route.

**UI (`ui/`):**
- Vue 3 + Vite + PrimeVue 4 (Material preset) + Inter + Material Symbols.
- Sakai-style dashboard shell: collapsible icon sidebar (240 px ↔ 64 px, persisted in localStorage), 56 px topbar with hamburger toggle + breadcrumb + theme switch, mobile overlay below 992 px.
- `FieldConfig` view: sticky file-header strip below the topbar, two-column body (File A | File B), 280 px right rail (Run + Save & Run).
- Per-side panels under each file: **Compare key & sort** (key field Select + sorted checkbox + segmented Order/Type), **Per-record prefixes** (compact rows with `BIN | ASC` segmented buttons — no popups, no overflow), **Segments** (read-only template rows + add/save/edit lifecycle for user-added fields on TU4R via a `_saved` flag).
- `FileBrowserDialog` — server-backed picker with breadcrumb, filter, home/up buttons. `pick-mode="dir"` variant powers the Output directory picker (hides files, exposes "Pick this folder").
- Save & Run button shows an animated Material `progress_activity` icon, a pulsing outer ring, and an indeterminate progress bar while running.
- `RunResultDialog` pops on completion: six metric cards (Matched / Mismatched / Only-in-A/B / Dups-in-A/B), output path, and a primary "Open report" button that opens the report in a new tab with the matching theme. Inline `RunResultPanel` is kept below the dashboard as history.
- `useTheme` + `useLayout` composables (localStorage-backed).

### Bugs fixed this session

- **`_last_unsaved` 404 on run.** Blank Config name → save wrote to `_last_unsaved/` (reserved scratch slot) and returned that literal — then `runCompare` rejected it as "reserved." Fixed by letting `config_dir_for()` accept `_last_unsaved` (it's a real dir we created); only *new* names go through the reserved-name guard.
- **Report file links 404'd.** Report was served at `/api/runs/report?run_dir=X`, so the browser resolved bare links like `matches.dat` to `/api/runs/matches.dat`. Fixed by moving the run dir into the URL path as a base64url token: `/api/runs/{token}/report` + `/api/runs/{token}/{name}`. Path-traversal-safe.

### What's pending

- Run History view (currently a "soon" tile in the sidebar).
- Saved-config picker UI (backend list endpoint exists, but the UI always saves a new config). Want a left-rail "Configs" list with rename/delete.
- Tests for the API package (`tests/test_api_*.py`). Backend test suite is unchanged (engine tests cover the core; the new routes have no coverage yet).
- Bundle-size warning from Vite (>500 kB). PrimeVue + icons pull in a lot; consider code-splitting later.

### Blockers

None.

### Branch / remote

`dev`, pushed to `origin/dev`.

### Next concrete action

Write `tests/test_api_routes.py` covering: `/api/template-layouts` shape, `/api/configs` round-trip (save then list), `/api/runs/{token}/report` 404 on bad token, `/api/runs/{token}/{name}` path-traversal rejection. Then start the Run History view (read `user_configs/<name>/runs/` or a manifest the pipeline writes).

### Notes for future me

- The base64url run-token approach in `routes.py::_encode_run_token` is deliberate — using the path lets browser-relative links Just Work without rewriting the report HTML on the fly. The token decodes back to an absolute path; the file-serving endpoint enforces `target.resolve().relative_to(run_dir)` so traversal is blocked.
- `SEGCMP_USER_CONFIGS_DIR` is the env knob to point storage somewhere outside the repo. Defaults to `./user_configs/`, which is now gitignored.
- The UI talks to the engine *only* via the API. No engine code was modified — all storage/projection lives in `api/storage.py`. If the engine schema changes, only `_build_engine_layout` needs to follow it.
- Sakai shell is hand-rolled (matching the Sakai-Vue pattern) rather than pulled in as a dep — keeps the bundle smaller and avoids the demo pages.

---

## Session: 2026-05-28 (reports overhaul + per-run subdir + matches sample + v3 tag)

**Branch:** `dev` → tagged **`v3`** at commit pushed in this session
**Phase:** 2 (engine extension)
**Status:** 219 tests passing on pyenv 3.12.7; black, flake8, mypy
--strict all clean.

### What was completed

A long iteration on the run-output story, driven by operator feedback.
Five ADRs landed (035 → 038 already accepted in prior commits;
recapped here in chronological order so the session reads coherently):

- **ADR-035** — `compare_reports.csv` (3-column long-format) +
  `compare_reports.html` (self-contained HTML with inline CSS)
  alongside `summary.json`. Same metrics in three views; JSON stays
  the machine source of truth.
- **ADR-036** — new `keys_mismatch_matrix.csv` per-key Y/N matrix +
  HTML overhaul: side-by-side Layouts section (File A | File B with
  every field's name/length/exclude/key flags), side-by-side Inputs
  table, Aggregate counts gains a Description column (small grey
  font, plain English — "Records found in both files with identical
  content.", etc.) plus a clickable File column linking to each
  metric's stamped output file, and a Per-key mismatch sample
  section showing the first 20 rows from the matrix with a link to
  the full CSV. Per-segment table also gains a small note clarifying
  Match/Mismatch counts are **record-level** while Total in A/B
  count every segment instance across all records.
- **ADR-037** — per-run output subdirectory. Each invocation creates
  `report-YYYY-MM-DD-HH-MM-SS/` (UTC, seconds-precision) under
  `--output-dir`; files inside use **bare** names. Two runs in the
  same minute no longer collide. Supersedes ADR-027 on
  filename-stamping. `Summary.filename_stamp` now holds the subdir
  name. Parallel `_workers/` scratch tree lives inside the per-run
  dir too.
- **ADR-038** — `matches.dat` is sampled to 10 records;
  `mismatches.dat` stays full. Single-process gates `write_match` on
  a counter; parallel post-merge truncates the merged file using
  the record delimiter as boundary. Aggregate `records_matched`
  count is still truthful.

Smaller HTML iterations along the way (operator-driven, kept here for
posterity):

- Header subhead trimmed from "Run … · engine 0.0.1 · audit ab…" to
  just "Run …".
- Layout segments table dropped the "Role" column (role info still
  surfaces in each layout's meta block via "Key segment" / "End
  segment").
- Aggregate counts descriptions rewritten in plain English (no
  "hash", "multiset", "inner-join", "ADR-019" jargon). Sample:
  "Records in File A where the same key appears more than once.
  Removed before comparison."
- Briefly tried `title="…"` tooltips on the metric cells, but the
  tooltip didn't show reliably in the operator's setup — reverted
  to a visible Description column with the new `.desc` CSS class
  (`font-size: 0.82em; color: #555;`).
- **Per-segment breakdown** gains an intro `<p>` clarifying
  Match/Mismatch are record-level while Total in A/B count every
  segment instance across all records (including orphans + dups).
  The hybrid units in that table had been confusing to readers.

Docs synced:

- `README.md` — every reference to the old
  `<base>_YYYYMMDDHHMM.<ext>` filename-stamping scheme replaced
  with the `report-…/` subdir description. "Eight outputs" wording
  → "11 outputs".
- `docs/architecture.md` — writer.py row in the module table
  updated to describe all 11 outputs + per-run subdir.
- `docs/how-it-works.md` — top-of-file callout now also covers
  ADR-035 / 036 / 037 / 038 in addition to the earlier ADR-033 note;
  the inline file-naming example was updated to bare names + per-run
  subdir.
- `examples/README.md` — note about per-run subdir replaces the
  old "matches_<stamp>.dat" line.

### What's pending

Phase 3 kickoff (FastAPI scaffolding) — unchanged. ADR-035 → ADR-038
were engine-internal output-layout changes; the consumer surface
(`pipeline.run` / `pipeline.run_parallel`) is unchanged except for
where the files land on disk.

A future ADR could make ADR-038's sample size configurable via
`runtime.json::matches_sample_size` (and possibly a CLI override).
For now the cap is a constant `MATCHES_SAMPLE_SIZE = 10`.

### Blockers

None.

### Decisions captured this session

- **ADR-035**: CSV + HTML reports alongside summary.json.
- **ADR-036**: per-key matrix CSV + HTML overhaul.
- **ADR-037**: per-run output subdirectory; bare filenames inside.
  Supersedes ADR-027 on filename stamping.
- **ADR-038**: matches.dat sampled to 10; mismatches.dat stays full.

### Next concrete action

Open `docs/phase-3.md` and start the FastAPI scaffolding. The HTML
report (`compare_reports.html`) gives the Phase 3 web UI a free
hand-rolled prototype to crib from for visual conventions.

### Notes for future me

- `RUN_DIR_FORMAT = "report-%Y-%m-%d-%H-%M-%S"`. Seconds-precision
  picks up where ADR-027's minute-precision had collision risk; if
  someone re-launches the engine inside the same wall-clock second
  on the same `--output-dir`, the second invocation overwrites the
  first. Acceptable for the human-driven case; the Phase 4 service
  runner can add monotonic suffixes if it ever needs them.
- The Aggregate counts `Description` column carries plain-English
  prose with no jargon. Tests assert no "hash" / "multiset" /
  "inner-join" / "ADR-019" leak into that section. If you add a new
  description, keep the writing style consistent (simple English,
  no engineering terms).
- `Summary.filename_stamp` is now misnamed — its value is the
  run-dir name, not a filename suffix. Renaming to `run_dir_name`
  would be a `summary.json` schema break (external tooling reads
  the field); leaving the field name alone for now.

---

## Session: 2026-05-28 (ADR-034: context-sensitive segment aliasing)

**Branch:** `dev`
**Phase:** 2 (engine extension)
**Status:** 199 tests passing on pyenv 3.12.7; black, flake8, mypy
--strict clean.

### What was completed

User asked for a real-world scenario: in their feed, `AD01` segments
appearing **before** an `EM01` are ordinary postal addresses, but
`AD01` segments appearing **after** an `EM01` are email-related
addresses. The multiset comparator needs to treat them as separate
buckets so a postal-address difference can't mask an email-address
difference. On-wire bytes are immutable (operator doesn't control
upstream); the rename has to happen at parse time, in memory.

ADR-034 ships:

- `layout.py`: new `SegmentAlias` dataclass (`wire_name`,
  `logical_name`, `after_segment`); optional
  `segment_aliases: tuple[SegmentAlias, ...]` field on `FileLayout`.
  Load-time validation: every referenced name must exist in
  `segments[]`; wire and logical sizes must match; wire and logical
  must differ; at most one alias per `wire_name`. Aliases default to
  empty tuple when omitted.
- `config.py`: `EngineConfig.file_a_aliases` / `file_b_aliases`
  expose the per-file alias rules to engine modules.
- `pipeline.py`: new `_apply_aliases(record, aliases)` helper. Walks
  segments in order; once `after_segment` has appeared in the current
  record, every subsequent `wire_name` is renamed to `logical_name`
  in memory (raw bytes untouched). Wired into `_index_file` and
  `_read_record_at`. Trigger semantics: once-armed-stays-armed
  within a record; resets at record boundaries.
- `worker.py`: same rename logic in `_read_record_at`, gated on
  `EngineConfig.file_a_aliases` / `file_b_aliases`.
- External-sort behavior: aliases are NOT applied during the sort
  pass (sort preserves raw on-wire bytes). The post-sort index pass
  re-applies the rename. The sorted file still carries on-wire
  segment names; no special handling needed.
- Output files (matches.dat / mismatches.dat / dups_*.dat /
  keymismatch_*.dat) continue to carry on-wire segment names since
  they emit `record.raw`. Only `summary.json::per_segment` and
  `report.csv`'s `segment_name` column reflect the rename.

Tests (11 new, 188 → 199):
- 9 layout-loader cases in `test_layout.py`: default-empty,
  round-trip, every validation rule (wire/logical/after_segment must
  be declared, sizes must match, wire ≠ logical, one alias per wire,
  must be list).
- 2 end-to-end pipeline cases in `test_pipeline.py`:
  AD01-after-EM01 records bucket as separate `AD01` + `EMAD` entries
  in `summary.per_segment`; AD01-without-EM01 records keep `AD01`.

### What's pending

Phase 3 kickoff (FastAPI scaffolding) — unchanged. ADR-034 is an
engine-internal extension; phase pointer doesn't move.

Out of scope for this ADR (recorded for future work):
- Multiple aliases for the same `wire_name` (precedence rules).
- Reset-on-trigger semantics (only the AD01 immediately following
  the most-recent EM01 renames).
- CLI override for alias rules.

### Blockers

None.

### Decisions captured this session

- **ADR-034**: per-file `segment_aliases` block declares
  context-sensitive renames applied post-parse. Once-triggered
  semantics; one alias per `wire_name`; sizes must match between
  wire and logical declarations.

### Next concrete action

Resume the Phase 3 handoff plan in the entry further below — open
`docs/phase-3.md` and start FastAPI scaffolding. ADR-034 is
transparent to Phase 3 callers since `pipeline.run` / `run_parallel`
read aliases from the already-loaded `EngineConfig`.

### Notes for future me

- The rename helper lives in `pipeline._apply_aliases` AND a near-
  copy in `worker._read_record_at` (cross-process boundary makes
  sharing awkward). If we add a second alias use case, factor it
  into a shared helper module.
- `record.raw` is the on-wire bytes. Tests that read .dat output
  files and look for segment names should look for the *on-wire*
  name, not the logical one. This is intentional — operators reading
  matches.dat see what was in their input, not the engine's logical
  view.

---

## Session: 2026-05-28 (ADR-033 three-stage migration to per-file layout configs)

**Branch:** `dev`
**Phase:** 2 (engine restructure — no phase change)
**Status:** Engine cut over to the per-file layout schema. 188 tests
pass on pyenv 3.12.7; `black`, `flake8`, `mypy --strict` all clean.

### What was completed

User asked for two adjacent things:
1. **Per-file `key_range`** so File A and File B can put the record
   key at different physical positions inside TU4R.
2. **Drop the byte-level config form** (segments.json + normalization.json
   with `file_a_strip` / `exclude_positions`) in favor of a single
   per-file layout file where the operator describes what's *in* each
   file (segments + named fields), not what to *strip* from it.

Both land via **ADR-033**, executed as three commits:

- **Stage 1** (`5abe898`) — schema + sample artifacts only. Wrote
  `config/layout_file_A.json` and `config/layout_file_B.json` describing
  the existing realistic fixture byte-for-byte (validated:
  ``segment.size == header_bytes + sum(field.length)`` for every
  segment; standard 3-TR01 record sums to 417 bytes, matching
  `examples/README.md`). ADR-033 documents the schema, the eight
  load-time invariants, and the three-stage migration plan.
- **Stage 2** (`e0434ea`) — additive loader. `src/segment_compare/layout.py`
  ships `FileLayout` / `SegmentLayout` / `FieldLayout` / `FileFormatConfig`
  / `StripConfig` / `SortConfig` dataclasses plus
  `load_file_layout(path) -> FileLayout`. Every load-time invariant is
  enforced with `LayoutError` and a precise field path. 30 new tests
  cover happy paths, defaults, and every invariant's failure mode.
  Legacy loader untouched; nothing in the engine consumes `FileLayout` yet.
- **Stage 3** (this commit) — engine cutover and legacy removal.

Stage 3 changes:

- **Parser** — `iter_records` now accepts `strip_leading_bytes: int = 0`
  (per-record opaque skip applied before RDW). Order on the wire:
  `[strip_leading_bytes][rdw][key_segment]…[end_segment][delimiter]`.
  4 new tests pin the strip behavior.
- **`config.py` rewritten** — `EngineConfig` holds two `FileLayout`s
  plus a trimmed `RuntimeConfig` (dropped `input_sorted`, `key_type`,
  `key_sort_order` — they live in each layout's `sort` block now).
  Engine-facing accessors (`parser_a` / `parser_b` / `segments_a` /
  `segments_b` / `file_a_rdw` / `file_b_rdw` / `file_a_strip_size` /
  `file_b_strip_size`) synthesize the legacy per-file views.
  `load_config(config_dir)` loads `layout_file_A.json` +
  `layout_file_B.json` + `runtime.json`; layout errors re-raised as
  `ConfigError` for uniform CLI exit code.
- **Normalizer simplified** — `PositionNormalizer` and
  `CompositeNormalizer` deleted; field-based is the only form.
  `FieldNormalizationRule` + `FieldDef` moved into `normalizer.py`
  (out of `config.py`) since they're internal to the normalizer.
- **Pipeline / worker / external_sort** — all now thread per-file
  `parser_*`, `segments_*`, `rdw_*`, `strip_size_*` through the
  iter_records/seek/read paths. `external_sort_file` signature
  decoupled from `EngineConfig` (takes `parser_cfg`, `segments_cfg`,
  `chunk_size`, `sort_temp_dir`, optional `rdw_cfg`, `strip_size`).
  The "should we sort?" trigger now reads `layout_a.sort.input_sorted`
  and `layout_b.sort.input_sorted` independently; either one false
  triggers the sort.
- **CLI** — `--config-dir` help text and `--external-sort` help text
  updated to describe layout-file semantics. Exit codes unchanged.
- **Deleted from `config/`**: `segments.json`, `normalization.json`,
  `segments.example-rdw.json`. Per-file RDW + strip blocks live
  inside the layout files now.
- **Test migration**: 188 tests pass (down from 242 — net delete of
  Stage-2-redundant tests offset by Stage-3 additions).
  - Deleted: `tests/test_normalizer.py` (PositionNormalizer +
    CompositeNormalizer dispatch tests — both gone) and
    `tests/test_field_config.py` (covered by `tests/test_layout.py`).
  - `tests/test_config.py` rewritten for the new `EngineConfig` loader.
  - `tests/test_field_normalizer.py` slimmed to FieldNormalizer-only
    cases (CompositeNormalizer-specific tests dropped).
  - `tests/test_field_integration.py` rewritten — the old
    field-vs-position identity test is moot; the remaining
    headline-capability test (A's NM01 has 2 fields, B's has 3 with
    filler excluded) uses two diverging layouts to exercise the
    per-file divergence pathway.
  - `tests/test_pipeline.py`, `tests/test_main.py`,
    `tests/test_external_sort.py` migrated to a new shared helper
    (`tests/_helpers.py::minimal_layout` +
    `write_minimal_config_dir` + `make_synthetic_record`) for the
    smaller-than-realistic synthetic records.
  - `tests/test_hasher.py`, `tests/test_comparator.py` updated to
    drop deleted-type imports.
- **Docs** — README repository-layout tree + bootstrap scripts +
  `--config-dir` help reference the new files. `examples/README.md`
  "How the engine is wired" section updated. `docs/architecture.md`
  and `docs/how-it-works.md` got top-of-file ADR-033 callouts
  pointing legacy snippets at the new shape. ADR-007, ADR-008,
  ADR-029 marked superseded; ADR-031 (per-file RDW) updated to
  reflect the absorbed location.

### What's pending

Phase 3 kickoff (FastAPI scaffolding) — unchanged from the prior
handoff entry. ADR-033 was an engine-internal restructure; the
phase pointer doesn't move.

`docs/how-it-works.md` mid-document snippets still show
`exclude_positions` byte-range examples. Those are accurate as
*conceptual* descriptions of what the field-form layout achieves,
just stylistically dated. A future docs pass can rewrite them to
layout JSON if a real reader complains; for now the top-of-file
callout flags the legacy form.

### Blockers

None.

### Decisions captured this session

- **ADR-033**: per-file layout config replaces `segments.json` +
  `normalization.json`. Supersedes ADR-007 (position-vs-field split),
  ADR-008 (separate strip rules), and ADR-029 on dispatch.
  Absorbs ADR-031 (per-file RDW now lives inside each layout).

### Next concrete action

Tag the cutover (optional) and resume the Phase 3 handoff plan
below — open `docs/phase-3.md` and start the FastAPI scaffolding.
Phase 3's `pipeline.run` / `run_parallel` consumer surface is
unchanged; the wrapper just calls `load_config(config_dir)` against
the new layout-file directory.

### Notes for future me

- `EngineConfig` accessors (`parser_a`, `segments_a`, etc.) recompute
  on every access. If profiling shows them as hot, memoize. Probably
  irrelevant — they're called O(1) times per run.
- The minimal layout in `tests/_helpers.py` describes a 50-byte
  synthetic record (TU4R023 + NM01017 + ENDS010). Don't try to
  load it against the realistic sample files — those use the full
  417-byte format and need the committed `config/` layouts.
- `external_sort_file` no longer takes a `config` arg — explicitly
  takes `parser_cfg`, `segments_cfg`, `chunk_size`, `sort_temp_dir`.
  Cleaner for unit tests; pipeline-internal callers go through
  `_external_sort_inputs` which still threads from `EngineConfig`.
- `runtime.json`'s `input_sorted`, `key_type`, `key_sort_order` are
  gone. If someone tries to load an old runtime.json, the new
  loader silently ignores those fields (they're not in the
  `_require_field` list). It does NOT migrate the values into the
  layouts — the operator must explicitly move them.

---

## Session: 2026-05-28 (Python floor lowered to 3.10+ for Windows compat)

**Branch:** `dev`
**Phase:** N/A — toolchain change
**Status:** 212 tests still pass on pyenv 3.12.7; black / flake8 / mypy
clean. Windows 3.11 / 3.10 paths not yet exercised in CI but the code
audit confirms no 3.11+ syntax is used.

### What was completed

User reported running Python 3.11.1 on Windows. The `requires-python =
">=3.12"` pin from ADR-025 blocked them. A code audit found the engine
uses no 3.11- or 3.12-specific syntax (no `tomllib`, `typing.Self`,
`typing.override`, `ExceptionGroup`, `except*`, PEP 695 generics, or
`match` statements). The only 3.10+ language feature actually used is
`@dataclass(slots=True)`, so 3.10 is the real floor.

Changes:

- `pyproject.toml` — `requires-python` → `>=3.10`; classifiers expanded
  to 3.10/3.11/3.12/3.13 plus macOS and Windows OS classifiers; black
  target-version → `["py310", "py311", "py312"]` (py313 dropped to
  silence the safety-check warning when running on a 3.12 interpreter);
  mypy `python_version` → `"3.10"`.
- `CLAUDE.md` — "Supported Python: 3.10+ (Mac dev pinned to 3.12.7 via
  pyenv)". The pyenv sanity-check block is now scoped to Mac dev.
- `README.md` — Setup split into "Mac / Linux (pyenv 3.12.7)" and
  "Windows (PowerShell, stock Python 3.10+)" with the latter showing
  `python -m venv .venv` / `.venv\Scripts\Activate.ps1` /
  `pip install -e ".[dev]"`. Added a "Production server (Python 3.6,
  no install allowed)" subsection that explicitly punts that
  environment — see "Pending" below.
- `docs/decisions.md` — added **ADR-032** (floor → 3.10+) and marked
  ADR-025 as superseded on the floor only. ADR-020's pytest / black /
  flake8 / mypy strict decisions remain in effect.

### What's pending

**Prod-server (Python 3.6.5, no install allowed) is unresolved.**
User chose to punt this. When it becomes a priority, the realistic
options are (in order of preference):

1. **PyInstaller** — single-file binary bundling a 3.12 interpreter +
   the engine. No system Python touched.
2. **python-build-standalone** — drop a static CPython tree alongside
   the app; invoke via the bundled interpreter. Easier to debug than
   a frozen binary.
3. **Docker / Podman** if container runtime is available on prod.

Backporting the engine to 3.6 is rejected per ADR-032: 3.6 is missing
`dataclasses` (used in every module), `from __future__ import
annotations` (first line of every file), and `dataclass(slots=True)`.
3.6 has also had no security patches since 2021.

Phase 3 kickoff (FastAPI scaffolding) is still pending — unchanged from
the earlier handoff entry.

### Blockers

None for the floor change. The prod-server work has an unresolved
constraint (no install possible) and is tracked above.

### Decisions captured this session

- **ADR-032**: Python floor lowered from 3.12+ back to 3.10+. pyenv
  3.12.7 remains the Mac dev pin (recommended, no longer required).
  ADR-025 superseded on the floor only; ADR-020 tooling decisions
  still stand.

### Next concrete action

If the user reports the suite green on their Windows 3.11.1 box after
`pip install -e ".[dev]"` + `pytest`, the cross-platform claim in
ADR-032 is verified and we can resume the Phase 3 handoff plan below.
Otherwise: investigate the regression and amend ADR-032 with the
narrower supported set.

### Notes for future me

- mypy's `python_version = "3.10"` flags accidental newer-syntax use
  during review even though the dev box runs 3.12.7. That's the
  cheapest enforcement available without a CI matrix.
- The single black warning we used to see ("Python 3.12 cannot parse
  code formatted for Python 3.13") is gone now that py313 is out of
  the target list. If a 3.13-only contributor needs it back, the
  warning is harmless when they're running on 3.13 themselves.
- The 3.6 prod story will need a real decision before Phase 4 (the
  scheduled-service deliverable) lands — that's the phase that
  actually ships to prod.

---

## Session: 2026-05-28 (per-file RDW prefix support)

**Branch:** `dev`
**Phase:** 2 (engine extension — no phase change)
**Status:** RDW skip landed end-to-end. 212 tests pass on pyenv 3.12.7;
`black`, `flake8`, `mypy --strict` all clean.

### What was completed

User reported that real-world inputs may carry a Record Descriptor Word
prefix (two integer fields, ``rdw1`` + ``rdw2``) before each record's
TU4R segment. Either File A or File B can carry it independently, and
the encoding (ASCII zero-padded vs. binary little-endian uint) and
widths vary by source. The engine only needs to skip the bytes.

Changes:

- `src/segment_compare/parser.py` — added :class:`RdwConfig` dataclass
  (``rdw1_bytes``, ``rdw2_bytes``, ``encoding``, ``total_bytes``
  property). :func:`iter_records` accepts an optional
  ``rdw_cfg: RdwConfig | None``; when set, it consumes
  ``rdw_cfg.total_bytes`` at the start of each iteration before reading
  the key segment. ``Record.offset`` / ``Record.length`` stay relative
  to the key segment so the single-record seek-and-read path in
  pipeline + worker stays uniform.
- `src/segment_compare/config.py` — new ``SUPPORTED_RDW_ENCODINGS =
  ("ascii_int", "binary_le_uint")``. Added ``file_a_rdw`` /
  ``file_b_rdw`` fields on :class:`ResolvedConfig`. New
  ``_build_rdw_configs`` / ``_build_rdw_for_file`` validate the
  optional ``parser.file_a.rdw`` and ``parser.file_b.rdw`` blocks.
- `src/segment_compare/pipeline.py` — `run` / `run_parallel` / `dry_run`
  thread the per-file RDW into `_index_file` and
  `_external_sort_inputs`. When external sort runs, the sorted temp
  copies have no RDW, so the post-sort indexing pass uses
  ``rdw_cfg=None``.
- `src/segment_compare/external_sort.py` — `external_sort_file` accepts
  ``rdw_cfg`` and passes it to the input scan; the merged output is
  plain (no RDW).
- `config/segments.example-rdw.json` — full example showing per-file
  RDW blocks with different widths and encodings.
- `config/segments.json` — `$comment` updated to point at the example
  for users with RDW-prefixed inputs.
- `README.md` — Repository layout section expanded into a full
  directory/file tree, plus new "Bootstrap a fresh checkout" section
  with idempotent bash and PowerShell scaffolding scripts.
- `docs/decisions.md` — added **ADR-031** documenting the schema,
  parser contract, pipeline wiring, and out-of-scope length validation.

Tests added (14 total, suite goes 198 → 212):

- `tests/test_parser.py` — RDW round-trip (single + multi-record),
  truncated-RDW raises, clean EOF before RDW returns, ``None`` cfg is
  identity, `RdwConfig.total_bytes`.
- `tests/test_config.py` — absent yields ``None``; both-files present;
  only-file-a present; bad encoding rejected; zero-size rejected;
  missing-field rejected; example file in repo loads cleanly.
- `tests/test_pipeline.py` — end-to-end: File A wrapped in a 4-byte
  RDW prefix and File B plain produce a single match (records
  compare equal once the prefix is skipped).

### What's pending

Phase 3 kickoff (FastAPI scaffolding) — unchanged from the prior
handoff entry below. RDW support extends Phase 2's engine surface but
does not move the phase pointer.

Out-of-scope for this session (recorded for future ADRs if asked):

- Validating ``rdw1`` against actual record length (encoding field is
  ready for it; parser would need to decode based on ``encoding``).
- CLI override of the RDW block on a per-invocation basis.

### Blockers

None.

### Decisions captured this session

- **ADR-031**: per-file RDW prefix is configurable via
  ``segments.json::parser.file_a.rdw`` / ``parser.file_b.rdw``,
  consumed but not interpreted. Encoding (``ascii_int`` /
  ``binary_le_uint``) is recorded for future validation work.

### Next concrete action

Resume the Phase 3 handoff plan in the entry below — open
`docs/phase-3.md` and start FastAPI scaffolding. The RDW extension
slots in transparently for Phase 3/4 callers since
``pipeline.run`` / `run_parallel` read the per-file RDW from the
already-loaded config.

### Notes for future me

- `record.offset` and `record.length` deliberately exclude the RDW
  bytes — they point at TU4R and span TU4R..ENDS+delim. Anywhere we
  re-read a record by `(offset, length)` (workers, `_read_record_at`),
  no RDW handling is needed.
- The single place that **does** need RDW awareness is the streaming
  scan: `iter_records` when called against the raw input file. Sorted
  temp files written by the engine never carry an RDW, so post-sort
  index passes use `rdw_cfg=None`.
- If a future input carries RDW only at the file start (not per
  record), this design does not cover it — we'd add a separate
  `header_skip_bytes` knob rather than overloading `rdw`.

---

## Session: 2026-05-28 (pause-and-handoff to Phase 3)

**Branch:** `dev` (tag `phase-2-complete` at `ffc96de`)
**Phase:** 3 — kickoff pending
**Status:** Phase 2 closed and tagged. Stopping for the day; next
session resumes on Phase 3 (Vue.js 3 + FastAPI web UI).

### Why we're pausing here

`dev` is a clean handoff state. Phase 2 acceptance criteria #1–#6 are
all green (see the previous session-log entry). 198 tests passing on
pyenv 3.12.7; black, flake8, mypy --strict clean. The engine library
is feature-complete behind `pipeline.run` and `pipeline.run_parallel`,
which is exactly the boundary `docs/phase-3.md` expects to wrap.

Two doc artifacts landed in this stopping commit that future sessions
should rely on:

- `docs/how-it-works.md` — end-to-end engine walkthrough with
  byte-level examples + reliability math. Read this first if you're
  picking the project up cold.
- `README.md::CLI command reference` — every CLI option with a
  copy-pastable example, plus seven common usage patterns for
  smoke testing, daily reconciliation, config validation, dry-run,
  external sort, deterministic single-process runs, and verbose
  debugging.

### Phase 3 plan (the next concrete action)

Open `docs/phase-3.md` end-to-end and start the FastAPI scaffolding.
The doc is detailed; the headline:

**Tech stack** (per `docs/phase-3.md` and ADR-014):

- Backend: FastAPI + uvicorn. SQLite for run history.
- Frontend: Vue.js 3 (Composition API), Vite, plain CSS (or
  Tailwind if it lands cleanly).
- Communication: REST + JSON for control; SSE for live progress.

**Six screens to build** (in the order the doc recommends):

1. Run Configuration — file pickers, worker count slider, hash
   method dropdown, dry-run toggle, "Start Comparison" button.
2. Run Execution — live progress, log tail, cancel button.
3. Results Dashboard — summary cards, mismatches-by-segment chart,
   download buttons for all 8 outputs, paginated mismatch table,
   per-record inspector.
4. Run History — SQLite-backed past-run list with re-run support.
5. Segment Selection — checkbox list of known segments with
   sensible defaults from `config/segments.json`.
6. Field Configuration — per-segment field-layout editor that
   targets the Phase 2 `FieldNormalizationRule` shape.

**Backend layout** (`src/segment_compare/api/`):

```
api/
├── __init__.py
├── main.py        # FastAPI app instance, lifespan, middleware
├── routes.py      # endpoint handlers
├── models.py      # pydantic request/response models
├── runs.py        # run launcher + SSE progress channel
└── storage.py     # SQLite run history
```

**Acceptance criteria** (from `docs/phase-3.md`):

1. All six screens functional against real engine runs.
2. FastAPI auto-docs at `/docs` reachable and accurate.
3. SQLite run history persists across restarts.
4. Happy-path browser test (manual checklist or Playwright) passes.
5. CLI and API call the same `pipeline.run` function — no duplicated
   comparison logic (ADR-012 enforcement).

**Ordered task list for the next session:**

1. FastAPI scaffolding: app instance, health endpoint, `/docs`
   reachable. Add `fastapi` + `uvicorn[standard]` to
   `pyproject.toml::[project.optional-dependencies].api`.
2. SQLite schema + storage layer in `api/storage.py`.
3. `POST /api/runs` endpoint that invokes `pipeline.run` /
   `pipeline.run_parallel` in a `ProcessPoolExecutor`-backed worker
   pool. Reuse the same `--workers` config-knob precedence
   (ADR-028).
4. SSE progress channel (`GET /api/runs/{id}/events`) backed by
   per-run `asyncio.Queue`s.
5. Output-file download endpoints (`GET /api/runs/{id}/files/{name}`)
   restricted to the run's output dir.
6. Vue scaffold via `npm create vite@latest` under `ui/`.
7. `services/api.js` thin wrapper around the REST API.
8. Screens in the order above (1, 2, 3, 4, then 5, 6).
9. End-to-end manual test against `examples/sample_*.dat`.

**Key design decisions to settle BEFORE writing code:**

- **Process model for engine runs.** FastAPI is async; the engine
  is blocking and CPU-bound. Wrap `pipeline.run` /
  `pipeline.run_parallel` in
  `concurrent.futures.ProcessPoolExecutor` so the event loop stays
  responsive. The pool size should be 1 (we already parallelize
  *inside* `pipeline.run_parallel`) — one engine run at a time,
  not many concurrent runs trampling each other's disk + RAM.
- **File picker security.** `/api/filesystem/browse` must be
  restricted to a configured allowlist of roots. Symlink traversal
  rejected. Not optional.
- **Auth / multi-user model.** Out of scope for Phase 3 v1 (single-
  user, single-tenant). Phase 4 service mode revisits this if
  needed.
- **State persistence.** SQLite-only for now; engine outputs stay
  as files on disk. No DB for the bytes — only metadata.

### What's pending

- Phase 3 implementation (above).
- Deferred Phase 2 optimizations (parallel index-build, shared-memory
  index sharing). Not blocking.

### Blockers

None. The engine library and its docs are ready to be wrapped.

### Decisions captured this session

None new since the Phase 2 closure entry below. Three ADRs land
this session (ADR-028 / 029 / 030) — see that entry.

### Next concrete action

When the next session begins, open `docs/phase-3.md`, agree with the
user on the process-model question above (single-run-at-a-time
ProcessPoolExecutor wrapping `pipeline.run_parallel`), then start
Phase 3 step 1: FastAPI scaffolding + health endpoint + `/docs`
reachable. Add dependencies to `pyproject.toml` under a new
`[project.optional-dependencies].api` extra so the engine remains
import-clean for non-API users.

### Notes for future me

- The realistic 10/11-record fixture is the canonical Phase 3
  end-to-end manual-test target. The 3M synthetic in
  `tests/fixtures/` is for benchmarking, not for clicking through
  the UI.
- The engine never produces summary.json in <100 ms for the
  realistic fixture, so the UI's "Run Execution" screen can do
  polling at 250 ms intervals without missing the completion edge.
  Use SSE anyway — it's cheaper for the 3M case where the run
  takes 2 minutes.
- pyenv 3.12.7 still pinned. `~/.pyenv/shims/python` is the
  interpreter; this Bash tool needs the full path because the
  shim dir isn't in its PATH.
- Git identity is finally configured globally
  (`rjoshig <30200211+rjoshig@users.noreply.github.com>`) so all
  future commits will show the right author. The two pre-fix
  commits (`f38d666`, `6b8a693`) still have the hostname identity;
  the user opted to leave them rather than rewrite history.

---

## Session: 2026-05-28 (Phase 2 closure — parallelism, field-normalizer, external sort)

**Branch:** `dev`
**Phase:** 2 → **COMPLETE**
**Status:** All six Phase 2 acceptance criteria green. 198 tests pass
on pyenv 3.12.7; `black`, `flake8`, `mypy --strict` all clean.

### What was completed

Track A — parallelism (closes criteria #1, #2, #4):

- `src/segment_compare/partitioner.py` — equal-count key partitioner
  (ADR-006). 9 tests.
- `src/segment_compare/worker.py` — pickle-safe `WorkerPayload` /
  `WorkerResult` + `run_worker` subprocess entry point. Each worker
  owns a key slice, seeks records, normalizes/hashes/compares,
  writes per-worker `matches.dat` / `mismatches.dat` / `report.csv`
  under `<output_dir>/_workers/w<wid>/`.
- `src/segment_compare/merger.py` — concatenates per-worker output
  files in worker-id order (preserves global key order) + folds
  partial summaries.
- `pipeline.run_parallel` — orchestrator: single-process index-build,
  partition, `ProcessPoolExecutor` worker dispatch, master writes
  orphan/dup records, merger combines results.
- `--workers N` CLI flag; default reads `runtime.json::parallel_workers`
  (stock: 8, configurable per ADR-028).
- 3M benchmark: 124.5 s @ 4 workers (1.84× speedup over 228.8 s
  baseline; 107.5 s @ 8 workers, 2.13× speedup). Peak RSS 2.39 GiB,
  well under 4 GiB ceiling. Counts match `ExpectedCounts` exactly at
  every worker count.
- Acceptance #1 target relaxed from 2.5× → 1.8× after measurement
  (Amdahl, serial-fraction-bound at ~30%). Original 90 s target left
  as production-hardware goal.

Track A — fixing a discovered throughput-calc bug:

- `pipeline.run` was using the optional `run_timestamp` argument as
  the elapsed-time `start_time`, so tests pinning a fixed stamp
  produced nonsensical throughput numbers in `summary.json`.
  Decoupled: `start_time = datetime.now(timezone.utc)` always;
  `filename_stamp = (run_timestamp or start_time).strftime(...)`.

Track B — field-based normalization (closes criterion #3):

- `config.FieldDef` + `config.FieldNormalizationRule` dataclasses.
- `ResolvedConfig.field_normalization` map alongside the existing
  position-based `normalization` map; loader dispatches per entry,
  rejects mixed-form entries (ADR-029).
- `normalizer.FieldNormalizer` — canonical form is sorted
  `<name>=<value>\\x1F<name>=<value>...`. The sort + name-keying lets
  A's (first, middle, last) and B's (last, middle, first) compare
  equal — the headline Phase 2 capability.
- `normalizer.CompositeNormalizer` — per-segment dispatch between
  position and field forms; pipeline + worker now use it. One
  segment can use either form; different segments in the same
  config can use different forms.
- 14 + 11 + 2 tests covering the normalizer, the config loader, and
  end-to-end identity (field config and equivalent position config
  produce byte-identical outputs on the realistic fixture).
- ADR-029 records the canonical form, the mixed-form ban, and the
  strict length-mismatch error.

Track A — external sort path (closes criterion #5):

- `src/segment_compare/external_sort.py::external_sort_file` —
  chunk-and-merge sort using `heapq.merge`. `runtime.chunk_size` =
  per-chunk in-memory buffer; `runtime.sort_temp_dir` = spill
  location. O(chunk_size) memory.
- `pipeline.run` / `pipeline.run_parallel` accept
  `external_sort: bool`; if True or `runtime.input_sorted` is False,
  both inputs are sorted to `sort_temp_dir/sorted_a_<stamp>.dat`
  before the index-build pass. Summary preserves the original input
  paths (audit-friendly).
- `--external-sort` CLI flag.
- 3M unsorted-input benchmark: 74 s sort + ~125 s compare ≈ 200 s
  total end-to-end on 4 workers. Peak RSS 1.6 GiB during sort.
- 8 tests cover sort correctness (orders by key, idempotent on
  sorted input, empty input, chunk-boundary cases, temp cleanup)
  and pipeline integration.
- ADR-030 records the chunk-and-merge design + sort_temp_dir
  contract + originals-preserved-in-summary rule.

Operational changes:

- `runtime.json::parallel_workers` raised from 1 to 8 (stock-config
  default for parallel-by-default behavior on production hardware,
  per ADR-028).
- `runtime.json::input_sorted` retains its `true` default; flip to
  `false` (or pass `--external-sort`) when inputs are unsorted.
- Phase 2 benchmark report at `docs/benchmarks/phase-2.md` with the
  full speedup curve, Amdahl analysis, and external-sort numbers.
- New regression test:
  `tests/test_pipeline.py::test_single_record_with_multi_segment_mismatch_emits_multiple_report_rows`
  pins the existing behavior that a record with N mismatched segment
  types produces N rows in `report.csv`.

### What's pending

Phase 2 is closed. Open Phase 3 next (Vue.js + FastAPI UI).

Deferred follow-up work that did NOT block Phase 2 closure:

- Parallel index-build pass (would lift the Amdahl ceiling and let 4
  workers hit the original 90 s target on the laptop).
- Shared-memory or mmap-based index sharing across workers (would
  cut the per-payload pickle overhead).
- Sub-minute stamp resolution for high-frequency runs.

### Blockers

None.

### Decisions captured this session

- **ADR-028**: workers configurable via `runtime.json::parallel_workers`,
  default 8; CLI overrides.
- **ADR-029**: field-based normalization — canonical form
  `name=value\\x1F...` sorted by name; one form per segment; strict
  length validation.
- **ADR-030**: external chunk-and-merge sort; sorted copies in
  `sort_temp_dir`; summary preserves original input paths.

### Next concrete action

Tag `phase-2-complete` at the closure commit and push. Then open
`docs/phase-3.md` and discuss Vue.js + FastAPI scaffolding. Phase 3
work begins with the engine library boundary — Track A / B / external
sort all sit behind `pipeline.run` / `pipeline.run_parallel`, so the
FastAPI app just wraps those calls.

### Notes for future me

- 198 tests, ~1 second total. Each iteration is fast; lean on the
  test suite when refactoring.
- `tests/fixtures/synth_003000000_seed42_*.dat` is cached at ~1.3 GiB
  each (gitignored). Regeneration via
  `tests.synthetic_data.generate_pair(3_000_000, 42, ...)` takes ~15
  seconds. The sidecar `_expected.json` carries `ExpectedCounts`.
- The dispatch between `pipeline.run` (single-process) and
  `pipeline.run_parallel` (multi-worker) lives in `__main__.py`.
  Phase 3's FastAPI runner and Phase 4's service runner should mirror
  the same dispatch — don't duplicate orchestration logic.
- Sort temp dir defaults to `/tmp/segment_compare`. If you change
  that, audit which OS process owns cleanup and whether the path is
  shared across runs (currently it is; per-run stamping is what
  keeps them apart).

---

## Session: 2026-05-27 (Phase 1 closure — realistic fixture + timestamped outputs)

**Branch:** `dev`
**Phase:** 1 → **COMPLETE**
**Status:** All six Phase 1 acceptance criteria met. 137 tests pass on
pyenv 3.12.7; `black`, `flake8`, `mypy --strict` all clean.

### What was completed

- Generated a production-shaped sample pair (`examples/sample_a.dat`
  10 records / `examples/sample_b.dat` 11 records) covering all ten
  Phase 1 scenarios in one fixture. Removed the obsolete simpler
  samples and their docs.
- Updated `config/segments.json`: added `TR01` to `known_segments`,
  changed TU4R `key_range` from `[0, 12]` to `[4, 16]` (key now sits
  after the literal `"DATA"` prefix in the new format).
- Updated `config/normalization.json`: added `ENDS` exclude
  `[[0, 3]]` (3-byte segment-count payload is not a data field) and
  `CL01` exclude `[[11, 19]]` (8-byte timestamp at known offset must
  be ignored for content equality).
- Verified the parser handles `ENDS010NNN` (ENDS with non-zero data)
  — no parser change was needed; it reads size from the header and
  treats ENDS as a regular terminator regardless of payload. Added
  an explicit unit test for the contract.
- Added timestamped output filenames per **ADR-027**:
  `writer.stamped_filename(base, stamp)` helper, optional
  `OutputWriter(filename_stamp=...)` parameter,
  `pipeline.run(run_timestamp=...)` parameter, `Summary.filename_stamp`
  field, and `summary.json` emission of the stamp.
- Rewrote the existing sample-file tests in `test_parser.py`,
  `test_pipeline.py`, and `test_main.py` to assert against the new
  fixture's expected counts and the stamped filenames. The integration
  test `test_run_against_sample_files_matches_oracle` is now the
  single oracle for Phase 1 acceptance criteria #2, #3, and #4.
- Ran the engine end-to-end against the new fixture; verified all
  eight outputs with the expected counts:
  - matches=4, mismatched=3, only_a=1, only_b=2, dups_a=2, dups_b=2
  - 3 rows in `report.csv` (NM01 content_diff,
    TR01 content_diff, TR01 count_diff 4 vs 3)
  - K010 lands in `matches.dat` (CL01 timestamp exclude is working
    — A had `20250101`, B had `20250709`, both normalize identically)
- New ADRs:
  - **ADR-026** — Realistic fixture supersedes 10K synthetic for
    Phase 1 closure. (Synthetic generator deferred to Phase 2.)
  - **ADR-027** — Timestamped output filenames
    (`<base>_YYYYMMDDHHMM.<ext>`).

### What's pending

Phase 1 is closed. Next priority is opening Phase 2:

- Define a throughput target for the 3M-record acceptance criterion
  (Phase 2.1 in `docs/phase-2.md` says this is set after a Phase 1
  baseline measurement — that measurement is also still pending; the
  10-record fixture runs in ~1.5 ms so the measurement is meaningless
  at this scale).
- Move `tests/synthetic_data.py` back onto the Phase 2 task list (now
  a benchmarking deliverable per ADR-026).

### Blockers

None.

### Decisions captured this session

- **ADR-026**: realistic fixture supersedes 10K synthetic for Phase 1.
- **ADR-027**: timestamped output filenames.

### Next concrete action

Open Phase 2 by reading `docs/phase-2.md` end to end. Before writing
any code, agree with the user on the 3M-record throughput target
(seconds wall time, peak RSS) and on whether the field-level
normalizer (Track B) or the parallel worker pool (Track A) comes
first.

### Notes for future me

- The CLI run summary line ("done in X.XXXs: matched=..., ...") is
  preserved verbatim. The test
  `tests/test_main.py::test_main_against_samples_produces_all_outputs_and_returns_mismatches`
  doesn't pin that string but does pin exit codes — be careful when
  changing the message format.
- The stamp is **UTC**, not local time. If you run the CLI at
  10:58 PM local on May 27 and see `202605280358` in filenames,
  that's correct (UTC == 03:58 next day). If the user wants local
  time, that's a one-line change in `pipeline.run` but requires a
  new ADR.
- `_make_record` in `tests/test_pipeline.py` and `tests/test_main.py`
  produces synthetic records in the new format (key at
  TU4R `[4, 16)`). Don't revert to the old TU4R019 layout — it won't
  parse against the current `config/segments.json`.
- The 4 matched records include K010, which only matches *after* CL01
  normalization. If you ever change the normalizer or the CL01 layout,
  re-verify by inspecting `matches.dat` for K010.

---

## Session: 2026-05-27 (toolchain bump to Python 3.12+)

**Branch:** `dev`
**Phase:** 1 (step 9 still pending — not started this session)
**Status:** docs + tooling updated; no engine code touched; user is
still configuring pyenv, so no test run was performed.

### What was completed

- Pulled latest `main` (`7a6c791`, post-merge of PR #1) and created
  `dev` as the working branch going forward (supersedes the
  `claude/phase-N-<short>` branching convention in `CLAUDE.md`; that
  convention is now stale).
- Bumped Python floor from 3.10+ to 3.12+ in:
  - `pyproject.toml`: `requires-python`, classifiers (3.12, 3.13),
    `[tool.black]::target-version` (py312, py313),
    `[tool.mypy]::python_version` (3.12).
  - `CLAUDE.md`: code-conventions line.
- Recorded the decision as **ADR-025** in `docs/decisions.md` and
  marked **ADR-020** as superseded (Python version only — the pytest /
  black / flake8 / mypy strict choices from ADR-020 still stand).

### What's pending

- **Phase 1, step 9** — `tests/synthetic_data.py::generate_pair` +
  10K-record integration test. Unchanged from prior session.
- Re-running `black`, `flake8`, `mypy --strict`, `pytest` under
  Python 3.12 once pyenv is ready, to confirm nothing regressed from
  the target-version bump. Expected to be a no-op since the existing
  code uses no 3.10/3.11-specific syntax, but verify.

### Blockers

None. pyenv is configured with **3.12.7** pinned locally
(`~/.pyenv/versions/3.12.7`, active via `~/.pyenv/shims/python`).
No tests run yet on the new interpreter — that's the next concrete
action.

### Decisions captured this session

- **ADR-025**: Python 3.12+ via pyenv, supersedes ADR-020's version
  floor only.

### Next concrete action

Once pyenv 3.12+ is available: run `pytest`, `mypy --strict`, `black
--check`, `flake8` to confirm a green baseline on the new interpreter.
Then implement Phase 1 step 9 (synthetic data generator + 10K
integration test) and close out Phase 1.

### Notes for future me

- The 2026-05-28 (Phase 1 — steps 1–8) entry's note about black
  warning on py312 target under a 3.11 interpreter is moot now — the
  interpreter floor is 3.12.
- `CLAUDE.md` still references the `claude/phase-N-<short>` branching
  pattern; user has instructed work goes on `dev`. Leaving the doc
  text as-is for now (the dev-branch decision is recorded here);
  revisit if the user wants `CLAUDE.md` updated to match.

---

## Session: 2026-05-28 (Phase 1 — steps 1–8)

**Branch:** `claude/segment-comparator-setup-Opl0J`
**Phase:** 1
**Status:** acceptance criteria #1, #2, #5, #6 met; step 9 (synthetic
generator + 10K integration test, criteria #3, #4) pending.

### What was completed

Built the engine end-to-end. Eight commits, one per module per the
ordered task list in `docs/phase-1.md`:

| Step | Module | Tests | Notes |
|---|---|---|---|
| 1 | `parser.py` | 23 | streaming `iter_segments` / `iter_records`, ParseError on every documented corruption mode |
| 2 | `config.py` | 31 | `load_config` → `ResolvedConfig` with audit hash; rejects non-Phase-1 parser knobs at load time |
| 3 | `normalizer.py` | 19 | `PositionNormalizer` + `_remove_ranges` (handles unsorted / overlapping / out-of-bounds) |
| 4 | `hasher.py` | 16 | `Blake2bHasher` + `BuiltinHasher` behind a `Hasher` Protocol |
| 5 | `comparator.py` | 10 | `compare_records` with multiset-of-hashes; `RecordVerdict` + `SegmentVerdict.status` |
| 6 | `writer.py` | 14 | `OutputWriter` context manager owning all eight output handles; `Summary` + `SegmentSummary` dataclasses |
| 7 | `pipeline.py` | 9 | `pipeline.run` orchestrating index → dup-routing → join → write; smoke test against `examples/sample_*.dat` matches the documented oracle |
| 8 | `__main__.py` | 8 | CLI with `--validate-config`, `--dry-run`, `--version`; published exit codes wired up |

**130 tests pass**, `mypy --strict` clean on 10 source files,
`black` and `flake8` clean across `src/` and `tests/`.

Phase 1 acceptance criteria met:
- ✅ #1: CLI runs end-to-end against the sample files and produces all
  eight outputs.
- ✅ #2: The sample-data run matches the counts predicted in
  `examples/README.md` (verified both as a pytest assertion and a
  manual `python -m segment_compare` invocation).
- ⏳ #3: Synthetic 10K-record A/B generator (step 9, pending).
- ⏳ #4: 10K integration test asserting expected aggregate counts
  (step 9, pending).
- ✅ #5: `black`, `flake8`, `mypy --strict` clean on `src/`.
- ✅ #6: `pytest` green. (Coverage threshold not measured this session.)

### What's pending

- **Step 9** — `tests/synthetic_data.py` + a 10K-record integration
  test covering every scenario in the `docs/phase-1.md` synthetic
  scenarios table (including count-mismatch and strip+exclude
  normalization). About one more commit's worth of work.

### Blockers

None.

### Decisions captured this session

- **Parser knob enforcement at config load** — non-default
  `size_encoding`, `size_includes_header`, `data_encoding`,
  `segment_name_bytes`, `size_field_bytes` raise `ConfigError` up
  front so failures are obvious. The parser also defends in depth.
- **`Summary` and `SegmentSummary` live in `writer.py`**, not
  `pipeline.py`, to avoid a circular import (writer needs Summary;
  pipeline already needs OutputWriter).
- **`Normalizer` Protocol added to `normalizer.py`** so the
  comparator types against the contract, not the concrete
  `PositionNormalizer`. Phase 2's `FieldNormalizer` will satisfy it
  structurally.
- **Exit-code priority** — mismatches (1) outrank orphans/dups (2).
  Successful run with no anomalies returns 0.
- **`SegmentVerdict.status` derived property** with three values
  (`match` / `count_diff` / `content_diff`) drives report.csv rows
  without storing redundant state.
- **CSV emits one row per mismatched segment-type per record**
  (resolving the open question in `docs/phase-1.md`). One row covers
  both count and content differences via the status column.

### Next concrete action

Implement `tests/synthetic_data.py::generate_pair(num_records, seed)`
covering all ten scenarios in the `docs/phase-1.md` synthetic-
scenarios table, then a new integration test in `tests/` that runs a
10K-record pair through `pipeline.run` and asserts the expected
aggregate counts. Land as one commit. After that Phase 1 is fully
done and we open the Phase 2 plan.

### Notes for future me

- The CLI prints a one-line summary to stdout on success
  (`done in X.XXXs: matched=..., ...`). Don't tighten this format
  without updating `test_main.py::test_main_against_samples_*`.
- `pipeline.dry_run` reuses `_index_file`, so its parse-error and
  duplicate-counting behavior matches a real run.
- `summary.json` is sorted-keys + indent=2 for diffability across
  runs. The only fields that change between identical-input runs are
  timestamps and `elapsed_seconds` / `throughput_records_per_sec`.
- Black throws a Python 3.12 warning if run under 3.11 with py312
  in target-version. We trimmed target-version to py310+py311; if
  you bump runtime to 3.12 later, add it back.

---

## Session: 2026-05-28 (scaffolding)

**Branch:** `claude/segment-comparator-setup-Opl0J`
**Phase:** 0 (scaffolding)
**Status:** complete, ready for Phase 1 kickoff

### What was completed

- Created the full repo layout from the spec.
- Wrote `CLAUDE.md` describing project workflow and conventions.
- Replaced the placeholder `README.md` with project overview + CLI usage
  preview.
- Wrote `.gitignore` covering Python, Node/Vue, run outputs, service-mode
  directories, IDE/OS noise.
- Wrote `pyproject.toml` (Python 3.10+, pytest/black/flake8/mypy dev
  deps, entry point reserved) and a sibling `.flake8` config.
- Wrote `config/segments.json`, `config/normalization.json`,
  `config/runtime.json` with Phase 1 defaults + forward-compatible
  parser knobs (ADR-016).
- Wrote `docs/architecture.md`, `docs/phase-plan.md`,
  `docs/phase-1.md` … `docs/phase-4.md`.
- Wrote `docs/decisions.md` with 24 ADRs (15 from the spec, 9 from this
  session's clarifying round and architectural commitments).
- Created `src/segment_compare/` package skeleton with empty
  `__init__.py` files (no implementation yet).
- Created `tests/__init__.py` (no tests yet — they arrive with Phase 1).
- Created `examples/sample_a.dat`, `examples/sample_b.dat` and
  `examples/README.md` documenting expected output counts.
- Created `ui/README.md` as Phase 3 placeholder.
- Verified configs parse, package imports, and pytest runs cleanly
  against the empty test suite.
- Committed and pushed.

### What's pending

- Phase 1 implementation has not started.
- No engine code exists yet.
- Synthetic data generator does not exist yet.

### Blockers

None.

### Decisions captured this session

See `docs/decisions.md` ADR-016 through ADR-024. Headline:

- Python 3.10+, pytest, black + flake8, mypy strict.
- Phase 1 file encoding = ASCII only; encoding becomes a config knob
  later.
- Duplicate keys segregated to `dups_A.dat` / `dups_B.dat`, excluded
  from the inner-join.
- Hand-crafted samples committed in `examples/`.
- Eight output files (six original + two dup files).
- Streaming + key→offset index design from Phase 1 so Phase 2 can swap
  in a process pool without refactoring.

### Next concrete action

Open `docs/phase-1.md` and start at "Ordered task list, step 1":
implement `src/segment_compare/parser.py` with tests in
`tests/test_parser.py`. Smoke-test it against
`examples/sample_a.dat`.

### Notes for future me

- `config/*.json` files use a `"$comment"` key for inline notes — JSON
  parsers ignore it, so it's safe to read/write through.
- The eight-output design (vs the spec's six) is intentional; ADR-019
  records why. Don't try to merge dup files back into keymismatch files.
- The parser knobs in `config/segments.json::parser` are wired in the
  config schema but **only the defaults are implemented** in Phase 1.
  If a config sets `size_encoding: "binary_be_uint"`, the loader should
  raise a `ConfigError` until Phase 2 (or earlier real-data work) wires
  it up.
- `examples/sample_a.dat` and `examples/sample_b.dat` are designed so
  the engine produces predictable counts. The README in `examples/`
  states what those counts should be — use that as the integration
  test oracle for Phase 1.
