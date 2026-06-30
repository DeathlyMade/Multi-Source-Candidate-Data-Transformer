# Multi-Source Candidate Data Transformer

Collapse many messy, overlapping, partly-broken sources into **one canonical
profile per candidate** — fixed schema, normalized, deduplicated, with per-field
**provenance**, **confidence**, and **recorded dissent**.

> **Core invariant: prefer-empty-over-wrong.** On an unresolved conflict we
> demote confidence or emit `null` and keep the losing values visible. We never
> fabricate. Wrong-but-confident silently pollutes hiring decisions; honestly-
> empty does not.

This is the Stage-2 implementation of the attached one-page design. The engine is
a **deterministic reduce over an append-only Evidence Ledger**, with a separate
**Projection layer** that makes configurable output a no-code feature.

---

## Pipeline

```
Ingest → Extract → Normalize → Canonicalize → Resolve → Reconcile → Calibrate → Project → Validate
```

| Phase | What happens | Where |
|---|---|---|
| **Ingest** | Each source is content-addressed `id = sha256(type + bytes)`; identical sources dedupe; bad sources are isolated. | `content_address.py`, `ledger.py`, `adapters/base.py` |
| **Extract** | One best-effort adapter per source type appends typed `Claim{path, raw, source_id, type, extractor, locator}` to the ledger. | `adapters/` |
| **Normalize** | Format: dates, phones (E.164), emails, links, text. Total & pure (`value | None` + reason, never throws). | `normalize/` |
| **Canonicalize** | Semantics: skills (versioned vocab), country (ISO-3166), org. | `normalize/skills.py`, `normalize/country.py` |
| **Resolve** | Conservative tiered identity match keys + union-find. | `resolve.py` |
| **Reconcile** | Evidence-weighted vote with the **honesty gate**; multi-valued union+dedupe. | `reconcile.py` |
| **Calibrate** | Confidence in `[0,1]` (noisy-OR corroboration, agreement, conflict penalty) + overall. | `calibrate.py` |
| **Project** | Pure declarative transform to the requested schema (default or custom config). | `project.py`, `config.py` |
| **Validate** | Two gates: internal invariants + output-schema conformance. | `validate.py` |

---

## Quick start

```bash
# 1. install (Python 3.10+)
python -m venv .venv && source .venv/bin/activate  # (Linux/Mac)
python -m venv .venv && .venv\Scripts\activate    # (Windows)

cd candidate_transformer

pip install -r requirements.txt          # pypdf, python-docx, chardet
#   (or: pip install -e .  → installs the `cdt` console command)

# 2. run end-to-end on the sample inputs → DEFAULT schema
python -m cdt transform samples/recruiter.csv samples/ats.json samples/github_janedoe.json samples/jane_doe_resume.pdf samples/notes.txt samples/broken.json samples/empty.txt --out output/profile_default.json

# 3. same engine, CUSTOM output config (the design-doc example)
python -m cdt transform samples/*.csv samples/ats.json samples/github_janedoe.json samples/jane_doe_resume.pdf samples/notes.txt --config configs/custom_example.json --out output/profile_custom_example.json

# 4. validate a config WITHOUT running (fail fast on a bad path/type)
python -m cdt validate-config configs/bad_config.json  # -> INVALID, exit 2

# 5. "why this value?" — provenance + per-field score breakdown
python -m cdt explain samples/recruiter.csv samples/ats.json --field location.country
```

Inputs auto-detect their source type from extension + a content peek. Force a
type with a `type=path` prefix, e.g. `recruiter_notes=samples/notes.txt`.

Run the tests:

```bash
pip install pytest
python -m pytest -q          # 45 tests
```

---

## Sources (≥1 structured + ≥1 unstructured)

**Structured**
- `recruiter_csv` — rows: name, email, phone, current_company, title, location.
- `ats_json` — semi-structured ATS export whose field names **do not** match ours
  (e.g. `given_name`/`family_name`, `contact.email_addresses`, `org`,
  `work_history[].organization`); the adapter owns the foreign-key mapping.

**Unstructured**
- `github` — a **saved export** of the public GitHub REST API (`user` + `repos`).
  Skills are *inferred* from repo languages (weighted lower than declared skills).
  We read a fixture for deterministic, offline, ToS-safe runs — the same stance
  the design takes for LinkedIn.
- `resume` — PDF / DOCX / TXT prose, parsed **section-aware** (Summary,
  Experience, Education, Skills) with bounded regex for contact details.
- `recruiter_notes` — free text: a **closed-vocabulary gazetteer** for skills +
  bounded regex for emails/phones/links. Precision over recall — a genuinely
  garbage note recognises nothing and yields **0 claims** (no guessing).

Each adapter is isolated: a corrupt / empty / 404 source produces 0 claims, is
logged, and the run continues (see `samples/broken.json`, `samples/empty.txt`).

---

## Default output schema

`candidate_id, full_name, emails[], phones[], location{city,region,country},
links{linkedin,github,portfolio,other[]}, headline, years_experience,
skills[{name,confidence,sources[]}], experience[{company,title,start,end,summary}],
education[{institution,degree,field,end_year}]` — plus `provenance[{field,source,
method}]`, `field_confidence`, `conflicts[]`, and `overall_confidence`.

**Normalized formats**

| Field | Canonical form |
|---|---|
| dates | `YYYY-MM` (+ precision); `present` → `null` + `is_current`; ambiguous `03/04/21` → locale hint else down-precision to year; 2-digit year via fixed pivot |
| phones | E.164; region from in-number `+CC` > candidate country > config default; invalid → `null` (never fabricate a country code); extensions kept aside |
| emails | lower + NFC; canonical key (strip `+tag`, dots for dot-insensitive providers) for matching; original stored as truth |
| country | ISO-3166-1 alpha-2 via alias map (`USA→US`, `UK→GB`); unknown kept as free text (`canonical=false`) |
| skills | versioned vocab + alias + deterministic fuzzy fallback; unknown kept with `canonical=false` + low confidence |
| links | https, tracking params stripped, classified linkedin/github/portfolio/other, canonical handle |
| years_experience | derived from merged non-overlapping closed spans vs. stated; conflict lowers confidence |

---

## Merge / conflict-resolution policy

**Identity resolution (conservative).** Tiered match keys:
- **A** — email / E.164 phone / github|linkedin handle → any one shared key merges.
- **B** — name + {company | school | location} → needs **two** B attributes.
- **C** — name alone → **never** merges.

Two records that both carry Tier-A keys but share none are assumed to be distinct
people (a false merge is as dangerous as wrong-but-confident). Union-find runs
over shared-key blocks → near-linear, not O(n²).

**Reconciliation (single-valued).** Evidence-weighted vote
`score(v) = Σ w(s,f)·q(s,v,f)·recency(s)`, where `w` comes from a **versioned,
field-aware source-reliability matrix** (`cdt/vocab/reliability_matrix.json` —
tunable with no code change). Normalized-equivalent values **collapse first**, so
the deterministic tie-break (reliability → lexicographic) only ever picks a stable
representative *among equivalents* — never between materially-different values.

**Honesty gate.** When materially-different values remain inside the tie margin:
- **high-stakes** identity/contact fields (`full_name`, `emails`, `phones`,
  `location.country`, links) → emit **`null`**;
- otherwise → **demote** to a floor confidence.
Losers are always kept in `alternatives[]`, and the field is logged in `conflicts[]`.

**Confidence** is `clamp(reliability · agreement · conflict_penalty)` using a
noisy-OR over per-source contributions (independent corroboration with diminishing
returns, deduped per source-origin), fixed-rounded and documented as **ordinal**.
`overall_confidence` is an importance-weighted mean minus coverage/conflict penalties.

---

## Configurable output (the twist)

The internal canonical profile is always built in full; a **separate, pure
projection layer** renders the requested view — *same engine, no code change*. A
config is declarative:

```json
{
  "fields": [
    {"path": "full_name", "type": "string", "required": true},
    {"path": "primary_email", "from": "emails[0]", "type": "string", "required": true},
    {"path": "phone", "from": "phones[0]", "type": "string", "normalize": "E164"},
    {"path": "skills", "from": "skills[].name", "type": "string[]", "normalize": "canonical"}
  ],
  "include_confidence": true,
  "on_missing": "null"
}
```

It can: select a subset of fields, rename/remap via `from` (`emails[0]`,
`skills[].name`, `location.country`), set per-field `normalize`, toggle
provenance/confidence/alternatives, and choose `on_missing: null | omit | error`.
**The config is validated against canonical paths + types *before* the run**
(bad path/type fails fast), and the projected result is validated against the
requested schema *after*. See `configs/` for the doc example plus a
subset/rename/omit example, and an intentionally-broken one.

---

## Edge cases handled (see `samples/` + `tests/`)

| Case | Handling |
|---|---|
| Conflicting value (country) | evidence vote + honesty gate → **null**, alternatives kept (demo: ATS `US` vs résumé `DE`, margin 0.0) |
| Conflicting value (headline) | non-high-stakes → kept but **demoted**, dissent logged |
| Different people, same name | tiered keys; name-only never merges (`samples/edge_two_people.csv` → 2 profiles) |
| Corrupt / empty source | adapter isolated → 0 claims, logged, run continues (`broken.json`, `empty.txt`) |
| Garbage free-text note | gazetteer/regex match only → nothing recognized = 0 claims |
| Ambiguous date `03/04/21` | locale hint else down-precision to year (never guess) |
| Encoding / mojibake / RTL | chardet decode + NFC + control-char stripping; never crash |
| Bad config path / missing field | pre-run config validation; `on_missing: null|omit|error` |

Everything is **deterministic**: content-addressed inputs, pure reduce, no
randomness/wall-clock in logic (recency is derived from source timestamps relative
to the newest seen), stable sorts, fixed rounding, versioned matrix/vocab, no LLM.
`candidate_id` is a `UUIDv5` over the canonical identity keys.

## Assumptions
- **GitHub and LinkedIn data are read from saved API-export fixtures**, not fetched live, which keeps runs deterministic, offline, and ToS-safe.
- **Recency comes from source-provided timestamps** (optional per source), measured relative to the newest source seen in the run rather than the wall-clock, so identical inputs always reduce to the same profile.
- **`years_experience` counts closed (start + end) spans only.** Open or "present" roles are flagged `is_current` but not measured, since measuring one would require a wall-clock and break determinism.
- **The skills vocab, country-alias map, and source-reliability matrix are versioned seed files** meant to be extended. Unknown skills and countries are kept and flagged (`canonical=false`), never invented or silently dropped.
- **Confidence is ordinal, not a calibrated probability.** It is meaningful for ranking and within-run comparison, not as an absolute likelihood.
- **One invocation processes a single in-memory batch.** Identity resolution merges records within that batch; global dedup across separate runs is a scale-out concern (shard per candidate-group), not handled in one process.

## Deliberately descoped (stated, not hidden)
ML/probabilistic linkage (rules instead) · LLM extraction (deterministic parsers
keep determinism + explainability) · full org/skill/geo ontologies (seed +
pluggable) · live LinkedIn/GitHub scraping (export fixture, ToS) · learned weights
(fixed, versioned matrix) · UI polish (a clean CLI suffices). `years_experience`
is derived from **closed** spans only — open/current roles are not measured because
that would require a wall-clock, which would break determinism.

---

## Project layout

```
cdt/
  content_address.py   ledger.py   model.py   vocab.py
  adapters/    recruiter_csv, ats_json, github_api, resume_file, recruiter_notes
  normalize/   text, dates, phones, emails, country, links, skills, names
  resolve.py   reconcile.py   calibrate.py   project.py   config.py   validate.py
  pipeline.py  cli.py
  vocab/       reliability_matrix.json  skills_vocab.json  country_alias.json
configs/   default.json  custom_example.json  custom_contacts.json  bad_config.json
samples/   recruiter.csv  ats.json  github_janedoe.json  jane_doe_resume.pdf|txt  notes.txt
           broken.json  empty.txt  edge_two_people.csv
output/    profile_default.json  profile_custom_example.json  profile_custom_contacts.json
tests/     normalizers · identity+gate · projection+validation · end-to-end  (45 tests)
```

---

## Output on Sample Inputs

Running the pipeline on the provided sample inputs:
```bash
python -m cdt transform samples/recruiter.csv samples/ats.json samples/github_janedoe.json samples/jane_doe_resume.pdf samples/notes.txt samples/broken.json samples/empty.txt --out output/profile_default.json
```

Produces the following canonical JSON profile (in `output/profile_default.json`), showcasing the **evidence ledger reduce**, the **honesty gate** (nullifying conflicting high-stakes country info), **confidence demotion** (for headline conflicts), and **provenance tracking**:

```json
{
  "candidate_id": "7ba49237-bd89-5f9c-a69c-87e732ca8007",
  "full_name": "Jane Doe",
  "emails": [
    "jane.doe@gmail.com",
    "j.doe@acme.com"
  ],
  "phones": [
    "+14155550142",
    "+49301234567"
  ],
  "location": {
    "city": "San Francisco",
    "region": null,
    "country": null
  },
  "links": {
    "linkedin": "https://linkedin.com/in/janedoe",
    "github": "https://github.com/janedoe",
    "portfolio": "https://janedoe.dev",
    "other": []
  },
  "headline": "Staff Engineer at Acme",
  "years_experience": 4,
  "skills": [
    {
      "name": "Python",
      "confidence": 0.989,
      "sources": [
        "ats_json:sha256:76a57a87afc89879",
        "github:sha256:be1e12373d8d3c94",
        "recruiter_notes:sha256:40d55ba06009152d",
        "resume:sha256:dbe4bb979e1d51cd"
      ]
    },
    {
      "name": "Kafka",
      "confidence": 0.964,
      "sources": [
        "ats_json:sha256:76a57a87afc89879",
        "recruiter_notes:sha256:40d55ba06009152d",
        "resume:sha256:dbe4bb979e1d51cd"
      ]
    },
    {
      "name": "Kubernetes",
      "confidence": 0.94,
      "sources": [
        "ats_json:sha256:76a57a87afc89879",
        "resume:sha256:dbe4bb979e1d51cd"
      ]
    },
    {
      "name": "PostgreSQL",
      "confidence": 0.94,
      "sources": [
        "ats_json:sha256:76a57a87afc89879",
        "resume:sha256:dbe4bb979e1d51cd"
      ]
    },
    {
      "name": "Distributed Systems",
      "confidence": 0.8,
      "sources": [
        "resume:sha256:dbe4bb979e1d51cd"
      ]
    },
    {
      "name": "Scala",
      "confidence": 0.8,
      "sources": [
        "resume:sha256:dbe4bb979e1d51cd"
      ]
    },
    {
      "name": "scikit-learn",
      "confidence": 0.8,
      "sources": [
        "resume:sha256:dbe4bb979e1d51cd"
      ]
    },
    {
      "name": "Spark",
      "confidence": 0.8,
      "sources": [
        "resume:sha256:dbe4bb979e1d51cd"
      ]
    },
    {
      "name": "Go",
      "confidence": 0.7,
      "sources": [
        "github:sha256:be1e12373d8d3c94"
      ]
    },
    {
      "name": "JavaScript",
      "confidence": 0.7,
      "sources": [
        "github:sha256:be1e12373d8d3c94"
      ]
    },
    {
      "name": "Machine Learning",
      "confidence": 0.7,
      "sources": [
        "ats_json:sha256:76a57a87afc89879"
      ]
    },
    {
      "name": "HCL",
      "confidence": 0.21,
      "sources": [
        "github:sha256:be1e12373d8d3c94"
      ]
    },
    {
      "name": "Shell",
      "confidence": 0.21,
      "sources": [
        "github:sha256:be1e12373d8d3c94"
      ]
    }
  ],
  "experience": [
    {
      "company": "Acme Corp",
      "title": "Staff Software Engineer",
      "start": "2021-01",
      "end": null,
      "summary": "Lead the candidate-data platform team. Python, Kafka, Kubernetes, PostgreSQL."
    },
    {
      "company": "DataWorks Inc",
      "title": "Senior Software Engineer",
      "start": "2017-06",
      "end": "2020-12",
      "summary": "Built streaming pipelines in Scala and Spark."
    }
  ],
  "education": [
    {
      "institution": "Stanford University",
      "degree": "M.S.",
      "field": "Computer Science",
      "end_year": 2017
    },
    {
      "institution": "UC Berkeley",
      "degree": "B.S.",
      "field": "Computer Science",
      "end_year": 2015
    }
  ],
  "overall_confidence": 0.804,
  "field_confidence": {
    "full_name": 0.994,
    "emails": 0.999,
    "phones": 0.984,
    "headline": 0.196,
    "years_experience": 0.6,
    "skills": 0.989,
    "experience": 0.994,
    "education": 0.927
  },
  "provenance": [
    {
      "field": "full_name",
      "source": "ats_json:sha256:76a57a87afc89879",
      "method": "corroborated"
    },
    ...
  ],
  "conflicts": [
    {
      "field": "headline",
      "reason": "demoted",
      "winner": "Staff Engineer at Acme",
      "alternatives": [
        {
          "value": "Backend engineer with 7 years building distributed data platforms at scale.",
          "score": 0.65,
          "sources": [
            "resume:sha256:dbe4bb979e1d51cd"
          ]
        },
        ...
      ],
      "margin": 0.071
    },
    {
      "field": "location.country",
      "reason": "honesty_gate_null",
      "winner": null,
      "alternatives": [
        {
          "value": "DE",
          "score": 0.75,
          "sources": [
            "resume:sha256:dbe4bb979e1d51cd"
          ]
        },
        {
          "value": "US",
          "score": 0.75,
          "sources": [
            "ats_json:sha256:76a57a87afc89879"
          ]
        }
      ],
      "margin": 0.0
    }
  ]
}
```

---

## Tests Written

The candidate transformer features an extensive test suite divided into logical categories matching the pipeline stages:

1. **`test_normalizers.py`** (Unit tests for pure normalizers)
   * **Dates**: ISO/Month parsing, ambiguous format handling (`03/04/21` Refuses to guess month without a locale hint), present/current role markers, and 2-digit pivot.
   * **Phones**: NANP parsing, E.164 normalization, calling-code prepending via candidate location context, isolation of extensions, and refusal to fabricate country codes.
   * **Emails**: Canonical matching key generation (removes Gmail tags/dots) vs preserved source string.
   * **Countries**: Alias map mappings (`USA` -> `US`, `UK` -> `GB`), unknown location handling (flagged as non-canonical, not guessed).
   * **Skills**: Skills vocabulary and alias mappings (`k8s` -> `Kubernetes`), confidence demotion of unknown skills.
   * **Links**: Classifier for LinkedIn/GitHub/Portfolio/Other links, stripping tracking arguments (e.g. `utm_source`).

2. **`test_identity_and_gate.py`** (Identity Resolution & Honesty Gate)
   * **Tier-A Merging**: Multi-source identity merging based on solid keys (matching emails, phones, github/linkedin handles).
   * **Tier-B Merging**: Multi-attribute validation (name + 2 matching fields) to merge keyless/incomplete files (such as recruiter notes) safely.
   * **Conservative Separation**: Asserts that matching names alone (Tier-C) never merge, keeping distinct people separate.
   * **Scalability Blockers**: Verifies low-cardinality values (e.g., shared city) do not lead to O(N^2) candidate-pairing paths.
   * **Honesty Gate Nullification**: Tests that high-stakes conflicts (such as conflicting countries with equal source weight) are safely set to `null` while recording dissent in `conflicts[]`.
   * **Confidence Demotion**: Verifies that non-high-stakes conflicts (e.g., headlines) are retained but their confidence score is demoted.

3. **`test_projection_and_validation.py`** (Projection & Validation)
   * **Default schema conformance**: Checks presence of all required canonical fields in the default projection.
   * **Declarative custom projection**: Tests renaming, subsetting, and field extraction (e.g. mapping `emails[0]` to `primary_email`).
   * **Missing Field behaviors**: Verifies custom options for missing data (`null`, `omit`, `error`).
   * **Fast Fail validation**: Rejects invalid config pathways or mismatched data types prior to starting pipeline execution.

4. **`test_end_to_end.py`** (End-to-End Pipeline)
   * Runs the full engine pipeline on provided files to check correctness of deduplication, date normalization, skill mapping, and final validation.
   * Verifies **determinism** (identical source byte representations resolve to identical JSON profiles).
   * Ensures that empty or corrupted files degrade gracefully without crashing the pipeline.
   * Assures that recruiter notes only extract signals that match regexes or closed-vocab gazetteers, preventing fabrication of phantom fields.
