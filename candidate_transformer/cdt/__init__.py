"""Multi-Source Candidate Data Transformer (cdt).

A deterministic, explainable engine that collapses many messy, overlapping,
partly-broken sources into one canonical profile per candidate -- fixed schema,
normalized, deduplicated, with per-field provenance, confidence, and recorded
dissent. Core invariant: prefer-empty-over-wrong.

Public API:
    from cdt import Pipeline, InputSpec, transform
"""
from __future__ import annotations

from .config import DEFAULT_CONFIG, ConfigError, load_config, validate_config
from .pipeline import InputSpec, Pipeline, RunResult, read_inputs, transform
from .validate import ValidationError

__version__ = "1.0.0"
__all__ = [
    "Pipeline", "InputSpec", "RunResult", "transform", "read_inputs",
    "load_config", "validate_config", "DEFAULT_CONFIG",
    "ConfigError", "ValidationError", "__version__",
]
