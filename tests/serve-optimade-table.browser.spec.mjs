import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { test } from "@playwright/test";

const assets = new URL("../src/httk/serve/web/assets/", import.meta.url);
const modulePath = fileURLToPath(new URL("serve-optimade-table.mjs", assets));
const protocolPath = fileURLToPath(new URL("serve-optimade-table-protocol.mjs", assets));
const cssPath = fileURLToPath(new URL("serve-optimade-table.css", assets));

let server;
let origin;
let requests;
let redirectServer;
let redirectOrigin;
let redirectRequests;

test.beforeAll(async () => {
  const [module, protocol, css] = await Promise.all([readFile(modulePath), readFile(protocolPath), readFile(cssPath)]);
  requests = [];
  redirectRequests = [];
  redirectServer = createServer((request, response) => {
    redirectRequests.push(request.url);
    send(response, 200, JSON.stringify(info(["structures"])), "application/vnd.api+json");
  });
  await new Promise((resolve) => redirectServer.listen(0, "127.0.0.1", resolve));
  redirectOrigin = `http://127.0.0.1:${redirectServer.address().port}`;
  server = createServer((request, response) => {
    const url = new URL(request.url, `http://${request.headers.host}`);
    requests.push(url.href);
    if (url.pathname === "/assets/serve-optimade-table.mjs") return send(response, 200, module, "text/javascript");
    if (url.pathname === "/assets/serve-optimade-table-protocol.mjs") return send(response, 200, protocol, "text/javascript");
    if (url.pathname === "/assets/serve-optimade-table.css") return send(response, 200, css, "text/css");
    if (url.pathname === "/") return send(response, 200, page(), "text/html");
    if (url.pathname === "/redirect/v1/info") {
      response.writeHead(302, { location: `${redirectOrigin}/must-not-receive-filter-or-cursor` });
      return response.end();
    }
    if (url.pathname === "/error/v1/info") return send(response, 503, JSON.stringify({ errors: [{ title: "Unavailable", detail: "opaque-error-token must stay private" }] }), "application/json");
    if (url.pathname.endsWith("/info")) return send(response, 200, JSON.stringify(info(url.pathname.includes("empty") ? ["empty"] : url.pathname.includes("race") ? ["race"] : ["structures"])), "application/vnd.api+json");
    if (url.pathname.endsWith("/info/structures")) return send(response, 200, JSON.stringify(entry("structures", ["name", "nsites"], ["nsites"])), "application/vnd.api+json");
    if (url.pathname.endsWith("/info/empty")) return send(response, 200, JSON.stringify(entry("empty", ["name"])), "application/vnd.api+json");
    if (url.pathname.endsWith("/info/race")) return send(response, 200, JSON.stringify(entry("race", ["name"])), "application/vnd.api+json");
    if (url.pathname === "/v1/structures") return send(response, 200, JSON.stringify(result([resource("one", "<img src=x onerror=alert(1)>", 2)], "/cursor-next?opaque=not-for-dom", "structures", { dataReturned: 2, dataAvailable: 5 })), "application/vnd.api+json");
    if (url.pathname === "/cursor-next") return send(response, 200, JSON.stringify(result([resource("two", "Second", 3)], null, "structures", { dataReturned: 2, dataAvailable: 5 })), "application/vnd.api+json");
    if (url.pathname === "/empty/v1/empty") return send(response, 200, JSON.stringify(result([], null, "empty")), "application/vnd.api+json");
    if (url.pathname === "/race/v1/race") return send(response, 200, JSON.stringify(result([resource("fresh", "Fresh", 1, "race")], null, "race")), "application/vnd.api+json");
    if (url.pathname === "/psize/v1/structures") {
      // Echo the requested page size back as that many rows, so the dropdown's effect is observable.
      // This stub deliberately honors ANY page_limit — a real httk-serve service caps it at
      // OptimadeConfig.page_limit_max and returns 403 above that — so this spec proves the widget
      // plumbing (URL param -> effective size -> page_limit) only, not the service ceiling.
      const limit = Number(url.searchParams.get("page_limit")) || 1;
      const rows = Array.from({ length: limit }, (_, i) => resource(`p${i}`, `Row ${i}`, i + 1));
      return send(response, 200, JSON.stringify(result(rows, null, "structures")), "application/vnd.api+json");
    }
    return send(response, 404, "not found", "text/plain");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});

test.afterAll(async () => Promise.all([server, redirectServer].map((item) => new Promise((resolve, reject) => item.close((error) => error ? reject(error) : resolve())))));

test("the browser controller loads, pages, and keeps protocol state private", async ({ page }) => {
  requests.length = 0;
  await page.goto(`${origin}/?filter=nsites%20%3E%3D%209`);
  await page.locator("#main [data-httk-serve-optimade-status]").getByText("1 results loaded.").waitFor();
  await page.locator("#mirror [data-httk-serve-optimade-status]").getByText("1 results loaded.").waitFor();
  await page.locator("#empty [data-httk-serve-optimade-status]").getByText("No OPTIMADE results found.").waitFor();
  await page.locator("#error [data-httk-serve-optimade-status]").getByText("The OPTIMADE service returned HTTP 503.").waitFor();
  await page.locator("#redirect [data-httk-serve-optimade-status]").getByText(/Network error/).waitFor();

  assert.equal(await page.locator("#main img").count(), 0);
  assert.equal(await page.locator("#main tbody td").nth(1).textContent(), "<img src=x onerror=alert(1)>");
  await assertLink(page, "#main tbody a", `${origin}/details?view=table&id=one`);
  assert.equal(await page.locator("#empty button[data-httk-serve-optimade-next]").isDisabled(), true);
  assert.equal(await page.locator("#error button.httk-serve-optimade-table__retry").count(), 1);
  assert.equal(await page.locator("#main").getAttribute("aria-busy"), "false");
  assert.equal(await page.locator("#invalid").getAttribute("aria-busy"), "false");
  assert.equal(await page.locator("#invalid tbody").textContent(), "OPTIMADE protocol error: OPTIMADE URL is invalid");
  assert.equal(await page.locator("#invalid button[data-httk-serve-optimade-next]").isDisabled(), true);
  assert.equal(await page.locator("#invalid button[data-httk-serve-optimade-next]").getAttribute("aria-disabled"), "true");
  assert.equal(await page.locator("#main button[data-httk-serve-optimade-next]").isDisabled(), false);
  assert.equal(await page.locator("#main button[data-httk-serve-optimade-next]").getAttribute("aria-disabled"), "false");
  assert.equal(requests.filter((url) => new URL(url).pathname === "/v1/info").length, 1, "shared discovery is reused");
  assert.equal(requests.some((url) => new URL(url).searchParams.get("filter") === "nsites >= 9"), true);
  assert.equal(redirectRequests.length, 0, "redirect destination must not receive a request");

  await page.locator("#main [data-httk-serve-optimade-next]").click();
  await page.locator("#main tbody").getByText("Second").waitFor();
  await page.locator("#main [data-httk-serve-optimade-previous]").click();
  await page.locator("#main tbody").getByText("<img src=x onerror=alert(1)>").waitFor();

  await page.waitForFunction(() => typeof window.releaseOptimadeRace === "function");
  await page.evaluate(async () => {
    const module = await import("/assets/serve-optimade-table.mjs");
    await module.controllerFor(document.querySelector("#race")).reload();
  });
  await page.locator("#race tbody").getByText("Fresh").waitFor();
  await page.evaluate(() => window.releaseOptimadeRace());
  await page.waitForTimeout(30);
  assert.equal(await page.locator("#race tbody").textContent(), "Fresh");

  const privateState = await page.evaluate(() => ({
    html: document.documentElement.outerHTML,
    events: JSON.stringify(window.optimadeEvents),
    href: location.href,
    local: localStorage.length,
    session: sessionStorage.length,
  }));
  assert.equal(privateState.html.includes("opaque=not-for-dom"), false);
  assert.equal(privateState.html.includes("opaque-error-token"), false);
  assert.equal(privateState.events.includes("opaque=not-for-dom"), false);
  assert.equal(privateState.events.includes("opaque-error-token"), false);
  assert.equal(privateState.href.includes("opaque=not-for-dom"), false);
  assert.equal(privateState.local, 0);
  assert.equal(privateState.session, 0);
  const controllerExposesUrlState = await page.evaluate(async () => {
    const module = await import("/assets/serve-optimade-table.mjs");
    const controller = module.controllerFor(document.querySelector("#main"));
    return Object.values(controller).some((value) => Array.isArray(value) || String(value).includes("opaque=not-for-dom"));
  });
  assert.equal(controllerExposesUrlState, false);
});

test("the results summary reports spec counts and renders a filter pill", async ({ page }) => {
  await page.goto(`${origin}/?filter=nsites%20%3E%3D%209`);
  const region = page.locator("#summary [data-httk-serve-optimade-summary]");
  await region.getByText("Showing 2 of 5 structures.").waitFor();
  const pill = region.locator(".httk-serve-optimade-table__pill").first();
  await pill.waitFor();
  assert.match(await pill.textContent(), /Sites\s+≥ 9/);
});

test("sortable column headers become sort links that toggle direction and set aria-sort", async ({ page }) => {
  await page.goto(`${origin}/`);
  const sortLink = page.locator("#sortable thead th").nth(2).locator("a.httk-serve-optimade-table__sort-link");
  await sortLink.waitFor();
  // The nsites column (a sortable, advertised field) links to sort ascending with an id tiebreaker.
  assert.equal(await sortLink.getAttribute("href"), "?sort=nsites%2Cid");
  // The name column is not advertised sortable, so its header is never wrapped in a link.
  assert.equal(await page.locator("#sortable thead th").nth(1).locator("a.httk-serve-optimade-table__sort-link").count(), 0);

  await page.goto(`${origin}/?sort=nsites%2Cid`);
  const active = page.locator("#sortable thead th").nth(2);
  const activeLink = active.locator("a.httk-serve-optimade-table__sort-link");
  await activeLink.waitFor();
  // Already sorted ascending by nsites: the link now flips to descending and aria-sort is set.
  assert.equal(await activeLink.getAttribute("href"), "?sort=-nsites%2Cid");
  assert.equal(await active.getAttribute("aria-sort"), "ascending");
});

test("the advanced-filter disclosure prefills the filter and carries the raw sort param", async ({ page }) => {
  await page.goto(`${origin}/?filter=nsites%20%3E%3D%209&sort=best`);
  const details = page.locator("#advanced [data-httk-serve-optimade-advanced]");
  await details.waitFor();
  const input = details.locator("[data-httk-serve-optimade-advanced-filter]");
  // The input is prefilled with the effective (URL-selected) filter value.
  await input.waitFor();
  assert.equal(await input.inputValue(), "nsites >= 9");
  // A bare sidebar-style filter (no advanced marker) leaves the disclosure closed.
  assert.equal(await details.evaluate((el) => el.open), false);
  // The raw URL sort alias is preserved in a hidden field so the GET form round-trips it verbatim.
  assert.equal(await details.locator('form input[type="hidden"][name="sort"]').inputValue(), "best");
  const help = details.locator("a.httk-serve-optimade-table__advanced-help");
  assert.equal(await help.getAttribute("href"), "/fields");
  assert.equal(await help.getAttribute("target"), "_blank");
  assert.equal(await help.getAttribute("rel"), "noopener noreferrer");

  // A URL carrying the advanced submit marker opens the disclosure on load.
  await page.goto(`${origin}/?filter=nsites%20%3E%3D%205&filter_advanced=1`);
  await page.locator("#advanced [data-httk-serve-optimade-advanced-filter]").waitFor();
  assert.equal(await page.locator("#advanced [data-httk-serve-optimade-advanced]").evaluate((el) => el.open), true);

  // Submitting the advanced form itself lands the marker in the URL and reopens it.
  await page.goto(`${origin}/`);
  const closed = page.locator("#advanced [data-httk-serve-optimade-advanced]");
  await page.locator("#advanced [data-httk-serve-optimade-status]").getByText("1 results loaded.").waitFor();
  assert.equal(await closed.evaluate((el) => el.open), false);
  await page.locator("#advanced summary").click();
  await closed.locator("[data-httk-serve-optimade-advanced-filter]").fill("nsites >= 7");
  await closed.locator('button[type="submit"]').click();
  await page.waitForURL(/filter_advanced=1/);
  const url = new URL(page.url());
  assert.equal(url.searchParams.get("filter_advanced"), "1");
  assert.equal(url.searchParams.get("filter"), "nsites >= 7");
  await page.locator("#advanced [data-httk-serve-optimade-advanced-filter]").waitFor();
  assert.equal(await page.locator("#advanced [data-httk-serve-optimade-advanced]").evaluate((el) => el.open), true);
});

test("the page-size dropdown reflects the URL value and drives how many rows are requested", async ({ page }) => {
  // A URL page size that is one of the options selects it and is requested from the service.
  await page.goto(`${origin}/?page_size=5`);
  const select = page.locator("#pagesize [data-httk-serve-optimade-page-size]");
  await select.waitFor();
  assert.deepEqual(await select.locator("option").allTextContents(), ["2", "5", "10"]);
  assert.equal(await select.inputValue(), "5");
  await page.locator("#pagesize [data-httk-serve-optimade-status]").getByText("5 results loaded.").waitFor();
  assert.equal(await page.locator("#pagesize tbody tr").count(), 5);

  // With no URL parameter the authored default (2) is selected and requested.
  await page.goto(`${origin}/`);
  await page.locator("#pagesize [data-httk-serve-optimade-status]").getByText("2 results loaded.").waitFor();
  assert.equal(await page.locator("#pagesize [data-httk-serve-optimade-page-size]").inputValue(), "2");
});

function page() {
  const main = shell("main", { base_url: "/v1", entry_type: "structures", columns: columns(), page_size: 2, filter: "nsites >= 1", filter_query: "filter", allowed_origins: [], detail_route: "/details?view=table", detail_column: "name", detail_query: "id" });
  const mirror = shell("mirror", { base_url: "/v1", entry_type: "structures", columns: columns(), page_size: 2, filter: null, filter_query: null, allowed_origins: [] });
  const empty = shell("empty", { base_url: "/empty/v1", entry_type: "empty", columns: [{ key: "name", label: "Name", align: "start" }], page_size: 2, filter: null, filter_query: null, allowed_origins: [] });
  const error = shell("error", { base_url: "/error/v1", entry_type: "structures", columns: [{ key: "id", label: "ID", align: "start" }], page_size: 2, filter: null, filter_query: null, allowed_origins: [] });
  const redirect = shell("redirect", { base_url: "/redirect/v1", entry_type: "structures", columns: [{ key: "id", label: "ID", align: "start" }], page_size: 2, filter: "secret filter", filter_query: null, allowed_origins: [] });
  const race = shell("race", { base_url: "/race/v1", entry_type: "race", columns: [{ key: "name", label: "Name", align: "start" }], page_size: 2, filter: null, filter_query: null, allowed_origins: [] });
  const summary = shell("summary", { base_url: "/v1", entry_type: "structures", columns: columns(), page_size: 2, filter: "nsites >= 1", filter_query: "filter", allowed_origins: [], summary: { noun: "structures", fields: { nsites: { label: "Sites", format: null, values: null } } } });
  const sortable = shell("sortable", { base_url: "/v1", entry_type: "structures", columns: columns(), page_size: 2, filter: null, filter_query: "filter", sort: null, sort_query: "sort", allowed_origins: [] });
  const advanced = shell("advanced", { base_url: "/v1", entry_type: "structures", columns: columns(), page_size: 2, filter: null, filter_query: "filter", sort: null, sort_query: "sort", allowed_origins: [], advanced_filter: { label: "Advanced search (OPTIMADE filter)", help_url: "/fields" } });
  const pagesize = shell("pagesize", { base_url: "/psize/v1", entry_type: "structures", columns: columns(), page_size: 2, page_size_options: [2, 5, 10], page_size_query: "page_size", filter: null, filter_query: null, allowed_origins: [] });
  const invalid = shell("invalid", { base_url: "/v1", entry_type: "structures", columns: [{ key: "id", label: "ID", align: "start" }], page_size: 2, filter: null, filter_query: null, allowed_origins: [] });
  // Deliberately break a required field after the shell is produced; this covers
  // defensive install failures rather than the trusted Python declaration.
  const invalidConfig = invalid.replace('"base_url":"/v1"', '"base_url":"http://"');
  return `<!doctype html><meta charset="utf-8"><title>OPTIMADE smoke</title><script>window.optimadeEvents=[];document.addEventListener("httk-serve:optimade-table-updated",e=>window.optimadeEvents.push(e.detail));{const original=window.fetch.bind(window);let first=true;window.fetch=(input,init)=>{if(first&&String(input).includes("/race/v1/race")){first=false;return new Promise(resolve=>{window.releaseOptimadeRace=()=>resolve(new Response(JSON.stringify(${JSON.stringify(result([resource("stale", "Stale", 1, "race")], null, "race"))}),{headers:{"content-type":"application/vnd.api+json"}}));});}return original(input,init);};}</script><link rel="stylesheet" href="/assets/serve-optimade-table.css">${main}${mirror}${empty}${error}${redirect}${race}${summary}${sortable}${advanced}${pagesize}${invalidConfig}<script type="module" src="/assets/serve-optimade-table.mjs"></script>`;
}

function shell(id, configuration) {
  const config = JSON.stringify(configuration).replace(/</g, "\\u003c");
  const heads = configuration.columns.map((column) => `<th>${column.label}</th>`).join("");
  const summary = configuration.summary ? '<div class="httk-serve-optimade-table__summary" data-httk-serve-optimade-summary hidden></div>' : "";
  const advanced = configuration.advanced_filter
    ? `<details class="httk-serve-optimade-table__advanced" data-httk-serve-optimade-advanced><summary>${configuration.advanced_filter.label}</summary><form method="get" class="httk-serve-optimade-table__advanced-form"><input type="hidden" name="${configuration.filter_query}_advanced" value="1"><label class="httk-serve-optimade-table__advanced-label">OPTIMADE filter <input type="text" name="${configuration.filter_query}" class="httk-serve-optimade-table__advanced-input" data-httk-serve-optimade-advanced-filter autocomplete="off" spellcheck="false"></label><button type="submit">Search</button>${configuration.advanced_filter.help_url ? `<a class="httk-serve-optimade-table__advanced-help" href="${configuration.advanced_filter.help_url}" target="_blank" rel="noopener noreferrer">Available fields</a>` : ""}</form></details>`
    : "";
  const pageSize = configuration.page_size_query
    ? `<label class="httk-serve-optimade-table__page-size">Results per page <select data-httk-serve-optimade-page-size>${(configuration.page_size_options ?? []).map((n) => `<option value="${n}">${n}</option>`).join("")}</select></label>`
    : "";
  return `<section id="${id}" class="httk-serve-optimade-table" data-httk-serve-optimade-table="1" data-config-id="${id}-config" aria-busy="true">${summary}${advanced}<table><thead><tr>${heads}</tr></thead><tbody></tbody></table><nav class="httk-serve-optimade-table__pager"><button type="button" data-httk-serve-optimade-previous disabled aria-disabled="true">Previous</button><span data-httk-serve-optimade-status role="status" aria-live="polite">Loading OPTIMADE results.</span><button type="button" data-httk-serve-optimade-next disabled aria-disabled="true">Next</button>${pageSize}</nav><script id="${id}-config" type="application/json">${config}</script></section>`;
}

function columns() { return [{ key: "id", label: "ID", align: "start" }, { key: "name", label: "Name", align: "start" }, { key: "nsites", label: "N", align: "end" }]; }
function info(entries) { return { data: { id: "/", type: "info", attributes: { api_version: "1.3.0", formats: ["json"], entry_types_by_format: { json: entries }, available_endpoints: ["info", ...entries] } } }; }
function entry(id, fields, sortable = []) { return { data: { id, type: "info", properties: Object.fromEntries(fields.map((field) => [field, sortable.includes(field) ? { sortable: true } : {}])), formats: ["json"], output_fields_by_format: { json: fields } } }; }
function resource(id, name, nsites, type = "structures") { return { id, type, attributes: type === "structures" ? { name, nsites } : { name } }; }
// data_returned is the filtered total independent of pagination (so it may
// exceed a single page); data_available is the unfiltered endpoint total.
function result(data, next, entryType = "structures", { dataReturned = data.length, dataAvailable = data.length } = {}) { return { data, meta: { api_version: "1.3.0", data_returned: dataReturned, data_available: dataAvailable, more_data_available: next !== null }, links: { next } }; }
function send(response, status, body, type) { response.writeHead(status, { "content-type": type }); response.end(body); }
async function assertLink(page, selector, expected) { assert.equal(await page.locator(selector).getAttribute("href"), expected); }
