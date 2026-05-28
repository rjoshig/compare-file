"""End-to-end test: per-file layouts with differing field counts still match.

ADR-033's headline capability: File A and File B can declare the same
segment with **different field counts** as long as the fields that
matter (un-excluded, same-named) align. Filler-only differences must
not classify as mismatches.

The pre-ADR-033 ``test_field_config_classifies_records_same_as_position_config``
identity test is gone — position-based normalization no longer exists, so
the two-form equivalence it pinned is moot.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from segment_compare.config import load_config
from segment_compare.pipeline import run

REPO_ROOT = Path(__file__).resolve().parent.parent

FIXED_TS = datetime(2026, 5, 28, 0, 0, tzinfo=timezone.utc)


def test_field_config_with_filler_exclude_matches_records_that_differ_only_in_filler(
    tmp_path: Path,
) -> None:
    """A's NM01 = first(5) + last(5); B's NM01 = first(5) + last(5) + filler(5, excluded).

    Same logical content → same canonical bytes → match. The two
    layouts declare different segment sizes for NM01 (17 vs 22) — only
    the un-excluded fields participate in the comparison.
    """
    layout_template = {
        "file_format": {
            "segment_name_bytes": 4,
            "size_field_bytes": 3,
            "size_encoding": "ascii_int",
            "size_includes_header": True,
            "data_encoding": "ascii",
            "record_delimiter": "\n",
        },
        "strip_leading_bytes": None,
        "rdw": None,
        "sort": {
            "input_sorted": True,
            "order": "ascending",
            "key_type": "alphanumeric",
        },
        "segments": [
            {
                "name": "TU4R",
                "role": "key",
                "size": 19,
                "fields": [
                    {"name": "account_nbr", "length": 12, "key": True},
                ],
            },
            {
                "name": "NM01",
                "size": 17,  # set per layout below
                "fields": [],  # set per layout below
            },
            {
                "name": "ENDS",
                "role": "end",
                "size": 10,
                "fields": [
                    {"name": "segment_count", "length": 3, "exclude": True},
                ],
            },
        ],
    }

    layout_a = json.loads(json.dumps(layout_template))  # deep copy
    layout_a["segments"][1]["size"] = 17  # 7 header + 10 data
    layout_a["segments"][1]["fields"] = [
        {"name": "first", "length": 5},
        {"name": "last", "length": 5},
    ]

    layout_b = json.loads(json.dumps(layout_template))
    layout_b["segments"][1]["size"] = 22  # 7 header + 15 data
    layout_b["segments"][1]["fields"] = [
        {"name": "first", "length": 5},
        {"name": "last", "length": 5},
        {"name": "filler", "length": 5, "exclude": True},
    ]

    runtime = {
        "hash_method": "blake2b",
        "blake2b_digest_size": 16,
        "sort_temp_dir": "/tmp/segment_compare",
        "parallel_workers": 1,
        "chunk_size": 10000,
        "partition_strategy": "equal_count",
    }

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "layout_file_A.json").write_text(json.dumps(layout_a))
    (cfg_dir / "layout_file_B.json").write_text(json.dumps(layout_b))
    (cfg_dir / "runtime.json").write_text(json.dumps(runtime))

    # Build records.
    # A: TU4R019 + 12-byte key + NM01017 + 10 bytes + ENDS010 + 3 bytes
    a_record = b"TU4R019KEY000000001NM01017ALICEDOE00ENDS010001"
    # B: TU4R019 + 12-byte key + NM01022 + 15 bytes (last 5 = junk filler) + ENDS010 + 3 bytes
    b_record = b"TU4R019KEY000000001NM01022ALICEDOE00\x00@!XYENDS010001"
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
