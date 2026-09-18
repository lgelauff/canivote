"""An account with no CentralAuth entry must not be refused.

The platform rules ask about global status on every check, so a mistake here is
not confined to one policy — it denies a real person everywhere. And the claim
would be false: a lock is a CentralAuth attribute, so an account with no record
cannot be under one. "Not locked" is the correct answer, not "no such account".
"""

import mediawiki
from app import app


class _LocalOnly(mediawiki.Lookup):
    """Exists on the wiki; has no CentralAuth global."""

    def _get(self, wiki, parameters):
        self.queries.append(parameters)
        if parameters.get("meta") == "globaluserinfo":
            return {"query": {"globaluserinfo": {"missing": True}}}
        if parameters.get("list") == "globalblocks":
            return {"query": {"globalblocks": []}}
        if parameters.get("list") == "usercontribs":
            rows = int(parameters.get("uclimit", 1))
            return {"query": {"usercontribs":
                    [{"revid": i, "timestamp": "2006-01-01T00:00:00Z"}
                     for i in range(rows)]}}
        return {"query": {"users": [{"name": "ExampleUser", "groups": [],
                                     "registration": "2005-01-01T00:00:00Z"}]}}


def test_no_global_record_does_not_mean_no_account(monkeypatch):
    monkeypatch.setattr(mediawiki, "Lookup", _LocalOnly)
    body = app.test_client().get(
        "/check?user=ExampleUser&policy=dewiki-stimmberechtigung").get_json()
    assert body.get("error") != "No such account."
    assert body["verdict"] in ("eligible", "not_eligible", "indeterminate")


def test_an_account_with_no_global_record_reads_as_not_locked(monkeypatch):
    """Not locked — a lock is a CentralAuth attribute, so no record means none."""
    monkeypatch.setattr(mediawiki, "Lookup", _LocalOnly)
    body = app.test_client().get(
        "/check?user=ExampleUser&policy=frwiki-sondage").get_json()
    lock = next(c for c in body["criteria"] if c["metric"] == "is_globally_locked")
    assert lock["passed"] is True, "no CentralAuth record cannot mean locked"


def test_the_missing_unified_account_is_its_own_named_failure(monkeypatch):
    """It fails, but by name, and visibly.

    Refusing the account is right — every account has been unified for years,
    so one without is anomalous. What was wrong before was refusing it as "no
    such account", for an account that demonstrably exists on the wiki asked
    about. Now the person can see which condition they failed.
    """
    monkeypatch.setattr(mediawiki, "Lookup", _LocalOnly)
    body = app.test_client().get(
        "/check?user=ExampleUser&policy=frwiki-sondage").get_json()
    assert body["verdict"] == "not_eligible"
    unified = next(c for c in body["criteria"] if c["metric"] == "has_global_account")
    assert unified["passed"] is False
    assert unified["source"] == "platform"
    assert "unified" in unified["label"]
