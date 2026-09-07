# canivote

Wikimedia communities each define who may vote, in their own words, on their own policy
pages. `canivote` answers one question over HTTP — *does this account meet this policy?* —
and shows its working: the community's original wording, an English rendering, per-rule
pass/fail, and the exact API queries used, so anybody can reproduce the verdict by hand.

It is a small, standalone, API-first service — no database, no OAuth, no accounts, no
background jobs. Everything it reads is public. [wiki-polis](https://github.com/lgelauff/wiki-polis)
is the first consumer, but `canivote` is not a wiki-polis component and does not inherit
that project's operational patterns.

## The `/check` contract

```
GET /check?user=<name>&policy=<policy id>
```

`event` is also accepted as an alias for `policy` (an existing client sends that name).

```
$ curl 'https://canivote.toolforge.org/check?user=Jimbo Wales&policy=meta-global'
```

```json
{
  "eligible": true,
  "verdict": "eligible",
  "user": "Jimbo Wales",
  "policy": "meta-global",
  "reason": "Meets every rule that can be checked automatically.",
  "checked_at": "2026-09-06T15:00:00+00:00",
  "cached": false,
  "criteria": [
    {
      "metric": "is_globally_locked",
      "label": "globally locked",
      "scope": "global",
      "operator": "is",
      "required": {"value": false, "display": "not globally locked"},
      "observed": {"value": false, "display": "not globally locked"},
      "passed": true
    },
    {
      "metric": "is_globally_blocked",
      "label": "globally blocked",
      "scope": "global",
      "operator": "is",
      "required": {"value": false, "display": "not globally blocked"},
      "observed": {"value": false, "display": "not globally blocked"},
      "passed": true
    }
  ],
  "policy_text": {
    "language": "en",
    "original": "Global locks prevent an account from logging in to any Wikimedia wiki.",
    "english": "Your account must not be globally locked or globally blocked by stewards.",
    "sources": ["https://meta.wikimedia.org/wiki/Global_locks",
               "https://meta.wikimedia.org/wiki/Global_blocks"],
    "verified": "2026-09-01",
    "stale": false
  },
  "queries": [
    "https://meta.wikimedia.org/w/api.php?action=query&meta=globaluserinfo&..."
  ]
}
```

Field notes:

- `metric` is the bare identifier; `label` is that metric **with its parameters**
  applied. They are not one-to-one — German Wikipedia's policy has two `edit_count`
  rules whose labels differ ("article-namespace edits on German Wikipedia" and
  "...in the 12 months before that"), because they measure different quantities.
  Switch on `metric`; show `label`.
- `scope` is where the measurement applies: a wiki hostname such as
  `de.wikipedia.org`, or `global` for anything CentralAuth answers (locks and
  global blocks). Group by it to say which set of requirements someone fails.
- `observed.bounded` appears only on counts. It means counting stopped once the
  threshold was met, so the real number is that or higher — we do not look further
  than the question needs.
- `eligible` is a plain boolean, kept for clients that already read it.
- `verdict` is `"eligible"`, `"not_eligible"`, or `"indeterminate"` — the tri-state
  `eligible` alone cannot express. A rule that could not be checked (see `passed: null`
  below) is not the same as a rule that failed; collapsing them into one `false` would
  discard information the design exists to keep.
- Each entry in `criteria` has `passed: true`, `passed: false`, or `passed: null`.
  `null` means the rule is unmeasurable for this account (for example, an account old
  enough to predate MediaWiki's registration logging) — it is scored as neither a pass
  nor a fail.
- `observed.bounded: true` means counting stopped once a threshold was met — the real
  number is *this value or higher*, not necessarily exact. Counting-to-a-cap is what
  makes a check with a large edit count cost one request instead of a full contributions
  scan; `bounded` is how that gets communicated back rather than hidden.
- `required` and `observed` carry both a machine-readable `value` and a rendered
  `display` string, so a consumer can compute ("you need 43 more edits"), localise, or
  chart, without having to parse English prose back into numbers.
- `policy_text.verified` is the date the policy's source pages were last read by a
  human, and `stale` is `true` once that is more than about a year old — a signal that
  the wording here may no longer match what the wiki actually says.
- `queries` lists the exact upstream API URLs used to reach the verdict. Anyone can open
  one in a browser and see exactly what this tool saw.


### Other endpoints

- `GET /policies` — every policy this tool knows, with its source wording and the rules
  it decomposes into. Lets you check our reading of a community's own policy without
  running a single query.
- `GET /health` — for uptime checks.
- `GET /` — name, repository, and a list of endpoints.

## Rate limits

`/check` is limited to 60 requests per minute. Behind Toolforge's front proxy this is currently one shared bucket for the whole tool rather than one per client. A request over the limit gets
`429 Too Many Requests` and makes no upstream call to Wikimedia — the limit is checked
before anything is asked of the MediaWiki API, not after.

Identical `user` + `policy` checks are also cached for up to 60 seconds (`cached: true`
on a cache hit, with the original `checked_at`). This is a courtesy against accidental
repeats, not a defence against a deliberate client — 60 seconds is a bound, not a
guarantee of freshness. Block and lock state in particular can change at any moment; a
steward acting mid-consultation is exactly the case those rules exist for.

`/check` is disallowed in `robots.txt` and sent with `X-Robots-Tag: noindex, nofollow`.
The bigger amplification risk here is crawlers following a `/check?user=…` link that
ends up on a wiki page, not a single abusive client — a per-IP limit does nothing
against a distributed crawl.

## Adding a policy

Everything a wiki's eligibility rule decomposes into lives in `policies.yaml`, one entry
per policy. It carries the community's wording verbatim, an English rendering of it, and
the rules that wording becomes — a reviewable diff, not a code change. `/policies` serves
this file directly, so what is written there is what the public sees.

A rule is one metric (see `metrics.py` for what can be measured — edit counts, time
since first edit, block/lock state, user groups, and so on), one operator (`at_least`,
`more_than`, `at_most`, `fewer_than`, `is`, `includes`, `excludes`), and one value.
Durations can be a plain integer of days or `{amount: N, unit: days|weeks|months|years}`.
Not everything is modellable: a clause requiring human judgement (e.g. "excluding
vandalism") should not be silently dropped. List it under that policy's `not_modelled`
instead, so a verdict never looks more complete than it actually is.

`edit_count` cannot support `at_most` or `fewer_than`: counting stops once a threshold
is met, so it can only prove "at least N", never "at most N". `load_policies()` refuses
to start if a policy tries this, at import time, rather than silently returning a wrong
verdict at request time.

## Privacy: the access log

The response body is entirely public data. The access log is not: it is the one place
that pairs *who asked* with *whom they asked about* — a fact this tool creates and that
does not exist anywhere else. Application logs deliberately keep usernames out for this
reason; be careful not to reintroduce them (for example, in error messages or debug
logging) if you're extending this code.

## Prior art

[Pathoschild's AccountEligibility](https://github.com/Pathoschild/Wikimedia.Bot.AccountEligibility)
answers a related question for enwiki RfAs and is worth knowing about if you're working
in this space. No code from it was copied into this project.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest ruff

.venv/bin/pytest -q
.venv/bin/ruff check .
```

Tests replay JSON fixtures recorded from the live MediaWiki API (`tests/fixtures/`) and
make no network calls themselves. See `tests/_record_fixtures.py` to re-record them.

## License

MIT — see `LICENSE`.
