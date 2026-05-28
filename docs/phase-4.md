# Phase 4 — Scheduled service mode

**Goal:** Airflow/cron triggers comparisons by dropping JSON config files
in a watched directory. Scheduling itself is **out of scope** — an
external scheduler runs the service binary on its own cadence.

## Service contract

**One invocation = one scan of the pending directory.**

```bash
python -m segment_compare.service \
    --watch-dir       /path/to/pending \
    --processing-dir  /path/to/processing \
    --archive-dir     /path/to/archive \
    --results-dir     /path/to/results
```

## Invocation flow

1. Acquire `service.lock` in `--processing-dir`. If already held → exit 0
   (another invocation is already running).
2. Scan `--watch-dir` for `*.json`, sort oldest-first by mtime.
3. For each config file:
   - Validate against the service-config JSON schema.
   - On invalid schema: move to `${archive_dir}/invalid/` with a
     sibling `.error.txt` and continue to the next file.
   - Move to `${processing_dir}/`.
   - Generate run ID: `{timestamp_utc}_{config_basename}`.
   - Call `pipeline.run(...)` with the resolved configs.
   - Send email via `mailx` per the config's `notification` block.
   - Move the config file to `${archive_dir}/{run_id}/config.json`.
   - Symlink or copy the result directory next to the archived config
     for traceability.
4. Files older than 24h in `--watch-dir` raise a stale-config alert
   (logged and emailed to a configured ops address).
5. Release the lock and exit with the worst exit code observed in the
   run loop.

## Service-config schema

```json
{
  "run_name": "daily_reconciliation_2026_05_27",
  "submitted_by": "data_team@company.com",
  "submitted_at": "2026-05-27T14:30:00Z",
  "files": {
    "file_a": {"path": "/data/source_a.dat", "label": "Source A"},
    "file_b": {"path": "/data/source_b.dat", "label": "Source B"}
  },
  "comparison": {
    "segments_to_compare": ["TR01", "NM01", "SC01"],
    "skip_segments": ["SH01"],
    "input_sorted": true
  },
  "normalization_config_ref": "/configs/normalization/standard_v3.json",
  "runtime": {
    "workers": 4,
    "hash_method": "blake2b"
  },
  "notification": {
    "email_to": ["alice@company.com"],
    "email_cc": ["audit@company.com"],
    "send_on_success": true,
    "send_on_failure": true,
    "attach_csv_report": true,
    "include_summary_in_body": true
  },
  "output": {
    "results_dir_override": null,
    "retain_days": 30
  }
}
```

`normalization_config_ref` is a path — service-mode lets ops point at a
versioned config without inlining it. Inline normalization config is
also accepted under `normalization` if both are absent the service's
default `normalization.json` is used.

## Email template (mailx-compatible plain text)

```
Subject: [Segment Compare] {run_name} - {SUCCESS|FAILURE|COMPLETED_WITH_MISMATCHES}

Run: {run_name}
Submitted: {submitted_at}
Completed: {completed_at}
Duration: {duration}
Status: {status}

FILES
  File A: {path} ({record_count} records)
  File B: {path} ({record_count} records)

RESULTS
  Fully matched:     {count} ({percent}%)
  With mismatches:   {count} ({percent}%)
  Only in File A:    {count}
  Only in File B:    {count}
  Duplicates in A:   {count}
  Duplicates in B:   {count}

MISMATCHES BY SEGMENT
  TR01: {count} records
  NM01: {count} records
  ...

OUTPUT FILES
  {path}/matches.dat       ({size})
  {path}/mismatches.dat    ({size})
  {path}/keymismatch_A.dat ({size})
  {path}/keymismatch_B.dat ({size})
  {path}/dups_A.dat        ({size})
  {path}/dups_B.dat        ({size})
  {path}/report.csv        ({size}) [ATTACHED]
  {path}/summary.json      ({size})

CONFIG ARCHIVED
  {archived_path}
```

## Exit codes

```
0   success, no mismatches
1   success, mismatches found
2   completed with warnings (e.g., orphan keys exist, dups present)
10  config validation error
11  input file not found
12  output write error
20  parse error (corrupt input)
30  runtime error
```

Service mode returns the worst code observed across all configs
processed in the scan. A single scan with one good run and one bad
config returns the bad code.

## Locking

- Lock file path: `${processing_dir}/service.lock`.
- Use `fcntl.flock` (POSIX) with `LOCK_EX | LOCK_NB`. On `BlockingIOError`,
  exit 0.
- Lock is released on process exit (file handle close); if the process
  is killed, the OS releases the lock.

## Stale config alerting

- During the scan, if any file's mtime is older than 24h, emit a
  `StaleConfigAlert` log entry and add it to an in-memory list.
- After the scan loop, if the list is non-empty, send a single
  consolidated email to the configured ops address.

## UI integration (extends Phase 3)

- New "Generate Service Config" tab in the UI.
- Same controls as "Run Now", but the submit action writes a JSON file
  (downloadable or POSTed to a Phase-4 endpoint that drops it in the
  pending dir, depending on deployment).

## Ordered task list

1. JSON-schema validator for the service-config format.
2. Lock-file acquisition helper.
3. Pending/processing/archive directory manager.
4. mailx email sender (subprocess call; capture stderr; treat non-zero
   as a notification-failure warning, not a run failure).
5. Stale-config detector.
6. `service.py` main loop.
7. End-to-end test: drop a config file → produce outputs + email +
   archived config.
8. UI tab (depends on Phase 3 being complete).
