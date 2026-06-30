"""Validation gates (design doc section 8: "two gates").

  * ``validate_internal`` -- invariants on the internal canonical profile
    (confidence in [0,1], every present field is provenanced, candidate_id is a
    UUID, list fields are lists, alternatives well-formed).
  * ``validate_output``   -- the projected result is checked against the
    REQUESTED schema (types + required), so we never return something the config
    promised but the engine failed to produce.

Both collect every problem and raise once with a full report (fail loud, but
only after we know everything that is wrong).
"""
from __future__ import annotations

import re

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ValidationError(ValueError):
    pass


def _is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def validate_internal(assembled) -> None:
    problems: list[str] = []
    cid = assembled.candidate_id
    if not isinstance(cid, str) or not _UUID_RE.match(cid):
        problems.append(f"candidate_id is not a UUID: {cid!r}")

    for fp, env in assembled.meta.items():
        conf = env.get("confidence")
        if conf is not None and not (0.0 <= conf <= 1.0):
            problems.append(f"{fp}: confidence {conf} out of [0,1]")
        for alt in env.get("alternatives", []):
            if "value" not in alt:
                problems.append(f"{fp}: malformed alternative {alt!r}")

    oc = assembled.overall_confidence
    if not (_is_number(oc) and 0.0 <= oc <= 1.0):
        problems.append(f"overall_confidence {oc} out of [0,1]")

    # every present field must be traceable (have >=1 provenance row) -- except
    # honesty-gate nulls, which are intentionally value-less.
    provenanced = {p["field"] for p in assembled.provenance}
    for fp in assembled.present:
        if fp not in provenanced:
            problems.append(f"{fp}: present but has no provenance (untraceable)")

    for lf in ("emails", "phones", "skills", "experience", "education"):
        v = assembled.view.get(lf)
        if v is not None and not isinstance(v, list):
            problems.append(f"{lf}: expected list, got {type(v).__name__}")

    if problems:
        raise ValidationError("internal invariants failed:\n  - " + "\n  - ".join(problems))


def _type_ok(value, typ: str) -> bool:
    if typ == "string":
        return isinstance(value, str)
    if typ == "number":
        return _is_number(value)
    if typ == "boolean":
        return isinstance(value, bool)
    if typ == "string[]":
        return isinstance(value, list) and all(isinstance(x, str) for x in value)
    if typ == "number[]":
        return isinstance(value, list) and all(_is_number(x) for x in value)
    if typ == "object":
        return isinstance(value, dict)
    if typ in ("object[]", "skill[]"):
        return isinstance(value, list) and all(isinstance(x, dict) for x in value)
    return True


def _get_out(output: dict, path: str):
    cur = output
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return _MISSING
        cur = cur[p]
    return cur


_MISSING = object()


def validate_output(output: dict, config: dict) -> None:
    """Gate 2: the projected output conforms to the REQUESTED schema."""
    problems: list[str] = []
    on_missing = config.get("on_missing", "null")

    for spec in config["fields"]:
        path = spec["path"]
        typ = spec.get("type", "string")
        required = bool(spec.get("required", False))
        present = _get_out(output, path)

        if present is _MISSING:
            if on_missing == "omit" and not required:
                continue
            if required:
                problems.append(f"required field {path!r} is absent")
            continue

        if present is None:
            if required:
                problems.append(f"required field {path!r} is null")
            continue  # null allowed for non-required

        if not _type_ok(present, typ):
            problems.append(f"field {path!r}: expected {typ}, got {type(present).__name__}={present!r}")

        if required and typ.endswith("[]") and isinstance(present, list) and not present:
            problems.append(f"required list field {path!r} is empty")

    if problems:
        raise ValidationError("output schema validation failed:\n  - " + "\n  - ".join(problems))
