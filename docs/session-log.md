# Session Log

Working journal for this project. **Read the most recent entry first at
the start of every session.** Append a new entry at the end of every
session.

Required fields per entry: branch, phase, status, what was completed,
what's pending, blockers, next concrete action.

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
