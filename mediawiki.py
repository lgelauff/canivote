"""Read-only lookups against the MediaWiki Action API.

Every request is recorded as a full URL and returned with the verdict, so anyone
can paste it into a browser and see exactly what this tool saw.

Two design notes:

* Counting stops at a cap. A policy only asks whether a threshold is met, so one
  request settles it and we never build a fuller picture of somebody than the
  question needs.
* Results are cached per lookup, so a policy with several rules about one wiki
  costs one request rather than one per rule.
* One session for the process, and `maxlag` on every call. We are a guest on
  shared infrastructure: reusing the connection and backing off when the
  replicas fall behind are the least we can do.
"""

import re
from datetime import datetime, timezone
from urllib.parse import urlencode

import requests

# The contact URL has to reach a human. A repository has an issue tracker; the
# tool's own front page would not, since it does not have one.
USER_AGENT = (
    "canivote/1.0 (https://github.com/lgelauff/canivote; "
    "Wikimedia eligibility checker)"
)
TIMEOUT_SECONDS = 10
MAX_ROWS_PER_REQUEST = 500  # Action API ceiling for clients without apihighlimits
MAX_LAG_SECONDS = 5         # standard Wikimedia etiquette: back off when replicas lag
MAX_USERNAME_LENGTH = 85    # MediaWiki's own limit

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT

# Characters MediaWiki will not accept in a username. Rejecting them here means
# junk costs us one string comparison instead of costing Wikimedia a request.
FORBIDDEN_IN_USERNAME = re.compile(r"[#<>\[\]|{}/@:]|^\s|\s$")


class UserNotFound(Exception):
    """No such account on the wiki we asked."""


class UsernameInvalid(Exception):
    """The name cannot be a username."""


class UpstreamUnavailable(Exception):
    """The Wikimedia API could not be reached, or answered with an error."""


def normalise_username(raw):
    """Put a name into the form MediaWiki itself uses.

    `foo_bar`, `foo bar` and `Foo bar` are one account. Normalising before we
    ask means we do not send three requests for one person, and do not keep
    three cache entries for one answer.
    """
    name = " ".join(raw.replace("_", " ").split())
    if not name:
        raise UsernameInvalid(raw)
    if len(name) > MAX_USERNAME_LENGTH or FORBIDDEN_IN_USERNAME.search(name):
        raise UsernameInvalid(raw)
    return name[0].upper() + name[1:]


def _timestamp(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Lookup:
    """Answers questions about one user, remembering every query it made."""

    def __init__(self, username):
        self.username = username
        self.queries = []
        self._accounts = {}
        self._global = None
        self._first_edits = {}

    def account(self, wiki):
        """Per-wiki account facts: groups and block state."""
        if wiki not in self._accounts:
            data = self._get(wiki, {
                "action": "query", "list": "users", "ususers": self.username,
                "usprop": "groups|blockinfo|registration",
            })
            account = data["query"]["users"][0]
            if account.get("invalid"):
                raise UsernameInvalid(self.username)
            if account.get("missing"):
                raise UserNotFound(self.username)
            self._accounts[wiki] = account
        return self._accounts[wiki]

    def global_account(self):
        """CentralAuth facts, including whether the account is globally locked."""
        if self._global is None:
            data = self._get("meta.wikimedia.org", {
                "action": "query", "meta": "globaluserinfo",
                "guiuser": self.username,
            })
            info = data["query"]["globaluserinfo"]
            if "missing" in info:
                raise UserNotFound(self.username)
            self._global = info
        return self._global

    def globally_blocked(self):
        """Whether stewards have placed a global block on this account."""
        data = self._get("meta.wikimedia.org", {
            "action": "query", "list": "globalblocks",
            "bgtargets": self.username, "bglimit": "1",
        })
        return bool(data.get("query", {}).get("globalblocks"))

    def first_edit(self, wiki):
        """When the account first edited this wiki, or None if it never has."""
        key = wiki
        if key not in self._first_edits:
            parameters = {
                "action": "query", "list": "usercontribs",
                "ucuser": self.username, "ucdir": "newer",
                "uclimit": "1", "ucprop": "timestamp",
            }
            edits = self._get(wiki, parameters)["query"]["usercontribs"]
            self._first_edits[key] = (
                datetime.fromisoformat(edits[0]["timestamp"].replace("Z", "+00:00"))
                if edits else None
            )
        return self._first_edits[key]

    def count_contributions(self, wiki, *, namespace=None, since=None, cap):
        """Count edits up to `cap`, returning (count, counted_them_all).

        Asking for exactly `cap` rows answers a threshold question in one
        request: getting `cap` rows back means the threshold is met, and there
        is no need to know by how much.
        """
        wanted = min(cap, MAX_ROWS_PER_REQUEST)
        parameters = {
            "action": "query", "list": "usercontribs",
            "ucuser": self.username, "uclimit": str(wanted),
            "ucprop": "ids",
        }
        if namespace is not None:
            parameters["ucnamespace"] = namespace
        if since:
            parameters["ucend"] = _timestamp(since)
        edits = self._get(wiki, parameters)["query"]["usercontribs"]
        return len(edits), len(edits) < wanted

    def _get(self, wiki, parameters):
        query = {
            **parameters,
            "format": "json", "formatversion": "2",
            "maxlag": str(MAX_LAG_SECONDS),
        }
        url = f"https://{wiki}/w/api.php?{urlencode(query)}"
        self.queries.append(url)
        try:
            response = _session.get(url, timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as unreachable:
            raise UpstreamUnavailable(
                f"{wiki} did not answer: {unreachable}"
            ) from unreachable
        # A MediaWiki-level error arrives as HTTP 200 with an `error` key —
        # maxlag rejections included. Indexing past it would raise KeyError and
        # surface as a 500 that blames us for their outage.
        if "error" in data:
            raise UpstreamUnavailable(
                f"{wiki} returned {data['error'].get('code', 'an error')}"
            )
        return data
