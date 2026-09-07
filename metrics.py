"""The quantities this tool can measure about a user.

A metric is one measurable quantity with an English label. Metrics are abstract:
"time since first edit", not "days since first edit" — the unit belongs to the
value a policy compares against, because communities write the same requirement
as two weeks, fourteen days, or half a month.

Every metric is measured as of a moment. That moment is normally when the
question is asked, but a rule may shift it backwards: Dutch Wikipedia counts
edits made before the vote was *proposed*, while its other clause runs to the
vote's start.

Metrics know how to ask the API. They do not know what counts as passing.
"""

from datetime import datetime

WIKI_NAMES = {
    "en.wikipedia.org": "English Wikipedia",
    "nl.wikipedia.org": "Dutch Wikipedia",
    "de.wikipedia.org": "German Wikipedia",
    "meta.wikimedia.org": "Meta-Wiki",
    "commons.wikimedia.org": "Wikimedia Commons",
}

# Two words, not the four that appear across community policies: "article" and
# "mainspace" mean the same thing, as do "all" and "any".
NAMESPACES = {"article": "0", "all": None}


class UnknownMetric(Exception):
    """A policy names a quantity this tool cannot measure."""


class NotMeasurable(Exception):
    """The quantity exists but cannot be measured for this account."""


class AtLeast(int):
    """A count that stopped at its cap: the real number is this or higher."""

    def __str__(self):
        return f"at least {int(self):,}"


def wiki_name(wiki):
    """'nl.wikipedia.org' -> 'Dutch Wikipedia', falling back to the hostname."""
    return WIKI_NAMES.get(wiki, wiki)


def describe_span(span):
    """A timedelta as the coarsest natural English unit."""
    days = span.days
    for size, unit in ((365, "year"), (30, "month"), (7, "week")):
        if days >= size and days % size == 0:
            count = days // size
            return f"{count} {unit}{'s' if count != 1 else ''}"
    return f"{days:,} day{'s' if days != 1 else ''}"


def time_since_first_edit(lookup, as_of, *, wiki):
    """How long before `as_of` the account first edited this wiki.

    First edit, not account creation: the two differ, and accounts made before
    MediaWiki logged registrations have no creation timestamp at all.
    """
    first = lookup.first_edit(wiki)
    if first is None:
        raise NotMeasurable(f"no edits on {wiki_name(wiki)}")
    return as_of - first, f"time since first edit on {wiki_name(wiki)}"


def edit_count(lookup, as_of, *, wiki, namespace="all", within=None, cap=None):
    """How many edits the account made, optionally in one namespace or window."""
    if namespace not in NAMESPACES:
        raise UnknownMetric(f"namespace '{namespace}'")
    counted, exact = lookup.count_contributions(
        wiki, namespace=NAMESPACES[namespace],
        since=(as_of - within) if within else None, cap=cap or 1,
    )
    label = f"edits on {wiki_name(wiki)}"
    if namespace == "article":
        label = f"article-namespace {label}"
    if within:
        label += f" in the {describe_span(within)} before that"
    return (counted if exact else AtLeast(counted)), label


def time_since_registration(lookup, as_of, *, wiki):
    """How long before `as_of` the account was created on this wiki.

    Accounts made before MediaWiki began logging registrations have no creation
    timestamp. That is not a failure of the account; it is a gap in the record,
    so we say so rather than quietly treating it as zero.
    """
    registered = lookup.account(wiki).get("registration")
    if not registered:
        raise NotMeasurable(
            f"{wiki_name(wiki)} has no registration date on record for this "
            "account, which is normal for accounts created before 2006"
        )
    created = datetime.fromisoformat(registered.replace("Z", "+00:00"))
    return as_of - created, f"time since account creation on {wiki_name(wiki)}"


def user_groups(lookup, as_of, *, wiki):
    """The groups the account belongs to, such as 'extendedconfirmed' or 'bot'."""
    return sorted(lookup.account(wiki)["groups"]), f"user groups on {wiki_name(wiki)}"


def is_blocked(lookup, as_of, *, wiki):
    """Whether the account is currently blocked site-wide on one wiki."""
    account = lookup.account(wiki)
    blocked = "blockid" in account and not account.get("blockpartial", False)
    return blocked, f"blocked site-wide on {wiki_name(wiki)}"


def is_globally_locked(lookup, as_of):
    """Whether stewards have locked the account across all Wikimedia wikis."""
    return "locked" in lookup.global_account(), "globally locked"


def is_globally_blocked(lookup, as_of):
    """Whether stewards have globally blocked the account."""
    return lookup.globally_blocked(), "globally blocked"


METRICS = {
    "time_since_first_edit": time_since_first_edit,
    "time_since_registration": time_since_registration,
    "edit_count": edit_count,
    "user_groups": user_groups,
    "is_blocked": is_blocked,
    "is_globally_locked": is_globally_locked,
    "is_globally_blocked": is_globally_blocked,
}


def measure(lookup, name, as_of, **parameters):
    """Measure one metric as of a moment, returning (value, English label)."""
    try:
        take = METRICS[name]
    except KeyError:
        raise UnknownMetric(name) from None
    return take(lookup, as_of, **parameters)
