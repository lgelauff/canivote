# How to add a new metric

A **metric** is one thing the tool can measure about an account, such as "number of
edits" or "time since first edit". Policies combine metrics into rules; see
[How to add a new policy](adding-a-policy.md).

Add a metric only when a policy needs something none of the existing metrics can
measure, even with their settings (`namespace`, `within`, `offset`). Adding a metric is a
small Python change in two or three files plus a test. This guide walks through one
complete example you can copy and adapt.

**The example:** "time since last edit", for policies that require recent activity
("must have edited in the last 6 months"). It is **not** in the code. It is here to
show the shape.

---

## Step 1 — Do you need a new API query?

Look at `Lookup` in [`mediawiki.py`](../mediawiki.py). It already knows how to fetch:

- `account(wiki)`: groups, block state, registration date (one query, reused);
- `global_account()`: CentralAuth record (lock state);
- `globally_blocked()`;
- `first_edit(wiki, before)`;
- `count_contributions(wiki, namespace, since, before, cap)`.

If your metric can be computed from one of these, skip to Step 2. "Last edit" cannot, so
the example adds a method next to `first_edit`, in `mediawiki.py`:

```python
    def last_edit(self, wiki, before=None):
        """When the account last edited this wiki before `before`, or None."""
        parameters = {
            "action": "query", "list": "usercontribs",
            "ucuser": self.username, "ucdir": "older",
            "uclimit": "1", "ucprop": "timestamp",
        }
        if before:
            parameters["ucstart"] = _timestamp(before)
        edits = self._get(wiki, parameters)["query"]["usercontribs"]
        if not edits:
            return None
        return datetime.fromisoformat(edits[0]["timestamp"].replace("Z", "+00:00"))
```

Rules for a new query:

- **Always go through `self._get`.** It records the URL so every verdict shows its
  queries, and it turns API errors into a clean "Wikimedia could not be reached".
- **Ask for as little as possible.** `uclimit=1` here. Never fetch a whole history.
- **One query per metric.** Each check has a budget of upstream calls:
  `MAX_UPSTREAM_CALLS_PER_CHECK` in [`app.py`](../app.py). If your metric pushes a real
  policy over it, lower `RATE_LIMIT` to match, or find a cheaper query.

## Step 2 — Write the metric

In [`metrics.py`](../metrics.py), add a function. Every metric has the same shape:

- arguments `(lookup, as_of, *, <settings>)`, where `as_of` is the moment to measure at;
- it returns `(value, label)`. The label is English prose naming what was measured.

```python
def time_since_last_edit(lookup, as_of, *, wiki):
    """How long before `as_of` the account last edited this wiki."""
    label = f"time since last edit on {wiki_name(wiki)}"
    last = lookup.last_edit(wiki, before=as_of)
    if last is None:
        raise Absent(f"no edits on {wiki_name(wiki)}", label=label)
    return as_of - last, label
```

When the measurement cannot give a normal answer, raise one of these two exceptions:

- **`Absent`**: there is nothing to measure, and that *settles* the rule. No edits means
  no recent edit, so the rule fails.
- **`NotMeasurable`**: the fact exists but cannot be read (for example, a registration
  date missing from very old accounts). The rule is reported as "could not be checked",
  never as passed or failed.

Then register it in the `METRICS` dictionary at the bottom of the same file:

```python
    "time_since_last_edit": time_since_last_edit,
```

**If the value is a span of time,** also add the name to `DURATION_METRICS` in
[`rules.py`](../rules.py), so that a plain number in a policy is read as days.

## Step 3 — Test it

Tests never touch the network. Give the metric a fake lookup that answers from fixed
facts. Add a file `tests/test_time_since_last_edit.py`:

```python
from datetime import datetime, timedelta, timezone

import pytest

from metrics import Absent, time_since_last_edit

NOW = datetime(2026, 9, 6, 15, 0, tzinfo=timezone.utc)


class FakeLookup:
    def __init__(self, last):
        self.last = last

    def last_edit(self, wiki, before=None):
        return self.last


def test_measures_from_the_moment_asked():
    value, label = time_since_last_edit(
        FakeLookup(NOW - timedelta(days=10)), NOW, wiki="nl.wikipedia.org")
    assert value == timedelta(days=10)
    assert label == "time since last edit on Dutch Wikipedia"


def test_no_edits_settles_it():
    with pytest.raises(Absent):
        time_since_last_edit(FakeLookup(None), NOW, wiki="nl.wikipedia.org")
```

For a test through the whole `/check` endpoint, copy `fake_lookup` from
[`tests/test_rule_groups.py`](../tests/test_rule_groups.py) and add a branch for your
query.

Run everything:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
```

## Step 4 — Use it and document it

1. Use it in a policy in `policies.yaml`:

   ```yaml
       - metric: time_since_last_edit
         wiki: xx.wikipedia.org
         operator: at_most
         value: {amount: 6, unit: months}
   ```

2. Add a row to the metric table in [adding-a-policy.md](adding-a-policy.md), and update
   the metric list in the README's "Adding a policy" section.
3. If the response can contain a new kind of `value`, check that `openapi.json` still
   describes it. `tests/test_openapi_contract.py` will fail if it does not.

## Two things not to do

- **Never log usernames.** The access log is the one place that links who asked with
  whom they asked about. Keep usernames out of error messages and debug logging.
- **Never interpret policy text in code.** A metric measures one thing. What a community
  meant belongs in `policies.yaml`, where a person writes it and anyone can review it.
