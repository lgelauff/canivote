"""`load_policies` must refuse a policy it cannot answer correctly, at startup.

Counting stops once `edit_count` reaches its cap, and the API will not return
more than 500 rows. A threshold past that could never be settled honestly, so
`load_policies` refuses it at startup rather than failing every voter at runtime.
"""

import pytest
import yaml

from app import load_policies

VALID_POLICY = {
    "wiki": "nl.wikipedia.org",
    "language": "en",
    "title": "test",
    "scope": "test",
    "sources": ["https://nl.wikipedia.org/wiki/Wikipedia:Stemprocedure"],
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


@pytest.mark.parametrize("operator,value,rows", [
    ("at_least", 501, 501),   # needs one row per edit
    ("is", 500, 501),         # needs one more, to tell 500 from 900
    ("more_than", 500, 501),
])
def test_a_threshold_past_the_api_ceiling_is_refused(tmp_path, operator, value, rows):
    """Counting stops at the cap and the API returns at most 500 rows.

    Past that we would compare against a number we never finished counting,
    fail every voter, and report "at least 500" while doing it. Refused at
    startup instead — enwiki extended-confirmed is 500 edits and steward
    elections want 600, so this is a policy someone will realistically add.
    """
    rule = {"metric": "edit_count", "wiki": "nl.wikipedia.org",
            "operator": operator, "value": value}
    path = _write(tmp_path, {**VALID_POLICY, "rules": [rule]})
    with pytest.raises(ValueError, match=f"needs {rows} rows"):
        load_policies(path)


@pytest.mark.parametrize("operator", ["at_most", "fewer_than", "is"])
def test_every_operator_is_allowed_within_the_ceiling(tmp_path, operator):
    """The cap is widened per operator, so all of them can be settled honestly."""
    rule = {"metric": "edit_count", "wiki": "nl.wikipedia.org",
            "operator": operator, "value": 100}
    assert load_policies(_write(tmp_path, {**VALID_POLICY, "rules": [rule]}))


def test_the_real_policies_file_loads_clean():
    from pathlib import Path
    policies = load_policies(Path(__file__).parent.parent / "policies.yaml")
    assert len(policies) == 5
