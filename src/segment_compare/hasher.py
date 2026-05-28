"""Hashers for segment-content comparison.

The comparator stores hash values as keys in a ``collections.Counter`` to
implement multiset-of-hashes comparison (ADR-001). Anything hashable
works; Phase 1 ships two implementations:

- :class:`Blake2bHasher` — ``hashlib.blake2b`` with configurable digest
  size. Production default. Cross-process stable.
- :class:`BuiltinHasher` — Python's built-in ``hash()``. Faster but
  process-local (PYTHONHASHSEED is randomized). Safe because hashes are
  never persisted across runs (ADR-002).

:func:`build_hasher` selects an implementation from a
:class:`RuntimeConfig`.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from segment_compare.config import RuntimeConfig

HashValue = bytes | int

MIN_BLAKE2B_DIGEST = 1
MAX_BLAKE2B_DIGEST = 64


class Hasher(Protocol):
    """Hashes segment data into a value usable as a ``Counter`` key."""

    def hash(self, data: bytes) -> HashValue:
        """Return a hash of ``data``."""
        ...


class Blake2bHasher:
    """``hashlib.blake2b`` wrapper with configurable digest size."""

    __slots__ = ("_digest_size",)

    def __init__(self, digest_size: int = 16) -> None:
        """Initialize.

        Args:
            digest_size: Output digest size in bytes; must be in
                ``[1, 64]`` per blake2b's limits.

        Raises:
            ValueError: If ``digest_size`` is outside the allowed range.
        """
        if not MIN_BLAKE2B_DIGEST <= digest_size <= MAX_BLAKE2B_DIGEST:
            raise ValueError(
                f"digest_size must be in [{MIN_BLAKE2B_DIGEST}, "
                f"{MAX_BLAKE2B_DIGEST}], got {digest_size}"
            )
        self._digest_size = digest_size

    @property
    def digest_size(self) -> int:
        """The digest size in bytes."""
        return self._digest_size

    def hash(self, data: bytes) -> bytes:
        """Return a blake2b digest of ``data``."""
        return hashlib.blake2b(data, digest_size=self._digest_size).digest()


class BuiltinHasher:
    """Python built-in ``hash()`` wrapper.

    Faster than blake2b but process-local. Safe for single-run
    comparisons because hashes are not persisted across runs.
    """

    __slots__ = ()

    def hash(self, data: bytes) -> int:
        """Return Python's built-in ``hash(data)``."""
        return hash(data)


def build_hasher(runtime_cfg: RuntimeConfig) -> Hasher:
    """Return the :class:`Hasher` implementation selected by config.

    Args:
        runtime_cfg: The validated runtime config.

    Returns:
        A :class:`Hasher` matching ``runtime_cfg.hash_method``.

    Raises:
        ValueError: If ``runtime_cfg.hash_method`` is not recognized.
            (This is also caught by config validation; the guard here
            is defense-in-depth.)
    """
    if runtime_cfg.hash_method == "blake2b":
        return Blake2bHasher(digest_size=runtime_cfg.blake2b_digest_size)
    if runtime_cfg.hash_method == "builtin":
        return BuiltinHasher()
    raise ValueError(f"unknown hash_method {runtime_cfg.hash_method!r}")
