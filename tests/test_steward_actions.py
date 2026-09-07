"""Locks and global blocks must be detected, not merely not-crash.

Both metrics read a positive result from the *presence* of something — a
`locked` key, a non-empty block list — and for a long time only the negative
case had ever been seen. That is the dangerous way round: if the shape were
different from what was assumed, the check would be permanently false and a
locked account would be judged eligible, in a design that is otherwise
fail-closed throughout.

The shapes below were confirmed against the live API on 2026-09-07, using
accounts the API itself supplied (`list=globalblocks`, and the `globalauth`
log). The fixtures are synthetic reproductions of those shapes: no real
account is named here, and none needs to be.
"""

from datetime import datetime, timezone

import mediawiki
import metrics

MOMENT = datetime(2026, 9, 7, tzinfo=timezone.utc)


class _Canned(mediawiki.Lookup):
    def __init__(self, username, payloads):
        super().__init__(username)
        self._payloads = payloads

    def _get(self, wiki, parameters):
        self.queries.append(parameters)
        return self._payloads[parameters.get("meta") or parameters.get("list")]


# A locked account carries `locked: true`. An unlocked one has no such key at
# all — which is what makes presence a sound signal rather than a lucky guess.
LOCKED = {"globaluserinfo": {"query": {"globaluserinfo": {
    "home": "examplewiki", "id": 1, "name": "ExampleUser",
    "registration": "2010-01-01T00:00:00Z", "locked": True}}}}
NOT_LOCKED = {"globaluserinfo": {"query": {"globaluserinfo": {
    "home": "examplewiki", "id": 2, "name": "ExampleUser",
    "registration": "2010-01-01T00:00:00Z"}}}}

BLOCKED = {"globalblocks": {"query": {"globalblocks": [{
    "id": 1, "target": "ExampleUser", "by": "ExampleSteward",
    "expiry": "infinity", "reason": "cross-wiki abuse"}]}}}
NOT_BLOCKED = {"globalblocks": {"query": {"globalblocks": []}}}


def test_a_locked_account_is_detected():
    seen, label = metrics.is_globally_locked(_Canned("ExampleUser", LOCKED), MOMENT)
    assert seen is True
    assert label == "globally locked"


def test_an_unlocked_account_has_no_locked_key():
    """The negative case must be the *absence* of the key, not `locked: false`.

    If it were ever `false`, presence-checking would report every account as
    locked — so this pins the assumption the positive test relies on.
    """
    lookup = _Canned("ExampleUser", NOT_LOCKED)
    assert "locked" not in lookup.global_account()
    assert metrics.is_globally_locked(lookup, MOMENT)[0] is False


def test_a_globally_blocked_account_is_detected():
    seen, label = metrics.is_globally_blocked(_Canned("ExampleUser", BLOCKED), MOMENT)
    assert seen is True
    assert label == "globally blocked"


def test_an_unblocked_account_returns_an_empty_list():
    assert metrics.is_globally_blocked(_Canned("ExampleUser", NOT_BLOCKED), MOMENT)[0] is False
