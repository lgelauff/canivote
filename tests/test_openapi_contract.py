"""The OpenAPI document is the contract; this pins it to what we actually return.

If a response stops matching the document, or the document drifts from the code,
one of these fails. That is the point: a consumer can generate a client from
openapi.json and trust it.
"""

from urllib.parse import urlencode

import mediawiki
import pytest

import app as app_module
from tests.conftest import use_fixture
from tests.openapi_contract import OPENAPI, validate


def _refs(node):
    if isinstance(node, dict):
        if "$ref" in node:
            yield node["$ref"]
        for value in node.values():
            yield from _refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from _refs(value)


def test_document_is_openapi_31_and_covers_every_route():
    assert OPENAPI["openapi"] == "3.1.0"
    assert set(OPENAPI["paths"]) == {
        "/check", "/policies", "/policies/{policy_id}", "/health", "/",
    }
    # Every reference must resolve, or a generated client is broken.
    for ref in _refs(OPENAPI):
        assert ref.startswith("#/components/schemas/"), ref
        name = ref.rsplit("/", 1)[-1]
        assert name in OPENAPI["components"]["schemas"], f"dangling $ref {ref}"


def test_version_is_written_down_once(client):
    assert OPENAPI["info"]["version"] == app_module.VERSION
    assert client.get("/").get_json()["version"] == app_module.VERSION
    assert client.get("/health").headers["Canivote-Version"] == app_module.VERSION


def test_the_document_is_served_verbatim(client):
    assert client.get("/openapi.json").get_json() == OPENAPI


def test_eligible_verdict_matches_the_contract(client, monkeypatch):
    use_fixture(monkeypatch, "meta-global")
    body = client.get("/check?user=ExampleUser&policy=meta-global").get_json()
    validate("Verdict", body)
    assert body["verdict"] == "eligible"


def test_not_eligible_verdict_matches_the_contract(client, monkeypatch):
    use_fixture(monkeypatch, "dewiki-stimmberechtigung")
    body = client.get(
        "/check?user=ExampleUser&policy=dewiki-stimmberechtigung").get_json()
    validate("Verdict", body)
    assert body["verdict"] == "not_eligible"


def test_nonexistent_account_verdict_matches_the_contract(client, monkeypatch):
    use_fixture(monkeypatch, "nonexistent_account")
    body = client.get(
        "/check?user=ThisAccountShouldNotExist0000"
        "&policy=nlwiki-stemprocedure").get_json()
    validate("Verdict", body)
    assert body["verdict"] == "not_eligible"
    assert body["criteria"] == []


class _NoRegistration(mediawiki.Lookup):
    """A pre-2006 account: MediaWiki holds no registration date for it.

    `queries` records URLs, as the real `Lookup._get` does — the contract says
    so, and a fake that records something else would test the fake.
    """

    def _get(self, wiki, parameters):
        self.queries.append(f"https://{wiki}/w/api.php?{urlencode(parameters)}")
        if parameters.get("meta") == "globaluserinfo":
            return {"query": {"globaluserinfo": {"home": "x", "id": 1, "name": "X"}}}
        if parameters.get("list") == "globalblocks":
            return {"query": {"globalblocks": []}}
        if parameters.get("list") == "usercontribs":
            rows = int(parameters.get("uclimit", 1))
            return {"query": {"usercontribs": [{"revid": i} for i in range(rows)]}}
        return {"query": {"users": [{"name": "X", "groups": [], "registration": None}]}}


def test_indeterminate_verdict_matches_the_contract(client, monkeypatch):
    monkeypatch.setattr(mediawiki, "Lookup", _NoRegistration)
    body = client.get("/check?user=ExampleUser&policy=enwiki-arbcom").get_json()
    validate("Verdict", body)
    assert body["verdict"] == "indeterminate"
    assert any(c["observed"] is None and c["passed"] is None
               for c in body["criteria"])


def test_policy_listing_matches_the_contract(client):
    validate("PolicyList", client.get("/policies").get_json())
    validate("PolicyList", client.get("/policies?wiki=nl.wikipedia.org").get_json())


def test_policy_detail_matches_the_contract(client):
    validate("PolicyDetail", client.get("/policies/meta-global").get_json())


def test_health_and_index_match_the_contract(client):
    validate("Health", client.get("/health").get_json())
    validate("Index", client.get("/").get_json())


@pytest.mark.parametrize(("url", "code"), [
    ("/check?policy=meta-global", "bad_request"),
    ("/check?user=ExampleUser", "bad_request"),
    ("/check?user=%3Cscript%3E&policy=meta-global", "invalid_username"),
    ("/check?user=ExampleUser&policy=nosuch", "unknown_policy"),
    ("/policies/nosuch", "unknown_policy"),
    ("/policies?wiki=nl.wikipedia.com", "unknown_wiki"),
])
def test_error_responses_match_the_contract(client, url, code):
    response = client.get(url)
    assert response.status_code in (400, 404)
    body = response.get_json()
    validate("Error", body)
    assert body["code"] == code


def test_event_is_no_longer_accepted_as_a_policy_name(client, monkeypatch):
    """The alias existed for a client that never reached us; it is gone."""
    monkeypatch.setattr(mediawiki, "Lookup", _NoRegistration)  # fail if called
    response = client.get("/check?user=ExampleUser&event=meta-global")
    assert response.status_code == 400
    assert response.get_json()["code"] == "bad_request"
