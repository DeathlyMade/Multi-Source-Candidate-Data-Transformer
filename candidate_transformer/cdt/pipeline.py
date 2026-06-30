"""Pipeline orchestrator (design doc section 2 & the phase list).

Ingest -> Extract -> Normalize -> Canonicalize -> Resolve -> Reconcile ->
Calibrate -> Project -> Validate.

The whole thing is a deterministic reduce over the Evidence Ledger. Per-candidate
reconciliation is stateless and embarrassingly parallel (design doc section 8),
so scaling out is "run this function per candidate group".
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .adapters import detect_source_type, get_adapter, safe_ingest
from .config import DEFAULT_CONFIG, load_config, validate_config
from .ledger import EvidenceLedger
from .normalize import NormalizeContext, normalize_claim
from .normalize.text import decode_bytes  # noqa: F401 (re-export convenience)
from .project import project
from .reconcile import Reconciler
from .resolve import build_entities, candidate_id, resolve
from .validate import validate_internal, validate_output
from .vocab import vocab_versions


@dataclass
class InputSpec:
    origin: str
    data: bytes
    source_type: str | None = None   # forced type, else auto-detect
    ts: str | None = None            # optional source timestamp (YYYY-MM-DD) for recency


@dataclass
class RunResult:
    profiles: list = field(default_factory=list)     # projected + validated outputs
    assembled: list = field(default_factory=list)    # internal canonical profiles
    config: dict = field(default_factory=dict)
    ledger: EvidenceLedger = None
    vocab: dict = field(default_factory=dict)

    @property
    def output(self):
        """Single object when there is exactly one candidate, else a list."""
        if len(self.profiles) == 1:
            return self.profiles[0]
        return self.profiles


class Pipeline:
    def __init__(self, config: dict | None = None, locale_hint: str | None = None,
                 default_country: str | None = None, tie_margin: float = 0.10):
        self.config = config if config is not None else DEFAULT_CONFIG
        validate_config(self.config)  # gate 1: fail fast on a bad config
        self.ctx = NormalizeContext(locale_hint=locale_hint, default_country=default_country)
        self.tie_margin = tie_margin

    # -- phases ---------------------------------------------------------
    def ingest(self, inputs: list[InputSpec]) -> EvidenceLedger:
        ledger = EvidenceLedger()
        for spec in inputs:
            stype = spec.source_type or detect_source_type(spec.origin, spec.data)
            try:
                adapter = get_adapter(stype)
            except KeyError as exc:
                ledger.log(f"skip {spec.origin}: {exc}")
                continue
            safe_ingest(adapter, spec.data, spec.origin, ledger, ts=spec.ts)
        return ledger

    def normalize(self, ledger: EvidenceLedger) -> list:
        return [normalize_claim(c, self.ctx) for c in ledger.claims]

    def run(self, inputs: list[InputSpec]) -> RunResult:
        ledger = self.ingest(inputs)                       # Ingest + Extract
        normalized = self.normalize(ledger)                # Normalize + Canonicalize
        entities = build_entities(normalized)              # Resolve (entities)
        groups = resolve(entities, log=ledger.log)         # Resolve (union-find)

        by_record: dict[str, list] = {}
        for nc in normalized:
            by_record.setdefault(nc.claim.record_id, []).append(nc)

        reconciler = Reconciler(self.ctx, tie_margin=self.tie_margin)
        sources = ledger.sources
        assembled_list, profiles = [], []
        for group in groups:
            group_claims = [nc for rid in group for nc in by_record.get(rid, [])]
            if not group_claims:
                continue
            assembled = reconciler.reconcile(group_claims, sources)   # Reconcile + Calibrate
            assembled.candidate_id = candidate_id(entities, group)
            validate_internal(assembled)                              # gate 1b
            out = project(assembled, self.config)                     # Project
            validate_output(out, self.config)                         # gate 2 (Validate)
            assembled_list.append(assembled)
            profiles.append(out)

        # deterministic ordering of candidates by candidate_id
        order = sorted(range(len(profiles)), key=lambda i: assembled_list[i].candidate_id)
        profiles = [profiles[i] for i in order]
        assembled_list = [assembled_list[i] for i in order]
        return RunResult(profiles=profiles, assembled=assembled_list,
                         config=self.config, ledger=ledger, vocab=vocab_versions())


# ---------------------------------------------------------------------- #
# convenience helpers
# ---------------------------------------------------------------------- #
def read_inputs(paths: list[str]) -> list[InputSpec]:
    """Read files into InputSpecs. Supports wildcard/glob expansion and an explicit ``type=path`` prefix."""
    import glob
    specs = []
    for p in paths:
        stype = None
        path_pattern = p
        if "=" in p and not os.path.exists(p):
            maybe_type, _, rest = p.partition("=")
            if maybe_type.strip():
                stype, path_pattern = maybe_type.strip(), rest.strip()
        
        matched = sorted(glob.glob(path_pattern))
        matched_files = [m for m in matched if os.path.isfile(m)]
        if not matched_files:
            if glob.has_magic(path_pattern):
                raise FileNotFoundError(f"No files matched pattern: {path_pattern}")
            matched_files = [path_pattern]
            
        for matched_path in matched_files:
            with open(matched_path, "rb") as fh:
                data = fh.read()
            specs.append(InputSpec(origin=os.path.basename(matched_path), data=data, source_type=stype))
    return specs


def transform(paths: list[str], config_path: str | None = None,
              locale_hint: str | None = None, default_country: str | None = None) -> RunResult:
    config = load_config(config_path)
    pipe = Pipeline(config=config, locale_hint=locale_hint, default_country=default_country)
    return pipe.run(read_inputs(paths))
