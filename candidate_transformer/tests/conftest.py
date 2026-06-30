"""Pytest fixtures / path setup so ``import cdt`` works from the repo root."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "samples")
CONFIGS = os.path.join(ROOT, "configs")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope="session")
def samples():
    return SAMPLES


@pytest.fixture(scope="session")
def configs():
    return CONFIGS


@pytest.fixture(scope="session")
def main_inputs():
    from cdt.pipeline import read_inputs
    files = ["recruiter.csv", "ats.json", "github_janedoe.json",
             "jane_doe_resume.pdf", "notes.txt", "broken.json", "empty.txt"]
    return read_inputs([os.path.join(SAMPLES, f) for f in files])


@pytest.fixture()
def default_profile(main_inputs):
    from cdt.pipeline import Pipeline
    from cdt.config import load_config
    return Pipeline(config=load_config(None)).run(main_inputs).output
