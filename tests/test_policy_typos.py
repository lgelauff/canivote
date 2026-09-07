"""A typo in policies.yaml must fail at startup, not at request time.

That file is meant to be edited by someone who does not read Python — the whole
design rests on a community member being able to check and change it. A misspelt
metric or operator would otherwise load without complaint and surface as a 500
the first time somebody asked about that policy, which is both late and mute.
"""

import pytest
import yaml

from app import load_policies

VALID = {
    "wiki": "nl.wikipedia.org", "language": "en",
    "sources": ["https://nl.wikipedia.org/wiki/Wikipedia:Stemprocedure"],
    "verified": "2026-09-01", "original_text": "t", "english": "e",
}


def _write(tmp_path, rule):
    path = tmp_path / "policies.yaml"
    path.write_text(yaml.safe_dump({"demo": {**VALID, "rules": [rule]}}))
    return path


def test_a_misspelt_metric_is_refused_by_name(tmp_path):
    path = _write(tmp_path, {"metric": "edit_conut", "operator": "at_least", "value": 5})
    with pytest.raises(ValueError, match="unknown metric 'edit_conut'"):
        load_policies(path)


def test_a_misspelt_operator_is_refused_by_name(tmp_path):
    path = _write(tmp_path, {"metric": "edit_count", "operator": "atleast", "value": 5})
    with pytest.raises(ValueError, match="unknown operator 'atleast'"):
        load_policies(path)


def test_the_error_lists_what_is_available(tmp_path):
    """Someone who mistyped needs to see the right spelling, not just a refusal."""
    path = _write(tmp_path, {"metric": "edit_conut", "operator": "at_least", "value": 5})
    with pytest.raises(ValueError, match="edit_count"):
        load_policies(path)


def test_a_correct_rule_still_loads(tmp_path):
    assert load_policies(_write(tmp_path, {"metric": "edit_count",
                                           "operator": "at_least", "value": 5}))
