"""The /try page: served, locked down, and a client of the public API only."""

import re
from html.parser import HTMLParser
from pathlib import Path

import app as app_module
from openapi_contract import validate

STATIC = Path(app_module.__file__).parent / "static"


class _Tags(HTMLParser):
    """Collect every start tag with its attributes, and the text of <script>."""

    def __init__(self):
        super().__init__()
        self.tags = []
        self.inline_script = ""
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        self._in_script = tag == "script"

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.inline_script += data


def _parsed(client):
    parser = _Tags()
    parser.feed(client.get("/try").get_data(as_text=True))
    return parser


def test_the_page_is_served_as_html(client):
    response = client.get("/try")
    assert response.status_code == 200
    assert response.mimetype == "text/html"


def test_the_page_is_locked_down(client):
    headers = client.get("/try").headers
    policy = headers["Content-Security-Policy"]
    for directive in ("default-src 'none'", "script-src 'self'", "style-src 'self'",
                      "connect-src 'self'", "frame-ancestors 'none'"):
        assert directive in policy
    assert "unsafe-inline" not in policy
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_the_page_needs_nothing_the_policy_would_block(client):
    """No inline script or style, which the Content-Security-Policy would refuse."""
    page = _parsed(client)
    assert page.inline_script.strip() == ""
    assert not any("style" in attrs for _, attrs in page.tags)
    assert not any(tag == "style" for tag, _ in page.tags)
    assert not any(name.startswith("on") for _, attrs in page.tags for name in attrs)


def test_every_resource_comes_from_the_tool_itself(client):
    """Toolforge's privacy rules: no third-party scripts, styles, fonts or images."""
    page = _parsed(client)
    loaded = [attrs.get("src") or attrs.get("href")
              for tag, attrs in page.tags
              if tag in ("script", "img") or (tag == "link" and attrs.get("rel") == "stylesheet")]
    assert loaded, "the page should load its script and stylesheet"
    for url in loaded:
        assert not re.match(r"^(https?:)?//", url), url
        assert client.get(f"/{url}").status_code == 200, url
    # The same for anything the script or stylesheet could load on its own.
    for name in ("try.js", "try.css"):
        text = (STATIC / name).read_text()
        assert not re.search(r"""(fetch\(|url\(|import\s)\s*["'`]?(https?:)?//""", text), name


def test_the_script_only_calls_public_endpoints():
    script = (STATIC / "try.js").read_text()
    targets = re.findall(r"fetch\(\s*(requestPath\(\)|[^,)]+)", script)
    assert len(targets) == script.count("fetch("), "every fetch should be accounted for"
    called = set()
    for target in targets:
        if target.strip() == "requestPath()":
            # The /check URL is built in one place, so the page shows exactly what it sends.
            built = re.search(r"function requestPath\(\) \{\s*return `([a-z_]+)\?", script)
            assert built, "requestPath() should build a /check URL"
            called.add(built.group(1))
        else:
            literal = re.match(r"""[`"']([a-z_]+)""", target.strip())
            assert literal, f"fetch of something other than a canivote endpoint: {target}"
            called.add(literal.group(1))
    assert called == {"policies", "check"}
    for endpoint in called:
        assert f"/{endpoint}" in app_module.OPENAPI["paths"]


def test_the_form_still_works_without_javascript(client):
    """With scripts off the form is a plain GET to /check using its own parameter names."""
    page = _parsed(client)
    form = next(attrs for tag, attrs in page.tags if tag == "form")
    assert (form["action"], form["method"]) == ("check", "get")
    names = {attrs.get("name") for tag, attrs in page.tags if tag in ("input", "select")}
    assert {"user", "policy", "as_of"} <= names


def test_a_datetime_local_moment_is_accepted_by_check(client, monkeypatch):
    """The no-JavaScript form sends as_of with no seconds and no zone; /check must take it."""
    from conftest import forbid_network
    forbid_network(monkeypatch)
    response = client.get("/check?user=Example&policy=meta-global&as_of=nonsense")
    assert response.status_code == 400
    assert app_module._parse_moment("2026-11-01T00:00").isoformat() == "2026-11-01T00:00:00"


def test_the_index_points_people_to_the_page(client):
    body = client.get("/").get_json()
    assert "/try" in body["endpoints"]
    validate("Index", body)
