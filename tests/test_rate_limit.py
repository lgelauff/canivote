"""The 429 path must never reach Wikimedia.

Repeating the same user+policy means every response after the first is served
from the 60s cache — so this also exercises the limiter counting *cached*
responses toward the bucket, not just fresh ones, while proving the thing that
actually matters: a client that gets rate-limited costs Wikimedia nothing.
"""

from tests.conftest import use_fixture

from app import RATE_LIMIT


def test_429_makes_no_upstream_call_and_sends_retry_after(client, monkeypatch):
    calls = use_fixture(monkeypatch, "frwiki-sondage")
    limit = int(RATE_LIMIT.split()[0])  # "60 per minute" -> 60

    statuses = []
    for _ in range(limit + 5):
        resp = client.get("/check?user=ExampleUser&policy=frwiki-sondage")
        statuses.append(resp)

    codes = [r.status_code for r in statuses]
    assert codes.count(200) == limit
    assert codes.count(429) == 5

    first_429 = next(r for r in statuses if r.status_code == 429)
    # A refusal has to say how long to wait, or a well-behaved client cannot
    # back off correctly and an impatient one just retries immediately. This
    # needs `headers_enabled=True` on the Limiter; without it flask-limiter
    # sends no Retry-After at all.
    assert "Retry-After" in first_429.headers
    assert int(first_429.headers["Retry-After"]) > 0

    # Every 200 after the first was a cache hit; the 429s made no request at
    # all. Either way, exactly one real upstream call for the whole run.
    assert len(calls) == 1
