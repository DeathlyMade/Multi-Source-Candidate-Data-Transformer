"""Projection + validation gate tests (design doc sections 7 & 8)."""
import os

import pytest

from cdt.config import ConfigError, DEFAULT_CONFIG, load_config, validate_config
from cdt.project import project, ProjectionError
from cdt.pipeline import Pipeline
from cdt.validate import ValidationError, validate_output


def _assemble(main_inputs, config):
    pipe = Pipeline(config=config)
    res = pipe.run(main_inputs)
    return res.assembled[0]


def test_default_schema_has_all_canonical_fields(default_profile):
    for f in ["candidate_id", "full_name", "emails", "phones", "location", "links",
              "headline", "years_experience", "skills", "experience", "education",
              "provenance", "overall_confidence"]:
        assert f in default_profile


def test_custom_example_config_shapes_output(main_inputs, configs):
    cfg = load_config(os.path.join(configs, "custom_example.json"))
    out = Pipeline(config=cfg).run(main_inputs).output
    assert set(out) >= {"full_name", "primary_email", "phone", "skills"}
    assert "emails" not in out                      # subset selection worked
    assert out["primary_email"] == "jane.doe@gmail.com"   # rename via from=emails[0]
    assert isinstance(out["skills"], list) and all(isinstance(s, str) for s in out["skills"])


def test_on_missing_omit_drops_null_fields(main_inputs, configs):
    cfg = load_config(os.path.join(configs, "custom_contacts.json"))
    out = Pipeline(config=cfg).run(main_inputs).output
    assert "country" not in out                     # country is null -> omitted
    assert out["name"] == "Jane Doe"                # rename full_name -> name


def test_bad_config_path_fails_fast(configs):
    import json
    cfg = json.load(open(os.path.join(configs, "bad_config.json")))
    with pytest.raises(ConfigError):
        validate_config(cfg)


def test_unknown_type_rejected():
    with pytest.raises(ConfigError):
        validate_config({"fields": [{"path": "x", "from": "full_name", "type": "blob"}]})


def test_on_missing_error_raises(main_inputs):
    cfg = {"fields": [{"path": "yx", "from": "years_experience", "type": "number"},
                      {"path": "missing", "from": "headline", "type": "string"}],
           "on_missing": "error"}
    # headline exists here, so craft a genuinely-missing path scenario:
    cfg2 = {"fields": [{"path": "linkedin", "from": "links.linkedin", "type": "string"}],
            "on_missing": "error"}
    # links.linkedin is present in the sample; assert error path with a truly absent value instead
    assembled = _assemble(main_inputs, DEFAULT_CONFIG)
    assembled.view["links"]["linkedin"] = None
    with pytest.raises(ProjectionError):
        project(assembled, cfg2)


def test_required_null_fails_output_gate(main_inputs):
    cfg = {"fields": [{"path": "country", "from": "location.country",
                       "type": "string", "required": True}],
           "on_missing": "null"}
    assembled = _assemble(main_inputs, DEFAULT_CONFIG)   # country is null (honesty gate)
    out = project(assembled, cfg)
    with pytest.raises(ValidationError):
        validate_output(out, cfg)


def test_output_type_mismatch_caught():
    cfg = {"fields": [{"path": "full_name", "type": "string", "required": True}],
           "on_missing": "null"}
    with pytest.raises(ValidationError):
        validate_output({"full_name": 123}, cfg)
