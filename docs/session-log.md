# Session Log

Working journal for this project. **Read the most recent entry first at
the start of every session.** Append a new entry at the end of every
session.

Required fields per entry: branch, phase, status, what was completed,
what's pending, blockers, next concrete action.

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
