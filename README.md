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

The full machine-readable contract for every endpoint is [`openapi.json`](openapi.json),
served at `/openapi.json` (OpenAPI 3.1). What follows is its `/check` in prose.

```
GET /check?user=<name>&policy=<policy id>
```

```
$ curl 'https://canivote.toolforge.org/check?user=Jimbo Wales&policy=meta-global'
```

```json
{
  "verdict": "eligible",
  "user": "Jimbo Wales",
  "policy": "meta-global",
  "reason": "Meets every rule that can be checked automatically.",
  "checked_at": "2026-09-07T12:00:00+00:00",
  "criteria": [
    {
      "metric": "is_blocked",
      "label": "not blocked site-wide on Meta-Wiki",
      "scope": "meta.wikimedia.org",
      "operator": "is",
      "required": {
        "value": false,
        "display": "not blocked site-wide on Meta-Wiki"
      },
      "observed": {
        "value": false,
        "display": "not blocked site-wide on Meta-Wiki"
      },
      "passed": true,
      "source": "platform"
    },
    {
      "metric": "is_globally_locked",
      "label": "not globally locked",
      "scope": "global",
      "operator": "is",
      "required": {
        "value": false,
        "display": "not globally locked"
      },
      "observed": {
        "value": false,
        "display": "not globally locked"
      },
      "passed": true,
      "source": "policy"
    },
    {
      "metric": "is_globally_blocked",
      "label": "not globally blocked",
      "scope": "global",
      "operator": "is",
      "required": {
        "value": false,
        "display": "not globally blocked"
      },
      "observed": {
        "value": false,
        "display": "not globally blocked"
      },
      "passed": true,
      "source": "policy"
    }
  ],
  "policy_text": {
    "language": "en",
    "original": "Global locks prevent an account from logging in to any Wikimedia wiki.",
    "english": "Your account must not be globally locked or globally blocked.",
    "sources": [
      "https://meta.wikimedia.org/wiki/Global_locks",
      "https://meta.wikimedia.org/wiki/Global_blocks"
    ],
    "verified": "2026-09-01"
  },
  "queries": [
    "https://meta.wikimedia.org/w/api.php?action=query&list=users&ususers=Jimbo+Wales&usprop=groups%7Cblockinfo%7Cregistration&format=json&formatversion=2&maxlag=5",
    "https://meta.wikimedia.org/w/api.php?action=query&meta=globaluserinfo&guiuser=Jimbo+Wales&format=json&formatversion=2&maxlag=5",
    "https://meta.wikimedia.org/w/api.php?action=query&list=globalblocks&bgtargets=Jimbo+Wales&bglimit=1&format=json&formatversion=2&maxlag=5"
  ]
}
```

Field notes:

- `metric` is the bare identifier; `label` is that metric **with its parameters**
  applied. They are not one-to-one — German Wikipedia's policy has two `edit_count`
  rules whose labels differ ("article-namespace edits on German Wikipedia" and
  "...in the 12 months before that"), because they measure different quantities.
  Switch on `metric`; show `label`.
- `source` says where a criterion came from: `policy` for something the community
  wrote, `platform` for a condition Wikimedia imposes whoever is asking (a global
  lock, a site-wide block). A policy that states one of the platform rules itself
  keeps its own version, and it is marked `policy`.
- `scope` is where the measurement applies: a wiki hostname such as
  `de.wikipedia.org`, or `global` for anything CentralAuth answers (locks and
  global blocks). Group by it to say which set of requirements someone fails.
- `observed.bounded` appears only on counts. It means counting stopped once the
  threshold was met, so the real number is that or higher — we do not look further
  than the question needs.
- `verdict` is `"eligible"`, `"not_eligible"`, or `"indeterminate"`. A rule that could
  not be checked (see `passed: null` below) is not the same as a rule that failed, and
  a single boolean cannot say so — collapsing them would discard the one distinction
  this design exists to keep.
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
  human. If that was a long time ago, the wording here may no longer match what the
  wiki actually says — the sources are listed so you can check.
- `queries` lists the exact upstream API URLs used to reach the verdict. Anyone can open
  one in a browser and see exactly what this tool saw.
- An entry in `criteria` is either a single criterion (it has `metric`) or a **group** (it
  has `group`, which is `any_of` or `all_of`, plus its own `passed` and a nested
  `criteria` list). Switch on which key is present. Every member of a group is evaluated
  and shown, so a reader can see which route they qualified by:

  ```json
  {
    "group": "any_of",
    "label": "at least one of these",
    "passed": true,
    "criteria": [
      {"metric": "user_groups", "passed": true, "...": "..."},
      {"group": "all_of", "label": "all of these", "passed": false, "criteria": ["..."]}
    ],
    "source": "policy"
  }
  ```


### Other endpoints

- `GET /policies` — every policy this tool knows, with a community's own wording and the
  rules a person wrote from it. Lets you check that translation without running a single
  query.
- `GET /health` — for uptime checks.
- `GET /` — name, repository, and a list of endpoints.

## What this tool does not do

It does not read policy pages, and it does not interpret prose. A person reads a
community's page and writes the machine-readable rules; the tool only executes
them. `original_text` and `sources` travel with every verdict so a reader can
audit that person's translation — they are evidence for the reader, never input
to the program.

That boundary is deliberate. A tool that inferred rules from wikitext would
leave nobody able to tell whether a verdict reflected the community's rule or
the tool's reading of it, which is the one thing this design exists to avoid.
Clauses that cannot be mechanised are carried as text under `not_checked`
rather than approximated.

### Asking as of a particular moment

A policy anchors on its own instant — "150 mainspace edits by 1 November", "two
weeks before the vote opened". Pass `as_of` to measure against it:

```
/check?user=Example&policy=enwiki-arbcom&as_of=2026-11-01T00:00:00Z
```

Without it, everything is measured from now, which quietly answers a different
question: asked on 15 November, "by 1 November" becomes "by 15 November" and a
fortnight of edits the policy excludes are counted.

A future `as_of` is allowed — asking whether you will be eligible when voting
opens is the ordinary case. The response reports `checked_at` (when we ran) and
`as_of` (what we measured against) separately.

## Rate limits

`/check` is rate limited. A request over the limit gets
`429 Too Many Requests` with a `Retry-After` header, and makes no upstream call to
Wikimedia — the limit is checked before anything is asked of the MediaWiki API, not
after.

The ceiling is set from what Wikimedia's API allows a well-behaved anonymous client,
divided by the number of calls one check costs. If you need a higher allowance for a
legitimate use, open an issue rather than working around it.


## Adding a policy

Step-by-step guides, written for someone who has not worked on this code before:

- [How to add a new policy](docs/adding-a-policy.md) — no programming needed.
- [How to add a new metric](docs/adding-a-metric.md) — a small Python change.

Everything a wiki's eligibility rule decomposes into lives in `policies.yaml`, one entry
per policy. It carries the community's wording verbatim, an English rendering of it, and
the rules that wording becomes — a reviewable diff, not a code change. `/policies` serves
this file directly, so what is written there is what the public sees.

A rule is one metric (see `metrics.py` for what can be measured — edit counts, time
since first edit, block/lock state, user groups, and so on), one operator (`at_least`,
`more_than`, `at_most`, `fewer_than`, `is`, `includes`, `excludes`), and one value.
Durations can be a plain integer of days or `{amount: N, unit: days|weeks|months|years}`.
`edit_count` takes a `namespace` of `article`, `all`, or a list of namespace numbers on
that wiki, such as `[0, 10, 14]`.
Not everything is modellable: a clause requiring human judgement (e.g. "excluding
vandalism") should not be silently dropped. List it under that policy's `not_modelled`
instead, so a verdict never looks more complete than it actually is.

Every operator works with `edit_count`. Counting stops at a cap, and the cap is widened
by one row for `more_than`, `at_most` and `is` — enough to distinguish "exactly N" from
"more than N". What `load_policies()` refuses at import time is a *threshold* the API
cannot count to: the ceiling is 500 rows, so a policy demanding 600 edits is rejected at
startup rather than failing every voter at request time while reporting "at least 500".

### Either/or rules

A policy's rules must **all** hold. When a community writes "this, or that", put the
alternatives in an `any_of` group; to require several things together inside one
alternative, use `all_of`. Ukrainian Wikipedia's adminship votes are an example: a voter
needs patroller rights, *or* 500 article edits and three months on the wiki.

```yaml
rules:
  - any_of:                           # at least one of these
      - metric: user_groups
        wiki: uk.wikipedia.org
        operator: includes
        value: editor                 # the group ukwiki calls "патрульні"
      - all_of:                       # all of these, together
          - metric: edit_count
            wiki: uk.wikipedia.org
            namespace: article
            operator: at_least
            value: 500
          - metric: time_since_first_edit
            wiki: uk.wikipedia.org
            operator: at_least
            value: {amount: 3, unit: months}
  - metric: edit_count                # and, separately, this must hold too
    ...
```

This is an inclusive OR, not an XOR: a patroller who *also* has 500 edits qualifies, as
the policy intends. A group needs at least two members and may nest. A member that
could not be checked is never read as a pass: the group passes as soon as one member
passes, fails only when every member fails, and is otherwise "could not be checked".

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
