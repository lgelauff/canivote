"""canivote — does this person meet a wiki's voting-eligibility policy?

  /check?user=&policy=   the verdict, the rules behind it, and the queries used
  /policies              every policy, its own wording, and what it decomposes to
  /health                for uptime checks
  /                      what this is and where to report problems

No login and no accounts. Everything it reads is public, and every verdict comes
with the API queries that produced it, so anyone can reproduce it by hand.

We are a guest on Wikimedia's infrastructure. One inbound request can cost them
several outbound ones, from an IP shared with every other Toolforge tool, so
this file is as careful about what it sends upstream as about what it answers.
"""

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from flask import Flask, jsonify, request
from flask_limiter import Limiter

import mediawiki
import rules
from metrics import UnknownMetric

REPOSITORY = "https://github.com/lgelauff/canivote"
RATE_LIMIT = "60 per minute"
CACHE_SECONDS = 60
CACHE_MAX_ENTRIES = 512
STALE_AFTER_DAYS = 365

# Counting stops once a threshold is met, so we can only ever prove "at least
# N". An "at most" rule would compare against a number we deliberately stopped
# short of and pass an account with far more edits than it allows.
CAPPED_METRICS = {"edit_count"}
UNSAFE_WITH_CAPPING = {"at_most", "fewer_than"}

REQUIRED_POLICY_FIELDS = (
    "wiki", "language", "sources", "verified", "original_text", "english", "rules",
)
REQUIRED_RULE_FIELDS = ("metric", "operator", "value")


def load_policies(path):
    """Read the policy file, refusing to start on a rule we cannot answer.

    A wrong verdict is worse than no service, and policies.yaml is meant to be
    a file someone can safely edit — so the check belongs here, at startup,
    where it is loud, rather than in the request path where it is not.
    """
    policies = yaml.safe_load(Path(path).read_text())
    for policy_id, policy in policies.items():
        def wrong(problem):
            return ValueError(f"policy '{policy_id}': {problem}")

        missing = [f for f in REQUIRED_POLICY_FIELDS if not policy.get(f) and f != "rules"]
        if missing or "rules" not in policy:
            raise wrong(f"missing {', '.join(missing or ['rules'])}")

        # A verdict is only as good as the wording it claims to implement, so a
        # source has to be somewhere a reader can actually go and check.
        for source in policy["sources"]:
            if not str(source).startswith(("https://", "http://")):
                raise wrong(f"source {source!r} is not a URL")

        # `verified` drives the staleness flag; an unparseable one would make
        # every verdict silently claim to be freshly checked.
        try:
            datetime.fromisoformat(str(policy["verified"]))
        except (TypeError, ValueError):
            raise wrong(f"verified {policy['verified']!r} is not a date") from None

        for rule in policy["rules"]:
            absent = [f for f in REQUIRED_RULE_FIELDS if f not in rule]
            if absent:
                raise wrong(f"a rule is missing {', '.join(absent)}")
            if (rule["metric"] in CAPPED_METRICS
                    and rule["operator"] in UNSAFE_WITH_CAPPING):
                raise ValueError(
                    f"policy '{policy_id}' uses '{rule['operator']}' on "
                    f"'{rule['metric']}', which counting-to-a-cap cannot answer "
                    f"correctly. Use a different metric or operator."
                )
    return policies


POLICIES = load_policies(Path(__file__).parent / "policies.yaml")

app = Flask(__name__)
app.json.sort_keys = False

# One bucket per client. NOTE: Toolforge runs behind a front proxy, so this is
# probably the proxy's address for everybody until the real hop count is known
# and ProxyFix is pinned to it. This function is the single place to fix that.
# headers_enabled is what makes flask-limiter emit Retry-After; without it a
# well-behaved client has no way to learn how long to back off for.
limiter = Limiter(lambda: request.remote_addr or "unknown", app=app,
                  default_limits=[], storage_uri="memory://",
                  headers_enabled=True)

_cache = OrderedDict()


@app.after_request
def _headers(response):
    # A public, read-only GET API: allowing cross-origin reads costs nothing and
    # means an on-wiki gadget can use this without waiting for a redeploy.
    response.headers["Access-Control-Allow-Origin"] = "*"
    if request.path == "/check":
        # Every /check URL is a fan-out to Wikimedia. Search engines following
        # them from a wiki page would amplify far past any one client, and a
        # per-IP limit does nothing about a distributed crawl.
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.get("/robots.txt")
def robots():
    return ("User-agent: *\nDisallow: /check\n", 200, {"Content-Type": "text/plain"})


@app.get("/")
def index():
    return jsonify(
        name="canivote",
        description="Does a Wikimedia account meet a community's voting-eligibility policy?",
        repository=REPOSITORY,
        endpoints={
            "/check": "?user=<name>&policy=<id>",
            "/policies": "every policy and the rules it becomes",
            "/health": "uptime check",
        },
    )


@app.get("/health")
def health():
    return jsonify(status="ok", policies=len(POLICIES))


@app.get("/policies")
def policies():
    """Every policy this tool knows, with its source wording and its rules.

    The mapping in full: what a community wrote, how it reads in English, and
    the rules it becomes. Anyone can check our reading of their own policy
    without running a single query.
    """
    return jsonify(policies=POLICIES)


@app.get("/check")
@limiter.limit(RATE_LIMIT)
def check():
    """Check one user against one policy."""
    raw_user = request.args.get("user") or ""
    # 'event' is accepted because wiki-polis's existing client sends that name.
    policy_id = (request.args.get("policy") or request.args.get("event") or "").strip()

    if not raw_user.strip() or not policy_id:
        return jsonify(error="Provide both 'user' and 'policy'."), 400
    if policy_id not in POLICIES:
        return jsonify(error=f"Unknown policy '{policy_id}'.",
                       known_policies=sorted(POLICIES)), 404

    # Normalise and validate before anything leaves the building: junk should
    # cost us a string comparison and Wikimedia nothing at all.
    try:
        username = mediawiki.normalise_username(raw_user)
    except mediawiki.UsernameInvalid:
        return jsonify(error="That is not a valid username."), 400

    cached = _from_cache(username, policy_id)
    if cached is not None:
        return jsonify(cached)

    policy = POLICIES[policy_id]
    moment = datetime.now(timezone.utc)
    lookup = mediawiki.Lookup(username)

    try:
        # Confirm the account exists before scoring any rule. Contribution
        # queries answer "no edits" for a nonexistent account just as they do
        # for a new one, so without this a typo would read as a failed vote.
        lookup.account(policy["wiki"])
        applied = [rules.apply(lookup, rule, moment) for rule in policy["rules"]]
    except mediawiki.UsernameInvalid:
        return jsonify(_verdict(username, policy_id, policy, [], lookup, moment,
                                verdict="not_eligible",
                                reason="That is not a valid username."))
    except mediawiki.UserNotFound:
        return jsonify(_verdict(username, policy_id, policy, [], lookup, moment,
                                verdict="not_eligible", reason="No such account."))
    except mediawiki.UpstreamUnavailable as outage:
        # Their outage, not ours, and not the user's fault either. Say so
        # plainly rather than returning a verdict we did not actually reach.
        return jsonify(error="The Wikimedia API could not be reached.",
                       detail=str(outage)), 502
    except (UnknownMetric, rules.UnknownOperator) as broken:
        return jsonify(error=f"Policy '{policy_id}' is not valid: {broken}"), 500

    failed = [rule for rule in applied if rule["passed"] is False]
    unknown = [rule for rule in applied if rule["passed"] is None]

    if failed:
        verdict, reason = "not_eligible", f"Fails {len(failed)} of {len(applied)} rules."
    elif unknown:
        verdict = "indeterminate"
        reason = f"{len(unknown)} of {len(applied)} rules could not be checked."
    else:
        verdict = "eligible"
        reason = "Meets every rule that can be checked automatically."

    body = _verdict(username, policy_id, policy, applied, lookup, moment,
                    verdict=verdict, reason=reason)
    _remember(username, policy_id, body)
    return jsonify(body)


def _verdict(username, policy_id, policy, applied, lookup, moment, *, verdict, reason):
    """Assemble the response. Order matters: verdict first, evidence after."""
    verified = policy.get("verified")
    body = {
        # `eligible` stays for clients that already read it; `verdict` carries
        # the distinction it cannot — a rule we could not check is not the same
        # as a rule that failed, and collapsing them loses the honest answer.
        "eligible": verdict == "eligible",
        "verdict": verdict,
        "user": username,
        "policy": policy_id,
        "reason": reason,
        "checked_at": moment.isoformat(timespec="seconds"),
        "cached": False,
        "criteria": applied,
        "policy_text": {
            "language": policy["language"],
            "original": policy["original_text"],
            "english": policy["english"],
            "sources": policy["sources"],
            # When we last read the policy page, and whether that was long
            # enough ago that a reader should go and check it themselves.
            "verified": verified,
            "stale": _is_stale(verified),
        },
        "queries": lookup.queries,
    }
    if policy.get("notes"):
        body["policy_text"]["notes"] = policy["notes"]
    if policy.get("not_modelled"):
        body["not_checked"] = policy["not_modelled"]
    return body


def _is_stale(verified):
    """Has nobody checked this community's policy page for a year?"""
    if not verified:
        return True
    checked = datetime.fromisoformat(str(verified)).replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - checked > timedelta(days=STALE_AFTER_DAYS)


def _from_cache(username, policy_id):
    """A recent identical answer, or None.

    Deliberately short-lived. Most of a verdict changes slowly, but block and
    lock status does not: a steward acting mid-consultation is precisely the
    case those rules exist for, and a stale 'eligible' would defeat them.
    """
    entry = _cache.get((username, policy_id))
    if entry is None:
        return None
    stored_at, body = entry
    if (datetime.now(timezone.utc) - stored_at).total_seconds() > CACHE_SECONDS:
        del _cache[(username, policy_id)]
        return None
    _cache.move_to_end((username, policy_id))
    # Say the answer is reused and when it was actually reached, rather than
    # claiming a freshness it does not have.
    return {**body, "cached": True}


def _remember(username, policy_id, body):
    _cache[(username, policy_id)] = (datetime.now(timezone.utc), body)
    _cache.move_to_end((username, policy_id))
    while len(_cache) > CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)


if __name__ == "__main__":
    app.run(port=5001, debug=True)
