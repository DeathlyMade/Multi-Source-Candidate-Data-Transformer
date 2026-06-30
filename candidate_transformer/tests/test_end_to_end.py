"""End-to-end pipeline tests on the sample inputs (design doc section 10)."""
import json
import os

from cdt.pipeline import Pipeline, read_inputs
from cdt.config import load_config


def test_runs_and_validates_schema(default_profile):
    assert default_profile["full_name"] == "Jane Doe"
    assert isinstance(default_profile["overall_confidence"], float)
    assert 0.0 <= default_profile["overall_confidence"] <= 1.0


def test_normalization_dates_phones(default_profile):
    # phones in E.164
    assert "+14155550142" in default_profile["phones"]
    assert all(p.startswith("+") for p in default_profile["phones"])
    # experience dates as YYYY-MM
    starts = [e["start"] for e in default_profile["experience"] if e["start"]]
    assert any(s == "2017-06" for s in starts)


def test_skills_canonicalised_and_unknowns_flagged(default_profile):
    names = {s["name"] for s in default_profile["skills"]}
    assert {"Python", "Kubernetes", "PostgreSQL", "Machine Learning"} <= names
    # unknown skills are kept but with low confidence
    by_name = {s["name"]: s for s in default_profile["skills"]}
    if "HCL" in by_name:
        assert by_name["HCL"]["confidence"] <= 0.3


def test_merge_dedupes_experience_and_education(default_profile):
    companies = [e["company"] for e in default_profile["experience"]]
    assert companies.count("Acme Corp") == 1     # concurrent Acme roles merged
    assert any(e["institution"] == "Stanford University" for e in default_profile["education"])


def test_provenance_and_confidence_populated(default_profile):
    fields = {p["field"] for p in default_profile["provenance"]}
    assert "full_name" in fields and "skills" in fields
    assert default_profile["field_confidence"]["emails"] > 0.9


def test_degrades_gracefully_on_bad_source(main_inputs):
    res = Pipeline(config=load_config(None)).run(main_inputs)
    # broken.json + empty.txt must be isolated (ok=False) yet the run completes
    failed = [s for s in res.ledger.sources if not s.ok]
    assert len(failed) >= 1
    assert len(res.profiles) == 1


def test_determinism_same_bytes_same_output(main_inputs):
    a = json.dumps(Pipeline(config=load_config(None)).run(main_inputs).output, sort_keys=True)
    b = json.dumps(Pipeline(config=load_config(None)).run(main_inputs).output, sort_keys=True)
    assert a == b


def test_garbage_freetext_yields_no_phantom_claims(samples):
    """A notes file with a garbage line recognises only real signal, nothing invented."""
    res = Pipeline(config=load_config(None)).run(read_inputs([os.path.join(samples, "notes.txt")]))
    p = res.output
    # notes alone: email + phone + a few gazetteer skills, but no fabricated name/company guess
    skill_names = {s["name"] for s in p["skills"]}
    assert skill_names <= {"Python", "Kafka", "Spark"}     # only closed-vocab hits
    assert "jane.doe@gmail.com" in p["emails"]


def test_glob_input_expansion(samples):
    # Test wildcard expansion
    specs = read_inputs([os.path.join(samples, "*.csv")])
    assert len(specs) == 2
    origins = {s.origin for s in specs}
    assert origins == {"recruiter.csv", "edge_two_people.csv"}

    # Test wildcard expansion with explicit type prefix
    specs_typed = read_inputs([f"recruiter={os.path.join(samples, '*.csv')}"])
    assert len(specs_typed) == 2
    assert all(s.source_type == "recruiter" for s in specs_typed)
    origins_typed = {s.origin for s in specs_typed}
    assert origins_typed == {"recruiter.csv", "edge_two_people.csv"}

    # Test nonexistent pattern falls back to raising FileNotFoundError
    import pytest
    with pytest.raises(FileNotFoundError):
        read_inputs([os.path.join(samples, "nonexistent*.csv")])
