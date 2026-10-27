"""Ukrainian Wikipedia's adminship voters: "patroller, or 500 edits and three
months; and 5 recent edits to the encyclopedic content".

No network: answers come from the fake Lookup in test_rule_groups.py. The
window query is checked to the second, because that is where an off-by-a-week
reading of the policy would hide.
"""

import pytest

import mediawiki
from tests.openapi_contract import validate
from tests.test_rule_groups import fake_lookup

POLICY = "ukwiki-adminship"
WINDOW = "0|6|8|10|14|100|828"


def _check(client, monkeypatch, **facts):
    monkeypatch.setattr(mediawiki, "Lookup", fake_lookup(**facts))
    body = client.get(f"/check?user=ExampleUser&policy={POLICY}").get_json()
    validate("Verdict", body)
    return body


@pytest.mark.parametrize("facts,verdict", [
    # Patrollers need neither the edit count nor the time on the wiki.
    ({"groups": ["editor"], "counts": {WINDOW: 5}}, "eligible"),
    ({"counts": {"0": 500, WINDOW: 5}, "first_edit_days_ago": 120}, "eligible"),
    ({"counts": {"0": 499, WINDOW: 5}, "first_edit_days_ago": 120}, "not_eligible"),
    ({"counts": {"0": 500, WINDOW: 5}, "first_edit_days_ago": 80}, "not_eligible"),
    # The recent-activity clause applies to everyone, patrollers included.
    ({"groups": ["editor"], "counts": {WINDOW: 4}}, "not_eligible"),
    # Autopatrolled is a different group and is not patroller rights.
    ({"groups": ["autoreview"], "counts": {WINDOW: 5}}, "not_eligible"),
])
def test_verdicts(client, monkeypatch, facts, verdict):
    assert _check(client, monkeypatch, **facts)["verdict"] == verdict


def test_the_window_runs_from_90_to_7_days_before_the_vote(client, monkeypatch):
    """FIXED_MOMENT is 2026-09-06T15:00Z: one week back is 30 August, 90 days 8 June."""
    body = _check(client, monkeypatch, groups=["editor"], counts={WINDOW: 5})
    window = [q for q in body["queries"] if "ucnamespace=0%7C6%7C8%7C10%7C14%7C100%7C828" in q]
    assert len(window) == 1
    assert "ucstart=2026-08-30T15%3A00%3A00Z" in window[0]
    assert "ucend=2026-06-08T15%3A00%3A00Z" in window[0]


def test_one_check_stays_within_the_upstream_budget(client, monkeypatch):
    import app
    body = _check(client, monkeypatch, counts={"0": 500, WINDOW: 5},
                  first_edit_days_ago=120)
    assert len(body["queries"]) <= app.MAX_UPSTREAM_CALLS_PER_CHECK


def test_the_policy_detail_matches_the_contract(client):
    validate("PolicyDetail", client.get(f"/policies/{POLICY}").get_json())
