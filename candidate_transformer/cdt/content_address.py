"""Content-addressing for sources (design doc section 2).

``source_id = hash(bytes + type)`` is computed once per source. Identical
sources therefore collapse to the same id (dedupe), and a run over the same
bytes is fully reproducible and cacheable. No wall-clock, no randomness.
"""
from __future__ import annotations

import hashlib


def content_address(data: bytes, source_type: str) -> str:
    """Return a stable content address for ``data`` of ``source_type``.

    The source *type* is folded into the hash so the same bytes interpreted as
    two different source types do not collide.
    """
    h = hashlib.sha256()
    h.update(source_type.encode("utf-8"))
    h.update(b"\x00")
    h.update(data)
    return "sha256:" + h.hexdigest()[:16]
