# Phase 2 — Production scale + field-level config

**Goal:** handle 3M-record files efficiently, and support cross-system
layout differences via field-based normalization.

## Acceptance criteria

1. 3M-record synthetic comparison completes within an agreed throughput
   target (set at Phase 2 kickoff after a Phase 1 baseline measurement).
2. Equal-count partitioning across N workers produces identical output
   to the single-process Phase 1 engine on the same inputs.
3. Field-level normalization config produces identical output to an
   equivalent position-based config on the same inputs.
4. `--workers N` CLI option spawns N processes; default = 1 (Phase 1
   behavior preserved).
5. Optional `--external-sort` path handles unsorted input.
6. Benchmark report committed in `docs/benchmarks/phase-2.md` covering
   wall time, peak memory, throughput across worker counts 1, 2, 4, 8.

## Track A — Performance

### Index-build pass

Same as Phase 1 but the resulting `dict[key, (offset, length)]` is
serialized to a tempfile keyed by key range. Workers load only their
slice.

### Equal-count partitioning

Sort the combined key set (union of both files' keys for the index, then
intersect for the join set). Split into N equal-count chunks. Each
chunk becomes a worker job. This resists skew from non-uniform key
distributions like `CUST00000001`…`CUST09999999`.

### Worker process

- Inputs: file paths, key-range slice, resolved config, output directory,
  worker ID.
- Outputs (per worker, in worker subdir):
  - `matches.{worker_id}.dat`
  - `mismatches.{worker_id}.dat`
  - `keymismatch_A.{worker_id}.dat`
  - `keymismatch_B.{worker_id}.dat`
  - `dups_A.{worker_id}.dat`
  - `dups_B.{worker_id}.dat`
  - `report.{worker_id}.csv`
  - `partial_summary.{worker_id}.json`

### Merge step

Single-process concatenation in key order. Per-worker summaries are
folded into a global `summary.json` (sums + min start time + max end
time + recomputed throughput).

### Optional external sort

If `runtime.json::input_sorted = false`, run an external sort over each
input file before the index-build pass. Use `sort_temp_dir` and the
Python `heapq.merge` pattern over chunked sorted runs.

## Track B — Field-level config

### Config schema (extends Phase 1)

```json
{
  "NM01": {
    "file_a_layout": [
      {"name": "first_name",  "length": 20, "exclude": false},
      {"name": "middle_name", "length": 15, "exclude": true},
      {"name": "last_name",   "length": 15, "exclude": false}
    ],
    "file_b_layout": [
      {"name": "first_name",  "length": 20, "exclude": false},
      {"name": "last_name",   "length": 15, "exclude": false},
      {"name": "middle_name", "length": 15, "exclude": true}
    ]
  }
}
```

The two layouts can differ in field order, field lengths, or which fields
exist. The comparator works on **logical field names**, not byte
positions — so two physically-different layouts with the same logical
schema match.

### `FieldNormalizer`

- Implements the same `Normalizer` protocol as `PositionNormalizer`.
- `normalize(segment_name, raw_data, source)`:
  1. Slice `raw_data` per the per-source layout.
  2. Drop fields where `exclude = true`.
  3. Emit a canonical representation: sort retained fields by logical
     name, concatenate `<name>=<value>` with a separator byte. This is
     what gets hashed.
- The canonical form depends only on logical field names + retained
  values — physical layout differences vanish.

### Config selection

`normalization.json` per segment chooses one of two shapes:

- Position-based (Phase 1 shape: `file_a_strip` / `file_b_strip` /
  `exclude_positions`) → resolves to `PositionNormalizer`.
- Field-based (`file_a_layout` / `file_b_layout`) → resolves to
  `FieldNormalizer`.

A segment may use either form; both can coexist within one config file.

## Benchmarking plan

- Single fixed 3M-record synthetic pair generated once and reused.
- Measurements: wall time, peak RSS, segments/sec, records/sec.
- Workers: 1, 2, 4, 8 (or up to detected CPU count).
- Hash methods: blake2b, builtin.
- Sorted vs unsorted (with external-sort enabled).
- Results committed as a markdown table and a CSV.

## Ordered task list

1. Refactor `pipeline.py` to consume an iterator of
   `(key, record_bytes_a, record_bytes_b)`.
2. Write the index-build pass with key-range slicing.
3. Implement equal-count partitioner.
4. Implement the worker entry point and merge step.
5. Implement `FieldNormalizer` + config dispatch in `config.py`.
6. Implement optional external sort path.
7. Build the 3M synthetic fixture (one-time, persisted under
   `tests/fixtures/`).
8. Run benchmarks, publish report.
