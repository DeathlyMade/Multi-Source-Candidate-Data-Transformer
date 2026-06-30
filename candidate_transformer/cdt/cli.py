"""Thin CLI surface (design doc section 10).

  transform <files...> [--config cfg.json] [--out profile.json]
                       [--locale US|EU|UK|IN] [--default-country US] [--explain]
  validate-config <cfg.json>          # pre-run config gate, fail fast
  explain <files...> [--field F]      # "why this value?" -- ledger + scores

A clean CLI is intentionally sufficient (the engine and correctness matter more
than surface polish). Inputs auto-detect their source type; an explicit
``type=path`` prefix forces it (e.g. ``recruiter_notes=samples/notes.txt``).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .config import ConfigError, load_config, validate_config
from .pipeline import Pipeline, read_inputs, transform
from .validate import ValidationError


def _dump(obj, indent=2) -> str:
    return json.dumps(obj, indent=indent, ensure_ascii=False)


def _summary(result) -> str:
    led = result.ledger
    ok = sum(1 for s in led.sources if s.ok)
    bad = sum(1 for s in led.sources if not s.ok)
    n_conf = sum(len(a.conflicts) for a in result.assembled)
    return (f"sources: {ok} ok, {bad} failed | claims: {len(led)} | "
            f"candidates: {len(result.profiles)} | conflicts: {n_conf} | "
            f"vocab: {result.vocab}")


def _cmd_transform(args) -> int:
    try:
        config = load_config(args.config)
    except (ConfigError, OSError, json.JSONDecodeError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    pipe = Pipeline(config=config, locale_hint=args.locale,
                    default_country=args.default_country)
    try:
        result = pipe.run(read_inputs(args.inputs))
    except ValidationError as exc:
        print(f"validation failed: {exc}", file=sys.stderr)
        return 3

    text = _dump(result.output, indent=args.indent)
    if args.out:
        out_dir = os.path.dirname(args.out)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)

    print(_summary(result), file=sys.stderr)
    if args.explain:
        _print_explain(result, field=None)
    return 0


def _cmd_validate_config(args) -> int:
    try:
        with open(args.config, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        validate_config(cfg)
    except (ConfigError, OSError, json.JSONDecodeError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {args.config} is a valid output config "
          f"({len(cfg.get('fields', []))} fields, on_missing={cfg.get('on_missing', 'null')})")
    return 0


def _cmd_explain(args) -> int:
    result = transform(args.inputs, config_path=args.config,
                       locale_hint=args.locale, default_country=args.default_country)
    _print_explain(result, field=args.field)
    print(_summary(result), file=sys.stderr)
    return 0


def _print_explain(result, field):
    led = result.ledger
    print("\n=== INGEST LOG ===", file=sys.stderr)
    for line in led.logs:
        print("  " + line, file=sys.stderr)
    for a in result.assembled:
        print(f"\n=== CANDIDATE {a.candidate_id} (overall={a.overall_confidence}) ===",
              file=sys.stderr)
        items = a.meta.items() if field is None else [(field, a.meta.get(field, {}))]
        for fp, env in items:
            if not env:
                continue
            line = f"  {fp}: value={env.get('value')!r} conf={env.get('confidence')} " \
                   f"method={env.get('method')} sources={env.get('sources')}"
            print(line, file=sys.stderr)
            for alt in env.get("alternatives", []):
                print(f"      alt: {alt}", file=sys.stderr)
        if a.conflicts:
            print("  -- conflicts --", file=sys.stderr)
            for c in a.conflicts:
                print(f"     {c.to_dict()}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cdt", description="Multi-Source Candidate Data Transformer")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("transform", help="run the pipeline and emit a profile")
    t.add_argument("inputs", nargs="+", help="input files (type auto-detected; or type=path)")
    t.add_argument("--config", help="output config JSON (default schema if omitted)")
    t.add_argument("--out", help="write JSON here (else stdout)")
    t.add_argument("--locale", choices=["US", "EU", "UK", "IN"], help="date locale hint")
    t.add_argument("--default-country", help="fallback ISO alpha-2 for phone region")
    t.add_argument("--indent", type=int, default=2)
    t.add_argument("--explain", action="store_true", help="print provenance/score breakdown")
    t.set_defaults(func=_cmd_transform)

    v = sub.add_parser("validate-config", help="validate an output config (fail fast)")
    v.add_argument("config")
    v.set_defaults(func=_cmd_validate_config)

    e = sub.add_parser("explain", help="why-this-value breakdown for the inputs")
    e.add_argument("inputs", nargs="+")
    e.add_argument("--config")
    e.add_argument("--field", help="restrict explanation to one field path")
    e.add_argument("--locale", choices=["US", "EU", "UK", "IN"])
    e.add_argument("--default-country")
    e.set_defaults(func=_cmd_explain)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
