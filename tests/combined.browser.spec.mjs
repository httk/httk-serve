// End-to-end: `httk serve combined serve` with the default site, driven in a real browser.
// Needs the Python environment with httk-serve installed on PATH (python3 and httk).
import { execFileSync, spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";

const fixtureScript = fileURLToPath(new URL("browser_fixtures/make_combined_store.py", import.meta.url));
const RECORDS_KIND = "_httk_total_energy";

let directory;
let fixture;
let origin;
let server;
let serverLog = "";

test.beforeAll(async () => {
  test.setTimeout(90_000);
  directory = mkdtempSync(join(tmpdir(), "httk-serve-combined-"));
  fixture = JSON.parse(execFileSync("python3", [fixtureScript, directory], { encoding: "utf8" }));
  const port = await freePort();
  origin = `http://127.0.0.1:${port}`;
  server = spawn("httk", ["serve", "combined", "serve", fixture.store, "--files-dir", fixture.files_dir, "--host", "127.0.0.1", "--port", String(port)], { stdio: ["ignore", "pipe", "pipe"] });
  server.stdout.on("data", (chunk) => { serverLog += chunk; });
  server.stderr.on("data", (chunk) => { serverLog += chunk; });
  await waitReady(`${origin}/optimade/v1/info`);
});

test.afterAll(async () => {
  if (server && server.exitCode === null) {
    const exited = new Promise((resolve) => server.once("exit", resolve));
    server.kill("SIGINT");
    await exited;
  }
  if (directory) rmSync(directory, { recursive: true, force: true });
});

test("home lists every served type with counts, the record kinds and the API base URL", async ({ page }) => {
  await page.goto(`${origin}/`);
  const catalog = page.locator("[data-httk-serve-optimade-catalog]");
  await expect(catalog.locator(".httk-serve-optimade-explorer__api code")).toHaveText(`${origin}/optimade/`);
  const counts = { _httk_records: "1 entry", structures: "2 entries", _httk_runs: "2 entries", files: "5 entries" };
  for (const [type, count] of Object.entries(counts)) {
    await expect(card(page, type).locator(".httk-serve-optimade-explorer__count")).toHaveText(count);
  }
  const kind = card(page, "_httk_records").locator(".httk-serve-optimade-explorer__kinds li");
  await expect(kind).toHaveCount(1);
  await expect(kind.locator("code")).toHaveText(RECORDS_KIND);
  await expect(kind.locator(".httk-serve-optimade-explorer__unit")).toHaveText("eV");
});

test("a record kind browses to its records and on to the entry with its provenance", async ({ page }) => {
  await page.goto(`${origin}/`);
  await card(page, "_httk_records").locator(".httk-serve-optimade-explorer__kinds a").click();
  await expect(page).toHaveURL((url) => url.pathname === "/browse" && url.searchParams.get("type") === "_httk_records" && url.searchParams.get("kind") === RECORDS_KIND);
  const rows = page.locator("[data-httk-serve-optimade-table] tbody tr");
  await expect(rows).toHaveCount(1);
  await expect(rows).toContainText("-1.5");
  await rows.getByRole("link", { name: fixture.energy }).click();

  await expect(page).toHaveURL((url) => url.pathname === "/entry" && url.searchParams.get("type") === "_httk_records" && url.searchParams.get("id") === fixture.energy);
  await expect(page.locator(".httk-serve-optimade-explorer__values dd")).toHaveText("-1.5 eV");
  const producer = group(page, "Produced by").locator(":scope > li > a").first();
  await expect(producer).toHaveText(fixture.job);
  expect(params(await producer.getAttribute("href"))).toEqual({ type: "_httk_runs", id: fixture.job });
});

test("the job run lists inputs and outputs, downloads files and links its follow-up run", async ({ page, request }) => {
  await page.goto(entryUrl("_httk_runs", fixture.job));
  const inputs = group(page, "Inputs").locator(":scope > li");
  await expect(inputs).toHaveCount(1);
  await expect(inputs.locator("strong")).toHaveText("initial_structure");
  expect(params(await inputs.locator("a").first().getAttribute("href"))).toEqual({ type: "structures", id: fixture.alt });

  const outputs = group(page, "Outputs").locator(":scope > li");
  await expect(outputs).toHaveCount(4);
  await expect(outputs.filter({ hasText: "energy" })).toContainText(`${RECORDS_KIND} = -1.5 eV`);
  await expect(outputs.filter({ hasText: "outcar" }).locator("a[download]")).toHaveCount(1);
  const lost = outputs.filter({ hasText: "lost.txt" });
  await expect(lost).toHaveCount(1);
  await expect(lost.locator("a[download]")).toHaveCount(0);

  const odd = outputs.filter({ hasText: fixture.odd_name }).locator("a[download]");
  const [download] = await Promise.all([page.waitForEvent("download"), odd.click()]);
  expect(download.suggestedFilename()).toBe(fixture.odd_name);
  expect(readFileSync(await download.path(), "utf8")).toBe(fixture.odd_bytes);
  const fetched = await request.get(await odd.getAttribute("href"));
  expect(fetched.status()).toBe(200);
  expect(await fetched.text()).toBe(fixture.odd_bytes);

  const followed = group(page, "Followed by").locator(":scope > li");
  await expect(followed).toHaveCount(1);
  expect(params(await followed.locator(":scope > a").getAttribute("href"))).toEqual({ type: "_httk_runs", id: fixture.post });

  // The lost file's own page shows its content hash as text, with no link.
  await page.goto(entryUrl("files", fixture.file_ids["lost.txt"]));
  const location = page.locator("dt:text-is('Stored location') + dd");
  await expect(location).toHaveText(`sha-256;${Buffer.from(fixture.lost_sha256, "hex").toString("base64url")}`);
  await expect(location.locator("a")).toHaveCount(0);
});

test("the staged-input alternative offers its source file and the main structure lists it", async ({ page }) => {
  await page.goto(entryUrl("structures", fixture.alt));
  await expect(page.locator("dt:text-is('Alternative kind') + dd")).toHaveText(fixture.alt.split("~")[1]);
  const source = group(page, "files").locator(":scope > li");
  await expect(source).toHaveCount(1);
  expect(params(await source.getByRole("link", { name: "POSCAR", exact: true }).getAttribute("href"))).toEqual({ type: "files", id: fixture.file_ids.POSCAR });
  expect(await source.locator("a[download]").getAttribute("href")).toBe(`${origin}/data/ws/jobs/a/POSCAR`);

  await page.goto(entryUrl("structures", fixture.main));
  const alternatives = page.locator(`h3:text-is("Alternatives") + ul > li`);
  await expect(alternatives).toHaveCount(1);
  expect(params(await alternatives.getByRole("link", { name: fixture.alt.split("~")[1] }).getAttribute("href"))).toEqual({ type: "structures", id: fixture.alt });
  expect(params(await alternatives.getByRole("link", { name: "POSCAR", exact: true }).getAttribute("href"))).toEqual({ type: "files", id: fixture.file_ids.POSCAR });
  expect(await alternatives.locator("a[download]").getAttribute("href")).toBe(`${origin}/data/ws/jobs/a/POSCAR`);
});

test("the condition builder offers only queryable properties and drives the filter", async ({ page }) => {
  await page.goto(`${origin}/browse?type=files`);
  const fileProperties = await conditionProperties(page);
  expect(fileProperties).toContain("id");
  expect(fileProperties).not.toContain("name");
  expect(fileProperties).not.toContain("size");

  await page.goto(`${origin}/browse?type=structures`);
  const rows = page.locator("[data-httk-serve-optimade-table] tbody tr");
  await expect(rows).toHaveCount(2);
  const form = page.locator("[data-httk-serve-optimade-conditions]");
  await expect(form).toBeVisible();
  await form.getByLabel("Property").selectOption("nelements");
  await form.getByLabel("Operator").selectOption("=");
  await form.getByLabel("Value").fill("2");
  await form.getByRole("button", { name: "Search" }).click();
  await expect(page).toHaveURL((url) => url.searchParams.get("type") === "structures" && url.searchParams.get("filter") === "nelements = 2");
  await expect(rows).toHaveCount(1);
  await expect(rows.getByRole("link", { name: fixture.other })).toHaveCount(1);

  const advanced = page.locator("[data-httk-serve-optimade-advanced]");
  await advanced.locator("summary").click();
  await advanced.locator("[data-httk-serve-optimade-advanced-filter]").fill("nelements = 1");
  await advanced.getByRole("button", { name: "Search" }).click();
  await expect(page).toHaveURL((url) => url.searchParams.get("type") === "structures" && url.searchParams.get("filter") === "nelements = 1");
  await expect(rows).toHaveCount(1);
  await expect(rows.getByRole("link", { name: fixture.main })).toHaveCount(1);
});

test("an unknown entry shows a not-found state linking back to browse", async ({ page }) => {
  await page.goto(entryUrl("structures", "nope"));
  const state = page.locator(".httk-serve-optimade-explorer__state");
  await expect(state.locator("p")).toHaveText("The requested entry was not found.");
  expect(params(await state.getByRole("link", { name: "Back to browse" }).getAttribute("href"))).toEqual({ type: "structures" });
});

test("/optimade redirects to /optimade/ and unregistered files are not served", async ({ page, request }) => {
  const redirect = await request.get(`${origin}/optimade`, { maxRedirects: 0 });
  expect(redirect.status()).toBe(308);
  expect(redirect.headers().location).toBe("/optimade/");
  await page.goto(`${origin}/optimade`);
  expect(new URL(page.url()).pathname).toBe("/optimade/");
  expect((await request.get(`${origin}/data/ws/jobs/a/unregistered.txt`)).status()).toBe(404);
  expect((await request.get(`${origin}/data/ws/jobs/a/OUTCAR`)).status()).toBe(200);
});

function card(page, type) {
  return page.locator("article.httk-serve-optimade-explorer__card").filter({ has: page.locator(`p.httk-serve-optimade-explorer__type:text-is("${type}")`) });
}

/** The list under an entry page's relationship heading. */
function group(page, heading) {
  return page.locator(`h4:text-is("${heading}") + ul`);
}

function entryUrl(type, id) {
  return `${origin}/entry?${new URLSearchParams({ type, id })}`;
}

function params(href) {
  return Object.fromEntries(new URL(href).searchParams);
}

async function conditionProperties(page) {
  const select = page.locator("[data-httk-serve-optimade-conditions]").getByLabel("Property");
  await expect(select).toBeVisible();
  return (await select.locator("option").evaluateAll((options) => options.map((option) => option.value))).filter(Boolean);
}

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

async function waitReady(url) {
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) throw new Error(`httk serve combined exited (${server.exitCode}):\n${serverLog}`);
    try {
      if ((await fetch(url)).ok) return;
    } catch {
      // Not listening yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`httk serve combined did not become ready:\n${serverLog}`);
}
