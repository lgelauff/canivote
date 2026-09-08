"""canivote — does this person meet a wiki's voting-eligibility policy?

  /check?user=&policy=   the verdict, the rules behind it, and the queries used
  /policies              every policy: a community's own wording, and the rules
                         a person wrote from it
  /health                for uptime checks
  /                      what this is and where to report problems

No login and no accounts. Everything it reads is public, and every verdict comes
with the API queries that produced it, so anyone can reproduce it by hand.

This tool does not read policy pages and does not interpret prose. A person
reads the community's page and writes the machine-readable rules; the tool only
executes those. The original wording and its sources travel with every verdict
so a reader can audit that person's translation — they are evidence for the
reader, never input to the program. Nothing here should ever infer a rule from
text, because then nobody could tell whether a verdict reflects the community's
rule or the tool's reading of it.

We are a guest on Wikimedia's infrastructure. One inbound request can cost them
several outbound ones, from an IP shared with every other Toolforge tool, so
this file is as careful about what it sends upstream as about what it answers.
"""

from datetime import datetime, timezone
from pathlib import Path

import yaml
from flask import Flask, jsonify, request
from flask_limiter import Limiter

import mediawiki
import metrics
import rules
from mediawiki import REPOSITORY
from metrics import UnknownMetric

# Wikimedia's gateway gives a compliant, unauthenticated User-Agent roughly
# 200 requests a minute. One check costs up to six upstream calls, so the
# inbound limit is that budget divided by the fan-out, not a round number
# chosen for looking reasonable. Raise the fan-out and this has to come down.
# We keep a fifth of it in reserve: sitting exactly on a shared ceiling is not
# a budget, and other Toolforge tools share the address we call from.
UPSTREAM_BUDGET_PER_MINUTE = 200
MAX_UPSTREAM_CALLS_PER_CHECK = 6   # measured: dewiki + the platform baseline
RATE_LIMIT = (
    f"{int(UPSTREAM_BUDGET_PER_MINUTE * 0.8) // MAX_UPSTREAM_CALLS_PER_CHECK} per minute"
)



# Conditions that hold whoever is asking, because they come from how Wikimedia
# works rather than from anything a community wrote — a global lock stops an
# account editing anywhere at all. Neither
# appears in a policy page, because neither needed saying.
#
# They are applied to every policy unless it opts out with `baseline: false`,
# and they are marked `source: platform` in the response so a reader can tell
# what the community asked for from what the software imposes.
GLOBAL_BASELINE = (
    {"metric": "has_global_account", "operator": "is", "value": True},
    {"metric": "is_globally_locked", "operator": "is", "value": False},
    {"metric": "is_globally_blocked", "operator": "is", "value": False},
)


def baseline_rules(policy):
    """The rules the software imposes on every check, whatever a policy says.

    Only conditions that hold movement-wide belong here. A *local* block is one
    community's sanction under its own blocking policy, and whether it also
    removes a vote is that community's decision to write down — some say it
    does, and WMF Board elections disqualify only an account blocked on more
    than one project. Adding it here would impose one community's sanction on
    policies that never asked for it.
    """
    if policy.get("baseline") is False:
        return []
    implied = [dict(rule) for rule in GLOBAL_BASELINE]
    # A policy stating one of these keeps its own version, so its wording stays
    # the authority. Keyed by wiki as well as metric: a rule about another wiki
    # is a different requirement and must not suppress this one.
    stated = {(rule["metric"], rule.get("wiki")) for rule in policy["rules"]}
    return [rule for rule in implied
            if (rule["metric"], rule.get("wiki")) not in stated]


def load_policies(path):
    """Read the policy file, refusing to start on anything we cannot answer.

    A wrong verdict is worse than no service, and policies.yaml is meant to be
    a file a non-programmer edits by diff — so what cannot be answered honestly
    is refused here, at startup, where it is loud.
    """
    policies = yaml.safe_load(Path(path).read_text())
    for policy_id, policy in policies.items():
        for source in policy["sources"]:
            # A verdict is only as good as the wording it claims to implement,
            # so a source has to be somewhere a reader can actually go.
            if not str(source).startswith(("https://", "http://")):
                raise ValueError(f"policy '{policy_id}': source {source!r} is not a URL")
        for rule in policy["rules"]:
            # A typo in a metric or operator name would otherwise load fine and
            # 500 at request time. This file is meant to be edited by someone
            # who does not read Python, so it fails here instead, by name.
            if rule["metric"] not in metrics.METRICS:
                raise ValueError(
                    f"policy '{policy_id}': unknown metric {rule['metric']!r}. "
                    f"Known: {', '.join(sorted(metrics.METRICS))}"
                )
            if rule["operator"] not in rules.OPERATORS:
                raise ValueError(
                    f"policy '{policy_id}': unknown operator {rule['operator']!r}. "
                    f"Known: {', '.join(sorted(rules.OPERATORS))}"
                )
            if rule["metric"] != "edit_count":
                continue
            # We settle a count by asking for one row per edit up to the
            # threshold, and the API will not return more than 500. Above that
            # we would compare against a number we never finished counting and
            # fail every voter while reporting "at least 500".
            needed = rule["value"] + (1 if rule["operator"] in rules.NEEDS_ONE_MORE_THAN_THRESHOLD else 0)
            if needed > mediawiki.MAX_ROWS_PER_REQUEST:
                raise ValueError(
                    f"policy '{policy_id}': a threshold of {rule['value']} needs "
                    f"{needed} rows, past the API's {mediawiki.MAX_ROWS_PER_REQUEST}. "
                    f"Counting that far needs a different data source."
                )
    return policies


POLICIES = load_policies(Path(__file__).parent / "policies.yaml")

app = Flask(__name__)
app.json.sort_keys = False

# One bucket for the whole tool, deliberately.
#
# Toolforge does not pass the client's address down to a tool: measured on the
# live service, remote_addr is an internal 192.168.x.x and X-Forwarded-For holds
# a single internal 172.16.x.x — the front proxy, not the caller. There is no
# client information in the request, so ProxyFix cannot recover one at any
# value of x_for. Per-client limiting is not achievable here.
#
# That is not the gap it looks like. Toolforge itself limits inbound traffic per
# source IP, so per-caller protection exists a layer above us. What this limit
# is for is the other thing entirely: keeping our own fan-out to the Wikimedia
# API inside the budget above, which is a property of the tool as a whole and is
# measured correctly by a single bucket.
# headers_enabled is what makes flask-limiter emit Retry-After; without it a
# well-behaved client has no way to learn how long to back off for.
limiter = Limiter(lambda: request.remote_addr or "unknown", app=app,
                  default_limits=[], storage_uri="memory://",
                  headers_enabled=True)


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
            "/policies": "every policy and the rules a person wrote from it",
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
    policy_id = (request.args.get("policy") or "").strip()

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

    policy = POLICIES[policy_id]
    moment = datetime.now(timezone.utc)
    lookup = mediawiki.Lookup(username)

    try:
        # Confirm the account exists before scoring any rule. Contribution
        # queries answer "no edits" for a nonexistent account just as they do
        # for a new one, so without this a typo would read as a failed vote.
        lookup.account(policy["wiki"])
        implied = baseline_rules(policy)
        applied = [{**rules.apply(lookup, rule, moment), "source": "platform"}
                   for rule in implied]
        applied += [{**rules.apply(lookup, rule, moment), "source": "policy"}
                    for rule in policy["rules"]]
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
    return jsonify(body)


def _verdict(username, policy_id, policy, applied, lookup, moment, *, verdict, reason):
    """Assemble the response. Order matters: verdict first, evidence after."""
    body = {
        "verdict": verdict,
        "user": username,
        "policy": policy_id,
        "reason": reason,
        "checked_at": moment.isoformat(timespec="seconds"),
        "criteria": applied,
        "policy_text": {
            "language": policy["language"],
            "original": policy["original_text"],
            "english": policy["english"],
            "sources": policy["sources"],
            # When a person last read the policy page. How old is too old is
            # the reader's call, not ours.
            # str(), because YAML reads an unquoted 2026-09-01 as a date object
            # and Flask would render that as "Tue, 01 Sep 2026 00:00:00 GMT".
            "verified": str(policy.get("verified", "")),
        },
        "queries": lookup.queries,
    }
    if policy.get("notes"):
        body["policy_text"]["notes"] = policy["notes"]
    if policy.get("not_modelled"):
        body["not_checked"] = policy["not_modelled"]
    return body






if __name__ == "__main__":
    app.run(port=5001, debug=True)
