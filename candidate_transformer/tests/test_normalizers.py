"""Unit tests for the total, pure normalizers (design doc section 4)."""
from cdt.normalize.dates import normalize_date
from cdt.normalize.emails import normalize_email
from cdt.normalize.phones import normalize_phone
from cdt.normalize.country import normalize_country
from cdt.normalize.skills import normalize_skill
from cdt.normalize.links import classify_link


# ---- dates ----------------------------------------------------------------
def test_date_iso_and_month():
    assert normalize_date("2021-03")[0] == "2021-03"
    assert normalize_date("Jan 2021")[0] == "2021-01"
    assert normalize_date("2017")[0] == "2017"


def test_date_present_is_current_never_fabricated():
    val, q, reason, meta = normalize_date("Present")
    assert val is None and meta["is_current"] is True


def test_date_ambiguous_down_precisions_to_year():
    # 03/04/21 with no locale hint -> we refuse to guess the month
    val, q, reason, meta = normalize_date("03/04/21")
    assert val == "2021" and meta["precision"] == "year"
    # with a locale hint we can resolve it
    assert normalize_date("03/04/21", locale_hint="US")[0] == "2021-03"
    assert normalize_date("03/04/21", locale_hint="EU")[0] == "2021-04"


def test_date_unambiguous_when_component_over_12():
    assert normalize_date("25/03/2020")[0] == "2020-03"   # 25 must be the day


def test_date_two_digit_pivot_is_fixed():
    assert normalize_date("Jan 49")[0] == "2049-01"
    assert normalize_date("Jan 50")[0] == "1950-01"


def test_date_garbage_returns_null_not_exception():
    val, q, reason, meta = normalize_date("not a date")
    assert val is None and q == 0.0


# ---- phones ---------------------------------------------------------------
def test_phone_e164_from_plus():
    assert normalize_phone("+1 (415) 555-0142")[0] == "+14155550142"


def test_phone_nanp_leading_one():
    assert normalize_phone("1-415-555-0142")[0] == "+14155550142"


def test_phone_region_from_candidate_country():
    # 10 national digits + known country -> prepend calling code
    assert normalize_phone("020 7946 0958", candidate_country="GB")[0].startswith("+44")


def test_phone_never_fabricates_country_code():
    val, key, q, why, meta = normalize_phone("555-0142")   # no +, no region
    assert val is None and "no_country" in why


def test_phone_extension_kept_aside():
    val, key, q, why, meta = normalize_phone("+1 415 555 0142 ext 22")
    assert meta.get("ext") == "22" and val == "+14155550142"


# ---- emails ---------------------------------------------------------------
def test_email_canonical_match_key_strips_tag_and_dots():
    val, key, q, why = normalize_email("Jane.Doe+recruiting@Gmail.com")
    assert val == "jane.doe+recruiting@gmail.com"   # stored truth (lowercased)
    assert key == "janedoe@gmail.com"               # canonical match key


def test_email_invalid_returns_null():
    val, key, q, why = normalize_email("not-an-email")
    assert val is None and q == 0.0


# ---- country --------------------------------------------------------------
def test_country_alias_map():
    assert normalize_country("USA")[0] == "US"
    assert normalize_country("United Kingdom")[0] == "GB"
    assert normalize_country("Berlin, Germany")[0] == "DE"


def test_country_unknown_kept_as_text_not_guessed():
    val, canonical, q, why = normalize_country("Atlantis")
    assert canonical is False and val == "Atlantis"


# ---- skills ---------------------------------------------------------------
def test_skill_alias_canonicalises():
    assert normalize_skill("k8s") == ("Kubernetes", True, 1.0, "")
    assert normalize_skill("nodejs")[0] == "JavaScript"
    assert normalize_skill("Postgres")[0] == "PostgreSQL"


def test_skill_unknown_kept_low_confidence():
    name, canonical, q, why = normalize_skill("Cobol-77-Mainframe")
    assert canonical is False and q <= 0.3 and name


# ---- links ----------------------------------------------------------------
def test_link_classify_and_strip_tracking():
    url, kind, handle, q, why = classify_link("https://www.linkedin.com/in/janedoe?utm_source=x")
    assert kind == "linkedin" and handle == "janedoe" and "utm" not in url


def test_link_github_handle():
    url, kind, handle, q, why = classify_link("github.com/janedoe")
    assert kind == "github" and handle == "janedoe" and url.startswith("https://")
