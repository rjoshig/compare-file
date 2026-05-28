# CLAUDE.md — How to work on this project

This file tells future Claude sessions (and humans) how to make progress on
this repo without losing context, breaking conventions, or skipping phases.

## Project in one paragraph

A Python tool that compares two large fixed-format segment-based data files,
identifies matches, mismatches, orphan keys, and duplicate keys, and writes
both human-readable and machine-readable outputs. The engine is a library
with three entry points (CLI, FastAPI web UI, scheduled service). Production
target: 3M records per file. POC scope: 10K. See `README.md` for the
30-second overview and `docs/architecture.md` for the design.

## Read first, write later

**At the start of every session, read `docs/session-log.md` first.** The last
entry's "Next concrete action" tells you where to pick up. If you are about
to do something that contradicts the log, stop and reconcile before acting.

**At the end of every session, append a new entry to `docs/session-log.md`**
with: what was completed, what's pending, blockers, current branch, and the
next concrete action. Without this the next session starts blind.

## Phase gating

Work proceeds in four phases. They are sequential, not parallel:

| Phase | Theme | Doc |
|---|---|---|
| 1 | Core engine (POC, single process, 10K records) | `docs/phase-1.md` |
| 2 | Production scale + field-level config | `docs/phase-2.md` |
| 3 | Vue.js + FastAPI web UI | `docs/phase-3.md` |
| 4 | Scheduled service mode | `docs/phase-4.md` |

Never start work on a later phase before the current phase's acceptance
criteria are met. If you think you need to, raise the question with the
user first.

Before starting work on phase N, read `docs/phase-N.md` end to end.

## Doc-driven workflow

- Any non-obvious design decision goes in `docs/decisions.md` as a new ADR
  entry with rationale. Future-you will not remember why.
- Architectural changes update `docs/architecture.md` in the same commit
  as the code change.
- Phase docs (`docs/phase-N.md`) get checked off as tasks land.

## Local environment

- Python version is managed with **pyenv**. Pinned version: **3.12.7**
  (set via `pyenv local 3.12.7` / `~/.python-version` resolves to
  `~/.pyenv/versions/3.12.7`). The active interpreter is
  `~/.pyenv/shims/python`.
- Quick sanity checks before starting work:
  ```bash
  pyenv version          # should show 3.12.7 (set by ...)
  which python           # should show ~/.pyenv/shims/python
  python --version       # should show Python 3.12.7
  ```
- If `pyenv version` reports anything other than 3.12.7 in this
  directory, fix that before running tests — version drift is a more
  common source of mysterious test failures than actual bugs.
- Virtualenv layout: `.venv/` at repo root (gitignored). Recreate with
  `python -m venv .venv && source .venv/bin/activate && pip install -e
  ".[dev]"` when needed.

## Code conventions

- Python 3.12+ (pyenv 3.12.7 is the pinned local version), type hints
  on every function signature, Google-style
  docstrings on every public function and class.
- Use `pathlib.Path` for filesystem paths, never string concatenation.
- Use `logging` module — never `print()`.
- No magic numbers — promote to module-level constants or config.
- Streaming I/O by default — never load a whole input file into memory
  unless there's a specific reason.
- Pure functions where possible — easier to test, easier to parallelize.
- CLI via `argparse`. Every option has help text.
- JSON for configs. Validate at load time, fail loudly on bad config.
- **Parallelism is configurable.** `runtime.json::parallel_workers`
  is the source of truth (default 8). The CLI `--workers N` flag
  overrides per-invocation; Phase 3/4 runners read the same config
  knob (ADR-028). Don't hardcode worker counts in code.

## Testing

- Write tests **as you write code**, not at the end of the phase.
- pytest for everything. One test file per module:
  `src/segment_compare/parser.py` → `tests/test_parser.py`.
- Synthetic data generator (Phase 1 deliverable) drives integration tests.
- `examples/sample_a.dat` / `sample_b.dat` are the smallest possible
  end-to-end smoke test — they should be parseable from the moment the
  parser lands.

## Lint / format / type-check

- `black .` for formatting.
- `flake8` for linting (config in `pyproject.toml`).
- `mypy src/` for type checking.
- All three should be clean before commit.

## Commits

- Group commits by logical unit (one module + its tests, or one config
  change). Don't pile unrelated work into a single commit.
- Commit messages: one-line summary, blank line, body if needed. Mention
  the phase: `phase 1: add streaming segment parser`.
- Never commit `results/` outputs, `__pycache__/`, or virtualenvs — the
  `.gitignore` covers these but double-check `git status` before adding.

## Branching

- All Phase 1 setup happens on `claude/segment-comparator-setup-Opl0J`.
- Subsequent feature branches should follow the pattern
  `claude/phase-N-<short-description>`.
- Never push to `main` directly.

## What "done" means for a task

A task is done when:
1. Code is written, typed, and documented.
2. Tests exist and pass.
3. `black`, `flake8`, `mypy` are clean.
4. The relevant phase doc is updated (task checked off).
5. `docs/session-log.md` reflects the new state.
6. Commit is pushed.

## When in doubt

- Re-read the relevant phase doc.
- Re-read `docs/decisions.md` to avoid relitigating decisions.
- Ask the user — cheaper than guessing wrong.
