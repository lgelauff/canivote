"""Integration tests for `/check`, replaying recorded fixtures — no network."""

from datetime import timedelta

from tests.conftest import forbid_network, use_fixture

import rules


# --- one test per real policy, against a recorded and redacted account (
# to ExampleUser) fixtures. Expected verdicts per the plan's own verification
# section: nl/en/meta/fr eligible, de not_eligible (fails the "50 edits in the
# last 12 months" clause) -----------------------------------------------------

def test_nlwiki_eligible(client, monkeypatch):
    use_fixture(monkeypatch, "nlwiki-stemprocedure")
    body = client.get("/check?user=ExampleUser&policy=nlwiki-stemprocedure").get_json()
    assert body["verdict"] == "eligible"


def test_dewiki_not_eligible(client, monkeypatch):
    use_fixture(monkeypatch, "dewiki-stimmberechtigung")
    body = client.get("/check?user=ExampleUser&policy=dewiki-stimmberechtigung").get_json()
    assert body["verdict"] == "not_eligible"
    failed = [c for c in body["criteria"] if c["passed"] is False]
    assert len(failed) == 1
    assert failed[0]["metric"] == "edit_count"


def test_enwiki_eligible(client, monkeypatch):
    use_fixture(monkeypatch, "enwiki-arbcom")
    body = client.get("/check?user=ExampleUser&policy=enwiki-arbcom").get_json()
    assert body["verdict"] == "eligible"


def test_meta_global_eligible(client, monkeypatch):
    use_fixture(monkeypatch, "meta-global")
    body = client.get("/check?user=ExampleUser&policy=meta-global").get_json()
    assert body["verdict"] == "eligible"


def test_frwiki_has_no_rules_of_its_own_but_still_gets_the_baseline(client, monkeypatch):
    """French Wikipedia codifies no eligibility for surveys — but "no policy"
    never meant "a blocked account may vote". The platform rules apply anyway,
    and are marked as such so nobody mistakes them for something frwiki wrote.
    """
    use_fixture(monkeypatch, "frwiki-sondage")
    body = client.get("/check?user=ExampleUser&policy=frwiki-sondage").get_json()
    assert body["verdict"] == "eligible"
    assert [c["source"] for c in body["criteria"]] == ["platform"] * 3
    assert not any(c["source"] == "policy" for c in body["criteria"])

def test_nonexistent_account_makes_exactly_one_query(client, monkeypatch):
    use_fixture(monkeypatch, "nonexistent_account")
    body = client.get(
        "/check?user=ThisAccountShouldNotExist0000&policy=nlwiki-stemprocedure"
    ).get_json()
    assert body["verdict"] == "not_eligible"
    assert body["reason"] == "No such account."
    assert len(body["queries"]) == 1
    assert body["criteria"] == []


# --- an invalid username is a 400, and never reaches Wikimedia at all ------

def test_invalid_username_is_400_with_zero_upstream_calls(client, monkeypatch):
    forbid_network(monkeypatch)
    resp = client.get("/check?user=<script>&policy=nlwiki-stemprocedure")
    assert resp.status_code == 400
    resp = client.get(f"/check?user={'x' * 200}&policy=nlwiki-stemprocedure")
    assert resp.status_code == 400


def test_missing_parameters_are_400_with_zero_upstream_calls(client, monkeypatch):
    forbid_network(monkeypatch)
    assert client.get("/check?policy=nlwiki-stemprocedure").status_code == 400
    assert client.get("/check?user=ExampleUser").status_code == 400


def test_unknown_policy_is_404_with_zero_upstream_calls(client, monkeypatch):
    forbid_network(monkeypatch)
    resp = client.get("/check?user=ExampleUser&policy=does-not-exist")
    assert resp.status_code == 404


def test_amount_unit_duration_converts_to_days():
    assert rules.as_duration({"amount": 2, "unit": "weeks"}) == timedelta(days=14)
    assert rules.as_duration({"amount": 12, "unit": "months"}) == timedelta(days=360)
    assert rules.as_duration(5) == timedelta(days=5)  # bare int still means days


def test_normalise_username_collapses_underscores_and_case():
    import mediawiki
    assert mediawiki.normalise_username("foo_bar") == "Foo bar"
    assert mediawiki.normalise_username("Foo bar") == "Foo bar"
    assert mediawiki.normalise_username("foo   bar") == "Foo bar"






def test_policy_text_carries_the_verification_date(client, monkeypatch):
    """A verdict cites a policy page, so it has to say when we last read it.

    How old is too old is the reader's call — we report the date and let them
    decide, rather than publishing a derived boolean based on our own cutoff.
    """
    use_fixture(monkeypatch, "frwiki-sondage")
    body = client.get("/check?user=ExampleUser&policy=frwiki-sondage").get_json()
    assert body["policy_text"]["verified"] == "2026-09-01"

def test_check_response_has_cors_and_noindex_headers(client, monkeypatch):
    use_fixture(monkeypatch, "frwiki-sondage")
    resp = client.get("/check?user=ExampleUser&policy=frwiki-sondage")
    assert resp.headers["Access-Control-Allow-Origin"] == "*"
    assert "noindex" in resp.headers["X-Robots-Tag"]


def test_robots_txt_disallows_check(client):
    resp = client.get("/robots.txt")
    assert "Disallow: /check" in resp.get_data(as_text=True)


def test_index_and_health(client):
    assert client.get("/").status_code == 200
    health = client.get("/health").get_json()
    assert health["status"] == "ok"
    assert health["policies"] == 5
