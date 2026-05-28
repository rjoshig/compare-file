"""End-to-end identity test: field-based vs position-based normalization.

Phase 2 acceptance criterion #3: a field-based normalization config
that encodes the same exclusion semantics as the stock position-based
config must classify every record the same way (matches in matches.dat,
mismatches in mismatches.dat, with the same per-segment report rows).

The two configs hash DIFFERENT canonical bytes (position-based hashes
the byte slice; field-based hashes a sorted ``name=value`` form), but
the EQUIVALENCE relation they encode is identical, so the comparator's
match/mismatch decisions agree everywhere.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from segment_compare.config import load_config
from segment_compare.pipeline import run
from segment_compare.writer import stamped_filename

REPO_ROOT = Path(__file__).resolve().parent.parent
STOCK_CONFIG_DIR = REPO_ROOT / "config"
EXAMPLES = REPO_ROOT / "examples"

FIXED_TS = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)
FIXED_STAMP = "202605280000"


def _write_field_config(target_dir: Path) -> None:
    """Write a config dir whose normalization.json uses the field form.

    Encodes the same exclusion semantics as the stock position-based
    config:

    - CL01: exclude the 8-byte timestamp at bytes [11, 19) of data.
    - ENDS: exclude the 3-byte segment count at bytes [0, 3) of data.

    All other segments pass through unchanged (matching the stock
    config, which has empty rules for them).
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    # Copy segments.json + runtime.json from the stock config dir verbatim.
    for name in ("segments.json", "runtime.json"):
        (target_dir / name).write_bytes((STOCK_CONFIG_DIR / name).read_bytes())

    # Field-based normalization.json. CL01 layout matches the realistic
    # fixture's 60-byte data area: prefix(11) + timestamp(8) + suffix(2) +
    # padding(39). ENDS layout: segment_count(3).
    field_norm = {
        "$comment": (
            "Field-based normalization config for the identity-test fixture. "
            "Mirrors the stock position-based config's CL01 timestamp exclude "
            "and ENDS segment-count exclude. All other segments use no rule "
            "(passthrough), matching the stock config."
        ),
        "CL01": {
            "file_a_layout": [
                {"name": "prefix", "length": 11, "exclude": False},
                {"name": "timestamp", "length": 8, "exclude": True},
                {"name": "suffix", "length": 2, "exclude": False},
                {"name": "padding", "length": 39, "exclude": False},
            ],
            "file_b_layout": [
                {"name": "prefix", "length": 11, "exclude": False},
                {"name": "timestamp", "length": 8, "exclude": True},
                {"name": "suffix", "length": 2, "exclude": False},
                {"name": "padding", "length": 39, "exclude": False},
            ],
        },
        "ENDS": {
            "file_a_layout": [
                {"name": "segment_count", "length": 3, "exclude": True},
            ],
            "file_b_layout": [
                {"name": "segment_count", "length": 3, "exclude": True},
            ],
        },
    }
    (target_dir / "normalization.json").write_text(json.dumps(field_norm, indent=2))


def test_field_config_classifies_records_same_as_position_config(tmp_path: Path) -> None:
    """Phase 2 acceptance criterion #3."""
    field_cfg_dir = tmp_path / "config_field"
    _write_field_config(field_cfg_dir)

    out_position = tmp_path / "out_position"
    out_field = tmp_path / "out_field"

    pos_summary = run(
        file_a=EXAMPLES / "sample_a.dat",
        file_b=EXAMPLES / "sample_b.dat",
        config=load_config(STOCK_CONFIG_DIR),
        output_dir=out_position,
        run_timestamp=FIXED_TS,
    )
    field_summary = run(
        file_a=EXAMPLES / "sample_a.dat",
        file_b=EXAMPLES / "sample_b.dat",
        config=load_config(field_cfg_dir),
        output_dir=out_field,
        run_timestamp=FIXED_TS,
    )

    # Same records in each output file → byte-identical *.dat / report.csv.
    for base in (
        "matches.dat",
        "mismatches.dat",
        "keymismatch_A.dat",
        "keymismatch_B.dat",
        "dups_A.dat",
        "dups_B.dat",
        "report.csv",
    ):
        position_path = out_position / stamped_filename(base, FIXED_STAMP)
        field_path = out_field / stamped_filename(base, FIXED_STAMP)
        assert (
            position_path.read_bytes() == field_path.read_bytes()
        ), f"{base} differs between position-based and field-based configs"

    # Aggregate counts must match exactly.
    assert pos_summary.records_matched == field_summary.records_matched
    assert pos_summary.records_mismatched == field_summary.records_mismatched
    assert pos_summary.keys_in_a_only == field_summary.keys_in_a_only
    assert pos_summary.keys_in_b_only == field_summary.keys_in_b_only
    assert pos_summary.dups_in_a == field_summary.dups_in_a
    assert pos_summary.dups_in_b == field_summary.dups_in_b

    # Per-segment counts must also match.
    pos_by_name = {s.segment_name: s for s in pos_summary.per_segment}
    field_by_name = {s.segment_name: s for s in field_summary.per_segment}
    assert set(pos_by_name) == set(field_by_name)
    for name in pos_by_name:
        assert pos_by_name[name].match_count == field_by_name[name].match_count, name
        assert pos_by_name[name].mismatch_count == field_by_name[name].mismatch_count, name


def test_field_config_with_filler_exclude_matches_records_that_differ_only_in_filler(
    tmp_path: Path,
) -> None:
    """The user's headline use case: B has a trailing filler that A lacks.

    A and B carry the same logical first_name+last_name, but B's NM01 has
    5 trailing bytes that A's NM01 doesn't. With a field config marking
    B's filler exclude=true, the engine must classify the pair as match.
    """
    # Build a tiny config: TU4R(key) + NM01(name) + ENDS.
    segments_json = {
        "known_segments": ["TU4R", "NM01", "ENDS"],
        "key_segment": "TU4R",
        "key_range": [0, 12],
        "end_segment": "ENDS",
        "record_delimiter": "\n",
        "parser": {
            "segment_name_bytes": 4,
            "size_field_bytes": 3,
            "size_encoding": "ascii_int",
            "size_includes_header": True,
            "data_encoding": "ascii",
        },
    }
    runtime_json = {
        "hash_method": "blake2b",
        "blake2b_digest_size": 16,
        "input_sorted": True,
        "sort_temp_dir": "/tmp/segment_compare",
        "parallel_workers": 1,
        "chunk_size": 10000,
        "partition_strategy": "equal_count",
        "key_type": "alphanumeric",
        "key_sort_order": "ascending",
    }
    # A's NM01 = first(5) + last(5) = 10 bytes.
    # B's NM01 = first(5) + last(5) + filler(5) = 15 bytes (filler excluded).
    normalization_json = {
        "NM01": {
            "file_a_layout": [
                {"name": "first", "length": 5, "exclude": False},
                {"name": "last", "length": 5, "exclude": False},
            ],
            "file_b_layout": [
                {"name": "first", "length": 5, "exclude": False},
                {"name": "last", "length": 5, "exclude": False},
                {"name": "filler", "length": 5, "exclude": True},
            ],
        },
    }
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "segments.json").write_text(json.dumps(segments_json))
    (cfg_dir / "runtime.json").write_text(json.dumps(runtime_json))
    (cfg_dir / "normalization.json").write_text(json.dumps(normalization_json))

    # Build records.
    # A: TU4R019 + 12-byte key + NM01017 + 10 bytes + ENDS007
    a_record = b"TU4R019KEY000000001NM01017ALICEDOE00ENDS007"
    # B: TU4R019 + 12-byte key + NM01022 + 15 bytes (last 5 = junk filler) + ENDS007
    b_record = b"TU4R019KEY000000001NM01022ALICEDOE00\x00@!XYENDS007"
    (tmp_path / "a.dat").write_bytes(a_record + b"\n")
    (tmp_path / "b.dat").write_bytes(b_record + b"\n")

    out = tmp_path / "out"
    summary = run(
        file_a=tmp_path / "a.dat",
        file_b=tmp_path / "b.dat",
        config=load_config(cfg_dir),
        output_dir=out,
        run_timestamp=FIXED_TS,
    )

    # Engine must classify this pair as a MATCH (filler excluded, retained
    # fields identical across A and B).
    assert summary.records_matched == 1
    assert summary.records_mismatched == 0
