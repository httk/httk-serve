import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

import { test } from "@playwright/test";

const assets = new URL("../src/httk/web/assets/", import.meta.url);
const modulePath = fileURLToPath(new URL("optimade-table.mjs", assets));
const protocolPath = fileURLToPath(new URL("optimade-table-protocol.mjs", assets));
const cssPath = fileURLToPath(new URL("optimade-table.css", assets));

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
    if (url.pathname === "/assets/optimade-table.mjs") return send(response, 200, module, "text/javascript");
    if (url.pathname === "/assets/optimade-table-protocol.mjs") return send(response, 200, protocol, "text/javascript");
    if (url.pathname === "/assets/optimade-table.css") return send(response, 200, css, "text/css");
    if (url.pathname === "/") return send(response, 200, page(), "text/html");
    if (url.pathname === "/redirect/v1/info") {
      response.writeHead(302, { location: `${redirectOrigin}/must-not-receive-filter-or-cursor` });
      return response.end();
    }
    if (url.pathname === "/error/v1/info") return send(response, 503, JSON.stringify({ errors: [{ title: "Unavailable", detail: "opaque-error-token must stay private" }] }), "application/json");
    if (url.pathname.endsWith("/info")) return send(response, 200, JSON.stringify(info(url.pathname.includes("empty") ? ["empty"] : url.pathname.includes("race") ? ["race"] : ["structures"])), "application/vnd.api+json");
    if (url.pathname.endsWith("/info/structures")) return send(response, 200, JSON.stringify(entry("structures", ["name", "nsites"])), "application/vnd.api+json");
    if (url.pathname.endsWith("/info/empty")) return send(response, 200, JSON.stringify(entry("empty", ["name"])), "application/vnd.api+json");
    if (url.pathname.endsWith("/info/race")) return send(response, 200, JSON.stringify(entry("race", ["name"])), "application/vnd.api+json");
    if (url.pathname === "/v1/structures") return send(response, 200, JSON.stringify(result([resource("one", "<img src=x onerror=alert(1)>", 2)], "/cursor-next?opaque=not-for-dom")), "application/vnd.api+json");
    if (url.pathname === "/cursor-next") return send(response, 200, JSON.stringify(result([resource("two", "Second", 3)], null)), "application/vnd.api+json");
    if (url.pathname === "/empty/v1/empty") return send(response, 200, JSON.stringify(result([], null, "empty")), "application/vnd.api+json");
    if (url.pathname === "/race/v1/race") return send(response, 200, JSON.stringify(result([resource("fresh", "Fresh", 1, "race")], null, "race")), "application/vnd.api+json");
    return send(response, 404, "not found", "text/plain");
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});

test.afterAll(async () => Promise.all([server, redirectServer].map((item) => new Promise((resolve, reject) => item.close((error) => error ? reject(error) : resolve())))));

test("the browser controller loads, pages, and keeps protocol state private", async ({ page }) => {
  requests.length = 0;
  await page.goto(`${origin}/?filter=nsites%20%3E%3D%209`);
  await page.locator("#main [data-httk-optimade-status]").getByText("1 results loaded.").waitFor();
  await page.locator("#mirror [data-httk-optimade-status]").getByText("1 results loaded.").waitFor();
  await page.locator("#empty [data-httk-optimade-status]").getByText("No OPTIMADE results found.").waitFor();
  await page.locator("#error [data-httk-optimade-status]").getByText("The OPTIMADE service returned HTTP 503.").waitFor();
  await page.locator("#redirect [data-httk-optimade-status]").getByText(/Network error/).waitFor();

  assert.equal(await page.locator("#main img").count(), 0);
  assert.equal(await page.locator("#main tbody td").nth(1).textContent(), "<img src=x onerror=alert(1)>");
  await assertLink(page, "#main tbody a", `${origin}/details?view=table&id=one`);
  assert.equal(await page.locator("#empty button[data-httk-optimade-next]").isDisabled(), true);
  assert.equal(await page.locator("#error button.httk-optimade-table__retry").count(), 1);
  assert.equal(await page.locator("#main").getAttribute("aria-busy"), "false");
  assert.equal(await page.locator("#invalid").getAttribute("aria-busy"), "false");
  assert.equal(await page.locator("#invalid tbody").textContent(), "OPTIMADE protocol error: OPTIMADE URL is invalid");
  assert.equal(await page.locator("#invalid button[data-httk-optimade-next]").isDisabled(), true);
  assert.equal(await page.locator("#invalid button[data-httk-optimade-next]").getAttribute("aria-disabled"), "true");
  assert.equal(await page.locator("#main button[data-httk-optimade-next]").isDisabled(), false);
  assert.equal(await page.locator("#main button[data-httk-optimade-next]").getAttribute("aria-disabled"), "false");
  assert.equal(requests.filter((url) => new URL(url).pathname === "/v1/info").length, 1, "shared discovery is reused");
  assert.equal(requests.some((url) => new URL(url).searchParams.get("filter") === "nsites >= 9"), true);
  assert.equal(redirectRequests.length, 0, "redirect destination must not receive a request");

  await page.locator("#main [data-httk-optimade-next]").click();
  await page.locator("#main tbody").getByText("Second").waitFor();
  await page.locator("#main [data-httk-optimade-previous]").click();
  await page.locator("#main tbody").getByText("<img src=x onerror=alert(1)>").waitFor();

  await page.waitForFunction(() => typeof window.releaseOptimadeRace === "function");
  await page.evaluate(async () => {
    const module = await import("/assets/optimade-table.mjs");
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
    const module = await import("/assets/optimade-table.mjs");
    const controller = module.controllerFor(document.querySelector("#main"));
    return Object.values(controller).some((value) => Array.isArray(value) || String(value).includes("opaque=not-for-dom"));
  });
  assert.equal(controllerExposesUrlState, false);
});

function page() {
  const main = shell("main", { base_url: "/v1", entry_type: "structures", columns: columns(), page_size: 2, filter: "nsites >= 1", filter_query: "filter", allowed_origins: [], detail_route: "/details?view=table", detail_column: "name", detail_query: "id" });
  const mirror = shell("mirror", { base_url: "/v1", entry_type: "structures", columns: columns(), page_size: 2, filter: null, filter_query: null, allowed_origins: [] });
  const empty = shell("empty", { base_url: "/empty/v1", entry_type: "empty", columns: [{ key: "name", label: "Name", align: "start" }], page_size: 2, filter: null, filter_query: null, allowed_origins: [] });
  const error = shell("error", { base_url: "/error/v1", entry_type: "structures", columns: [{ key: "id", label: "ID", align: "start" }], page_size: 2, filter: null, filter_query: null, allowed_origins: [] });
  const redirect = shell("redirect", { base_url: "/redirect/v1", entry_type: "structures", columns: [{ key: "id", label: "ID", align: "start" }], page_size: 2, filter: "secret filter", filter_query: null, allowed_origins: [] });
  const race = shell("race", { base_url: "/race/v1", entry_type: "race", columns: [{ key: "name", label: "Name", align: "start" }], page_size: 2, filter: null, filter_query: null, allowed_origins: [] });
  const invalid = shell("invalid", { base_url: "/v1", entry_type: "structures", columns: [{ key: "id", label: "ID", align: "start" }], page_size: 2, filter: null, filter_query: null, allowed_origins: [] });
  // Deliberately break a required field after the shell is produced; this covers
  // defensive install failures rather than the trusted Python declaration.
  const invalidConfig = invalid.replace('"base_url":"/v1"', '"base_url":"http://"');
  return `<!doctype html><meta charset="utf-8"><title>OPTIMADE smoke</title><script>window.optimadeEvents=[];document.addEventListener("httk:optimade-table-updated",e=>window.optimadeEvents.push(e.detail));{const original=window.fetch.bind(window);let first=true;window.fetch=(input,init)=>{if(first&&String(input).includes("/race/v1/race")){first=false;return new Promise(resolve=>{window.releaseOptimadeRace=()=>resolve(new Response(JSON.stringify(${JSON.stringify(result([resource("stale", "Stale", 1, "race")], null, "race"))}),{headers:{"content-type":"application/vnd.api+json"}}));});}return original(input,init);};}</script><link rel="stylesheet" href="/assets/optimade-table.css">${main}${mirror}${empty}${error}${redirect}${race}${invalidConfig}<script type="module" src="/assets/optimade-table.mjs"></script>`;
}

function shell(id, configuration) {
  const config = JSON.stringify(configuration).replace(/</g, "\\u003c");
  const heads = configuration.columns.map((column) => `<th>${column.label}</th>`).join("");
  return `<section id="${id}" class="httk-optimade-table" data-httk-optimade-table="1" data-config-id="${id}-config" aria-busy="true"><table><thead><tr>${heads}</tr></thead><tbody></tbody></table><nav class="httk-optimade-table__pager"><button type="button" data-httk-optimade-previous disabled aria-disabled="true">Previous</button><span data-httk-optimade-status role="status" aria-live="polite">Loading OPTIMADE results.</span><button type="button" data-httk-optimade-next disabled aria-disabled="true">Next</button></nav><script id="${id}-config" type="application/json">${config}</script></section>`;
}

function columns() { return [{ key: "id", label: "ID", align: "start" }, { key: "name", label: "Name", align: "start" }, { key: "nsites", label: "N", align: "end" }]; }
function info(entries) { return { data: { id: "/", type: "info", attributes: { api_version: "1.3.0", formats: ["json"], entry_types_by_format: { json: entries }, available_endpoints: ["info", ...entries] } } }; }
function entry(id, fields) { return { data: { id, type: "info", properties: Object.fromEntries(fields.map((field) => [field, {}])), formats: ["json"], output_fields_by_format: { json: fields } } }; }
function resource(id, name, nsites, type = "structures") { return { id, type, attributes: type === "structures" ? { name, nsites } : { name } }; }
function result(data, next, entryType = "structures") { return { data, meta: { api_version: "1.3.0", data_returned: data.length, more_data_available: next !== null }, links: { next } }; }
function send(response, status, body, type) { response.writeHead(status, { "content-type": type }); response.end(body); }
async function assertLink(page, selector, expected) { assert.equal(await page.locator(selector).getAttribute("href"), expected); }
