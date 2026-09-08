"""The two traps that live in `rules.apply`'s return shape, not in its logic.

Both are cases where the obvious assertion passes for the wrong reason. These
tests exist to fail loudly if that ever becomes true again — see the "prove
it's not vacuous" check in the task report for how each was confirmed to
actually catch its bug.
"""

from tests.conftest import FIXED_MOMENT, use_fixture

import mediawiki
import rules
from metrics import AtLeast, edit_count


def test_atleast_bounded_count_is_not_a_plain_int(monkeypatch):
    """Trap #1: `AtLeast(101) == 101` is True, so `assert value == 101` would
    pass whether or not the count was really capped. Assert on the type, or
    the `bounded` flag the response carries specifically for this reason.
    """
    use_fixture(monkeypatch, "nlwiki-stemprocedure")
    lookup = mediawiki.Lookup("ExampleUser")

    # At the metrics layer, the raw measured value: counting stopped at the
    # cap, so `edit_count` hands back an `AtLeast`, not a plain int.
    seen, _ = edit_count(lookup, FIXED_MOMENT, wiki="nl.wikipedia.org", cap=101)
    assert seen == 101  # true of a plain int too — proves nothing by itself
    assert isinstance(seen, AtLeast)
    assert type(seen) is not int  # AtLeast, specifically - not plain int

    # At the response layer, `rules.apply` converts the value to a plain int
    # for JSON (`_machine()`), so the *only* place the distinction survives
    # is the `bounded` flag — asserting `value == 101` here is the vacuous
    # version of this test.
    rule = {"metric": "edit_count", "wiki": "nl.wikipedia.org",
            "operator": "more_than", "value": 100}
    result = rules.apply(lookup, rule, FIXED_MOMENT)
    assert result["observed"]["value"] == 101
    assert not isinstance(result["observed"]["value"], AtLeast)  # stripped by design
    assert result["observed"]["bounded"] is True  # ...so this is the real signal
    assert result["passed"] is True


def test_unmeasurable_rule_is_none_not_false():
    """Trap #2: an account with no registration date on record is neither
    passing nor failing `time_since_registration` — it is unanswerable. If the
    code ever collapsed that to `False`, `assert not result["passed"]` would
    pass for both "failed" and "could not check", silently losing the
    distinction the whole `NotMeasurable` mechanism exists to preserve.
    """

    class NoRegistrationDate:
        """A minimal stand-in for Lookup: no network, just the one method
        `time_since_registration` calls."""

        def account(self, wiki):
            return {"groups": []}  # no "registration" key at all

    rule = {"metric": "time_since_registration", "wiki": "nl.wikipedia.org",
            "operator": "at_least", "value": {"amount": 1, "unit": "months"}}
    result = rules.apply(NoRegistrationDate(), rule, FIXED_MOMENT)

    assert result["passed"] is None
    # The trap itself: both of these read as "not passed", but only one of
    # them is true. A regression that scored unmeasurable rules as failing
    # would make this first assertion pass by accident.
    assert not result["passed"]
    assert result["passed"] is not False
    assert "unmeasurable" in result


def test_an_unmeasurable_criterion_still_reports_prose(monkeypatch):
    """`label` must mean one thing on every branch.

    The unmeasurable path used to fall back to the metric identifier, so a
    reader saw "time_since_first_edit" on one criterion and "edits on Dutch
    Wikipedia" on the next — the same key carrying two kinds of thing depending
    on which branch happened to run.
    """
    from app import app
    body = app.test_client().get(
        "/check?user=Eiabot&policy=nlwiki-stemprocedure").get_json()
    unknown = next(c for c in body["criteria"] if c["passed"] is None)
    assert unknown["label"] != unknown["metric"]
    assert "Dutch Wikipedia" in unknown["label"]
