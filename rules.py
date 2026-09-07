"""Rules: one metric, one operator, one value, one result.

A rule is a mathematical statement about a measured quantity, and nothing more.
It carries no wiki-specific knowledge — that lives in metrics.py — and no
judgement about what a community meant, which lives in the policy's own words.

"""

from datetime import timedelta

from metrics import AtLeast, NotMeasurable, describe_span, measure

UNITS = {
    "days": 1,
    "weeks": 7,
    "months": 30,   # policies say "two months"; nobody means 61 days exactly
    "years": 365,
}

# Counting stops at a cap, so a comparison is only answerable if the cap is set
# high enough to distinguish the cases either side of it. "At least 200" needs
# 200 rows; "exactly 200" needs 201, because 200 rows cannot tell 200 from 900.
NEEDS_ONE_MORE_THAN_THRESHOLD = {"more_than", "at_most", "is"}

OPERATORS = {
    "at_least": (lambda seen, want: seen >= want, "at least"),
    "more_than": (lambda seen, want: seen > want, "more than"),
    "at_most": (lambda seen, want: seen <= want, "at most"),
    "fewer_than": (lambda seen, want: seen < want, "fewer than"),
    "is": (lambda seen, want: seen == want, "is"),
    "includes": (lambda seen, want: want in seen, "includes"),
    "excludes": (lambda seen, want: want not in seen, "does not include"),
}


class UnknownOperator(Exception):
    """A rule uses a comparison this tool does not know."""


def as_duration(value):
    """{'amount': 2, 'unit': 'weeks'} -> timedelta. Plain integers mean days."""
    if isinstance(value, dict):
        return timedelta(days=value["amount"] * UNITS[value["unit"]])
    return timedelta(days=value)


def _machine(value):
    """The same value as JSON a consumer can compute with.

    The English rendering is for people; this is so wiki-polis can say "you
    need 43 more edits" and a translator can put it in another language.
    Durations become whole days, which is the coarsest unit every policy here
    is written in.
    """
    if isinstance(value, timedelta):
        return value.days
    if isinstance(value, bool):
        return value
    if isinstance(value, AtLeast):
        return int(value)
    if isinstance(value, (int, list, str)):
        return value
    return str(value)


def _readable(value):
    """Render a measured value or a threshold as English."""
    if isinstance(value, timedelta):
        return describe_span(value)
    if isinstance(value, AtLeast):
        return str(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(value) if value else "none"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def apply(lookup, rule, moment):
    """Evaluate one rule, returning a dict that reads plainly as JSON."""
    try:
        compare, phrase = OPERATORS[rule["operator"]]
    except KeyError:
        raise UnknownOperator(rule.get("operator")) from None

    # Where the measurement applies. A consumer can then group criteria by
    # wiki, or say "you meet the global requirements but not the German ones",
    # without parsing it back out of the English label.
    scope = rule.get("wiki", "global")

    threshold = rule["value"]
    parameters = {
        key: value for key, value in rule.items()
        if key not in ("metric", "operator", "value")
    }
    # A trailing window is written the way policies write it ("12 months").
    if "within" in parameters:
        parameters["within"] = as_duration(parameters["within"])

    # A threshold written as {amount, unit} is a duration and has to become a
    # timedelta before it can be compared with one.
    if isinstance(threshold, dict):
        threshold = as_duration(threshold)
    # A count threshold doubles as the cap, so counting can stop once it is met.
    if rule["metric"] == "edit_count":
        parameters["cap"] = threshold + (
            1 if rule["operator"] in NEEDS_ONE_MORE_THAN_THRESHOLD else 0
        )

    try:
        seen, label = measure(lookup, rule["metric"], moment, **parameters)
    except NotMeasurable as gap:
        # Neither pass nor fail: a rule we cannot evaluate must not be scored as
        # though we had, in either direction.
        return {
            "metric": rule["metric"],
            "label": rule["metric"],
            "scope": scope,
            "operator": phrase,
            "required": {"value": _machine(threshold), "display": _readable(threshold)},
            "observed": None,
            "passed": None,
            "unmeasurable": str(gap),
        }

    # A bare "yes"/"no" against a label like "globally locked" makes the reader
    # assemble the meaning themselves. Say the state outright instead.
    def shown(value):
        if isinstance(value, bool):
            return label if value else f"not {label}"
        return _readable(value)

    observed = {"value": _machine(seen), "display": shown(seen)}
    # `bounded` only means something for a count we deliberately stopped taking.
    # On a boolean or a duration it is noise that reads like a missing feature.
    if isinstance(seen, int) and not isinstance(seen, bool):
        observed["bounded"] = isinstance(seen, AtLeast)

    result = {
        # `metric` is always the identifier and `label` always the prose. One
        # key, one kind of thing — a reader should never have to guess which.
        "metric": rule["metric"],
        "label": label,
        "scope": scope,
        "operator": phrase,
        "required": {"value": _machine(threshold), "display": shown(threshold)},
        "observed": observed,
        "passed": bool(compare(seen, threshold)),
    }
    return result
