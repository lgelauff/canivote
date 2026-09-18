"""canivote — does this person meet a wiki's voting-eligibility policy?

  /check?user=&policy=   the verdict, the rules behind it, and the queries used
  /policies              what there is to choose from; ?wiki= narrows it
  /policies/<id>         one policy in full, and the rules it becomes
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

import inspect
import re
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
    # No attempt to suppress a rule a policy also states. Rules are ANDed and
    # lookups are memoised, so a repeat costs no upstream request and cannot
    # change a verdict — the only thing deduplication bought was a shorter
    # list, and it bought that at the price of deciding when two rules are
    # "the same", which is where it went wrong. If a community states a
    # condition the software also imposes, the response shows both, one marked
    # `policy` and one `platform`, which is the more honest reading anyway.
    return [dict(rule) for rule in GLOBAL_BASELINE]


def _parse_moment(text):
    """Parse an ISO 8601 timestamp from a query string.

    A `+` in a query string decodes to a space, so `...T00:00:00+02:00` arrives
    as `...T00:00:00 02:00` and will not parse. Rather than tell a caller their
    valid timestamp is invalid, restore the sign — but only on a trailing
    offset, since ISO also allows a space where the `T` goes.
    """
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.fromisoformat(re.sub(r" (\d{2}:\d{2})$", r"+\1", text))


def resolve(policy):
    """Every rule that will actually be evaluated, paired with where it came from.

    One place answers "what applies to this policy", so the endpoint that
    reports the rules and the endpoint that runs them cannot disagree. They did
    disagree: /policies served the file verbatim while /check also applied the
    platform's rules, so a policy stating none was published as requiring
    nothing while three conditions were being checked.

    When policies gain a parent (#4), only this walks the chain — both callers
    follow without changing.
    """
    return ([(rule, "platform") for rule in baseline_rules(policy)]
            + [(rule, "policy") for rule in policy["rules"]])


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
            # Names are not enough: a metric that takes no wiki, given one,
            # or one that requires a wiki, given none, both load fine and then
            # raise TypeError at request time as a 500. Bind the parameters
            # here so the mistake is named at startup instead.
            given = {key: value for key, value in rule.items()
                     if key not in ("metric", "operator", "value", "offset")}
            try:
                inspect.signature(metrics.METRICS[rule["metric"]]).bind(
                    None, None, **given)
            except TypeError as mismatch:
                raise ValueError(
                    f"policy '{policy_id}': rule {rule['metric']!r} — {mismatch}"
                ) from None
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
    """What there is to choose from — enough to pick one, not the whole file.

    Answering "which policies exist" should not cost a reader every word of
    every community's page. Each entry names itself and links to its own detail;
    `?wiki=` narrows to one project, since that is how somebody looking for a
    policy actually looks.
    """
    wanted = (request.args.get("wiki") or "").strip()
    known_wikis = sorted({p["wiki"] for p in POLICIES.values() if p.get("wiki")})
    if wanted and wanted not in known_wikis:
        return jsonify(error=f"No policies for '{wanted}'.",
                       known_wikis=known_wikis), 404

    chosen = {policy_id: policy for policy_id, policy in POLICIES.items()
              if not wanted or policy.get("wiki") == wanted}
    return jsonify(
        wikis=known_wikis,
        policies=[{
            "id": policy_id,
            "title": policy.get("title"),
            "wiki": policy.get("wiki"),
            "scope": policy.get("scope"),
            "verified": str(policy.get("verified", "")),
            "rule_count": len(resolve(policy)),
            "detail": f"/policies/{policy_id}",
        } for policy_id, policy in sorted(chosen.items())],
    )


@app.get("/policies/<policy_id>")
def policy_detail(policy_id):
    """One policy in full: the community's wording, and the rules it becomes.

    Every rule that would actually be evaluated, tagged by origin — the same
    resolution /check runs, so the two cannot describe different things.
    """
    policy = POLICIES.get(policy_id)
    if policy is None:
        return jsonify(error=f"Unknown policy '{policy_id}'.",
                       known_policies=sorted(POLICIES)), 404
    return jsonify({**policy,
                    "id": policy_id,
                    "verified": str(policy.get("verified", "")),
                    "rules": [{**rule, "source": origin}
                              for rule, origin in resolve(policy)]})


@app.get("/check")
@limiter.limit(RATE_LIMIT)
def check():
    """Check one user against one policy."""
    raw_user = request.args.get("user") or ""
    # `event` is what wiki-polis's deployed client sends (v2/app.py:1547). It
    # was removed once on the grounds that no consumer existed; the consumer
    # existed and was in production, and only the URL pointing at us was
    # missing. Accepting both costs one line and cannot break on a name.
    policy_id = (request.args.get("policy")
                 or request.args.get("event") or "").strip()

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
    now = datetime.now(timezone.utc)
    # A policy anchors on its own moment — "150 edits by 1 November", "two
    # weeks before the vote opened". Without being told which, we can only
    # measure from now, which quietly answers a different question. Callers
    # that know the date say so; a future one is allowed, since asking before
    # a vote opens is the ordinary case.
    requested = (request.args.get("as_of") or "").strip()
    if requested:
        try:
            moment = _parse_moment(requested)
        except ValueError:
            return jsonify(error="'as_of' must be an ISO 8601 timestamp, "
                                 "for example 2026-11-01T00:00:00Z"), 400
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = now
    lookup = mediawiki.Lookup(username)

    try:
        # Confirm the account exists before scoring any rule. Contribution
        # queries answer "no edits" for a nonexistent account just as they do
        # for a new one, so without this a typo would read as a failed vote.
        lookup.account(policy["wiki"])
        applied = [{**rules.apply_safely(lookup, rule, moment), "source": origin}
                   for rule, origin in resolve(policy)]
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

    body = _verdict(username, policy_id, policy, applied, lookup, moment, now=now,
                    verdict=verdict, reason=reason)
    return jsonify(body)


def _verdict(username, policy_id, policy, applied, lookup, moment, *, verdict, reason, now=None):
    """Assemble the response. Order matters: verdict first, evidence after."""
    body = {
        "verdict": verdict,
        "user": username,
        "policy": policy_id,
        "reason": reason,
        # When we ran, and what we measured against. They differ whenever a
        # caller supplies the moment its policy anchors on.
        "checked_at": (now or moment).isoformat(timespec="seconds"),
        "as_of": moment.isoformat(timespec="seconds"),
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
