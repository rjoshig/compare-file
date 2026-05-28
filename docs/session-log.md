# Session Log

Working journal for this project. **Read the most recent entry first at
the start of every session.** Append a new entry at the end of every
session.

Required fields per entry: branch, phase, status, what was completed,
what's pending, blockers, next concrete action.

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
