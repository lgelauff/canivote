"""Integration tests for `/check`, replaying recorded fixtures — no network."""

from datetime import datetime, timedelta

from tests.conftest import FIXED_MOMENT, forbid_network, use_fixture

import app as app_module
import rules


# --- one test per real policy, against the recorded Effeietsanders (redacted
# to ExampleUser) fixtures. Expected verdicts per the plan's own verification
# section: nl/en/meta/fr eligible, de not_eligible (fails the "50 edits in the
# last 12 months" clause) -----------------------------------------------------

def test_nlwiki_eligible(client, monkeypatch):
    use_fixture(monkeypatch, "nlwiki-stemprocedure")
    body = client.get("/check?user=ExampleUser&policy=nlwiki-stemprocedure").get_json()
    assert body["verdict"] == "eligible"
    assert body["eligible"] is True


def test_dewiki_not_eligible(client, monkeypatch):
    use_fixture(monkeypatch, "dewiki-stimmberechtigung")
    body = client.get("/check?user=ExampleUser&policy=dewiki-stimmberechtigung").get_json()
    assert body["verdict"] == "not_eligible"
    assert body["eligible"] is False
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


def test_frwiki_eligible_with_no_rules(client, monkeypatch):
    use_fixture(monkeypatch, "frwiki-sondage")
    body = client.get("/check?user=ExampleUser&policy=frwiki-sondage").get_json()
    assert body["verdict"] == "eligible"
    assert body["criteria"] == []
    # Even a policy with zero rules confirms the account exists first.
    assert len(body["queries"]) == 1


# --- trap #4: a nonexistent account is checked once, before any rule runs ---

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


# --- offset shifts the measurement window -----------------------------------

def test_offset_shifts_the_measurement_window(monkeypatch):
    use_fixture(monkeypatch, "offset_rule")
    import mediawiki
    lookup = mediawiki.Lookup("ExampleUser")

    rule = {"metric": "edit_count", "wiki": "nl.wikipedia.org",
            "operator": "at_least", "value": 1,
            "offset": {"amount": 30, "unit": "days"}}
    result = rules.apply(lookup, rule, FIXED_MOMENT)

    # Recorded fixture proves the query was actually built with `ucstart` 30
    # days before FIXED_MOMENT (see tests/fixtures/offset_rule.json); if the
    # code stopped applying `offset`, `use_fixture` would raise a missing-URL
    # AssertionError here rather than this passing by accident.
    assert result["measured"] == "1 month earlier"
    assert result["passed"] is True


# --- {amount, unit} durations convert ---------------------------------------

def test_amount_unit_duration_converts_to_days():
    assert rules.as_duration({"amount": 2, "unit": "weeks"}) == timedelta(days=14)
    assert rules.as_duration({"amount": 12, "unit": "months"}) == timedelta(days=360)
    assert rules.as_duration(5) == timedelta(days=5)  # bare int still means days


# --- the cache -----------------------------------------------------------

def test_cache_returns_cached_true_on_repeat(client, monkeypatch):
    calls = use_fixture(monkeypatch, "frwiki-sondage")

    first = client.get("/check?user=ExampleUser&policy=frwiki-sondage").get_json()
    assert first["cached"] is False
    assert len(calls) == 1

    second = client.get("/check?user=ExampleUser&policy=frwiki-sondage").get_json()
    assert second["cached"] is True
    assert len(calls) == 1  # no new upstream query for the cached repeat


def test_cache_normalises_username_case_to_one_entry(client, monkeypatch):
    calls = use_fixture(monkeypatch, "frwiki-sondage")

    first = client.get("/check?user=exampleUser&policy=frwiki-sondage").get_json()
    assert first["user"] == "ExampleUser"  # normalised: first letter uppercased
    assert first["cached"] is False

    second = client.get("/check?user=ExampleUser&policy=frwiki-sondage").get_json()
    assert second["cached"] is True
    assert len(calls) == 1


def test_normalise_username_collapses_underscores_and_case():
    import mediawiki
    assert mediawiki.normalise_username("foo_bar") == "Foo bar"
    assert mediawiki.normalise_username("Foo bar") == "Foo bar"
    assert mediawiki.normalise_username("foo   bar") == "Foo bar"


# --- staleness ---------------------------------------------------------------

def test_stale_is_true_for_an_old_verified_date():
    assert app_module._is_stale("2020-01-01") is True


def test_stale_is_true_for_no_verified_date_at_all():
    assert app_module._is_stale(None) is True


def test_stale_is_false_for_a_recent_verified_date():
    recent = (FIXED_MOMENT - timedelta(days=10)).date().isoformat()
    assert app_module._is_stale(recent) is False


def test_policy_text_carries_verified_and_stale(client, monkeypatch):
    use_fixture(monkeypatch, "frwiki-sondage")
    body = client.get("/check?user=ExampleUser&policy=frwiki-sondage").get_json()
    # `verified:` in policies.yaml is YAML-typed as a date, and Flask's default
    # JSON provider renders date/datetime objects as an RFC 1123 string (not
    # ISO-8601) — this is how the field actually arrives on the wire today.
    verified = datetime.strptime(body["policy_text"]["verified"], "%a, %d %b %Y %H:%M:%S GMT")
    assert verified.date().isoformat() == "2026-09-01"
    assert body["policy_text"]["stale"] is False


# --- headers, robots, misc endpoints -----------------------------------------

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
