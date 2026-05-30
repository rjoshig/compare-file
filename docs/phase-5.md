# Phase 5 — Parallelism & throughput efficiency

**Status:** planned (not started). Documented now per operator request; do not
start before it is explicitly prioritized.

**Goal:** make the existing parallel engine *efficient at the production 3M-record
scale*. Phase 2 already delivered working parallelism (a multi-process pipeline,
`--workers N`, equal-count key partitioning, external chunk-and-merge sort) — but
the measured speedup was **1.84× at 4 workers** (`docs/benchmarks/phase-2.md`),
short of the original 2.5× aspiration. Phase 5 is about closing that gap, not
introducing parallelism from scratch.

## Context (what exists today)

- `pipeline.run` orchestrates: index-build → dup filter → inner-join iteration →
  compare → write (see `docs/architecture.md`).
- Parallelism is process-based: the key space is partitioned into equal-count
  ranges, each worker processes one range and writes to a worker subdir, then a
  merge step combines outputs. `parallel_workers` is read from `runtime.json`
  (CLI `--workers` overrides).
- Determinism is verified: N=1/2/4 produce identical output
  (`tests/test_parallel.py`).

## Scope (proposed — refine before starting)

1. **Profile first.** Establish where wall time actually goes at 3M records and
   4/8 workers (index build vs sort vs compare vs merge vs IPC/serialization vs
   I/O). No optimization lands without a before/after benchmark.
2. **Reduce per-worker overhead.** Investigate serialization/IPC costs of
   handing partitions to workers; prefer memory-mapped or offset-range reads so
   workers stream their slice directly from the input rather than receiving
   copied data.
3. **Better load balancing.** Equal-*count* partitioning can still skew on
   record *size*/segment-count. Evaluate size-aware partitioning or a
   work-stealing queue so no single worker dominates the tail.
4. **Tune defaults.** Derive sensible `parallel_workers` / chunk-size defaults
   from CPU count and file size; document the knobs.
5. **Optional: lighter merge.** Examine whether the final merge can overlap with
   worker completion (streaming merge) instead of running as a serial tail.

## Out of scope

- Distributed / multi-machine execution.
- Changing output semantics or the comparison algorithm — Phase 5 must preserve
  byte-identical outputs (the `tests/test_parallel.py` determinism guarantee).
- GPU / native extensions.

## Exit criteria (draft)

1. A reproducible 3M-record benchmark harness reports wall time, peak RSS, and
   throughput at 1/2/4/8 workers (extend `docs/benchmarks/phase-2.md`).
2. Measurable speedup improvement over the Phase-2 baseline (target to be set
   from the profiling results — e.g. ≥ 2.5× at 4 workers).
3. Output remains byte-identical to the single-process run at every worker count.
4. `black` / `flake8` / `mypy` clean; new knobs documented; an ADR records the
   approach and the measured trade-offs.
