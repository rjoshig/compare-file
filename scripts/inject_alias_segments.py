"""One-shot fixture splice: inject AD01 / EM01 / AD01 into the sample files.

Inserts, after each record's first ``NM01`` segment, three segments with
IDENTICAL content on both sides:

    AD01 (postal address, BEFORE EM01 -> stays AD01)
    EM01 (email marker, the alias trigger)
    AD01 (email address, AFTER EM01 -> renamed to EMAD by ADR-034)

Because the inserted bytes are identical between corresponding A/B records,
every record-level match / mismatch / orphan / dup verdict is preserved, so
the oracle counts in tests/test_pipeline.py + tests/test_main.py don't move.
Only summary.json::per_segment gains AD01 / EM01 / EMAD buckets.

The ``ENDS`` segment-count payload is rebuilt to the new segment count (it is
excluded from comparison, but kept truthful for fixture realism).

Run once from the repo root:

    PYTHONPATH=src python scripts/inject_alias_segments.py
"""

from __future__ import annotations

from pathlib import Path

NAME_BYTES = 4
SIZE_BYTES = 3
HEADER = NAME_BYTES + SIZE_BYTES  # size_includes_header=True
DELIM = b"\n"

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _seg(name: str, fields: list[str]) -> bytes:
    """Build one fixed-format segment: name(4) + size(3) + padded data."""
    data = "".join(fields).encode("ascii")
    size = HEADER + len(data)
    return name.encode("ascii") + f"{size:03d}".encode("ascii") + data


# The three inserted segments (constant across all records and both files).
# AD01 layout matches the committed config + tests/synthetic_data.py:
# street(30) + city(15) + state(2) + zip5(5) = 52 data bytes.
AD01_POSTAL = _seg("AD01", ["100 MAIN ST".ljust(30), "NEW YORK".ljust(15), "NY", "10001"])
EM01 = _seg("EM01", ["user@example.com".ljust(40)])
AD01_EMAIL = _seg("AD01", ["PO BOX 9000".ljust(30), "SAN FRANCISCO".ljust(15), "CA", "94105"])
TRIO = AD01_POSTAL + EM01 + AD01_EMAIL


def _split_segments(record: bytes) -> list[bytes]:
    """Walk a record into its raw segment byte-strings."""
    out: list[bytes] = []
    pos = 0
    while pos < len(record):
        size = int(record[pos + NAME_BYTES : pos + HEADER].decode("ascii"))
        out.append(record[pos : pos + size])
        pos += size
    return out


def _rebuild(record: bytes) -> bytes:
    """Insert the trio after the first NM01 and rebuild the ENDS count."""
    segs = _split_segments(record)
    nm01_idx = next(i for i, s in enumerate(segs) if s[:NAME_BYTES] == b"NM01")
    segs[nm01_idx + 1 : nm01_idx + 1] = [AD01_POSTAL, EM01, AD01_EMAIL]

    end_idx = next(i for i, s in enumerate(segs) if s[:NAME_BYTES] == b"ENDS")
    count = len(segs)
    segs[end_idx] = _seg("ENDS", [f"{count:03d}"])
    return b"".join(segs)


def process(path: Path) -> None:
    raw = path.read_bytes()
    records = [r for r in raw.split(DELIM) if r]
    if any(b"AD01" in r for r in records):
        print(f"{path.name}: already injected (AD01 present) — skipping.")
        return
    rebuilt = [_rebuild(r) for r in records]
    path.write_bytes(DELIM.join(rebuilt) + DELIM)
    lens = sorted({len(r) for r in rebuilt})
    print(f"{path.name}: {len(records)} records, new record len(s)={lens}")


def main() -> None:
    for fn in ("sample_a.dat", "sample_b.dat"):
        process(EXAMPLES / fn)


if __name__ == "__main__":
    main()
