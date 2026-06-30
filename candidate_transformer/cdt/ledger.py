"""The append-only Evidence Ledger (design doc section 2).

A lightweight, frozen, per-run record -- *not* a database. Adapters only ever
append; nothing is mutated or deleted. The canonical profile is a deterministic
reduce over this ledger, so the ledger is the single source of truth and the
backbone of explainability ("why this value?" -> read the claims).
"""
from __future__ import annotations

from typing import Iterable, Iterator

from .model import Claim, Source


class EvidenceLedger:
    def __init__(self) -> None:
        self._sources: dict[str, Source] = {}
        self._claims: list[Claim] = []
        self._log: list[str] = []  # per-source ingest log (errors etc.)

    # -- sources --------------------------------------------------------
    def add_source(self, source: Source) -> bool:
        """Register a source. Returns False if it was already seen (dedupe)."""
        if source.source_id in self._sources:
            self._log.append(
                f"dedupe: source {source.source_id} ({source.origin}) already ingested"
            )
            return False
        self._sources[source.source_id] = source
        if not source.ok:
            self._log.append(
                f"source {source.source_id} ({source.origin}) failed: {source.error}"
            )
        return True

    def source(self, source_id: str) -> Source | None:
        return self._sources.get(source_id)

    @property
    def sources(self) -> list[Source]:
        return list(self._sources.values())

    # -- claims ---------------------------------------------------------
    def append(self, claim: Claim) -> None:
        self._claims.append(claim)

    def extend(self, claims: Iterable[Claim]) -> None:
        for c in claims:
            self._claims.append(c)

    @property
    def claims(self) -> list[Claim]:
        return list(self._claims)

    def __iter__(self) -> Iterator[Claim]:
        return iter(self._claims)

    def __len__(self) -> int:
        return len(self._claims)

    # -- logging --------------------------------------------------------
    def log(self, msg: str) -> None:
        self._log.append(msg)

    @property
    def logs(self) -> list[str]:
        return list(self._log)
