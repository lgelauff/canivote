# How to add a new policy

A **policy** is one community's rule for who may vote, for example "voting in adminship
requests on Ukrainian Wikipedia". Adding one means adding a short entry to one text file,
[`policies.yaml`](../policies.yaml). No programming is needed, and you can do all of it in
your web browser.

**You need:** a GitHub account, the community's policy page open in another tab, and
about an hour the first time.

---

## Step 1 — Read the policy and list its conditions

Open the community's policy page and find the part that says who may vote. Copy that
text somewhere: you will paste it in unchanged later.

Write each condition on its own line, in plain words. For example:

> - has made at least 500 edits to articles
> - first edited at least 3 months ago
> - has made at least 5 edits in the 3 months before the vote

For each condition, note:

- **Where** it is measured: which wiki.
- **When** it is measured: "now", "when the vote opens", "when the proposal was posted".
- Whether it says **"or"**. Most rules are a list where everything must hold. If the page
  says "this *or* that", see [Either/or](#either-or-rules) below.

If a sentence is ambiguous, look at the policy's talk page, or ask the community. Do not
guess. Write down which reading you chose and why, because it goes into `notes`.

## Step 2 — Check that each condition can be measured

The tool can measure these things (they are called **metrics**):

| Metric | What it measures | Settings it needs |
|---|---|---|
| `edit_count` | number of edits | `wiki`; optional `namespace`, `within` |
| `time_since_first_edit` | how long ago the first edit was | `wiki` |
| `time_since_registration` | how long ago the account was created | `wiki` |
| `user_groups` | the account's groups, such as `bot` or `editor` | `wiki` |
| `is_blocked` | whether the account is blocked site-wide on that wiki | `wiki` |
| `has_global_account` | whether the account is unified across Wikimedia | none |
| `is_globally_locked` | whether the account is globally locked | none |
| `is_globally_blocked` | whether the account is globally blocked | none |

If a condition is not in this table, you have two choices:

- It needs human judgement ("no vandalism", "in good standing"): list it under
  `not_modelled` (Step 4). That is normal, and it is better than leaving it out.
- It is measurable but missing: ask for a new metric, or see
  [How to add a new metric](adding-a-metric.md).

You do **not** need to add "not globally locked", "not globally blocked" or "not
blocked on this wiki". The tool adds those to every policy by itself.

## Step 3 — Open the file for editing

1. Go to <https://github.com/lgelauff/canivote/blob/main/policies.yaml>.
2. Click the **pencil icon** ("Edit this file"). If you are not a member of the project,
   GitHub will offer to make your own copy ("fork"). Accept.
3. Scroll to the bottom of the file.

## Step 4 — Add the entry

Copy an existing entry that looks like yours, paste it at the bottom, and change it.
`nlwiki-stemprocedure` is a good simple example. Here is a full template:

```yaml
xxwiki-procedure:                 # the policy's id: lowercase, words joined by hyphens
  wiki: xx.wikipedia.org
  language: xx                    # language code of the original text
  title: Voting eligibility on Xxx Wikipedia
  scope: Which votes this covers
  sources:
    - https://xx.wikipedia.org/wiki/Policy_page
  verified: 2026-10-27            # the day you read the page (YYYY-MM-DD)
  original_text: >-
    The community's own words, pasted unchanged. Long text can continue on the
    next lines, indented like this.
  english: >-
    A plain English rendering of the same rule.
  rules:
    - metric: edit_count
      wiki: xx.wikipedia.org
      namespace: article
      operator: at_least
      value: 500
  not_modelled:
    - >-
      Any clause the tool cannot check, quoted, with one line on why.
  notes: >-
    Anything a reader needs to know: which reading of an ambiguous sentence you
    chose and why, which moment the rule is measured from, known approximations.
```

### Writing a rule

Each rule is **one metric, one operator, one value**:

| Operator | Means |
|---|---|
| `at_least` | ≥ |
| `more_than` | > |
| `at_most` | ≤ |
| `fewer_than` | < |
| `is` | exactly equal, e.g. `is` `false` for "not blocked" |
| `includes` | the list contains, e.g. `user_groups includes editor` |
| `excludes` | the list does not contain, e.g. `user_groups excludes bot` |

**Durations** are a number of days (`value: 14`) or written out
(`value: {amount: 2, unit: weeks}`). Units are `days`, `weeks`, `months` and `years`.
A month counts as 30 days and a year as 365.

**Namespaces** (only for `edit_count`):

- `namespace: article` counts article edits;
- `namespace: all` (or leaving it out) counts every edit;
- a list of numbers counts several namespaces, for example
  `namespace: [0, 10, 14]` for articles, templates and categories.

Namespace numbers differ between wikis. Look them up at
`https://xx.wikipedia.org/w/api.php?action=query&meta=siteinfo&siprop=namespaces`.

**Recent edits** ("50 edits in the last 12 months"): add `within: {amount: 12, unit: months}`.

**Measured earlier** ("100 edits *a week before* the vote"): add
`offset: {amount: 1, unit: weeks}` to that rule. With both `offset` and `within`, the
window ends at the offset and runs back from there.

**Limit:** edit counts can only be checked up to 500. A rule needing more is refused.

### Either/or rules

Everything listed under `rules:` must hold. For "this **or** that", use `any_of`. To
require several things together as one of the alternatives, use `all_of` inside it:

```yaml
  rules:
    - any_of:                     # at least one of these
        - metric: user_groups
          wiki: xx.wikipedia.org
          operator: includes
          value: editor
        - all_of:                 # all of these together
            - metric: edit_count
              wiki: xx.wikipedia.org
              namespace: article
              operator: at_least
              value: 500
            - metric: time_since_first_edit
              wiki: xx.wikipedia.org
              operator: at_least
              value: {amount: 3, unit: months}
```

A group needs at least two rules in it.

### YAML tips

- Indent with **spaces**, never tabs. Line things up exactly as in the examples.
- A line starting with `- ` is one item in a list.
- `>-` lets long text run over several indented lines.
- Put text in quotes if it contains a colon followed by a space (`: `).

## Step 5 — Propose the change

1. At the top of the edit page, click **Commit changes…**.
2. Write a short message, such as "Add Xxx Wikipedia adminship policy".
3. Choose **Propose changes** (or "Create a new branch"), then **Create pull request**.

The project's automatic checks run within a few minutes:

- A **green tick** means the file loads and every test passes.
- A **red cross** means something is wrong. Click **Details** and look for a line
  starting `ValueError: policy '…'`, then fix it with the pencil icon on your pull
  request's branch.

| The error says | What to do |
|---|---|
| `unknown metric 'edit_conut'` | A typo. The message lists the correct names. |
| `unknown operator 'atleast'` | A typo. The message lists the correct operators. |
| `needs 501 rows` | The edit count is over 500, which cannot be checked. |
| `source … is not a URL` | Each source must start with `https://`. |
| `… got an unexpected keyword argument 'wiki'` | That metric takes no `wiki`. Remove it. |
| `… missing a required argument: 'wiki'` | Add `wiki:` to that rule. |
| `namespace … is not one of …` | Use `article`, `all`, or a list of numbers. |
| `'within': … is not a duration` (or `'offset'`, `'value'`) | Write a number of days, or `{amount: 3, unit: months}`. The unit is plural: `months`, not `month`. |
| `has no 'value'` | Every rule needs a `value:` line, the number or setting it is compared with. |
| `'any_of' needs a list of at least two rules` | Add a second rule, or drop the group. |
| `has no 'metric', and is not a group` | Check the indentation of that rule. |
| `mapping values are not allowed here` | YAML layout: check indentation and colons. |

## Step 6 — What happens next

A maintainer reviews the pull request. Expect questions about how you read the
policy; that is what review is for. Once it is merged, the change goes live the next
time the tool on Toolforge is updated. Then check:

`https://canivote.toolforge.org/policies/<your-policy-id>`

## Optional — check it on your own computer first

If you have Python 3.13, run these in a copy of the repository:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest ruff jsonschema
.venv/bin/pytest -q
```

`passed` with no `failed` means the same checks GitHub runs will pass.
