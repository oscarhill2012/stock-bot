# tests/unit/test_cloudbuild_yaml.py
"""cloudbuild.yaml is valid YAML and contains the expected build/push/deploy steps."""
from __future__ import annotations

from pathlib import Path

import yaml

CLOUDBUILD = Path(__file__).resolve().parents[2] / "deploy" / "cloudbuild.yaml"


def test_yaml_parses():
    data = yaml.safe_load(CLOUDBUILD.read_text())
    assert isinstance(data, dict)
    assert "steps" in data


def test_has_build_push_and_deploy_steps():
    data = yaml.safe_load(CLOUDBUILD.read_text())
    step_names = {s.get("id", "") for s in data["steps"]}
    assert "build" in step_names
    assert "push" in step_names
    assert "deploy" in step_names


def test_substitutions_documented():
    data = yaml.safe_load(CLOUDBUILD.read_text())
    subs = data.get("substitutions", {})
    assert "_REGION" in subs
    assert "_REPO" in subs
    assert "_JOB_NAME" in subs
