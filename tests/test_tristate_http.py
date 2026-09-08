"""A rule we could not check must not be reported as a rule that failed.

`test_traps.py` proves `rules.apply` keeps the distinction. This proves it
survives the HTTP layer, where `app.check` turns a list of unknowns into a
verdict string — the place a consumer actually reads it.

The two are genuinely different answers. "Not eligible" says we checked and you
fall short. "Indeterminate" says we could not check, which is a reason to look
by hand, not a reason to turn someone away.
"""

import mediawiki
from app import app


class _NoRegistration(mediawiki.Lookup):
    """A pre-2006 account: MediaWiki holds no registration date for it."""

    def _get(self, wiki, parameters):
        self.queries.append(parameters)
        if parameters.get("meta") == "globaluserinfo":
            return {"query": {"globaluserinfo": {"home": "x", "id": 1, "name": "X"}}}
        if parameters.get("list") == "globalblocks":
            return {"query": {"globalblocks": []}}
        if parameters.get("list") == "usercontribs":
            rows = int(parameters.get("uclimit", 1))
            return {"query": {"usercontribs": [{"revid": i} for i in range(rows)]}}
        return {"query": {"users": [{"name": "X", "groups": [], "registration": None}]}}


def _check(monkeypatch, policy):
    monkeypatch.setattr(mediawiki, "Lookup", _NoRegistration)
    return app.test_client().get(f"/check?user=ExampleUser&policy={policy}").get_json()


def test_an_unmeasurable_rule_makes_the_verdict_indeterminate(monkeypatch):
    body = _check(monkeypatch, "enwiki-arbcom")
    assert body["verdict"] == "indeterminate"
    assert body["verdict"] != "not_eligible"          # the distinction, stated outright


def test_the_unmeasurable_rule_is_reported_as_unchecked_not_failed(monkeypatch):
    body = _check(monkeypatch, "enwiki-arbcom")
    unknown = [c for c in body["criteria"] if c["passed"] is None]
    failed = [c for c in body["criteria"] if c["passed"] is False]
    assert len(unknown) == 1
    assert not failed
    assert "could not be checked" in body["reason"]
    assert unknown[0]["unmeasurable"]                 # and says why
