"""Re-record the fixtures from the live API.

Run this when `policies.yaml` changes, when a query's shape changes, or when a
verdict looks wrong in a way the recorded responses cannot explain. Nothing
detects a stale fixture automatically: the MediaWiki API could change shape and
the suite would stay green until somebody noticed a wrong answer in production.

One-off script: record real MediaWiki API responses as test fixtures.

Not part of the test suite (no test_ prefix, not collected by pytest). Run by
hand, against the live API, whenever a fixture needs to be re-recorded:

    .venv/bin/python tests/_record_fixtures.py

It calls the same code paths app.py uses (Lookup.account, rules.apply) with a
FIXED moment so the generated query URLs are reproducible, captures every
(url -> response JSON) pair, redacts the real username to "ExampleUser", and
writes one JSON file per scenario into tests/fixtures/.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import mediawiki  # noqa: E402
import rules  # noqa: E402
from app import baseline_rules  # noqa: E402
import yaml  # noqa: E402

REAL_USER = "Effeietsanders"
FAKE_USER = "ExampleUser"
NONEXISTENT_USER = "ThisAccountShouldNotExist0000"

# Must match tests/conftest.py's FIXED_MOMENT exactly, so replayed tests build
# byte-identical query URLs to the ones recorded here.
FIXED_MOMENT = datetime(2026, 9, 6, 15, 0, 0, tzinfo=timezone.utc)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def redact(obj):
    """Replace every occurrence of the real username with the fake one."""
    if isinstance(obj, str):
        return obj.replace(REAL_USER, FAKE_USER)
    if isinstance(obj, dict):
        return {redact(k): redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def record(scenario_name, run):
    """Run `run(lookup)` against the live API and save every query it made."""
    store = {}
    original_get = mediawiki.Lookup._get

    def instrumented(self, wiki, parameters):
        data = original_get(self, wiki, parameters)
        store[self.queries[-1]] = data
        return data

    mediawiki.Lookup._get = instrumented
    try:
        lookup = mediawiki.Lookup(REAL_USER)
        run(lookup)
    finally:
        mediawiki.Lookup._get = original_get

    redacted = redact(store)
    path = FIXTURES_DIR / f"{scenario_name}.json"
    path.write_text(json.dumps(redacted, indent=2, sort_keys=True) + "\n")
    print(f"wrote {path} ({len(redacted)} queries)")


def record_policy(policy_id, policies):
    def run(lookup):
        policy = policies[policy_id]
        lookup.account(policy["wiki"])
        # The same assembly /check uses, baseline included — recording only the
        # policy's own rules would miss the platform ones and leave the fixtures
        # unable to answer half of what a real request asks.
        for rule in baseline_rules(policy) + policy["rules"]:
            rules.apply(lookup, rule, FIXED_MOMENT)

    record(policy_id, run)


def record_nonexistent_account():
    store = {}
    original_get = mediawiki.Lookup._get

    def instrumented(self, wiki, parameters):
        data = original_get(self, wiki, parameters)
        store[self.queries[-1]] = data
        return data

    mediawiki.Lookup._get = instrumented
    try:
        lookup = mediawiki.Lookup(NONEXISTENT_USER)
        try:
            lookup.account("nl.wikipedia.org")
        except mediawiki.UserNotFound:
            pass
    finally:
        mediawiki.Lookup._get = original_get

    path = FIXTURES_DIR / "nonexistent_account.json"
    path.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n")
    print(f"wrote {path} ({len(store)} queries)")



if __name__ == "__main__":
    FIXTURES_DIR.mkdir(exist_ok=True)
    policies = yaml.safe_load((Path(__file__).parent.parent / "policies.yaml").read_text())
    for policy_id in policies:
        record_policy(policy_id, policies)
    record_nonexistent_account()
