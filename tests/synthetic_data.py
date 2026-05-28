"""Synthetic A/B fixture generator for Phase 2 benchmarking.

Produces a deterministic pair of fixed-format data files that mirror the
realistic ``examples/sample_*.dat`` layout (TU4R / SH01 / NM01 / AD01 /
TR01 × {2,3,4} / SC01 × 2 / CL01 / ENDS) and cover every Phase 1
scenario in proportions tuned for production realism (most records
match; a small minority exercise mismatch / orphan / duplicate /
exclude paths).

The generator is **not** a pytest fixture in the conftest sense — it
writes physical files to ``tests/fixtures/`` so benchmarks and
acceptance tests can mmap or stream them without redundant
regeneration. The output directory is gitignored.

API:
    paths, counts = generate_pair(
        num_records=3_000_000,
        seed=42,
        out_dir=Path("tests/fixtures"),
    )
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Segment builders (mirror the realistic-fixture format in examples/sample_*.dat)
# ---------------------------------------------------------------------------


def _seg(name: str, data: str) -> bytes:
    """Build a segment with the size field auto-computed and encoded ASCII."""
    total = 7 + len(data)
    out = f"{name}{total:03d}{data}"
    assert len(out) == total
    return out.encode("ascii")


def _tu4r(key: str, trailer: str = "POSNYC1") -> bytes:
    assert len(key) == 12
    assert len(trailer) == 7
    return _seg("TU4R", "DATA" + key + trailer)


def _sh01() -> bytes:
    return _seg("SH01", "01NYY020305" + " " * 17)


def _nm01(first: str, middle: str, last: str) -> bytes:
    data = first.ljust(20)[:20] + middle.ljust(15)[:15] + last.ljust(15)[:15]
    return _seg("NM01", data)


def _ad01(street: str, city: str, state: str, zip_code: str) -> bytes:
    assert len(state) == 2
    assert len(zip_code) == 5
    data = street.ljust(30)[:30] + city.ljust(15)[:15] + state + zip_code
    return _seg("AD01", data)


def _tr01(prefix27: str, txnref10: str) -> bytes:
    assert len(prefix27) == 27, f"prefix must be 27 bytes, got {len(prefix27)}: {prefix27!r}"
    assert len(txnref10) == 10
    return _seg("TR01", prefix27 + txnref10 + " " * 6)


def _sc01(code10: str) -> bytes:
    assert len(code10) == 10
    return _seg("SC01", code10 + " " * 17)


def _cl01(timestamp8: str) -> bytes:
    assert len(timestamp8) == 8
    return _seg("CL01", "PUBL  ABC. " + timestamp8 + " I" + " " * 39)


def _ends(seg_count: int) -> bytes:
    return _seg("ENDS", f"{seg_count:03d}")


# Canonical TR01 instance pool. Sampling without replacement within a record
# guarantees the multiset hash comparison stays sensible across reordering.
_TR01_POOL: tuple[bytes, ...] = (
    _tr01("A1111111  ABCBANK 2000 4000", "TXNREF0001"),
    _tr01("A2222222  ABCBANK 2100 4100", "TXNREF0002"),
    _tr01("A3333333  ABCBANK 2200 4200", "TXNREF0003"),
    _tr01("A4444444  ABCBANK 2300 4300", "TXNREF0004"),
    _tr01("A5555555  ABCBANK 2400 4400", "TXNREF0005"),
)
_TR01_MOD: bytes = _tr01("B1111111  ABCBANK 2000 4000", "TXNREF0001")


_FIRST_NAMES: tuple[str, ...] = (
    "ALICE",
    "BOB",
    "CAROL",
    "DAVID",
    "EVE",
    "FRANK",
    "GRACE",
    "HENRY",
    "IRENE",
    "JAMES",
    "KATHY",
    "LARRY",
    "MARY",
    "NICK",
    "OLIVER",
    "PAULA",
    "QUINN",
    "ROBERT",
    "SARAH",
    "THOMAS",
    "URSULA",
    "VICTOR",
    "WENDY",
    "XAVIER",
    "YVONNE",
    "ZACHARY",
)
_LAST_NAMES: tuple[str, ...] = (
    "ANDERSON",
    "SMITH",
    "DAVIS",
    "WILSON",
    "MARTINEZ",
    "JOHNSON",
    "BROWN",
    "TAYLOR",
    "MOORE",
    "LEE",
    "HALL",
    "KING",
    "SCOTT",
    "BELL",
    "NELSON",
    "MITCHELL",
    "BLACK",
    "GREEN",
    "WHITE",
    "WALKER",
)
_CITIES: tuple[tuple[str, str, str], ...] = (
    ("NEW YORK", "NY", "10001"),
    ("LOS ANGELES", "CA", "90001"),
    ("CHICAGO", "IL", "60601"),
    ("HOUSTON", "TX", "77001"),
    ("PHOENIX", "AZ", "85001"),
    ("PHILADELPHIA", "PA", "19101"),
    ("DALLAS", "TX", "75201"),
    ("SAN DIEGO", "CA", "92101"),
    ("SAN JOSE", "CA", "95101"),
    ("AUSTIN", "TX", "78701"),
    ("BOSTON", "MA", "02101"),
    ("SEATTLE", "WA", "98101"),
    ("DENVER", "CO", "80201"),
    ("MIAMI", "FL", "33101"),
)


def _pick_name(rng: random.Random) -> tuple[str, str, str]:
    first = rng.choice(_FIRST_NAMES)
    middle = rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    last = rng.choice(_LAST_NAMES)
    return first, middle, last


def _pick_address(rng: random.Random) -> tuple[str, str, str, str]:
    number = rng.randint(1, 999)
    street_name = rng.choice(
        (
            "MAIN ST",
            "OAK AVE",
            "PINE RD",
            "ELM ST",
            "MAPLE LN",
            "BIRCH DR",
            "CEDAR CT",
            "WALNUT ST",
            "ASH AVE",
            "CHERRY ST",
            "SPRUCE RD",
            "POPLAR LN",
            "FIR ST",
            "BEECH AVE",
            "HOLLY DR",
        )
    )
    street = f"{number} {street_name}"
    city, state, zip_code = rng.choice(_CITIES)
    return street, city, state, zip_code


def _pick_tr01_set(rng: random.Random, count: int) -> tuple[bytes, ...]:
    """Sample ``count`` distinct TR01 instances from the canonical pool."""
    return tuple(rng.sample(_TR01_POOL, k=count))


def _record(
    key: str,
    name: tuple[str, str, str],
    addr: tuple[str, str, str, str],
    tr_list: tuple[bytes, ...],
    timestamp: str = "20250709",
) -> bytes:
    """Assemble a single record's wire bytes (without the trailing delimiter)."""
    parts = [
        _tu4r(key),
        _sh01(),
        _nm01(*name),
        _ad01(*addr),
        *tr_list,
        _sc01("+340020103"),
        _sc01("+740022103"),
        _cl01(timestamp),
    ]
    parts.append(_ends(len(parts) + 1))
    return b"".join(parts)


# ---------------------------------------------------------------------------
# Scenario distribution
# ---------------------------------------------------------------------------


# Per-key scenario fractions. Each scenario contributes 0/1/2 records to A
# and to B; the totals depend on the mix. The fractions deliberately weight
# matches heavily so the fixture resembles a real-world reconciliation pair
# (most records align; only a small tail exercises the edge cases).
SCENARIO_FRACTIONS: dict[str, float] = {
    "match": 0.92,
    "nm01_mismatch": 0.01,
    "tr01_content_mismatch": 0.01,
    "tr01_count_mismatch": 0.01,
    "ad01_mismatch": 0.01,
    "only_in_a": 0.015,
    "only_in_b": 0.015,
    "dup_in_a": 0.003,
    "dup_in_b": 0.003,
    "cl01_timestamp_differs": 0.004,
}
assert abs(sum(SCENARIO_FRACTIONS.values()) - 1.0) < 1e-9, "scenario fractions must sum to 1.0"

_SCENARIO_NAMES: tuple[str, ...] = tuple(SCENARIO_FRACTIONS.keys())
_SCENARIO_WEIGHTS: tuple[float, ...] = tuple(SCENARIO_FRACTIONS.values())


@dataclass(frozen=True)
class ExpectedCounts:
    """Aggregate counts the generated pair must produce when compared.

    Attributes:
        num_keys: Number of distinct key slots iterated.
        file_a_records: Total records emitted in File A (sum of all
            scenario contributions to A, including dup occurrences).
        file_b_records: Same for File B.
        matches: Joined keys whose records compare as equal after
            normalization (includes ``cl01_timestamp_differs`` because
            the CL01 timestamp is excluded).
        mismatches: Joined keys whose records compare as unequal.
        only_in_a: Keys present only in File A's good index.
        only_in_b: Keys present only in File B's good index.
        dups_in_a: Total duplicate-key occurrences in File A
            (2× number of dup_in_a scenarios).
        dups_in_b: Same for File B.
        report_rows: Number of rows in ``report.csv`` (sum of
            mismatched segment-types across all mismatched records).
    """

    num_keys: int
    file_a_records: int
    file_b_records: int
    matches: int
    mismatches: int
    only_in_a: int
    only_in_b: int
    dups_in_a: int
    dups_in_b: int
    report_rows: int


def _pick_scenarios(num_keys: int, seed: int) -> list[str]:
    """Deterministically assign one scenario per key slot."""
    rng = random.Random(seed)
    return rng.choices(_SCENARIO_NAMES, weights=_SCENARIO_WEIGHTS, k=num_keys)


def _accumulate(scenarios: list[str]) -> ExpectedCounts:
    """Sum per-scenario record contributions into aggregate counts."""
    counters = {name: 0 for name in _SCENARIO_NAMES}
    for s in scenarios:
        counters[s] += 1

    n_match = counters["match"]
    n_nm = counters["nm01_mismatch"]
    n_tr_c = counters["tr01_content_mismatch"]
    n_tr_n = counters["tr01_count_mismatch"]
    n_ad = counters["ad01_mismatch"]
    n_only_a = counters["only_in_a"]
    n_only_b = counters["only_in_b"]
    n_dup_a = counters["dup_in_a"]
    n_dup_b = counters["dup_in_b"]
    n_cl = counters["cl01_timestamp_differs"]

    file_a_records = n_match + n_nm + n_tr_c + n_tr_n + n_ad + n_only_a + 2 * n_dup_a + n_cl
    file_b_records = n_match + n_nm + n_tr_c + n_tr_n + n_ad + n_only_b + 2 * n_dup_b + n_cl

    matches = n_match + n_cl
    mismatches = n_nm + n_tr_c + n_tr_n + n_ad
    report_rows = n_nm + n_tr_c + n_tr_n + n_ad  # one row per mismatched segment-type

    return ExpectedCounts(
        num_keys=len(scenarios),
        file_a_records=file_a_records,
        file_b_records=file_b_records,
        matches=matches,
        mismatches=mismatches,
        only_in_a=n_only_a,
        only_in_b=n_only_b,
        dups_in_a=2 * n_dup_a,
        dups_in_b=2 * n_dup_b,
        report_rows=report_rows,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_pair(
    num_records: int,
    seed: int,
    out_dir: Path,
) -> tuple[Path, Path, ExpectedCounts]:
    """Generate or reuse a synthetic A/B pair.

    Args:
        num_records: Number of distinct key slots to iterate. Each
            slot becomes 0, 1, or 2 records in each file depending on
            the scenario drawn. Actual records-per-file is slightly
            less than ``num_records`` (about 98.5% with the default
            fractions); pass a slightly larger value if you need at
            least N records per file.
        seed: PRNG seed. Same seed + same ``num_records`` ⇒ same
            files byte-for-byte, including expected counts.
        out_dir: Directory to write into. Created if absent. Existing
            files with the same stem are reused.

    Returns:
        ``(file_a_path, file_b_path, expected_counts)``.

    Notes:
        - Files are written in sorted-key order.
        - Caching: if all three on-disk artifacts (``*_a.dat``,
          ``*_b.dat``, ``*_expected.json``) already exist for the
          given ``(num_records, seed)``, generation is skipped and
          the cached expected counts are returned.
        - Generated artifacts are large (~500 bytes/record). 3M
          records ≈ 1.5 GB per file; ``tests/fixtures/`` is
          gitignored.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"synth_{num_records:09d}_seed{seed}"
    path_a = out_dir / f"{stem}_a.dat"
    path_b = out_dir / f"{stem}_b.dat"
    path_expected = out_dir / f"{stem}_expected.json"

    if path_a.exists() and path_b.exists() and path_expected.exists():
        cached = json.loads(path_expected.read_text())
        logger.info(
            "synthetic fixture cache hit: %s (%d records A, %d records B)",
            stem,
            cached["file_a_records"],
            cached["file_b_records"],
        )
        return path_a, path_b, ExpectedCounts(**cached)

    scenarios = _pick_scenarios(num_records, seed)
    expected = _accumulate(scenarios)

    logger.info(
        "generating synthetic fixture %s: %d keys → A=%d records, B=%d records",
        stem,
        num_records,
        expected.file_a_records,
        expected.file_b_records,
    )

    # Generation RNG is independent from scenario RNG so scenario assignment
    # is stable regardless of how many bytes the per-record helpers consume.
    content_rng = random.Random(seed ^ 0xC0FFEE)

    with path_a.open("wb") as fh_a, path_b.open("wb") as fh_b:
        for i, scenario in enumerate(scenarios, start=1):
            key = f"KEY{i:09d}"
            _emit_scenario(scenario, key, content_rng, fh_a, fh_b)

    path_expected.write_text(json.dumps(asdict(expected), indent=2) + "\n")
    return path_a, path_b, expected


# ---------------------------------------------------------------------------
# Per-scenario record emission
# ---------------------------------------------------------------------------


def _emit_scenario(
    scenario: str,
    key: str,
    rng: random.Random,
    fh_a: IO[bytes],
    fh_b: IO[bytes],
) -> None:
    """Write the 0–2 records contributed by ``scenario`` to A and B."""
    name = _pick_name(rng)
    addr = _pick_address(rng)
    tr_count = rng.choice((2, 3, 4))

    if scenario == "match":
        tr_set = _pick_tr01_set(rng, tr_count)
        rec = _record(key, name, addr, tr_set)
        fh_a.write(rec + b"\n")
        fh_b.write(rec + b"\n")

    elif scenario == "nm01_mismatch":
        tr_set = _pick_tr01_set(rng, tr_count)
        # Pick a first name guaranteed to differ from A's first. A bare
        # rng.choice would collide ~1/26 of the time and silently turn the
        # scenario into a match — breaking the ExpectedCounts contract.
        new_first = rng.choice(_FIRST_NAMES)
        while new_first == name[0]:
            new_first = rng.choice(_FIRST_NAMES)
        name_b = (new_first, name[1], name[2])
        fh_a.write(_record(key, name, addr, tr_set) + b"\n")
        fh_b.write(_record(key, name_b, addr, tr_set) + b"\n")

    elif scenario == "tr01_content_mismatch":
        tr_set_a = _pick_tr01_set(rng, max(tr_count, 2))
        # Replace one TR01 instance in B with the canonical MOD variant.
        tr_set_b = (_TR01_MOD,) + tr_set_a[1:]
        fh_a.write(_record(key, name, addr, tr_set_a) + b"\n")
        fh_b.write(_record(key, name, addr, tr_set_b) + b"\n")

    elif scenario == "tr01_count_mismatch":
        tr_count_a = rng.choice((3, 4))
        tr_count_b = rng.choice((2, 3))
        # Ensure they actually differ
        if tr_count_a == tr_count_b:
            tr_count_b = tr_count_a - 1
        tr_set_a = _pick_tr01_set(rng, tr_count_a)
        tr_set_b = tr_set_a[:tr_count_b]
        fh_a.write(_record(key, name, addr, tr_set_a) + b"\n")
        fh_b.write(_record(key, name, addr, tr_set_b) + b"\n")

    elif scenario == "ad01_mismatch":
        tr_set = _pick_tr01_set(rng, tr_count)
        # Force a different street number so the canonical bytes can't accidentally
        # equal A's. Picking a brand-new random address would collide rarely but
        # not never; this is cheaper and exact.
        addr_b = (f"X{addr[0]}", addr[1], addr[2], addr[3])
        fh_a.write(_record(key, name, addr, tr_set) + b"\n")
        fh_b.write(_record(key, name, addr_b, tr_set) + b"\n")

    elif scenario == "only_in_a":
        tr_set = _pick_tr01_set(rng, tr_count)
        fh_a.write(_record(key, name, addr, tr_set) + b"\n")

    elif scenario == "only_in_b":
        tr_set = _pick_tr01_set(rng, tr_count)
        fh_b.write(_record(key, name, addr, tr_set) + b"\n")

    elif scenario == "dup_in_a":
        tr_set = _pick_tr01_set(rng, tr_count)
        rec = _record(key, name, addr, tr_set)
        fh_a.write(rec + b"\n")
        fh_a.write(rec + b"\n")

    elif scenario == "dup_in_b":
        tr_set = _pick_tr01_set(rng, tr_count)
        rec = _record(key, name, addr, tr_set)
        fh_b.write(rec + b"\n")
        fh_b.write(rec + b"\n")

    elif scenario == "cl01_timestamp_differs":
        tr_set = _pick_tr01_set(rng, tr_count)
        ts_a = f"2025{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
        ts_b = f"2026{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"
        fh_a.write(_record(key, name, addr, tr_set, timestamp=ts_a) + b"\n")
        fh_b.write(_record(key, name, addr, tr_set, timestamp=ts_b) + b"\n")

    else:
        raise ValueError(f"unknown scenario: {scenario!r}")
