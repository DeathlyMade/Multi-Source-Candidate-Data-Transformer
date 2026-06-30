"""Adapter framework (design doc section 3 & 8: adapter isolation).

Each adapter is independent and best-effort. ``safe_ingest`` wraps an adapter so
that a corrupt / empty / malformed / 404 source produces **0 claims**, is logged,
and the run continues -- a bad source can never crash the pipeline.
"""
from __future__ import annotations

import traceback
from typing import Iterable

from ..content_address import content_address
from ..ledger import EvidenceLedger
from ..model import Claim, Source


class Adapter:
    source_type: str = "unknown"

    def parse(self, data: bytes, source: Source) -> Iterable[Claim]:  # pragma: no cover
        raise NotImplementedError


def make_claim(source: Source, record_id: str, path, raw, extractor: str, locator: str) -> Claim:
    return Claim(
        path=path,
        raw=raw,
        source_id=source.source_id,
        source_type=source.source_type,
        extractor=extractor,
        locator=locator,
        record_id=record_id,
        ts=source.ts,
    )


def safe_ingest(adapter: Adapter, data: bytes, origin: str, ledger: EvidenceLedger,
                ts: str | None = None) -> int:
    """Content-address, register, and extract claims from one source.

    Returns the number of claims appended. Any failure is isolated: the source
    is recorded as failed (ok=False) and the run continues.
    """
    source_id = content_address(data if data is not None else b"", adapter.source_type)
    # Build the source record first so even a hard failure is traceable.
    try:
        if data is None or len(data) == 0:
            src = Source(source_id, adapter.source_type, origin, ok=False,
                         error="empty_source", ts=ts)
            ledger.add_source(src)
            return 0
        src = Source(source_id, adapter.source_type, origin, ok=True, ts=ts)
        if not ledger.add_source(src):
            return 0  # duplicate content -> already ingested
        claims = list(adapter.parse(data, src))
        ledger.extend(claims)
        ledger.log(f"ingest ok: {origin} [{adapter.source_type}] -> {len(claims)} claims")
        return len(claims)
    except Exception as exc:  # adapter isolation -- never propagate
        ledger.add_source(Source(source_id, adapter.source_type, origin, ok=False,
                                 error=f"{type(exc).__name__}: {exc}", ts=ts))
        ledger.log(
            f"ingest FAILED (isolated): {origin} [{adapter.source_type}]: "
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc().splitlines()[-1]}"
        )
        return 0
