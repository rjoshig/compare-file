"""Tests for ``segment_compare.hasher``."""

from __future__ import annotations

from pathlib import Path

import pytest

from segment_compare.config import RuntimeConfig
from segment_compare.hasher import (
    Blake2bHasher,
    BuiltinHasher,
    Hasher,
    build_hasher,
)


def _runtime(hash_method: str = "blake2b", digest_size: int = 16) -> RuntimeConfig:
    return RuntimeConfig(
        hash_method=hash_method,
        blake2b_digest_size=digest_size,
        input_sorted=True,
        sort_temp_dir=Path("/tmp/segment_compare"),
        parallel_workers=1,
        chunk_size=10000,
        partition_strategy="equal_count",
        key_type="alphanumeric",
        key_sort_order="ascending",
    )


# ---------------------------------------------------------------------------
# Blake2bHasher
# ---------------------------------------------------------------------------


def test_blake2b_default_digest_is_16_bytes() -> None:
    h = Blake2bHasher()
    assert h.digest_size == 16
    assert len(h.hash(b"hello")) == 16


def test_blake2b_custom_digest_size() -> None:
    h = Blake2bHasher(digest_size=32)
    assert h.digest_size == 32
    assert len(h.hash(b"hello")) == 32


def test_blake2b_is_deterministic() -> None:
    h = Blake2bHasher()
    assert h.hash(b"abc") == h.hash(b"abc")


def test_blake2b_differs_on_different_input() -> None:
    h = Blake2bHasher()
    assert h.hash(b"abc") != h.hash(b"abd")


def test_blake2b_empty_bytes() -> None:
    h = Blake2bHasher()
    assert len(h.hash(b"")) == 16


def test_blake2b_returns_bytes() -> None:
    assert isinstance(Blake2bHasher().hash(b"x"), bytes)


def test_blake2b_rejects_invalid_digest_size() -> None:
    with pytest.raises(ValueError):
        Blake2bHasher(digest_size=0)
    with pytest.raises(ValueError):
        Blake2bHasher(digest_size=65)


# ---------------------------------------------------------------------------
# BuiltinHasher
# ---------------------------------------------------------------------------


def test_builtin_is_deterministic_within_process() -> None:
    h = BuiltinHasher()
    assert h.hash(b"abc") == h.hash(b"abc")


def test_builtin_differs_on_different_input() -> None:
    h = BuiltinHasher()
    # Different content very likely yields different hashes; pick inputs
    # large enough to avoid hash-randomization collisions on tiny strings.
    assert h.hash(b"abcdef" * 10) != h.hash(b"abcdeg" * 10)


def test_builtin_returns_int() -> None:
    assert isinstance(BuiltinHasher().hash(b"x"), int)


# ---------------------------------------------------------------------------
# Protocol satisfaction
# ---------------------------------------------------------------------------


def test_blake2b_satisfies_hasher_protocol() -> None:
    h: Hasher = Blake2bHasher()
    assert h.hash(b"x") is not None


def test_builtin_satisfies_hasher_protocol() -> None:
    h: Hasher = BuiltinHasher()
    assert h.hash(b"x") is not None


# ---------------------------------------------------------------------------
# build_hasher
# ---------------------------------------------------------------------------


def test_build_hasher_blake2b() -> None:
    h = build_hasher(_runtime("blake2b", digest_size=16))
    assert isinstance(h, Blake2bHasher)
    assert h.digest_size == 16


def test_build_hasher_blake2b_uses_configured_digest_size() -> None:
    h = build_hasher(_runtime("blake2b", digest_size=32))
    assert isinstance(h, Blake2bHasher)
    assert h.digest_size == 32


def test_build_hasher_builtin() -> None:
    h = build_hasher(_runtime("builtin"))
    assert isinstance(h, BuiltinHasher)


def test_build_hasher_unknown_method_raises() -> None:
    bad = RuntimeConfig(
        hash_method="md5",  # bypasses ConfigError to exercise the guard
        blake2b_digest_size=16,
        input_sorted=True,
        sort_temp_dir=Path("/tmp"),
        parallel_workers=1,
        chunk_size=1,
        partition_strategy="equal_count",
        key_type="alphanumeric",
        key_sort_order="ascending",
    )
    with pytest.raises(ValueError):
        build_hasher(bad)
