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
    path = _write(tmp_path, {"metric": "edit_conut", "wiki": "nl.wikipedia.org",
                             "operator": "at_least", "value": 5})
    with pytest.raises(ValueError, match="unknown metric 'edit_conut'"):
        load_policies(path)


def test_a_misspelt_operator_is_refused_by_name(tmp_path):
    path = _write(tmp_path, {"metric": "edit_count", "wiki": "nl.wikipedia.org",
                             "operator": "atleast", "value": 5})
    with pytest.raises(ValueError, match="unknown operator 'atleast'"):
        load_policies(path)


def test_the_error_lists_what_is_available(tmp_path):
    """Someone who mistyped needs to see the right spelling, not just a refusal."""
    path = _write(tmp_path, {"metric": "edit_conut", "wiki": "nl.wikipedia.org",
                             "operator": "at_least", "value": 5})
    with pytest.raises(ValueError, match="edit_count"):
        load_policies(path)


def test_a_correct_rule_still_loads(tmp_path):
    assert load_policies(_write(tmp_path, {"metric": "edit_count",
                                           "wiki": "nl.wikipedia.org",
                                           "operator": "at_least", "value": 5}))


def test_a_parameter_the_metric_does_not_take_is_refused(tmp_path):
    """`is_globally_locked` asks about the whole movement, so it takes no wiki.

    Writing one is a plausible mistake — every other rule in the file has one.
    It used to load fine and raise TypeError at request time, as a 500.
    """
    path = _write(tmp_path, {"metric": "is_globally_locked", "wiki": "en.wikipedia.org",
                             "operator": "is", "value": False})
    with pytest.raises(ValueError, match="unexpected keyword"):
        load_policies(path)


def test_a_missing_required_parameter_is_refused(tmp_path):
    """`is_blocked` asks about one wiki, so it needs to be told which."""
    path = _write(tmp_path, {"metric": "is_blocked", "operator": "is", "value": False})
    with pytest.raises(ValueError, match="missing a required"):
        load_policies(path)
