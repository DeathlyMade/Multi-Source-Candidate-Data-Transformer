"""Output config: loading + PRE-RUN validation (design doc section 7).

The configurable-output twist runs on the SAME engine with no code change. A
config is a declarative projection: ``fields[] = {path, from, type, required,
normalize}`` plus ``include_confidence`` / ``include_provenance`` /
``include_alternatives`` and ``on_missing: null|omit|error``.

The config is validated against the canonical paths + types BEFORE the run, so a
bad ``from`` path or unknown type fails fast (design doc edge case
"Bad config path / missing field").
"""
from __future__ import annotations

import json
import re

# Canonical addressable paths -> type. This is the contract the projection
# `from` expressions are validated against.
CANONICAL_TYPES = {
    "candidate_id": "string",
    "full_name": "string",
    "emails": "string[]",
    "phones": "string[]",
    "location": "object",
    "location.city": "string",
    "location.region": "string",
    "location.country": "string",
    "links": "object",
    "links.linkedin": "string",
    "links.github": "string",
    "links.portfolio": "string",
    "links.other": "string[]",
    "headline": "string",
    "years_experience": "number",
    "skills": "skill[]",
    "skills[].name": "string[]",
    "skills[].confidence": "number[]",
    "experience": "object[]",
    "experience[].company": "string[]",
    "experience[].title": "string[]",
    "experience[].start": "string[]",
    "experience[].end": "string[]",
    "experience[].summary": "string[]",
    "education": "object[]",
    "education[].institution": "string[]",
    "education[].degree": "string[]",
    "education[].field": "string[]",
    "education[].end_year": "number[]",
    "provenance": "object[]",
    "overall_confidence": "number",
}

VALID_TYPES = {"string", "number", "boolean", "string[]", "number[]",
               "object", "object[]", "skill[]"}
VALID_NORMALIZE = {"E164", "canonical", "lower", "iso_country", "title", "none"}
VALID_ON_MISSING = {"null", "omit", "error"}
VALID_COMPUTE = {"provenance", "overall_confidence", "conflicts", "candidate_id"}

_INDEX_RE = re.compile(r"\[\d+\]")


class ConfigError(ValueError):
    """Raised on an invalid output config (fail fast, before the run)."""


def canonical_pattern(from_path: str) -> str:
    """Normalise a concrete `from` to its canonical pattern.

    "emails[0]" -> "emails"      (indexing a list field yields its element type)
    "skills[].name" -> "skills[].name"
    "location.country" -> "location.country"
    """
    p = _INDEX_RE.sub("", from_path)   # drop [0], [1], ...
    return p


def _from_base_type(from_path: str) -> str | None:
    pat = canonical_pattern(from_path)
    if pat in CANONICAL_TYPES:
        t = CANONICAL_TYPES[pat]
        # If the original indexed into a list, the result is the element type.
        if _INDEX_RE.search(from_path) and t.endswith("[]"):
            return t[:-2]
        return t
    return None


def validate_config(config: dict) -> None:
    """Validate structure, paths and types. Raises ConfigError on any problem."""
    if not isinstance(config, dict):
        raise ConfigError("config must be a JSON object")
    fields = config.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ConfigError("config.fields must be a non-empty list")

    on_missing = config.get("on_missing", "null")
    if on_missing not in VALID_ON_MISSING:
        raise ConfigError(f"on_missing must be one of {sorted(VALID_ON_MISSING)}; got {on_missing!r}")

    for flag in ("include_confidence", "include_provenance", "include_alternatives",
                 "include_conflicts"):
        if flag in config and not isinstance(config[flag], bool):
            raise ConfigError(f"{flag} must be boolean")

    seen_paths = set()
    for i, spec in enumerate(fields):
        if not isinstance(spec, dict):
            raise ConfigError(f"fields[{i}] must be an object")
        path = spec.get("path")
        if not path or not isinstance(path, str):
            raise ConfigError(f"fields[{i}].path is required and must be a string")
        if path in seen_paths:
            raise ConfigError(f"duplicate output path {path!r}")
        seen_paths.add(path)

        typ = spec.get("type")
        if typ is not None and typ not in VALID_TYPES:
            raise ConfigError(f"fields[{i}] ({path}): invalid type {typ!r}; valid={sorted(VALID_TYPES)}")

        norm = spec.get("normalize")
        if norm is not None and norm not in VALID_NORMALIZE:
            raise ConfigError(f"fields[{i}] ({path}): invalid normalize {norm!r}")

        compute = spec.get("compute")
        if compute is not None:
            if compute not in VALID_COMPUTE:
                raise ConfigError(f"fields[{i}] ({path}): invalid compute {compute!r}")
            continue  # computed fields do not reference a canonical `from`

        from_path = spec.get("from", path)
        base_type = _from_base_type(from_path)
        if base_type is None:
            raise ConfigError(
                f"fields[{i}] ({path}): `from` {from_path!r} does not resolve to a "
                f"canonical path. Known bases: {sorted(set(canonical_pattern(k) for k in CANONICAL_TYPES))}"
            )
        if "required" in spec and not isinstance(spec["required"], bool):
            raise ConfigError(f"fields[{i}] ({path}): required must be boolean")


# The built-in default config == the default output schema (design doc section 7
# "Default schema = the built-in default config"). Same engine, no code change.
DEFAULT_CONFIG = {
    "name": "default",
    "fields": [
        {"path": "candidate_id", "compute": "candidate_id", "type": "string"},
        {"path": "full_name", "type": "string"},
        {"path": "emails", "type": "string[]"},
        {"path": "phones", "type": "string[]", "normalize": "E164"},
        {"path": "location", "type": "object"},
        {"path": "links", "type": "object"},
        {"path": "headline", "type": "string"},
        {"path": "years_experience", "type": "number"},
        {"path": "skills", "type": "skill[]", "normalize": "canonical"},
        {"path": "experience", "type": "object[]"},
        {"path": "education", "type": "object[]"},
        {"path": "overall_confidence", "compute": "overall_confidence", "type": "number"},
    ],
    "include_confidence": True,
    "include_provenance": True,
    "include_alternatives": False,
    "include_conflicts": True,
    "on_missing": "null",
}


def load_config(path: str | None) -> dict:
    """Load + validate a config file, or return the validated default config."""
    if path is None:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    else:
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    validate_config(cfg)
    return cfg
