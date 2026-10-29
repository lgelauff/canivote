// The /try page: pick a policy and an account, see the /check request and its answer.
//
// Plain script, no libraries, nothing fetched from anywhere but canivote itself.
// Every piece of text from a response goes in through textContent, never as HTML.
"use strict";

const $ = (id) => document.getElementById(id);

const form = $("form");
const policySelect = $("policy");
const userInput = $("user");
const nowRadio = $("when-now");
const momentRadio = $("when-moment");
const asOfInput = $("as_of");
const submitButton = $("submit");

const policyDetails = new Map(); // id -> the /policies/<id> body, fetched once

// Build an element: el("li", {class: "rule"}, child, "text", ...).
function el(tag, attributes = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === "class") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

// ---- The request -----------------------------------------------------------

// datetime-local gives "2026-11-01T00:00" or "2026-11-01T00:00:05", with no zone.
// The page says UTC, so say so in the request too, rather than leave it implied.
function asOfParameter() {
  if (!momentRadio.checked || !asOfInput.value) return "";
  const value = asOfInput.value.length === 16 ? `${asOfInput.value}:00` : asOfInput.value;
  return `${value}Z`;
}

function requestParameters() {
  const parameters = new URLSearchParams();
  parameters.set("user", userInput.value.trim());
  parameters.set("policy", policySelect.value);
  const asOf = asOfParameter();
  if (asOf) parameters.set("as_of", asOf);
  return parameters;
}

// The query string as a person would write it: %20 for a space, a colon left as
// a colon. Quotes are still encoded, so the curl line can single-quote it.
function queryString(parameters) {
  return [...parameters].map(([key, value]) => `${key}=${encodeURIComponent(value)
    .replace(/%3A/gi, ":").replace(/'/g, "%27")}`).join("&");
}

function requestPath() {
  return `check?${queryString(requestParameters())}`;
}

function showRequest() {
  const ready = Boolean(policySelect.value && userInput.value.trim());
  $("request-missing").hidden = ready;
  const url = new URL(requestPath(), document.baseURI).href;
  $("url").textContent = url;
  $("curl").textContent = `curl -s '${url}'`;
  document.querySelector("dl.request").classList.toggle("incomplete", !ready);
}

// ---- Policies --------------------------------------------------------------

async function loadPolicies() {
  let body;
  try {
    const response = await fetch("policies", {headers: {Accept: "application/json"}});
    body = await response.json();
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  } catch (error) {
    policySelect.replaceChildren(el("option", {value: ""}, "Could not load policies"));
    showPolicyInfo(el("p", {class: "error-text"}, `Could not load the policy list: ${error.message}`));
    return;
  }
  const byWiki = new Map();
  for (const policy of body.policies) {
    if (!byWiki.has(policy.wiki)) byWiki.set(policy.wiki, []);
    byWiki.get(policy.wiki).push(policy);
  }
  const options = [el("option", {value: "", disabled: ""}, "Choose a policy…")];
  for (const [wiki, list] of byWiki) {
    const group = el("optgroup", {label: wiki});
    for (const policy of list) group.append(el("option", {value: policy.id}, policy.title || policy.id));
    options.push(group);
  }
  policySelect.replaceChildren(...options);
  policySelect.value = "";
}

function showPolicyInfo(...nodes) {
  const box = $("policy-info");
  box.replaceChildren(...nodes);
  box.hidden = nodes.length === 0;
}

async function describePolicy() {
  const id = policySelect.value;
  if (!id) return showPolicyInfo();
  if (!policyDetails.has(id)) {
    try {
      const response = await fetch(`policies/${encodeURIComponent(id)}`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      policyDetails.set(id, await response.json());
    } catch (error) {
      return showPolicyInfo(el("p", {class: "error-text"}, `Could not load this policy: ${error.message}`));
    }
  }
  if (policySelect.value !== id) return; // the reader moved on while we waited
  const policy = policyDetails.get(id);
  const sources = el("ul", {class: "sources"});
  for (const source of policy.sources || []) {
    sources.append(el("li", {}, el("a", {href: source, rel: "noopener noreferrer"}, decodeURI(source))));
  }
  showPolicyInfo(
    policy.scope ? el("p", {}, el("strong", {}, "Covers: "), policy.scope) : null,
    el("p", {}, el("strong", {}, "The rule: "), policy.english || ""),
    el("details", {},
      el("summary", {}, "Original wording, sources and rules"),
      el("blockquote", {lang: policy.language || ""}, policy.original_text || ""),
      sources,
      el("p", {}, `Last read by a person: ${policy.verified || "unknown"}. `,
        el("a", {href: `policies/${encodeURIComponent(id)}`}, "This policy as JSON"))),
  );
}

// ---- The response ----------------------------------------------------------

const VERDICTS = {
  eligible: ["Eligible", "eligible"],
  not_eligible: ["Not eligible", "not-eligible"],
  indeterminate: ["Can't tell", "indeterminate"],
};

function mark(passed) {
  if (passed === true) return el("span", {class: "mark pass"}, "✓", el("span", {class: "sr"}, " met"));
  if (passed === false) return el("span", {class: "mark fail"}, "✗", el("span", {class: "sr"}, " not met"));
  return el("span", {class: "mark unknown"}, "?", el("span", {class: "sr"}, " could not be checked"));
}

// One entry of `criteria`: a plain rule, or a group of them (any_of / all_of).
function renderEntry(entry) {
  if (entry.group) {
    const members = el("ul", {class: "criteria"}, ...entry.criteria.map(renderEntry));
    return el("li", {class: "group"},
      el("div", {class: "row"}, mark(entry.passed),
        el("span", {class: "label"}, capitalise(entry.label)),
        el("code", {class: "metric"}, entry.group)),
      members);
  }
  const observed = entry.observed ? entry.observed.display : "not measured";
  // A yes/no rule is already named after what must hold ("not globally locked"),
  // so repeating the requirement after it would only say the same thing twice.
  const wanted = entry.label === entry.required.display
    ? capitalise(entry.label)
    : `${capitalise(entry.label)}: ${entry.operator} ${entry.required.display}`;
  return el("li", {class: "rule"},
    el("div", {class: "row"}, mark(entry.passed),
      el("span", {class: "label"}, wanted),
      el("span", {class: `badge ${entry.source}`}, entry.source)),
    el("div", {class: "detail"},
      `Found: ${observed}`,
      entry.observed && entry.observed.bounded ? " (counting stopped once the rule was met)" : "",
      entry.measured ? ` · measured ${entry.measured}` : ""),
    entry.unmeasurable ? el("div", {class: "detail warn"}, `Could not be measured: ${entry.unmeasurable}`) : null,
    entry.broken ? el("div", {class: "detail warn"}, `Internal error in this rule: ${entry.broken}`) : null);
}

function capitalise(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

function renderVerdict(body) {
  const [title, kind] = VERDICTS[body.verdict] || [body.verdict, "indeterminate"];
  const nodes = [
    el("div", {class: `verdict ${kind}`},
      el("p", {class: "verdict-title"}, title),
      el("p", {}, body.reason || "")),
    el("p", {class: "hint"}, `${body.user} against ${body.policy}, measured as of ${body.as_of}`,
      body.checked_at !== body.as_of ? ` (checked at ${body.checked_at})` : "", "."),
  ];
  if (body.criteria && body.criteria.length) {
    nodes.push(el("h3", {}, "Rules"),
      el("p", {class: "hint"}, "“platform” rules apply to every vote on Wikimedia; “policy” rules come from this community's own wording."),
      el("ul", {class: "criteria top"}, ...body.criteria.map(renderEntry)));
  }
  if (body.not_checked && body.not_checked.length) {
    nodes.push(el("h3", {}, "Not checked: needs a person"),
      el("ul", {class: "plain"}, ...body.not_checked.map((clause) => el("li", {}, clause))));
  }
  if (body.queries) {
    nodes.push(el("h3", {}, `Wikimedia API calls canivote made (${body.queries.length})`),
      el("p", {class: "hint"}, "Each link reproduces one step of the answer, straight from Wikimedia."),
      el("ol", {class: "queries"}, ...body.queries.map((url) =>
        el("li", {}, el("a", {href: url, rel: "noopener noreferrer"}, el("code", {}, url))))));
  }
  return nodes;
}

function renderError(body, response) {
  const nodes = [el("div", {class: "verdict error"},
    el("p", {class: "verdict-title"}, body && body.error ? body.error : `HTTP ${response.status}`),
    body && body.code ? el("p", {}, el("code", {}, body.code)) : null)];
  if (body && body.detail) nodes.push(el("p", {class: "hint"}, body.detail));
  if (response.status === 429) {
    const wait = response.headers.get("Retry-After");
    nodes.push(el("p", {class: "hint"},
      `The tool shares one rate limit across everyone using it${wait ? `; try again in ${wait} seconds` : ""}.`));
  }
  return nodes;
}

async function check(event) {
  event.preventDefault();
  if (!policySelect.value || !userInput.value.trim()) {
    (policySelect.value ? userInput : policySelect).focus();
    return;
  }
  const parameters = requestParameters();
  history.replaceState(null, "", `?${queryString(parameters)}`); // a shareable link to this question
  $("result").hidden = false;
  $("status").textContent = "Checking…";
  $("answer").replaceChildren();
  $("raw-body").textContent = "";
  submitButton.disabled = true;
  const started = performance.now();
  let response;
  let text;
  try {
    response = await fetch(requestPath(), {headers: {Accept: "application/json"}});
    text = await response.text();
  } catch (error) {
    $("status").textContent = `The request did not complete: ${error.message}`;
    submitButton.disabled = false;
    return;
  }
  submitButton.disabled = false;
  const elapsed = performance.now() - started;
  const took = elapsed < 1000 ? `${Math.round(elapsed)} ms` : `${(elapsed / 1000).toFixed(1)} s`;
  let body = null;
  try { body = JSON.parse(text); } catch { /* shown raw below */ }
  const version = response.headers.get("Canivote-Version");
  $("status").textContent = `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ""}`
    + ` · ${took}${version ? ` · canivote ${version}` : ""}`;
  $("answer").replaceChildren(...(response.ok && body && body.verdict
    ? renderVerdict(body) : renderError(body, response)));
  $("raw-body").textContent = body ? JSON.stringify(body, null, 2) : text;
}

// ---- Wiring ----------------------------------------------------------------

function syncMoment() {
  asOfInput.disabled = !momentRadio.checked;
  if (momentRadio.checked && !asOfInput.value) {
    asOfInput.value = new Date().toISOString().slice(0, 19); // now, in UTC
  }
  showRequest();
}

// Fill the form from ?policy=&user=&as_of=, so a shared link opens the same question.
// It does not run the check: a link should never spend Wikimedia's API on its own.
function prefill() {
  const given = new URLSearchParams(location.search);
  if (given.has("policy")) policySelect.value = given.get("policy");
  if (given.has("user")) userInput.value = given.get("user");
  const asOf = (given.get("as_of") || "").match(/^(\d{4}-\d\d-\d\d)(?:T(\d\d:\d\d(?::\d\d)?))?(?:Z|\+00:00)?$/);
  if (asOf) {
    momentRadio.checked = true;
    asOfInput.value = `${asOf[1]}T${asOf[2] || "00:00"}`;
  }
}

async function copy(event) {
  const source = $(event.target.dataset.copy);
  try {
    await navigator.clipboard.writeText(source.textContent);
    event.target.textContent = "Copied";
  } catch {
    event.target.textContent = "Copy failed";
  }
  setTimeout(() => { event.target.textContent = "Copy"; }, 1500);
}

nowRadio.addEventListener("change", syncMoment);
momentRadio.addEventListener("change", syncMoment);
policySelect.addEventListener("change", () => { showRequest(); describePolicy(); });
userInput.addEventListener("input", showRequest);
asOfInput.addEventListener("input", showRequest);
form.addEventListener("submit", check);
for (const button of document.querySelectorAll("button.copy")) button.addEventListener("click", copy);

loadPolicies().then(() => {
  prefill();
  syncMoment();
  describePolicy();
});
