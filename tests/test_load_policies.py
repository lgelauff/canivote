"""`load_policies` must refuse a policy it cannot answer correctly, at startup.

Trap #3: counting stops once `edit_count` hits its cap, so `at_most` / `fewer_than`
would compare against a number we deliberately stopped short of. `rules.py`'s own
`CAPPED_METRICS` / `UNSAFE_WITH_CAPPING` guard exists to reject that at load time —
this is the test that stops it silently regressing back to "returns a wrong answer".
"""

import pytest
import yaml

from app import load_policies

VALID_POLICY = {
    "wiki": "nl.wikipedia.org",
    "language": "en",
    "title": "test",
    "scope": "test",
    "sources": [],
    "verified": "2026-01-01",
    "original_text": "x",
    "english": "x",
    "rules": [
        {"metric": "edit_count", "wiki": "nl.wikipedia.org",
         "operator": "at_least", "value": 10},
    ],
}


def _write(tmp_path, policy):
    path = tmp_path / "policies.yaml"
    path.write_text(yaml.safe_dump({"test-policy": policy}))
    return path


def test_at_least_on_edit_count_is_accepted(tmp_path):
    path = _write(tmp_path, VALID_POLICY)
    policies = load_policies(path)
    assert "test-policy" in policies


@pytest.mark.parametrize("operator", ["at_most", "fewer_than"])
def test_at_most_and_fewer_than_on_edit_count_are_rejected(tmp_path, operator):
    policy = {**VALID_POLICY, "rules": [
        {"metric": "edit_count", "wiki": "nl.wikipedia.org",
         "operator": operator, "value": 10},
    ]}
    path = _write(tmp_path, policy)
    with pytest.raises(ValueError, match=operator):
        load_policies(path)


def test_at_most_on_a_different_metric_is_still_allowed(tmp_path):
    # The cap only applies to edit_count's counting-to-a-threshold trick; other
    # metrics measure exactly, so `at_most` on them is a real, answerable rule.
    policy = {**VALID_POLICY, "rules": [
        {"metric": "time_since_first_edit", "wiki": "nl.wikipedia.org",
         "operator": "at_most", "value": 10},
    ]}
    path = _write(tmp_path, policy)
    policies = load_policies(path)
    assert "test-policy" in policies


def test_the_real_policies_file_loads_clean():
    from pathlib import Path
    policies = load_policies(Path(__file__).parent.parent / "policies.yaml")
    assert len(policies) == 5
