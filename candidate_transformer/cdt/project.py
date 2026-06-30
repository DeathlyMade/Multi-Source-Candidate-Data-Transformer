"""Projection layer -- the configurable-output twist (design doc section 7).

A PURE declarative transform from the full internal canonical profile to the
requested view. The same engine renders the default schema and any custom config
(no code change). Keeping this layer separate from reconciliation is what makes
configurable output a no-code feature.

`from` supports dotted paths, list indexing (`emails[0]`) and list projection
(`skills[].name`). Per-field `normalize`, `required`, and `on_missing`
(null|omit|error) are honoured here; the result is then validated by ``validate``.
"""
from __future__ import annotations

import re
from typing import Any

from .normalize.country import normalize_country
from .normalize.phones import normalize_phone
from .normalize.skills import normalize_skill

_MISSING = object()
_TOKEN = re.compile(r"^([A-Za-z0-9_]+)(\[\d+\]|\[\])?$")


class ProjectionError(ValueError):
    pass


def _resolve(view: dict, from_path: str):
    """Resolve a `from` expression against the canonical view.

    Returns the value or ``_MISSING``. Never raises on absent paths.
    """
    cur: Any = view
    segments = from_path.split(".")
    for seg in segments:
        m = _TOKEN.match(seg)
        if not m:
            return _MISSING
        name, suffix = m.group(1), m.group(2)
        if isinstance(cur, list):
            # projecting a previous "[]": map this segment across items
            out = []
            for item in cur:
                if isinstance(item, dict) and name in item:
                    out.append(item[name])
            cur = out
            if suffix and suffix.startswith("[") and suffix != "[]":
                idx = int(suffix[1:-1])
                cur = cur[idx] if 0 <= idx < len(cur) else _MISSING
            continue
        if not isinstance(cur, dict) or name not in cur:
            return _MISSING
        cur = cur[name]
        if suffix == "[]":
            if not isinstance(cur, list):
                return _MISSING
            # leave as list; next segment maps across it
        elif suffix:  # [idx]
            idx = int(suffix[1:-1])
            if not isinstance(cur, list) or not (0 <= idx < len(cur)):
                return _MISSING
            cur = cur[idx]
    return cur


def _apply_normalize(value, how: str | None, candidate_country):
    if value is None or how in (None, "none"):
        return value
    if how == "lower":
        return value.lower() if isinstance(value, str) else value
    if how == "title":
        return value.title() if isinstance(value, str) else value
    if how == "iso_country":
        if isinstance(value, str):
            v, _, _, _ = normalize_country(value)
            return v
        return value
    if how == "E164":
        def one(x):
            v, _, _, _, _ = normalize_phone(x, candidate_country=candidate_country)
            return v
        return [one(x) for x in value] if isinstance(value, list) else one(value)
    if how == "canonical":
        def one(x):
            if isinstance(x, dict):   # a skill object -> canonicalise its name only
                nm = x.get("name")
                cv = normalize_skill(nm)[0] if nm is not None else None
                return {**x, "name": cv}
            return normalize_skill(x)[0]
        return [one(x) for x in value] if isinstance(value, list) else one(value)
    return value


def _coerce(value, typ: str):
    """Coerce/shape the resolved value to the declared type (best-effort, total)."""
    if value is None:
        return None
    if typ == "string":
        return value if isinstance(value, str) else str(value)
    if typ == "number":
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    if typ == "boolean":
        return bool(value)
    if typ == "string[]":
        items = value if isinstance(value, list) else [value]
        return [x if isinstance(x, str) else str(x) for x in items if x is not None]
    if typ == "number[]":
        items = value if isinstance(value, list) else [value]
        return [x for x in items if isinstance(x, (int, float)) and not isinstance(x, bool)]
    if typ == "skill[]":
        items = value if isinstance(value, list) else [value]
        out = []
        for it in items:
            if isinstance(it, dict):
                out.append({"name": it.get("name"), "confidence": it.get("confidence"),
                            "sources": list(it.get("sources", []))})
            elif it is not None:
                out.append({"name": it, "confidence": None, "sources": []})
        return out
    # object / object[] -> pass through
    return value


def _set_out(out: dict, path: str, value):
    """Write ``value`` into ``out`` at a dotted ``path`` (creates nested dicts)."""
    parts = path.split(".")
    cur = out
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def project(assembled, config: dict) -> dict:
    """Render ``assembled`` (internal canonical profile) per ``config``."""
    view = dict(assembled.view)
    view["candidate_id"] = assembled.candidate_id
    meta = assembled.meta
    country = (view.get("location") or {}).get("country")
    candidate_country = country if (isinstance(country, str) and len(country) == 2) else None

    on_missing = config.get("on_missing", "null")
    include_conf = config.get("include_confidence", False)
    include_prov = config.get("include_provenance", False)
    include_alts = config.get("include_alternatives", False)
    include_conflicts = config.get("include_conflicts", False)

    out: dict = {}
    field_conf: dict = {}

    for spec in config["fields"]:
        path = spec["path"]
        typ = spec.get("type", "string")
        compute = spec.get("compute")

        if compute == "candidate_id":
            _set_out(out, path, assembled.candidate_id)
            continue
        if compute == "overall_confidence":
            _set_out(out, path, assembled.overall_confidence)
            continue
        if compute == "provenance":
            _set_out(out, path, list(assembled.provenance))
            continue
        if compute == "conflicts":
            _set_out(out, path, [c.to_dict() for c in assembled.conflicts])
            continue

        from_path = spec.get("from", path)
        resolved = _resolve(view, from_path)

        if resolved is _MISSING or resolved is None:
            if on_missing == "error":
                raise ProjectionError(f"missing value for {path!r} (from {from_path!r})")
            if on_missing == "omit":
                continue
            _set_out(out, path, None)   # on_missing == "null"
            continue

        value = _apply_normalize(resolved, spec.get("normalize"), candidate_country)
        value = _coerce(value, typ)
        _set_out(out, path, value)

        # compact field_confidence keyed by the canonical base path
        if include_conf:
            base = re.sub(r"\[\d*\]", "", from_path).split(".")[0]
            base_full = re.sub(r"\[\d*\].*$", "", from_path)
            for k in (from_path, base_full, base):
                if k in meta and meta[k].get("confidence") is not None:
                    field_conf[path] = meta[k]["confidence"]
                    break

    if include_conf and field_conf:
        out["field_confidence"] = field_conf
    if include_prov:
        out["provenance"] = list(assembled.provenance)
    if include_conflicts:
        out["conflicts"] = [c.to_dict() for c in assembled.conflicts]
    if include_alts:
        out["alternatives"] = {k: v.get("alternatives", []) for k, v in meta.items()
                               if v.get("alternatives")}
    return out
