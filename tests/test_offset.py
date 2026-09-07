"""A criterion can be measured at a different moment from its neighbours.

Communities write sustained-participation rules — "at least 100 edits three
months ago, and 500 now" — to distinguish a long-standing contributor from
someone who arrived last week and made 500 edits in a fortnight.

That needs two anchors, and it is not expressible with `within` alone: `within`
moves the *start* of a window that still ends now, while `offset` moves the
*end*. The gap is fixed by the policy, not by any particular vote, which is why
it belongs in the policy file rather than in the request.
"""

from datetime import datetime, timedelta, timezone

import mediawiki
import rules

MOMENT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


class Recording(mediawiki.Lookup):
    """Answers every contribution query in full, and remembers the bounds asked for."""

    def _get(self, wiki, parameters):
        rows = int(parameters.get("uclimit", 1))
        self.queries.append(parameters)
        return {"query": {"usercontribs": [{"revid": i} for i in range(rows)]}}


def _apply(rule):
    lookup = Recording("ExampleUser")
    return rules.apply(lookup, rule, MOMENT), lookup.queries[0]


def test_offset_moves_the_end_of_the_window_back():
    """The edit count is taken as of three months earlier, not as of now."""
    result, query = _apply({
        "metric": "edit_count", "wiki": "en.wikipedia.org",
        "operator": "at_least", "value": 100,
        "offset": {"amount": 3, "unit": "months"},
    })
    assert result["passed"] is True
    # ucstart is where an `older`-direction contributions walk begins.
    started = datetime.strptime(query["ucstart"], "%Y-%m-%dT%H:%M:%SZ")
    assert started < MOMENT.replace(tzinfo=None)
    assert MOMENT.replace(tzinfo=None) - started == timedelta(days=90)


def test_without_an_offset_the_window_ends_now():
    _, query = _apply({
        "metric": "edit_count", "wiki": "en.wikipedia.org",
        "operator": "at_least", "value": 500,
    })
    assert query["ucstart"] == MOMENT.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_the_response_says_a_criterion_was_measured_earlier():
    """A reader must be able to see that two criteria used different moments.

    Without this the response would show two edit-count rules with different
    thresholds and no indication that they were taken at different times.
    """
    result, _ = _apply({
        "metric": "edit_count", "wiki": "en.wikipedia.org",
        "operator": "at_least", "value": 100,
        "offset": {"amount": 3, "unit": "months"},
    })
    assert result["measured"] == "as of 3 months earlier"


def test_a_neighbouring_criterion_is_unaffected():
    """Offsets are per-criterion: one shifted rule does not move the others."""
    shifted, _ = _apply({
        "metric": "edit_count", "wiki": "en.wikipedia.org",
        "operator": "at_least", "value": 100,
        "offset": {"amount": 3, "unit": "months"},
    })
    plain, _ = _apply({
        "metric": "edit_count", "wiki": "en.wikipedia.org",
        "operator": "at_least", "value": 500,
    })
    assert "measured" in shifted
    assert "measured" not in plain
