"""What happens when Wikimedia does not answer.

Every other test replaces `Lookup._get` wholesale, which means the body of the
real one — the timeout, the status check, the JSON decode, and the branch that
catches a MediaWiki error arriving as a perfectly good HTTP 200 — never runs.
That is the one call in this tool that can genuinely fail, so these tests mock
one layer lower, at the session, and let the real `_get` execute.
"""

import pytest
import requests

import mediawiki
from app import app


class _Response:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    return app.test_client()


def _session_returns(monkeypatch, outcome):
    def fake_get(url, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    monkeypatch.setattr(mediawiki._session, "get", fake_get)


@pytest.mark.parametrize("failure", [
    requests.ConnectTimeout("timed out"),
    requests.ConnectionError("no route"),
    requests.HTTPError("503"),
])
def test_transport_failure_becomes_upstream_unavailable(monkeypatch, failure):
    _session_returns(monkeypatch, failure)
    with pytest.raises(mediawiki.UpstreamUnavailable):
        mediawiki.Lookup("ExampleUser").account("en.wikipedia.org")


def test_a_mediawiki_error_in_a_200_is_not_treated_as_data(monkeypatch):
    """maxlag rejections arrive as HTTP 200 with an `error` key.

    Indexing past that would raise KeyError and surface as a 500 — our fault,
    for their outage.
    """
    _session_returns(monkeypatch, _Response({"error": {"code": "maxlag"}}))
    with pytest.raises(mediawiki.UpstreamUnavailable, match="maxlag"):
        mediawiki.Lookup("ExampleUser").account("en.wikipedia.org")


def test_unparseable_body_is_not_a_crash(monkeypatch):
    class Undecodable(_Response):
        def json(self):
            raise ValueError("not JSON")
    _session_returns(monkeypatch, Undecodable(None))
    with pytest.raises(mediawiki.UpstreamUnavailable):
        mediawiki.Lookup("ExampleUser").account("en.wikipedia.org")


def test_check_reports_502_and_blames_the_right_party(client, monkeypatch):
    """A verdict we could not reach is not a verdict of ineligible."""
    _session_returns(monkeypatch, requests.ConnectTimeout("timed out"))
    response = client.get("/check?user=ExampleUser&policy=frwiki-sondage")
    assert response.status_code == 502
    body = response.get_json()
    assert "verdict" not in body          # never guess when we could not look
    assert "Wikimedia" in body["error"]


def test_one_wiki_costs_one_account_query(monkeypatch):
    """The per-lookup cache is claimed in a docstring; this checks it.

    enwiki's policy has five rules and four of them ask about the same account.
    Without memoisation that is four identical requests to Wikimedia for one
    question, which is exactly the discourtesy this tool is careful about.
    """
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _Response({"query": {"users": [{"name": "ExampleUser", "groups": []}]}})

    monkeypatch.setattr(mediawiki._session, "get", fake_get)
    lookup = mediawiki.Lookup("ExampleUser")
    for _ in range(4):
        lookup.account("en.wikipedia.org")
    assert len(calls) == 1
