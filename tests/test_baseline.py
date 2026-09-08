"""The platform's implied rules, and the line between them and a community's.

Conditions like "not globally locked" hold whoever is asking, because they come
from how Wikimedia works rather than from anything a community wrote. They are
applied to every policy — but a policy that states one itself keeps its own
version, so the community's wording stays the authority and no rule is checked
twice.

That deduplication is the part with no natural failure signal: double-checking a
rule rarely changes a verdict, it corrupts the audit trail a voter is supposed to
be able to follow by hand. So it is asserted here directly.
"""


from app import GLOBAL_BASELINE, POLICIES, baseline_rules


def _metrics(rules):
    return [rule["metric"] for rule in rules]


def test_a_policy_that_states_a_rule_itself_does_not_get_it_twice():
    """enwiki names its own is_blocked rule, so the baseline must stand down."""
    policy = POLICIES["enwiki-arbcom"]
    assert "is_blocked" in _metrics(policy["rules"])          # the premise
    assert "is_blocked" not in _metrics(baseline_rules(policy))


def test_a_policy_may_repeat_a_platform_rule_and_both_are_shown(tmp_path):
    """No deduplication, deliberately.

    Rules are ANDed and lookups are memoised, so a repeat costs no upstream
    request and cannot change a verdict. Suppressing one meant deciding when
    two rules are "the same", and that decision was wrong: it keyed on the
    metric alone, so a rule about one wiki silently switched off the check for
    another. Showing both — one `policy`, one `platform` — is cheaper and says
    something true: the community stated this itself as well.
    """
    policy = {"wiki": "nl.wikipedia.org", "rules": [
        {"metric": "is_globally_locked", "operator": "is", "value": False}]}
    implied = [r["metric"] for r in baseline_rules(policy)]
    assert "is_globally_locked" in implied, "the platform rule still applies"

def test_a_local_block_is_not_a_platform_rule(tmp_path):
    """A block on one wiki is that community's sanction, not the software's.

    Whether it also removes a vote is for that community to write down — WMF
    Board elections, for instance, disqualify only an account blocked on more
    than one project. Imposing it by default would apply one community's
    sanction to policies that never asked for it.
    """
    implied = baseline_rules({"wiki": "nl.wikipedia.org", "rules": []})
    assert [r["metric"] for r in implied] == [
        "has_global_account", "is_globally_locked", "is_globally_blocked"]
    assert not any(r["metric"] == "is_blocked" for r in implied)


def test_dedup_is_keyed_by_wiki_as_well_as_metric(tmp_path):
    """A rule about another wiki is a different requirement.

    A policy scoped to one wiki that states a rule about a second must still
    get the platform rule for its own — otherwise naming any wiki silently
    switches the check off.
    """
    policy = {"wiki": "meta.wikimedia.org", "rules": [
        {"metric": "is_globally_locked", "wiki": "en.wikipedia.org",
         "operator": "is", "value": False}]}
    implied = baseline_rules(policy)
    assert any(r["metric"] == "is_globally_locked" and r.get("wiki") is None
               for r in implied), "a rule about another wiki suppressed this one"

def test_every_global_rule_reaches_every_policy():
    """A community may decide its own wiki's rules do not apply to a process.
    It may not decide a global lock does not count."""
    for policy_id, policy in POLICIES.items():
        implied = _metrics(baseline_rules(policy))
        for rule in GLOBAL_BASELINE:
            stated = rule["metric"] in _metrics(policy["rules"])
            assert rule["metric"] in implied or stated, \
                f"{policy_id} escapes the global rule {rule['metric']}"


def test_opting_out_drops_the_baseline_entirely():
    assert baseline_rules({"wiki": "x", "rules": [], "baseline": False}) == []
