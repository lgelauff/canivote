"""Shared test plumbing: fixture loading, a frozen clock, and no network.

Every test that reaches `/check` (or otherwise drives `mediawiki.Lookup`) does
so against JSON files recorded from the live API and replayed here — see
tests/_record_fixtures.py for how they were made. Nothing in this suite makes
a real HTTP request; `use_fixture()` raises loudly if a test asks for a query
that was never recorded, rather than silently falling through to the network.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import app as app_module  # noqa: E402
import mediawiki  # noqa: E402

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Must match tests/_record_fixtures.py's FIXED_MOMENT exactly: the query URLs
# built at test time have to be byte-identical to the ones recorded there.
FIXED_MOMENT = datetime(2026, 9, 6, 15, 0, 0, tzinfo=timezone.utc)


def load_fixture(name):
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text())


def use_fixture(monkeypatch, name):
    """Make every `mediawiki.Lookup` query in this test replay from `name`.

    Keyed by the exact request URL, the same way `Lookup._get` builds it —
    so a rule that asks for a query nobody recorded fails the test instead of
    reaching out to Wikimedia.
    """
    data = load_fixture(name)
    calls = []

    def _get(self, wiki, parameters):
        query = {
            **parameters, "format": "json", "formatversion": "2",
            "maxlag": str(mediawiki.MAX_LAG_SECONDS),
        }
        url = f"https://{wiki}/w/api.php?{urlencode(query)}"
        self.queries.append(url)
        if url not in data:
            raise AssertionError(
                f"fixture '{name}' has no recorded response for:\n{url}\n"
                f"(re-record with tests/_record_fixtures.py if this query is "
                f"supposed to happen)"
            )
        calls.append(url)
        return data[url]

    monkeypatch.setattr(mediawiki.Lookup, "_get", _get)
    return calls


def forbid_network(monkeypatch):
    """Fail loudly if this test's code path reaches out to Wikimedia at all."""

    def _get(self, wiki, parameters):
        raise AssertionError(
            "this test must make zero upstream calls, but Lookup._get was "
            f"invoked for {wiki} with {parameters}"
        )

    monkeypatch.setattr(mediawiki.Lookup, "_get", _get)


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture(autouse=True)
def _isolated_app_state(monkeypatch):
    """Freeze the clock and reset the two pieces of module-level state.

    The rate limiter lives for the life of the process, not
    the life of a request, so without this a test earlier in the file would
    leak consumed rate-limit quota into a later one.
    """

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return FIXED_MOMENT

    monkeypatch.setattr(app_module, "datetime", _FrozenDatetime)
    app_module.limiter.reset()
    yield
    app_module.limiter.reset()
