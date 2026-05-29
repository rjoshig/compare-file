"""Test helpers — minimal layout + matching synthetic-record factory.

The committed ``config/layout_file_*.json`` describe the realistic
production-shaped fixture (record size ≈ 417 bytes). Most pipeline /
external-sort / parallel tests want shorter synthetic records so each
case stays readable. This module provides:

- :func:`minimal_layout` — a small valid layout (TU4R + NM01 + ENDS,
  3 logical fields total) for synthetic tests.
- :func:`write_minimal_config_dir` — stages a config directory using
  that layout for both A and B + a stock ``runtime.json``.
- :func:`make_synthetic_record` — emits one record whose bytes match
  the minimal layout, given a 12-char key and 10-byte name.

Tests that exercise the realistic fixture (``examples/sample_*.dat``)
keep using the real ``config/`` directory directly.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from segment_compare.writer import RUN_DIR_FORMAT


def run_dir_for(ts: datetime) -> str:
    """Compute the per-run subdirectory name for a fixed test timestamp (ADR-037)."""
    return ts.strftime(RUN_DIR_FORMAT)


def minimal_layout() -> dict:
    """A small but complete layout valid against :func:`make_synthetic_record`.

    Segment shape:

    - TU4R: size 23 = 7 header + 4 ``"DATA"`` prefix + 12-byte key
    - NM01: size 17 = 7 header + 10 bytes of name data (one field)
    - ENDS: size 10 = 7 header + 3 bytes of segment-count (excluded)
    """
    return {
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
                "size": 23,
                "fields": [
                    {"name": "data_prefix", "length": 4, "exclude": True},
                    {"name": "account_nbr", "length": 12, "key": True},
                ],
            },
            {
                "name": "NM01",
                "size": 17,
                "fields": [
                    {"name": "full_name", "length": 10},
                ],
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


def minimal_runtime() -> dict:
    """A stock runtime.json payload compatible with the minimal layout."""
    return {
        "hash_method": "blake2b",
        "blake2b_digest_size": 16,
        "sort_temp_dir": "/tmp/segment_compare",
        "parallel_workers": 1,
        "chunk_size": 10000,
        "partition_strategy": "equal_count",
    }


def write_minimal_config_dir(
    tmp_path: Path,
    *,
    layout_a: dict | None = None,
    layout_b: dict | None = None,
    runtime: dict | None = None,
) -> Path:
    """Stage a config directory under ``tmp_path/config/`` and return it.

    Defaults to the minimal layout on both sides plus the stock
    runtime payload. Pass per-arg overrides to inject deliberate
    schema variations (RDW, leading-byte strip, divergent A/B layouts,
    sort=false, etc.).
    """
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "layout_file_A.json").write_text(
        json.dumps(layout_a if layout_a is not None else minimal_layout())
    )
    (cfg_dir / "layout_file_B.json").write_text(
        json.dumps(layout_b if layout_b is not None else minimal_layout())
    )
    (cfg_dir / "runtime.json").write_text(
        json.dumps(runtime if runtime is not None else minimal_runtime())
    )
    return cfg_dir


def make_synthetic_record(key: str, name: bytes = b"NAME_XYZ__") -> bytes:
    """One synthetic record matching :func:`minimal_layout`.

    Layout:

    - ``TU4R023`` header + ``"DATA"`` prefix + 12-byte key  (23 bytes)
    - ``NM01017`` header + 10-byte name                     (17 bytes)
    - ``ENDS010`` header + 3-byte segment count             (10 bytes)

    Total **50 bytes** on the wire (the record delimiter is added by
    the file-writing helpers, not here).
    """
    if len(key) != 12:
        raise ValueError(f"key must be 12 bytes, got {len(key)}: {key!r}")
    if len(name) != 10:
        raise ValueError(f"name must be 10 bytes, got {len(name)}: {name!r}")
    return b"TU4R023DATA" + key.encode("ascii") + b"NM01017" + name + b"ENDS010001"
