"""Confidence calibration helpers (design doc section 6).

Confidence in [0,1] = clamp(reliability . agreement . normalization . conflict
penalty), fixed-rounded and deterministic, documented as ordinal (not a
probability). Corroboration is modelled with a noisy-OR over per-source
contributions so independent sources combine with diminishing returns, after a
per-source-origin dedup (no double counting).

Recency carries NO wall-clock: it is derived purely from source-provided
timestamps relative to the newest timestamp seen in the run (data-derived, so a
re-run on the same bytes yields the same numbers).
"""
from __future__ import annotations

from .model import FIELD_IMPORTANCE, round_conf

FLOOR_DEMOTE = 0.30   # confidence floor for a demoted (non-high-stakes) conflict
CONTRIB_CAP = 0.999   # keep single-source noisy-OR strictly below 1.0


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def contribution(w: float, q: float, recency: float) -> float:
    return clamp01(min(CONTRIB_CAP, w * q * recency))


def noisy_or(contribs: list[float]) -> float:
    """1 - prod(1 - c): corroboration with natural diminishing returns."""
    prod = 1.0
    for c in contribs:
        prod *= (1.0 - clamp01(c))
    return clamp01(1.0 - prod)


def _year_of(ts: str | None) -> float | None:
    if not ts or len(ts) < 4 or not ts[:4].isdigit():
        return None
    try:
        y = int(ts[:4])
        m = int(ts[5:7]) if len(ts) >= 7 and ts[5:7].isdigit() else 1
        return y + (m - 1) / 12.0
    except (ValueError, TypeError):
        return None


def recency_map(sources) -> dict[str, float]:
    """Map source_id -> recency multiplier in [0.7, 1.0]. Undated -> 1.0.

    Reference point is the newest dated source in the run (deterministic).
    """
    years = {s.source_id: _year_of(s.ts) for s in sources}
    dated = [y for y in years.values() if y is not None]
    out: dict[str, float] = {}
    if not dated:
        return {s.source_id: 1.0 for s in sources}
    ref = max(dated)
    for sid, y in years.items():
        if y is None:
            out[sid] = 1.0
        else:
            out[sid] = clamp01(max(0.7, 1.0 - 0.05 * (ref - y)))
    return out


def field_confidence(reliability: float, agreement: float, demoted: bool) -> float:
    """Combine the calibrated reliability with the agreement share."""
    conf = clamp01(reliability * agreement)
    if demoted:
        conf = min(conf, FLOOR_DEMOTE)
    return round_conf(conf)


def overall_confidence(field_confs: dict[str, float], present: set[str],
                       n_conflicts: int) -> float:
    """Importance-weighted mean of field confidences minus coverage/conflict penalties."""
    num = den = 0.0
    for field, w in FIELD_IMPORTANCE.items():
        if field in field_confs:
            num += w * field_confs[field]
            den += w
    base = (num / den) if den else 0.0
    important = set(FIELD_IMPORTANCE)
    missing = important - present
    coverage_penalty = 0.15 * (len(missing) / len(important)) if important else 0.0
    conflict_penalty = min(0.20, 0.05 * n_conflicts)
    return round_conf(clamp01(base - coverage_penalty - conflict_penalty))
