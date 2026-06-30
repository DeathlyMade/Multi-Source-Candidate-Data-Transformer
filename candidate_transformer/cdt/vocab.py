"""Loader for the versioned vocab/matrix data (design doc section 8).

Everything that tunes engine behaviour -- the source-reliability matrix, the
skill gazetteer, the country alias/calling-code maps -- lives in versioned JSON
under ``cdt/vocab/``. Loading them here (cached) keeps the engine pure and the
weights tunable with no code change.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

_VOCAB_DIR = os.path.join(os.path.dirname(__file__), "vocab")


def _load(name: str) -> dict:
    with open(os.path.join(_VOCAB_DIR, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def reliability_matrix() -> dict:
    return _load("reliability_matrix.json")


@lru_cache(maxsize=1)
def skills_vocab() -> dict:
    return _load("skills_vocab.json")


@lru_cache(maxsize=1)
def country_alias() -> dict:
    return _load("country_alias.json")


def reliability(source_type: str, field: str) -> float:
    """w(source_type, field): field-aware source reliability in [0,1]."""
    m = reliability_matrix()
    by_type = m.get("by_source_type", {}).get(source_type)
    if by_type is None:
        return float(m.get("default", 0.5))
    if field in by_type:
        return float(by_type[field])
    # location.country -> fall back to "location" group, then the type default.
    if "." in field:
        head = field.split(".", 1)[0]
        if head in by_type:
            return float(by_type[head])
    return float(by_type.get("_default", m.get("default", 0.5)))


def vocab_versions() -> dict:
    """The versions actually loaded -- echoed into output for explainability."""
    return {
        "reliability_matrix": reliability_matrix().get("version"),
        "skills_vocab": skills_vocab().get("version"),
        "country_alias": country_alias().get("version"),
    }
