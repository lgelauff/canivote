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


def test_no_rule_is_ever_evaluated_twice():
    """No policy ends up asking the same question twice.

    Identity is the whole rule, not the metric name: a policy may legitimately
    have two `edit_count` rules measuring different quantities — German
    Wikipedia wants 200 all-time and 50 in the last twelve months. What must
    never happen is the *same* question appearing twice, which is what the
    baseline dedup prevents.
    """
    for policy_id, policy in POLICIES.items():
        combined = baseline_rules(policy) + policy["rules"]
        seen = [tuple(sorted((k, str(v)) for k, v in rule.items())) for rule in combined]
        assert len(seen) == len(set(seen)), f"{policy_id} asks the same question twice"


def test_a_wiki_scoped_policy_gets_a_local_block_rule():
    policy = POLICIES["frwiki-sondage"]
    assert not policy["rules"]                                 # states nothing itself
    implied = baseline_rules(policy)
    assert _metrics(implied) == ["is_globally_locked", "is_globally_blocked", "is_blocked"]
    assert implied[-1]["wiki"] == "fr.wikipedia.org"


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
