"""Rule groups: `any_of` and `all_of`, and counting across a list of namespaces.

A community rule like "patroller, or 500 edits and three months" is an OR, and a
flat list of rules can only say AND. These tests hold groups to the same promise
as single rules: a member we could not check is never read as a pass, and the
response shows every route so a reader can see which one they qualified by.

No network: a fake Lookup answers from a few numbers set per test.
"""

from datetime import timedelta
from urllib.parse import urlencode

import pytest
import yaml

import app as app_module
import mediawiki
import metrics
from app import load_policies
from rules import combine
from tests.conftest import FIXED_MOMENT
from tests.openapi_contract import validate

WIKI = "test.wikipedia.org"

# "Patroller, or (at least 10 article edits and a first edit 30 days ago)."
GROUPED = {
    "wiki": WIKI, "language": "en", "title": "demo", "scope": "demo",
    "sources": ["https://test.wikipedia.org/wiki/Policy"],
    "verified": "2026-10-27", "original_text": "t", "english": "e",
    "rules": [
        {"any_of": [
            {"metric": "user_groups", "wiki": WIKI,
             "operator": "includes", "value": "patroller"},
            {"all_of": [
                {"metric": "edit_count", "wiki": WIKI, "namespace": "article",
                 "operator": "at_least", "value": 10},
                {"metric": "time_since_first_edit", "wiki": WIKI,
                 "operator": "at_least", "value": 30},
            ]},
        ]},
    ],
}


def fake_lookup(groups=(), counts=None, first_edit_days_ago=None):
    """A Lookup answering from fixed facts. `counts` maps ucnamespace to edits."""
    counts = counts or {}

    class Fake(mediawiki.Lookup):
        def _get(self, wiki, parameters):
            self.queries.append(f"https://{wiki}/w/api.php?{urlencode(parameters)}")
            if parameters.get("meta") == "globaluserinfo":
                return {"query": {"globaluserinfo": {"home": "x", "id": 1, "name": "X"}}}
            if parameters.get("list") == "globalblocks":
                return {"query": {"globalblocks": []}}
            if parameters.get("list") == "usercontribs":
                if parameters.get("ucdir") == "newer":
                    if first_edit_days_ago is None:
                        return {"query": {"usercontribs": []}}
                    first = FIXED_MOMENT - timedelta(days=first_edit_days_ago)
                    stamp = first.strftime("%Y-%m-%dT%H:%M:%SZ")
                    return {"query": {"usercontribs": [{"timestamp": stamp}]}}
                have = counts.get(parameters.get("ucnamespace"), 0)
                rows = min(int(parameters["uclimit"]), have)
                return {"query": {"usercontribs": [{"revid": i} for i in range(rows)]}}
            return {"query": {"users": [{"name": "X", "groups": list(groups),
                                         "registration": "2020-01-01T00:00:00Z"}]}}

    return Fake


@pytest.fixture
def grouped_policy(monkeypatch):
    monkeypatch.setitem(app_module.POLICIES, "demo-groups", GROUPED)


def _check(client, monkeypatch, lookup):
    monkeypatch.setattr(mediawiki, "Lookup", lookup)
    body = client.get("/check?user=ExampleUser&policy=demo-groups").get_json()
    validate("Verdict", body)
    return body


# --- the logic, on its own ---------------------------------------------------

@pytest.mark.parametrize("results,expected", [
    ([True, False], True),
    ([False, True], True),
    ([True, None], True),     # one route passing settles it
    ([False, False], False),
    ([False, None], None),    # the unchecked route might have passed
    ([None, None], None),
])
def test_any_of(results, expected):
    assert combine("any_of", results) is expected


@pytest.mark.parametrize("results,expected", [
    ([True, True], True),
    ([True, False], False),
    ([False, None], False),   # one failure settles it
    ([True, None], None),     # the unchecked member might fail
    ([None, None], None),
])
def test_all_of(results, expected):
    assert combine("all_of", results) is expected


# --- through /check -------------------------------------------------------------

def test_eligible_by_the_first_route(client, monkeypatch, grouped_policy):
    body = _check(client, monkeypatch, fake_lookup(groups=["patroller"]))
    assert body["verdict"] == "eligible"
    group = body["criteria"][-1]
    assert group["group"] == "any_of" and group["passed"] is True
    route_one, route_two = group["criteria"]
    assert route_one["passed"] is True
    # The other route is still evaluated and shown, failing.
    assert route_two["group"] == "all_of" and route_two["passed"] is False


def test_eligible_by_the_second_route(client, monkeypatch, grouped_policy):
    lookup = fake_lookup(counts={"0": 10}, first_edit_days_ago=60)
    body = _check(client, monkeypatch, lookup)
    assert body["verdict"] == "eligible"
    route_one, route_two = body["criteria"][-1]["criteria"]
    assert route_one["passed"] is False
    assert route_two["passed"] is True


def test_not_eligible_when_every_route_fails(client, monkeypatch, grouped_policy):
    lookup = fake_lookup(counts={"0": 10}, first_edit_days_ago=10)
    body = _check(client, monkeypatch, lookup)
    assert body["verdict"] == "not_eligible"
    # The group counts as one rule, beside the four platform rules.
    assert body["reason"] == "Fails 1 of 5 rules."


def test_every_member_is_tagged_with_its_source(client, monkeypatch, grouped_policy):
    body = _check(client, monkeypatch, fake_lookup(groups=["patroller"]))

    def sources(entry):
        yield entry["source"]
        for member in entry.get("criteria", []):
            yield from sources(member)

    assert set(sources(body["criteria"][-1])) == {"policy"}


@pytest.fixture
def broken_first_edit(monkeypatch):
    def broken(lookup, as_of, **parameters):
        raise RuntimeError("a shape we did not expect")
    monkeypatch.setitem(metrics.METRICS, "time_since_first_edit", broken)


def test_an_unchecked_route_does_not_block_a_passing_one(
        client, monkeypatch, grouped_policy, broken_first_edit):
    body = _check(client, monkeypatch, fake_lookup(groups=["patroller"]))
    assert body["verdict"] == "eligible"


def test_an_unchecked_route_is_not_read_as_a_pass(
        client, monkeypatch, grouped_policy, broken_first_edit):
    """Route one fails, route two cannot be settled: we do not know."""
    body = _check(client, monkeypatch, fake_lookup(counts={"0": 10}))
    assert body["verdict"] == "indeterminate"
    assert body["criteria"][-1]["passed"] is None


def test_the_policy_detail_shows_the_group(client, grouped_policy):
    body = client.get("/policies/demo-groups").get_json()
    validate("PolicyDetail", body)
    assert body["rules"][-1]["any_of"][1]["all_of"][0]["value"] == 10


def test_a_plain_number_on_a_time_metric_means_days():
    """The README always said so; it used to raise TypeError instead."""
    from rules import apply
    rule = {"metric": "time_since_first_edit", "wiki": WIKI,
            "operator": "at_least", "value": 30}
    result = apply(fake_lookup(first_edit_days_ago=31)("X"), rule, FIXED_MOMENT)
    assert result["passed"] is True
    assert result["required"] == {"value": 30, "display": "1 month"}


# --- a list of namespaces ---------------------------------------------------

def test_a_namespace_list_is_asked_for_in_one_query(client, monkeypatch):
    rule = {"metric": "edit_count", "wiki": WIKI, "namespace": [10, 0, 6],
            "operator": "at_least", "value": 3}
    monkeypatch.setitem(app_module.POLICIES, "demo-groups", {**GROUPED, "rules": [rule]})
    body = _check(client, monkeypatch, fake_lookup(counts={"0|6|10": 3}))
    assert body["verdict"] == "eligible"
    assert any("ucnamespace=0%7C6%7C10" in query for query in body["queries"])
    assert body["criteria"][-1]["label"] == "edits in namespaces 0, 6, 10 on test.wikipedia.org"


@pytest.mark.parametrize("namespace", ["mainspace", [], ["Template"], [-1], [True]])
def test_a_namespace_we_cannot_ask_for_is_refused_at_startup(tmp_path, namespace):
    rule = {"metric": "edit_count", "wiki": WIKI, "namespace": namespace,
            "operator": "at_least", "value": 3}
    with pytest.raises(ValueError, match="namespace"):
        load_policies(_write(tmp_path, [rule]))


# --- what load_policies refuses -------------------------------------------------

def _write(tmp_path, rules):
    path = tmp_path / "policies.yaml"
    path.write_text(yaml.safe_dump({"demo": {**GROUPED, "rules": rules}}))
    return path


PLAIN = {"metric": "edit_count", "wiki": WIKI, "operator": "at_least", "value": 5}


def test_the_grouped_policy_loads(tmp_path):
    assert load_policies(_write(tmp_path, GROUPED["rules"]))


@pytest.mark.parametrize("group,message", [
    ({"any_of": []}, "at least two rules"),
    ({"any_of": [PLAIN]}, "at least two rules"),
    ({"any_of": PLAIN}, "at least two rules"),
    ({"any_of": [PLAIN, PLAIN], "all_of": [PLAIN, PLAIN]}, "also has all_of"),
    ({"any_of": [PLAIN, PLAIN], "metric": "edit_count"}, "also has metric"),
    ({"any_of": [PLAIN, {**PLAIN, "metric": "edit_conut"}]}, "unknown metric 'edit_conut'"),
    ({"any_of": [PLAIN, {**PLAIN, "value": 501}]}, "needs 501 rows"),
    ({"any_of": [PLAIN, {"all_of": [PLAIN, {"wiki": WIKI}]}]}, "has no 'metric'"),
])
def test_a_group_we_cannot_answer_is_refused_at_startup(tmp_path, group, message):
    with pytest.raises(ValueError, match=message):
        load_policies(_write(tmp_path, [group]))
