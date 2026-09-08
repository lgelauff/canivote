"""Finding a policy without downloading every policy.

Asking "which policies exist" should not cost a reader every word of every
community's page. The listing names each one and links to its own detail; the
wiki is the axis somebody actually searches on.
"""

from app import POLICIES, app


def _client():
    return app.test_client()


def test_the_listing_is_far_smaller_than_the_detail():
    listing = len(_client().get("/policies").get_data())
    detail = sum(len(_client().get(f"/policies/{p}").get_data()) for p in POLICIES)
    assert listing < detail / 3, "the listing should not be the whole file again"


def test_every_policy_appears_with_a_link_to_itself():
    body = _client().get("/policies").get_json()
    assert {p["id"] for p in body["policies"]} == set(POLICIES)
    for entry in body["policies"]:
        assert _client().get(entry["detail"]).status_code == 200


def test_the_wiki_filter_narrows_it():
    body = _client().get("/policies?wiki=nl.wikipedia.org").get_json()
    assert [p["id"] for p in body["policies"]] == ["nlwiki-stemprocedure"]


def test_an_unknown_wiki_says_which_ones_exist():
    """A filter that matches nothing is usually a typo, so answer the real question."""
    response = _client().get("/policies?wiki=nl.wikipedia.com")
    assert response.status_code == 404
    assert "nl.wikipedia.org" in response.get_json()["known_wikis"]


def test_the_detail_shows_the_rules_that_will_actually_run():
    """Including the platform's, so the listing cannot describe a smaller check."""
    body = _client().get("/policies/frwiki-sondage").get_json()
    assert [r["source"] for r in body["rules"]] == ["platform"] * 3


def test_an_unknown_policy_says_which_ones_exist():
    response = _client().get("/policies/nosuch")
    assert response.status_code == 404
    assert "frwiki-sondage" in response.get_json()["known_policies"]
