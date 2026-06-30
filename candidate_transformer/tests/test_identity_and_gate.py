"""Identity resolution + honesty gate tests (design doc sections 5 & 6)."""
import os

from cdt.pipeline import Pipeline, InputSpec, read_inputs
from cdt.config import load_config


def _run(specs):
    return Pipeline(config=load_config(None)).run(specs)


# ---- identity resolution --------------------------------------------------
def test_tier_a_email_merges(samples):
    """The same person across CSV + ATS + GitHub + resume + notes -> ONE profile."""
    files = ["recruiter.csv", "ats.json", "github_janedoe.json", "jane_doe_resume.pdf", "notes.txt"]
    res = _run(read_inputs([os.path.join(samples, f) for f in files]))
    assert len(res.profiles) == 1


def test_same_name_different_people_never_merge(samples):
    """Tier-C (name alone) never merges; conflicting Tier-A keys stay distinct."""
    res = _run(read_inputs([os.path.join(samples, "edge_two_people.csv")]))
    assert len(res.profiles) == 2
    emails = sorted(p["emails"][0] for p in res.profiles)
    assert emails == ["jane.doe2@outlook.com", "jane.doe@gmail.com"]


def test_candidate_id_is_deterministic(samples):
    files = ["recruiter.csv", "ats.json"]
    a = _run(read_inputs([os.path.join(samples, f) for f in files])).output["candidate_id"]
    b = _run(read_inputs([os.path.join(samples, f) for f in files])).output["candidate_id"]
    assert a == b


# ---- Tier-B scalability fix: keyless-only pairing + selective blocking -----
def test_keyless_note_still_merges_via_tier_b():
    """A keyless recruiter note (no email/phone) must still merge into a keyed
    record when it shares >=2 attributes + name -- the Tier-B path is intact."""
    csv_data = ("name,email,phone,current_company,title,location\n"
                "Alan Turing,alan@bletchley.org,,Bletchley Park,Cryptanalyst,London\n").encode()
    note_data = (b"Name: Alan Turing\nCompany: Bletchley Park\nLocation: London\n"
                 b"Strong cryptography background.\n")
    res = _run([InputSpec(origin="recruiter.csv", data=csv_data, source_type="recruiter_csv"),
                InputSpec(origin="notes.txt", data=note_data, source_type="recruiter_notes")])
    assert len(res.profiles) == 1


def test_unique_contacts_sharing_a_city_do_not_false_merge():
    """Many records with UNIQUE emails but the SAME city must stay distinct.

    This is the case that used to drive O(n^2): a shared low-cardinality city no
    longer creates candidate pairs, and keyed records are never paired in Tier B.
    """
    rows = ["name,email,phone,current_company,title,location"]
    for i in range(40):
        rows.append(f"Person {i},p{i}@example.com,,Acme,Engineer,San Francisco")
    data = ("\n".join(rows) + "\n").encode()
    res = _run([InputSpec(origin="recruiter.csv", data=data, source_type="recruiter_csv")])
    assert len(res.profiles) == 40


def test_keyless_same_name_shared_city_only_stays_conservative():
    """Two keyless 'John Smith' sharing ONLY a city (no company/school) do NOT
    merge -- city is confirming-only, never a blocking key (prefer-empty-over-wrong)."""
    a = b"Name: John Smith\nLocation: San Francisco\n"
    b = b"Name: John Smith\nLocation: San Francisco\nNote: distinct second record.\n"
    res = _run([InputSpec(origin="a.txt", data=a, source_type="recruiter_notes"),
                InputSpec(origin="b.txt", data=b, source_type="recruiter_notes")])
    assert len(res.profiles) == 2


# ---- honesty gate ---------------------------------------------------------
def _csv(name, email, country):
    data = (f"name,email,phone,current_company,title,country\n"
            f"{name},{email},,Acme,Engineer,{country}\n").encode()
    return data


def test_true_tie_high_stakes_emits_null(samples):
    """Two equally-weighted sources disagree on country -> null, both kept."""
    files = ["ats.json", "jane_doe_resume.pdf"]   # ATS says US, resume says Germany
    res = _run(read_inputs([os.path.join(samples, f) for f in files]))
    p = res.output
    assert p["location"]["country"] is None
    conflicts = {c["field"]: c for c in p["conflicts"]}
    assert "location.country" in conflicts
    alts = {a["value"] for a in conflicts["location.country"]["alternatives"]}
    assert alts == {"US", "DE"}
    assert conflicts["location.country"]["reason"] == "honesty_gate_null"


def test_non_high_stakes_conflict_demotes_but_keeps_value(default_profile):
    """Headline has materially-different values -> kept but demoted, dissent logged."""
    conflicts = {c["field"]: c for c in default_profile["conflicts"]}
    assert "headline" in conflicts
    assert conflicts["headline"]["reason"] == "demoted"
    assert default_profile["headline"] is not None            # value kept
    assert default_profile["field_confidence"]["headline"] < 0.5  # but demoted
    assert len(conflicts["headline"]["alternatives"]) >= 1    # losers visible


def test_corroborated_field_has_high_confidence(default_profile):
    # full_name agreed by 4 sources -> high confidence, no conflict
    assert default_profile["full_name"] == "Jane Doe"
    assert default_profile["field_confidence"]["full_name"] > 0.9
