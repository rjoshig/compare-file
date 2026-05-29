# How the comparison engine works

A walkthrough of what happens between
`python -m segment_compare --file-a A --file-b B ...` and the eight
output files that land in your output directory. Everything below is
grounded in real bytes from `examples/sample_a.dat` /
`examples/sample_b.dat` so you can copy-paste and verify.

This is the "I want to understand the engine before trusting it"
document. ADRs in `decisions.md` cover the *why* of individual
choices; this document covers the *how* end-to-end.

> **Heads-up (ADR-033):** Some snippets below still reference the
> legacy `segments.json` + `normalization.json` config files and the
> position-based `exclude_positions` form. The current engine uses
> two per-file layouts (`config/layout_file_A.json` +
> `config/layout_file_B.json`) plus `config/runtime.json`; the
> equivalent of `exclude_positions` is a per-field `"exclude": true`
> in the layout. The byte-level pipeline (parse → index → normalize
> → hash → compare) described here is unchanged.
>
> **Output-layout update (ADR-035 / ADR-036 / ADR-037 / ADR-038):**
> The run now produces **13 output files** (not 8). Three were added
> after the original draft of this document: ``compare_reports.csv``
> and ``compare_reports.html`` (ADR-035, with the HTML overhaul in
> ADR-036 adding side-by-side layouts, file-linked aggregate counts,
> a per-key sample, and a Description column with small-font prose
> under each metric) and ``keys_mismatch_matrix.csv`` (ADR-036,
> per-key Y/N matrix). Each run lands in its own
> ``report-YYYY-MM-DD-HH-MM-SS`` subdirectory; files inside use bare
> names (ADR-037). ``matches.dat`` is now capped at 10 records
> (ADR-038); ``mismatches.dat`` carries the full set as before.

---

## TL;DR

Two large fixed-format files come in, 13 output files come out (in
a per-run subdir). In between:

1. **Parse** — both files are streamed end-to-end; each record's
   bytes are sliced into segments (e.g., `TU4R`, `NM01`, `TR01`,
   `ENDS`). The record's key (12 bytes of the `TU4R` segment) lets
   us identify "the same record" across A and B.
2. **Index** — build a `dict[key → (byte_offset, byte_length)]` for
   each file. Duplicate keys are pulled out into `dups_*.dat`. Keys
   present in only one file are pulled out into `keymismatch_*.dat`.
3. **Compare** — for each key present in both files, normalize each
   segment's bytes (drop fields the user marked "exclude"), hash the
   result, group hashes per segment type into a `Counter`, and
   compare counters. Equal counters → records match. Different
   counters → records mismatch on that segment type.
4. **Write** — matched record bytes go to `matches.dat`; mismatched
   records get a side-by-side block in `mismatches.dat` plus one row
   per mismatched segment type in `report.csv`; aggregates land in
   `summary.json`.

Per-record cost is **O(n)** where n is the number of segments in
the record. Comparison correctness rests on cryptographic-strength
hashing (blake2b, 128-bit) with a per-3M-records collision
probability of ~10⁻²⁶ — practically zero.

---

## The flow at a glance

```
                  ┌──────────────────────┐
                  │   ResolvedConfig     │
                  │  (segments.json,     │
                  │   normalization.json,│
                  │   runtime.json)      │
                  └──────────┬───────────┘
                             │
                             ▼
  ┌─ File A ─┐         ┌──────────────────────┐         ┌─ File B ─┐
  │   .dat   │ ──────► │   pipeline.run /     │ ◄────── │   .dat   │
  └──────────┘         │   pipeline.run_parallel        └──────────┘
                       └──────────┬───────────┘
              ┌───────────────────┼───────────────────┐
              ▼                   ▼                   ▼
        parser.py           normalizer.py         comparator.py
       (stream parse)     (position OR field   (Counter-of-hashes
       record-by-record   based; drops bytes/  multiset comparison)
       keyed by TU4R)     fields marked
                          exclude)
                                  │
                                  ▼
                            hasher.py
                       (blake2b 128-bit
                       default; builtin
                       64-bit available)
                                  │
                                  ▼
                            writer.py
                       (8 output files,
                       timestamped names)
```

---

## Sample file format

Every record is a sequence of variable-length segments, framed by a
`TU4R` segment (the key) at the start and an `ENDS` segment at the
end. Records are separated by `\n`.

Every segment header is **7 bytes**:

```
T U 4 R 0 3 0 D A T A K E Y 0 0 0 0 0 0 0 0 1 P O S N Y C 1
└─name─┘ └size┘ └────────── 23 bytes of data ──────────────┘
                 (parsed per the per-segment data layout)
```

`TU4R` is the segment name (4 bytes). `030` is the ASCII-encoded
total segment length in bytes (3 bytes). Everything after that is
the segment's data (`size - 7 = 23` bytes in this case).

The first 12 bytes of `TU4R`'s data **after the literal `DATA`
prefix** is the record's **key** — the value the engine uses to
align records across File A and File B. In the example above the
key is `KEY000000001`. The key range is configurable
(`config/segments.json::key_range`).

`ENDS` terminates a record. Anything after `ENDS` (until the next
`\n`) is invalid. In the realistic fixture `ENDS` carries 3 bytes
of payload (an informational segment count) that is excluded from
comparison via `normalization.json`.

### A real record, byte-for-byte

Here is `KEY000000001` from `examples/sample_a.dat`, all **417 bytes**
on the wire (newline not shown):

```
TU4R030DATAKEY000000001POSNYC1
SH0103501NYY020305                 
NM01057ALICE               MARIE          ANDERSON       
TR01050A1111111  ABCBANK 2000 4000TXNREF0001      
TR01050A2222222  ABCBANK 2100 4100TXNREF0002      
TR01050A3333333  ABCBANK 2200 4200TXNREF0003      
SC01034+340020103                 
SC01034+740022103                 
CL01067PUBL  ABC. 20250709 I                                       
ENDS010010
```

10 segments. Note `TR01` repeats three times — that's the engine's
"multiset of hashes per segment type" job to compare correctly even
when File B happens to emit the three `TR01`s in a different order.

---

## Step 1 — Parse

`src/segment_compare/parser.py` does streaming byte parsing. The two
public functions:

- **`iter_segments(stream, parser_cfg)`** yields raw `Segment`
  objects one at a time.
- **`iter_records(stream, parser_cfg, segments_cfg)`** groups
  segments between `TU4R` (key segment) and `ENDS` (terminator)
  into a `Record`.

The parser is **streaming**: nothing more than one record plus a
small header buffer is held in memory. For the 3M-record / 1.3 GiB
production fixture the parser uses about 5 MiB of RAM during its
pass.

What the parser produces for the example record above:

```python
Record(
    key="KEY000000001",
    segments=(
        Segment(name="TU4R", size=30, data=b"DATAKEY000000001POSNYC1",     offset=0),
        Segment(name="SH01", size=35, data=b"01NYY020305...",              offset=30),
        Segment(name="NM01", size=57, data=b"ALICE   ...ANDERSON       ",  offset=65),
        Segment(name="TR01", size=50, data=b"A1111111  ABCBANK ...0001",   offset=122),
        Segment(name="TR01", size=50, data=b"A2222222  ABCBANK ...0002",   offset=172),
        Segment(name="TR01", size=50, data=b"A3333333  ABCBANK ...0003",   offset=222),
        Segment(name="SC01", size=34, data=b"+340020103                 ", offset=272),
        Segment(name="SC01", size=34, data=b"+740022103                 ", offset=306),
        Segment(name="CL01", size=67, data=b"PUBL  ABC. 20250709 I  ...",  offset=340),
        Segment(name="ENDS", size=10, data=b"010",                         offset=407),
    ),
    raw=b"TU4R030DATAKEY000000001...ENDS010010",
    offset=0,
    length=418,  # 417 bytes record + 1 byte newline delimiter
)
```

Note: the parser never decodes the data — it keeps it as `bytes`.
Decoding to text is the job of normalization (and only for output
formatting). The hash comparison runs on raw bytes.

### What the parser rejects

The parser raises a clear `ParseError(offset, message)` for every
documented corruption mode:

| Corruption                            | Error message contains      |
|---------------------------------------|-----------------------------|
| Truncated segment header (< 7 bytes)  | `truncated`                 |
| Size field not an ASCII integer       | `ASCII integer`             |
| Declared size < 7 bytes               | `smaller than header`       |
| Stream ends before declared data      | `EOF`                       |
| Record doesn't start with `TU4R`      | `must start with`           |
| Record never terminates with `ENDS`   | `terminator` / `EOF`        |
| Wrong byte after `ENDS` (not `\n`)    | `delimiter`                 |
| Key extraction reads beyond data      | `key_range`                 |

The offset in every error message points to the exact byte where the
problem was detected, which is more useful than "parse failed".

---

## Step 2 — Index

`pipeline._index_file` streams the file end-to-end and builds:

```python
good_index    : dict[str, tuple[int, int]]  # key → (offset, length)
dup_offsets   : dict[str, list[tuple[int, int]]]  # duplicate keys
total_records : int
segment_counts: Counter[str]  # how many of each segment type seen
```

For `examples/sample_a.dat` the resulting `good_index` (after dups
are pulled out) has 8 entries (KEY000000001 through KEY000000011,
minus the duplicate KEY000000008). `dup_offsets` has one entry,
KEY000000008, pointing to both occurrences.

`good_index` is what makes the inner join cheap. To find
"KEY000000004's record bytes", the engine does:

```python
off, length = good_index["KEY000000004"]
fh.seek(off)
raw = fh.read(length)
```

No re-scan of the file from the beginning. This is the
streaming-with-offset-index design from **ADR-018**.

---

## Step 3 — Determine the comparison sets

Three set operations on the two indexes:

```python
keys_a = set(index_a)         # keys in A's good index
keys_b = set(index_b)         # keys in B's good index
only_a_keys = sorted(keys_a - keys_b)
only_b_keys = sorted(keys_b - keys_a)
both_keys   = sorted(keys_a & keys_b)
```

Keys in `only_a_keys` → records to `keymismatch_A.dat`. Keys in
`only_b_keys` → `keymismatch_B.dat`. Keys in `both_keys` go to the
inner-join loop. Keys in the dup map for either file → `dups_A.dat`
or `dups_B.dat` (ADR-019).

For the realistic fixture this produces:

| Bucket           | Keys                                       | Count |
|------------------|--------------------------------------------|-------|
| both_keys        | KEY...01, 02, 03, 04, 05, 10, 11           | 7     |
| only_a_keys      | KEY...06                                   | 1     |
| only_b_keys      | KEY...07, KEY...12                         | 2     |
| dups_in_a        | KEY...08 (×2)                              | 2     |
| dups_in_b        | KEY...09 (×2)                              | 2     |

---

## Step 4 — Compare each joined key

For every key in `both_keys`, the engine reads A's record bytes and
B's record bytes (via the indexes), parses both into `Record`
objects, and runs `comparator.compare_records(rec_a, rec_b,
normalizer, hasher)`.

### Step 4a — Normalize each segment

A segment's raw data may contain bytes that shouldn't influence
match/mismatch decisions:

- **Timestamps** that differ across runs even when the content is
  the same.
- **System-specific filler** that A's source system emits but B's
  doesn't (or vice versa).
- **Padding** that one side uses and the other doesn't.

Normalization removes those bytes before hashing. Two strategies are
supported:

#### Position-based normalization (Phase 1)

Configured per segment as byte ranges to drop. For our `CL01`
segment, the relevant entry in `config/normalization.json` is:

```json
"CL01": {
  "file_a_strip": [],
  "file_b_strip": [],
  "exclude_positions": [[11, 19]]
}
```

That `[[11, 19]]` means "drop bytes 11 through 18 of the data area
(end-exclusive)". For `CL01`, bytes 11–18 of the data are the
8-byte timestamp. So:

```
Before normalization (A's CL01 for KEY...10):
  "PUBL  ABC. 20250101 I" + 39 spaces

Before normalization (B's CL01 for KEY...10):
  "PUBL  ABC. 20250709 I" + 39 spaces

After exclude_positions [[11, 19]]:
  A: "PUBL  ABC.  I" + 39 spaces
  B: "PUBL  ABC.  I" + 39 spaces
```

Both A and B produce the **same byte string** after normalization.
The hash of that string is identical → engine reports a match.
Without the exclude rule, A and B's `CL01` would mismatch on the
timestamp every run.

#### Field-based normalization (Phase 2)

For cross-system reconciliation — when A and B's data come from
different sources that emit fields in different order, different
widths, or with extra trailing filler — describe the segment as a
named-field layout per file:

```json
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
```

The two layouts can disagree in **field order**, **per-field
length**, or even **field count** (one side may carry trailing
filler that the other side doesn't). The field normalizer slices
each side per its own layout, drops `exclude: true` fields, then
emits a canonical sorted `name=value` byte string:

```
A's NM01 data: "ALICE               " + "MARIE          " + "ANDERSON       "
  → after field slicing: first_name=ALICE..., middle_name=MARIE..., last_name=ANDERSON...
  → drop middle_name (exclude=true)
  → sort by name, encode:
       first_name=ALICE               \x1Flast_name=ANDERSON       

B's NM01 data: "ALICE               " + "ANDERSON       " + "MARIE          "
  → after field slicing: first_name=ALICE..., last_name=ANDERSON..., middle_name=MARIE...
  → drop middle_name
  → sort by name, encode:
       first_name=ALICE               \x1Flast_name=ANDERSON       
```

Byte-identical canonical forms despite the different *physical*
layout. ADR-029 has the design details.

Both normalizers implement the same `Normalizer` protocol:
`normalize(segment_name, raw_data, source) -> bytes`. The pipeline
uses a `CompositeNormalizer` that dispatches per segment based on
which form the config defines.

### Step 4b — Hash each normalized segment

The hasher takes the post-normalization bytes and returns either a
16-byte digest (blake2b) or a 64-bit integer (builtin). The
comparator doesn't care which — it only checks if hashes are equal.

See [The hash function](#the-hash-function) below for the trade-offs.

### Step 4c — Per-segment-type multiset comparison

This is the core of the comparator. For each segment **type** that
appears in either record:

```python
counter_a = Counter(hash(normalize(s)) for s in record_a if s.name == segment_type)
counter_b = Counter(hash(normalize(s)) for s in record_b if s.name == segment_type)
if counter_a == counter_b:
    # this segment type matches
else:
    # mismatch
```

For our realistic example with three `TR01`s in both files:

```
A's TR01 instances:                B's TR01 instances (reordered):
  TR01[0] = "A1111111..."            TR01[0] = "A2222222..."
  TR01[1] = "A2222222..."            TR01[1] = "A3333333..."
  TR01[2] = "A3333333..."            TR01[2] = "A1111111..."

After hashing each:
  hash("A1111111...") = h1            hash("A2222222...") = h2
  hash("A2222222...") = h2            hash("A3333333...") = h3
  hash("A3333333...") = h3            hash("A1111111...") = h1

Counter for A: {h1: 1, h2: 1, h3: 1}
Counter for B: {h2: 1, h3: 1, h1: 1}

Counter equality is order-independent.   counter_a == counter_b → MATCH
```

If A has 3 `TR01`s and B has 2 `TR01`s (the count-mismatch
scenario), the counters differ in size:

```
Counter for A: {h1: 1, h2: 1, h3: 1}
Counter for B: {h1: 1, h2: 1}

counter_a != counter_b → MISMATCH (status: count_diff, a_count=3, b_count=2)
```

The verdict struct records exactly that:

```python
SegmentVerdict(segment_name="TR01", matched=False, a_count=3, b_count=2)
# .status property derived: "count_diff" (a_count != b_count) or
# "content_diff" (counts match but bytes don't)
```

### Step 4d — Aggregate to record-level verdict

A `Record` matches overall iff **every** segment type's counter
matches. Even one segment-type mismatch makes the whole record a
mismatch:

```python
RecordVerdict(
    key="KEY000000005",
    matched=False,
    segment_verdicts=(
        SegmentVerdict("TU4R", matched=True,  ...),
        SegmentVerdict("SH01", matched=True,  ...),
        SegmentVerdict("NM01", matched=True,  ...),
        SegmentVerdict("TR01", matched=False, a_count=4, b_count=3),   # ← the offender
        SegmentVerdict("SC01", matched=True,  ...),
        SegmentVerdict("CL01", matched=True,  ...),
        SegmentVerdict("ENDS", matched=True,  ...),  # exclude rule drops the count
    ),
)
```

This verdict is what the writer turns into one row per mismatched
segment type in `report.csv` and a side-by-side block in
`mismatches.dat`.

---

## Step 5 — Write outputs

The writer (`writer.py`) emits 13 output files per run. Successive
runs land in their own ``report-YYYY-MM-DD-HH-MM-SS`` subdirectories
under ``--output-dir`` (ADR-037, supersedes ADR-027's
filename-stamping rule), so files inside use bare names
(``matches.dat``, ``summary.json``, ``compare_reports.html``, …).

| Output file              | Contents                                                 |
|--------------------------|----------------------------------------------------------|
| `matches.dat`            | File A's raw bytes for every matched record              |
| `mismatches.dat`         | `=== KEY: ... ===` + side-by-side A/B blocks             |
| `keymismatch_A.dat`      | A's record bytes for keys only in A                      |
| `keymismatch_B.dat`      | B's record bytes for keys only in B                      |
| `dups_A.dat`             | All occurrences of duplicate-keyed records in A          |
| `dups_B.dat`             | Same for B                                               |
| `report.csv`             | One row per mismatched segment type per record           |
| `summary.json`           | Aggregates, timings, audit hash, engine version          |

For matched records, only A's bytes are written to `matches.dat` —
the records are *equivalent after normalization*, so emitting both
copies would double the file size for no information gain (ADR-010).

---

## The hash function

The engine supports two hash functions, chosen at runtime via
`config/runtime.json::hash_method`.

### `blake2b` (default)

Cryptographic-grade hash from Python's `hashlib`. Configured with
`digest_size = 16` (128-bit output).

```python
import hashlib
def hash_blake2b(data: bytes) -> bytes:
    return hashlib.blake2b(data, digest_size=16).digest()
```

Why 128 bits and not 256?
- The comparison is in-memory only — we don't persist hashes, so
  the per-record bandwidth cost matters.
- 128 bits gives a collision probability at 3M records of about
  `(3 × 10⁶)² / (2 × 2¹²⁸) ≈ 1.3 × 10⁻²⁶`. Even at trillion-
  record scale, you'd need many universes of trials before a
  collision was likely.
- Going to 256 bits doubles the per-hash bytes for ~10⁻⁵⁰
  collision probability. Not worth the bandwidth on the per-record
  Counter comparisons.

blake2b is the **only safe choice for multi-worker (Phase 2) runs**
because it's deterministic across processes.

### `builtin`

Python's builtin `hash()` function, called on the bytes:

```python
def hash_builtin(data: bytes) -> int:
    return hash(data)
```

This is **fast** — it's a few CPU cycles per call versus the few
hundred for blake2b. But:

- **64 bits.** Collision probability at 3M records is about
  `(3 × 10⁶)² / (2 × 2⁶⁴) ≈ 2.4 × 10⁻⁷` — roughly 1 in 4 million.
  Tolerable for one-shot single-process runs; rolling the dice if
  you're running many comparisons a day.
- **PYTHONHASHSEED is randomized per process.** Two different
  Python processes hashing the same bytes get different results.
  In Phase 2's parallel pipeline this would produce **wrong
  mismatches** across worker boundaries (a record A worker 1 sees
  as `hash=H1` would be `hash=H1'` if it landed on worker 2). The
  config validator does not enforce this — if you select
  `hash_method = "builtin"` and `parallel_workers > 1`, the
  pipeline will run but may produce nonsense. ADR-002 documents
  this constraint.

**Recommendation:** use `blake2b` unless you have a specific reason
to switch and have proven it doesn't break correctness for your
workload.

---

## Reliability — is it 100% reliable?

**It is reliable to a degree that exceeds every other source of
error in the system.**

Here's the math, applied to a 3-million-record production run.

### Where hash collisions could cause a false match

Two segments would be incorrectly reported as matching iff they
have *different* normalized bytes but produce the *same* hash. For
the comparison to silently disagree with reality:

1. Two normalized byte strings that differ must collide.
2. The collision must happen for a segment type *within the same
   record*, where the count of hashes is identical between A and B.
3. The collision must not affect the count, so even the count-
   based mismatch detection misses it.

That's an extremely narrow window. For random-looking byte content
(which production data closely resembles), blake2b at 128 bits has
a birthday-bound collision probability of about:

```
P(any collision in N hashes) ≈ N² / (2 × 2^bits)
```

| Records | blake2b (128-bit) | builtin (64-bit) |
|--------:|------------------:|-----------------:|
|       1k|        2.9 × 10⁻³⁴|       2.7 × 10⁻¹⁴|
|      1M|        2.9 × 10⁻²⁸|       2.7 × 10⁻⁸ |
|      3M|        1.3 × 10⁻²⁶|       2.4 × 10⁻⁷ |
|    100M|        1.5 × 10⁻²³|       2.7 × 10⁻⁴ |

For blake2b, even at 100M records the chance of *any* collision is
about 10⁻²³ — far less likely than cosmic rays flipping a bit in
RAM (~10⁻¹⁵ per byte per year). The comparison engine is *not* the
weak link.

For builtin hash at 100M records the picture is different — about
1 in 4000 runs would have a hash collision. **This is the reason
blake2b is the default.**

### What's NOT covered by the hash analysis

The math above assumes the hash function is well-distributed.
blake2b is — it's a NIST-vetted cryptographic primitive.

What it doesn't address:

- **Bugs in normalization config.** If you write a position rule
  that drops the wrong bytes, the engine will faithfully compare
  the bytes you told it to compare and miss real mismatches. The
  field-level integration tests
  (`tests/test_field_integration.py`) and the
  `test_run_against_sample_files_matches_oracle` integration test
  guard against drift in *the engine*, but they can't guard against
  config typos. Read the audit hash (`config.audit_hash` in
  `summary.json`) to confirm you ran with the config you think you
  ran with.
- **Bugs in the parser.** A subtly-malformed input that the parser
  accepts but mis-segments would lead to wrong hashes. The parser
  tests cover every documented corruption mode but can't enumerate
  every possible malformation. If you have a suspicion the parser
  is misreading a specific segment type, the smallest reproduction
  is one record into `iter_segments()` and inspect the output.
- **Bugs in the comparator's per-segment-type grouping.** Covered
  by `tests/test_comparator.py`.
- **Single-bit RAM errors.** Outside the engine's control. ECC RAM
  is the answer.

### One number to remember

For a 3M-record comparison with blake2b: the probability the engine
silently produces a wrong match because of a hash collision is
~10⁻²⁶. The probability your build server has a single-bit memory
error during the same run is ~10⁻¹². **The hashing is 14 orders of
magnitude more reliable than the hardware running it.**

---

## Why O(n) per record (not O(n²))

The naive way to compare two records that contain repeating
segments (3 `TR01`s in A, 3 `TR01`s in B in some other order) is:

```
for each TR01 in A:
    for each TR01 in B:
        if A.TR01 == B.TR01:
            ...
```

That's **O(n × m)** where n is A's TR01 count and m is B's. With
ordering it's even worse — you have to track which B-segments are
"used" so the same B-segment doesn't double-match.

The hash-and-Counter trick:

```
counter_a = Counter(hash(s) for s in A's TR01s)   # O(n) hash + insert
counter_b = Counter(hash(s) for s in B's TR01s)   # O(m) hash + insert
counter_a == counter_b                            # O(min(n, m)) dict compare
```

**Total: O(n + m) per segment type.** Hashing is the dominant cost
and it's linear in input size. Counter equality is a dict
comparison — also linear in size of the counters.

Across the whole record, with s segment types each appearing some
constant number of times, total per-record work is **O(s)** ≈ O(n)
where n is the number of segments in the record. For our realistic
record (10 segments), this is 10 hashes + 10 counter operations
versus a worst-case 3 × 3 = 9 cross-comparisons just for the `TR01`
group. The hash approach wins clearly even at our small scale, and
the gap widens as repeating-segment counts grow.

Across the whole file with N records:

| Approach                         | Total work        |
|----------------------------------|-------------------|
| Naive (no index, no hash)        | O(N²)             |
| Sort-merge (no hash)             | O(N log N + N·n²) |
| **Sort-merge + hash multiset**   | **O(N log N + N·n)** ← us |

Where `N log N` is the per-file sort/index step (one-time) and `N·n`
is the comparison phase. At 3M records and ~10 segments per record
that's ~30M hash operations — about 3 seconds on modern hardware
for blake2b.

ADRs **ADR-001** and **ADR-003** discuss this design choice.

---

## End-to-end worked example

Let's trace `KEY000000010` from the realistic fixture through the
full pipeline. This record is a *match* — but only after
normalization, because the CL01 timestamp differs between A and B.

### Input bytes

```
A's record (from sample_a.dat):
  TU4R030DATAKEY000000010POSNYC1
  SH0103501NYY020305                 
  NM01057IRENE               S              TAYLOR         
  TR01050A1111111  ABCBANK 2000 4000TXNREF0001      
  TR01050A2222222  ABCBANK 2100 4100TXNREF0002      
  TR01050A3333333  ABCBANK 2200 4200TXNREF0003      
  SC01034+340020103                 
  SC01034+740022103                 
  CL01067PUBL  ABC. 20250101 I                                       
  ENDS010010

B's record (from sample_b.dat):
  TU4R030DATAKEY000000010POSNYC1
  SH0103501NYY020305                 
  NM01057IRENE               S              TAYLOR         
  TR01050A1111111  ABCBANK 2000 4000TXNREF0001      
  TR01050A2222222  ABCBANK 2100 4100TXNREF0002      
  TR01050A3333333  ABCBANK 2200 4200TXNREF0003      
  SC01034+340020103                 
  SC01034+740022103                 
  CL01067PUBL  ABC. 20250709 I                                       
  ENDS010010
```

The only difference is the 8-byte timestamp in `CL01`:
`20250101` vs `20250709`. Without normalization the byte-level
comparison would mismatch.

### Per-segment processing

| Segment | A's data (after normalization) | B's data (after normalization) | A hash | B hash | Match? |
|---------|-------------------------------|-------------------------------|--------|--------|:------:|
| `TU4R`  | `DATAKEY000000010POSNYC1`     | (same)                        | `h_tu` | `h_tu` | ✓      |
| `SH01`  | `01NYY020305     ...`         | (same)                        | `h_sh` | `h_sh` | ✓      |
| `NM01`  | `IRENE...S...TAYLOR...`       | (same)                        | `h_nm` | `h_nm` | ✓      |
| `TR01[0]` | `A1111111...`               | (same)                        | `h_t1` | `h_t1` | ✓      |
| `TR01[1]` | `A2222222...`               | (same)                        | `h_t2` | `h_t2` | ✓      |
| `TR01[2]` | `A3333333...`               | (same)                        | `h_t3` | `h_t3` | ✓      |
| `SC01[0]` | `+340020103...`             | (same)                        | `h_s1` | `h_s1` | ✓      |
| `SC01[1]` | `+740022103...`             | (same)                        | `h_s2` | `h_s2` | ✓      |
| `CL01`  | `PUBL  ABC.  I` + 39 spaces (timestamp excluded `[11, 19)`) | `PUBL  ABC.  I` + 39 spaces (same after exclude) | `h_cl` | `h_cl` | ✓ |
| `ENDS`  | `` (3-byte segment-count excluded) | `` (same)                | `h_e`  | `h_e`  | ✓      |

### Per-segment-type counters

| Segment type | Counter for A          | Counter for B          | Equal? |
|--------------|-----------------------|-----------------------|:------:|
| `TU4R`       | `{h_tu: 1}`           | `{h_tu: 1}`           | ✓      |
| `SH01`       | `{h_sh: 1}`           | `{h_sh: 1}`           | ✓      |
| `NM01`       | `{h_nm: 1}`           | `{h_nm: 1}`           | ✓      |
| `TR01`       | `{h_t1: 1, h_t2: 1, h_t3: 1}` | (same) | ✓      |
| `SC01`       | `{h_s1: 1, h_s2: 1}`  | `{h_s1: 1, h_s2: 1}`  | ✓      |
| `CL01`       | `{h_cl: 1}`           | `{h_cl: 1}`           | ✓      |
| `ENDS`       | `{h_e: 1}`            | `{h_e: 1}`            | ✓      |

Every segment type's counter equals → **records match** → A's bytes
written to `matches.dat`. No row in `report.csv` for this key.

### The `KEY000000005` mismatch case (TR01 count differs)

In `sample_a.dat`, `KEY000000005` has **4 TR01s** (4444444 is the
extra one). In `sample_b.dat` it has **3 TR01s**.

After hashing:

```
Counter for A's TR01: {h_t1: 1, h_t2: 1, h_t3: 1, h_t4: 1}
Counter for B's TR01: {h_t1: 1, h_t2: 1, h_t3: 1}

Counters unequal → segment-type mismatch
Status: count_diff (a_count = 4, b_count = 3)
```

`report.csv` gets:
```
KEY000000005,TR01,count_diff,4,3
```

`mismatches.dat` gets a `=== KEY: KEY000000005 | MISMATCH: TR01 ===`
block with A's full record + B's full record side-by-side.

---

## What can go wrong (and what the engine does about it)

| Condition                              | What the engine does                                  |
|----------------------------------------|--------------------------------------------------------|
| Malformed segment (any of 7 patterns)  | `ParseError` with byte offset; CLI exit 20             |
| Missing input file                     | `InputFileError`; CLI exit 11                          |
| Bad config (any field)                 | `ConfigError` with field path; CLI exit 10             |
| Output write failure                   | `WriteError`; CLI exit 12                              |
| Duplicate key in A                     | All occurrences → `dups_A.dat`; excluded from join     |
| Duplicate key in B                     | All occurrences → `dups_B.dat`; excluded from join     |
| Key only in A                          | Record → `keymismatch_A.dat`; counted in summary       |
| Key only in B                          | Record → `keymismatch_B.dat`; counted in summary       |
| Field-layout length ≠ data length      | `ValueError` from `FieldNormalizer`; pipeline aborts   |
| Per-segment count mismatch (3 vs 2)    | report.csv row `count_diff,3,2`; record → mismatches   |
| Per-segment content mismatch           | report.csv row `content_diff`; record → mismatches     |
| Whole record matches after normalization | A's bytes → matches.dat                              |

Three exit-code priorities (when multiple conditions hold):

1. Mismatches > 0   → exit 1
2. Orphans or dups > 0 → exit 2
3. Otherwise → exit 0

Plus the error codes (10–30) for the failure conditions above.

---

## Try it yourself

The realistic fixture is committed; the engine produces predictable
output:

```bash
# 1. From the repo root, activate the venv (pyenv 3.12.7):
source .venv/bin/activate

# 2. Run the engine against the committed sample files:
python -m segment_compare \
    --file-a examples/sample_a.dat \
    --file-b examples/sample_b.dat \
    --config-dir config/ \
    --output-dir results/

# Expected stdout:
#   done in 0.XXXs: matched=4, mismatched=3, only_a=1, only_b=2, dups_a=2, dups_b=2

# 3. Inspect what landed:
ls results/
#   dups_A_<stamp>.dat  dups_B_<stamp>.dat
#   keymismatch_A_<stamp>.dat  keymismatch_B_<stamp>.dat
#   matches_<stamp>.dat  mismatches_<stamp>.dat
#   report_<stamp>.csv  summary_<stamp>.json

# 4. The mismatch report:
cat results/report_*.csv
#   key,segment_name,status,a_count,b_count
#   KEY000000003,NM01,content_diff,1,1
#   KEY000000004,TR01,content_diff,3,3
#   KEY000000005,TR01,count_diff,4,3
```

To see the parallelism in action:

```bash
# Single-process baseline
python -m segment_compare --workers 1 ...

# Default (8 workers from config)
python -m segment_compare ...

# Explicit 4 workers
python -m segment_compare --workers 4 ...

# Unsorted input (engine sorts first)
python -m segment_compare --external-sort ...
```

All four invocations produce byte-identical `*.dat` outputs and the
same exit code; only the timing differs.

For a 3M-record stress test against the synthetic fixture:

```bash
python -c "
from pathlib import Path
from tests.synthetic_data import generate_pair
generate_pair(3_000_000, 42, Path('tests/fixtures'))
"

python -m segment_compare \
    --file-a tests/fixtures/synth_003000000_seed42_a.dat \
    --file-b tests/fixtures/synth_003000000_seed42_b.dat \
    --config-dir config/ \
    --output-dir results-3m/
```

Expect ~125 s at 4 workers, ~107 s at 8 workers. See
`docs/benchmarks/phase-2.md` for the speedup curve.

---

## Where to read further

| Question                                          | Doc                              |
|---------------------------------------------------|----------------------------------|
| What's the high-level architecture?               | `docs/architecture.md`           |
| What was decided (and why) at each turn?          | `docs/decisions.md` (30 ADRs)    |
| What does each phase aim for?                     | `docs/phase-plan.md`             |
| What changed in the last session?                 | `docs/session-log.md`            |
| What's the format of the sample files exactly?    | `examples/README.md`             |
| How fast is it really?                            | `docs/benchmarks/phase-2.md`     |
| What's in `summary.json`?                         | `summary.json` fields are        |
|                                                   | the public Phase 1 contract;     |
|                                                   | see `writer.Summary` dataclass.  |

For the deep dive on individual hot points:

| ADR    | Topic                                                        |
|--------|--------------------------------------------------------------|
| 001    | Hash-based multiset comparison beats pairwise O(n²)          |
| 002    | blake2b default, builtin opt-in                              |
| 009    | Exclude removes bytes (not masks them)                       |
| 010    | matches.dat = A's bytes only; mismatches.dat = side-by-side  |
| 017    | Run reproducibility via config audit hash                    |
| 018    | Streaming + key→offset index design                          |
| 019    | Duplicate keys → dedicated dup files                         |
| 024    | Comparator iterator interface (Phase 2 parallelization seam) |
| 027    | Timestamped output filenames                                 |
| 028    | Configurable parallelism via `runtime.json`                  |
| 029    | Field-based canonical form + dispatch                        |
| 030    | External chunk-and-merge sort                                |

---

## One paragraph if you only read one paragraph

The engine streams both input files, builds a `key → file_offset`
index per file, walks the intersection of keys in sorted order, and
for each shared key reads both records' bytes, normalizes each
segment (drops bytes/fields the config marks as "exclude"), hashes
each normalized segment, groups hashes per segment type into a
`Counter`, and declares a match iff every segment type's two counters
are equal. Hashing is blake2b 128-bit by default — collision
probability at 3M records is ~10⁻²⁶, which is fourteen orders of
magnitude smaller than a single-bit RAM error during the same run.
Per-record cost is **O(n)** in the number of segments; total cost is
**O(N log N + N·n)** across N records. Eight output files capture
every kind of result the engine can produce — matched records,
mismatched records (side-by-side + per-segment report rows), orphan
keys (only in A or only in B), duplicate-keyed records, the CSV
report, and a `summary.json` with the run's audit hash so you can
prove later that "the run was correct" means "the run was correct
*with this exact config*."
