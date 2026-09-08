"""A defect in one rule must not destroy the whole verdict.

A rule that raises is our bug. The other rules were answered honestly and a
reader is entitled to them — losing everything to an HTML 500 tells them
nothing, not even which rule went wrong.
"""

import metrics
import mediawiki
import pytest
from app import app


@pytest.fixture
def broken_edit_count(monkeypatch):
    def broken(lookup, as_of, **parameters):
        raise RuntimeError("upstream returned a shape we did not expect")
    monkeypatch.setitem(metrics.METRICS, "edit_count", broken)


def _check():
    return app.test_client().get(
        "/check?user=Effeietsanders&policy=dewiki-stimmberechtigung")


def test_a_broken_rule_does_not_lose_the_other_rules(broken_edit_count):
    response = _check()
    assert response.status_code == 200
    working = [c for c in response.get_json()["criteria"] if c["passed"] is True]
    assert len(working) >= 3, "rules that answered should still be reported"


def test_a_broken_rule_is_named_and_neither_passed_nor_failed(broken_edit_count):
    criteria = _check().get_json()["criteria"]
    broken = [c for c in criteria if c.get("broken")]
    assert broken, "the failure must be visible in the response"
    assert all(c["passed"] is None for c in broken), "a bug is not a failed rule"
    assert "RuntimeError" in broken[0]["broken"]


def test_the_verdict_cannot_be_eligible_while_a_rule_is_broken(broken_edit_count):
    """Unknown is not permission. A condition we did not evaluate might fail."""
    assert _check().get_json()["verdict"] == "indeterminate"


def test_upstream_failure_still_fails_the_whole_request(monkeypatch):
    """Not the same case: if Wikimedia is unreachable, nothing else is knowable.

    Reporting three broken rules and one answer would look more complete than
    it is, so this one is deliberately left to become a 502.
    """
    def unreachable(url, **kwargs):
        raise mediawiki.requests.ConnectTimeout("timed out")
    monkeypatch.setattr(mediawiki._session, "get", unreachable)
    assert _check().status_code == 502
