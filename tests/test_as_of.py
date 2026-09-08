"""Evaluating a policy at the moment the policy anchors on.

A policy names its own instant — "150 mainspace edits by 1 November", "two
weeks before the vote opened". Measuring from now instead answers a different
question, and the error is not small: asked on 15 November, "by 1 November"
silently becomes "by 15 November", and a fortnight of edits that the policy
excludes are counted.

Precision was never the problem. We already measure to the second; we were
measuring from the wrong second.
"""

from datetime import datetime, timedelta, timezone

import mediawiki
from app import app


class _Recording(mediawiki.Lookup):
    def _get(self, wiki, parameters):
        self.queries.append(parameters)
        if parameters.get("meta") == "globaluserinfo":
            return {"query": {"globaluserinfo": {"id": 1, "name": "X"}}}
        if parameters.get("list") == "globalblocks":
            return {"query": {"globalblocks": []}}
        if parameters.get("list") == "usercontribs":
            rows = int(parameters.get("uclimit", 1))
            return {"query": {"usercontribs":
                              [{"revid": i, "timestamp": "2006-01-01T00:00:00Z"}
                               for i in range(rows)]}}
        return {"query": {"users": [{"name": "X", "groups": [],
                                     "registration": "2005-01-01T00:00:00Z"}]}}


def _get(monkeypatch, query):
    monkeypatch.setattr(mediawiki, "Lookup", _Recording)
    return app.test_client().get(query)


def test_an_absent_as_of_means_the_moment_of_asking(monkeypatch):
    body = _get(monkeypatch, "/check?user=X&policy=enwiki-arbcom").get_json()
    assert body["as_of"] == body["checked_at"]


def test_a_supplied_as_of_is_reported_separately_from_when_we_ran(monkeypatch):
    """A verdict measured against November must not claim it was taken then."""
    body = _get(monkeypatch,
                "/check?user=X&policy=enwiki-arbcom&as_of=2026-11-01T00:00:00Z").get_json()
    assert body["as_of"].startswith("2026-11-01")
    assert not body["checked_at"].startswith("2026-11-01")


def test_as_of_moves_the_moment_the_rules_are_measured_from(monkeypatch):
    """The queries must carry the supplied instant, not today's."""
    response = _get(monkeypatch,
                    "/check?user=X&policy=enwiki-arbcom&as_of=2026-11-01T00:00:00Z")
    assert response.status_code == 200
    bounded = [q["ucstart"] for q in response.get_json()["queries"] if "ucstart" in q]
    assert bounded, "contribution queries should be bounded by the moment"
    assert all(stamp.startswith("2026-11-01") for stamp in bounded)


def test_a_future_as_of_is_allowed(monkeypatch):
    """Asking before a vote opens is the ordinary case, not an error.

    Someone wants to know whether they will be eligible when voting starts.
    Refusing that would leave the question unanswerable at the moment it is
    most often asked.
    """
    ahead = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    assert _get(monkeypatch, f"/check?user=X&policy=enwiki-arbcom&as_of={ahead}"
                ).status_code == 200


def test_an_unparseable_as_of_is_refused_before_anything_is_asked(monkeypatch):
    response = _get(monkeypatch, "/check?user=X&policy=enwiki-arbcom&as_of=nonsense")
    assert response.status_code == 400
    assert "ISO 8601" in response.get_json()["error"]
