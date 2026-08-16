import assert from "node:assert/strict";
import test from "node:test";

import {
  discoveryCacheKey,
  effectiveFilter,
  effectivePageSize,
  effectiveSort,
  formatCellValue,
  OptimadeTableController,
  pageSizeHref,
  parseFilterPills,
  parseSortPill,
  pillParts,
  renderSummary,
  resolveSortAlias,
  sortComponents,
  sortHref,
} from "../src/httk/serve/web/assets/serve-optimade-table.mjs";

function stubDocument() {
  const make = () => ({ children: [], className: "", textContent: "", append(...nodes) { this.children.push(...nodes); } });
  return { createElement: () => make(), createTextNode: (value) => ({ text: value }) };
}

function stubElement() {
  return { hidden: true, children: [], replaceChildren() { this.children = []; }, append(...nodes) { this.children.push(...nodes); } };
}

function flatten(node) {
  if (node.text !== undefined) return node.text;
  if (node.textContent) return node.textContent;
  return (node.children ?? []).map(flatten).join("");
}

const configuration = {
  base_url: "/optimade/v1",
  entry_type: "structures",
  columns: [{ key: "id" }, { key: "chemical_formula_reduced" }],
  allowed_origins: ["https://b.example.test", "https://a.example.test"],
  filter: "nsites >= 2",
  filter_query: "filter",
  page_size: 10,
};

test("a URL filter overrides the authored filter completely, including an empty value", () => {
  assert.equal(effectiveFilter(configuration, { search: "?filter=elements+HAS+%22Si%22" }), 'elements HAS "Si"');
  assert.equal(effectiveFilter(configuration, { search: "?filter=" }), null);
  assert.equal(effectiveFilter(configuration, { search: "?other=1" }), "nsites >= 2");
  assert.throws(() => effectiveFilter(configuration, { search: `?filter=${"x".repeat(4097)}` }), /4096/);
});

test("a URL sort overrides the authored sort completely and is bounded", () => {
  const withSort = { ...configuration, sort: "-nsites", sort_query: "sort" };
  assert.equal(effectiveSort(withSort, { search: "?sort=chemical_formula_reduced" }), "chemical_formula_reduced");
  assert.equal(effectiveSort(withSort, { search: "?sort=" }), null);
  assert.equal(effectiveSort(withSort, { search: "?other=1" }), "-nsites");
  assert.throws(() => effectiveSort(withSort, { search: `?sort=${"x".repeat(4097)}` }), /4096/);
});

test("sort_aliases resolve display sort values to OPTIMADE sorts before querying", () => {
  const aliased = {
    ...configuration,
    sort: "rank",
    sort_query: "sort",
    sort_aliases: { rank: "id", best: "-nsites,id" },
  };
  // A display alias in the URL is translated, never sent verbatim.
  assert.equal(effectiveSort(aliased, { search: "?sort=rank" }), "id");
  assert.equal(effectiveSort(aliased, { search: "?sort=best" }), "-nsites,id");
  // An unmapped value passes through unchanged (a real OPTIMADE sort, or the server's problem).
  assert.equal(effectiveSort(aliased, { search: "?sort=chemical_formula_reduced" }), "chemical_formula_reduced");
  // The authored default is resolved through the map too.
  assert.equal(effectiveSort(aliased, { search: "?other=1" }), "id");
  // No map: values pass through unchanged.
  assert.equal(effectiveSort({ ...configuration, sort_query: "sort" }, { search: "?sort=rank" }), "rank");
});

test("discovery cache keys include discovery inputs but not table-only options", () => {
  const first = discoveryCacheKey(configuration, "https://site.example.test/guide/");
  const reorderedOrigins = discoveryCacheKey({ ...configuration, allowed_origins: [...configuration.allowed_origins].reverse(), detail_route: "details" }, "https://site.example.test/guide/");
  const changedFields = discoveryCacheKey({ ...configuration, columns: [{ key: "id" }] }, "https://site.example.test/guide/");
  assert.equal(first, reorderedOrigins);
  assert.notEqual(first, changedFields);
});

test("cell presentation is deterministic, bounded, and gives null an accessible sentinel", () => {
  assert.deepEqual(formatCellValue(null), { text: "—", missing: true, truncated: false });
  assert.equal(formatCellValue({ z: [true, 2], a: "x" }).text, '{"a":"x","z":[true,2]}');
  assert.deepEqual(formatCellValue("abcdefgh", 5), { text: "abcd…", missing: false, truncated: true });
});

test("filter pills honour quoted strings, every clause shape, and refuse ambiguous filters", () => {
  // A quoted " AND " must not split the clause.
  assert.deepEqual(parseFilterPills('name = "Ca AND Ti"'), [{ property: "name", op: "=", values: [{ type: "string", value: "Ca AND Ti" }] }]);
  // Each recognized clause shape.
  assert.deepEqual(parseFilterPills("nsites >= 3"), [{ property: "nsites", op: ">=", values: [{ type: "number", value: 3 }] }]);
  assert.deepEqual(parseFilterPills('name CONTAINS "xy"'), [{ property: "name", op: "CONTAINS", values: [{ type: "string", value: "xy" }] }]);
  assert.deepEqual(parseFilterPills('name STARTS WITH "x"'), [{ property: "name", op: "STARTS WITH", values: [{ type: "string", value: "x" }] }]);
  assert.deepEqual(parseFilterPills('name ENDS WITH "y"'), [{ property: "name", op: "ENDS WITH", values: [{ type: "string", value: "y" }] }]);
  assert.deepEqual(parseFilterPills('elements HAS "Si"'), [{ property: "elements", op: "HAS", values: [{ type: "string", value: "Si" }] }]);
  assert.deepEqual(parseFilterPills('elements HAS ALL "Si", "O"'), [{ property: "elements", op: "HAS ALL", values: [{ type: "string", value: "Si" }, { type: "string", value: "O" }] }]);
  // Top-level AND yields multiple pills.
  assert.equal(parseFilterPills('nsites >= 2 AND elements HAS "O"').length, 2);
  // Empty is a rendered-empty list; unrepresentable filters vanish entirely.
  assert.deepEqual(parseFilterPills(null), []);
  assert.deepEqual(parseFilterPills(""), []);
  assert.equal(parseFilterPills('a = 1 OR b = 2'), null);
  assert.equal(parseFilterPills('NOT a = 1'), null);
  assert.equal(parseFilterPills('(a = 1)'), null);
  assert.equal(parseFilterPills('1 = nsites'), null);
  assert.equal(parseFilterPills('a ~ 1'), null);
});

test("pill parts apply value maps, number formatting, and operator glyphs", () => {
  const collinearity = { label: "Collinearity", values: { collinear: "Collinear" } };
  assert.deepEqual(pillParts({ property: "_c", op: "HAS", values: [{ type: "string", value: "collinear" }] }, collinearity), { label: "Collinearity", text: "Collinear" });
  // Unmapped string falls back to the raw value.
  assert.deepEqual(pillParts({ property: "_c", op: "=", values: [{ type: "string", value: "other" }] }, collinearity), { label: "Collinearity", text: "other" });
  // A scaled/suffixed number format with a comparison operator glyph.
  const fraction = { label: "Fraction", format: { name: "number", digits: 0, scale: 100, suffix: " %" } };
  assert.deepEqual(pillParts({ property: "_x", op: ">=", values: [{ type: "number", value: 0.25 }] }, fraction), { label: "Fraction", text: "≥ 25 %" });
  // No field spec: label is the raw property and numbers stringify plainly.
  assert.deepEqual(pillParts({ property: "nsites", op: "!=", values: [{ type: "number", value: 4 }] }, undefined), { label: "nsites", text: "≠ 4" });
});

test("pill value text is capped like a table cell", () => {
  const parts = pillParts({ property: "x", op: "=", values: [{ type: "string", value: "y".repeat(400) }] }, undefined);
  assert.equal(parts.text.length, 256);
  assert.equal(parts.text.endsWith("…"), true);
});

test("renderSummary composes counts, pills, hidden state, and re-renders idempotently", () => {
  const doc = stubDocument();
  const summary = { noun: "entries", fields: { nsites: { label: "Sites" } } };
  const nothing = { filter: [], sort: null };

  let el = stubElement();
  renderSummary(el, doc, summary, nothing, true, { dataReturned: 2, dataAvailable: 5 });
  assert.equal(el.hidden, false);
  assert.equal(flatten(el.children[0]), "Showing 2 of 5 entries.");

  el = stubElement();
  renderSummary(el, doc, summary, nothing, true, { dataReturned: 2, dataAvailable: null });
  assert.equal(flatten(el.children[0]), "Showing 2 entries.");

  el = stubElement();
  renderSummary(el, doc, summary, nothing, false, { dataReturned: 2, dataAvailable: 5 });
  assert.equal(flatten(el.children[0]), "Showing all 5 entries.");

  el = stubElement();
  renderSummary(el, doc, summary, nothing, false, { dataReturned: null, dataAvailable: null });
  assert.equal(el.hidden, true);
  assert.equal(el.children.length, 0);

  el = stubElement();
  const clause = { property: "nsites", op: ">=", values: [{ type: "number", value: 9 }] };
  renderSummary(el, doc, summary, { filter: [clause], sort: null }, false, { dataReturned: null, dataAvailable: null });
  assert.equal(el.hidden, false);
  assert.equal(flatten(el.children[0]), "Sites ≥ 9");

  el = stubElement();
  renderSummary(el, doc, summary, { filter: [], sort: [{ property: "nsites", descending: true }] }, false, { dataReturned: null, dataAvailable: null });
  assert.equal(flatten(el.children[0]), "Sorted by Sites ↓");
  // Idempotent re-render clears the previous children and re-hides when empty.
  renderSummary(el, doc, summary, nothing, false, { dataReturned: null, dataAvailable: null });
  assert.equal(el.children.length, 0);
  assert.equal(el.hidden, true);
});

test("sort pills drop id tiebreakers and the authored default", () => {
  assert.deepEqual(parseSortPill("-a,id", null), [{ property: "a", descending: true }]);
  assert.equal(parseSortPill("id", null), null);
  assert.equal(parseSortPill("-nsites", "-nsites"), null);
  assert.deepEqual(parseSortPill("nsites,-energy", "-nsites"), [{ property: "nsites", descending: false }, { property: "energy", descending: true }]);
  assert.equal(parseSortPill(null, null), null);
});

test("an aliased authored default is suppressed while a URL-overridden sort still pills", () => {
  // Reproduces installOptimadeTable's contract: the authored default is alias-resolved
  // before comparison, so an aliased default matches its effective sort and is dropped.
  const config = { sort: "best", sort_aliases: { best: "-nsites,id" }, sort_query: "sort" };
  const authoredDefault = resolveSortAlias(config.sort, config.sort_aliases);
  assert.equal(parseSortPill(effectiveSort(config, { search: "?other=1" }), authoredDefault), null);
  // A URL-supplied sort differs from the resolved default, so its pill survives.
  assert.deepEqual(parseSortPill(effectiveSort(config, { search: "?sort=chemical_formula_reduced" }), authoredDefault), [
    { property: "chemical_formula_reduced", descending: false },
  ]);
});

test("sortHref toggles direction, appends the id tiebreaker, and preserves other params", () => {
  // No current sort: ascending default with an id tiebreaker, other params kept.
  assert.equal(sortHref("nsites", null, "sort", "?filter=nsites+%3E%3D+2"), "?filter=nsites+%3E%3D+2&sort=nsites%2Cid");
  // The column is the current ascending primary: flip to descending.
  assert.equal(sortHref("nsites", [{ property: "nsites", descending: false }], "sort", "?sort=nsites%2Cid"), "?sort=-nsites%2Cid");
  // The column is the current descending primary: flip back to ascending.
  assert.equal(sortHref("nsites", [{ property: "nsites", descending: true }], "sort", "?sort=-nsites%2Cid"), "?sort=nsites%2Cid");
  // A different column stays ascending default regardless of the current sort.
  assert.equal(sortHref("energy", [{ property: "nsites", descending: false }], "sort", "?sort=nsites%2Cid"), "?sort=energy%2Cid");
  // The id column never gets a duplicate ,id tiebreaker.
  assert.equal(sortHref("id", null, "sort", ""), "?sort=id");
  // Unrelated params survive verbatim while the sort param is replaced.
  assert.equal(sortHref("nsites", null, "sort", "?a=1&sort=old&b=2"), "?a=1&sort=nsites%2Cid&b=2");
});

test("effectivePageSize restricts the URL value to the configured options", () => {
  const config = { page_size: 50, page_size_options: [25, 50, 100], page_size_query: "page_size" };
  // No wiring: always the authored page_size.
  assert.equal(effectivePageSize({ page_size: 50 }, { search: "?page_size=100" }), 50);
  // Absent param falls back to the authored page_size.
  assert.equal(effectivePageSize(config, { search: "?other=1" }), 50);
  // A value matching an option (string-exact) selects it.
  assert.equal(effectivePageSize(config, { search: "?page_size=100" }), 100);
  // Non-option, non-integer, and negative values all fall back.
  assert.equal(effectivePageSize(config, { search: "?page_size=37" }), 50);
  assert.equal(effectivePageSize(config, { search: "?page_size=abc" }), 50);
  assert.equal(effectivePageSize(config, { search: "?page_size=-25" }), 50);
});

test("pageSizeHref sets only the page-size param and preserves everything else", () => {
  assert.equal(pageSizeHref(100, "page_size", "?filter=nsites+%3E%3D+2&sort=-nsites"), "?filter=nsites+%3E%3D+2&sort=-nsites&page_size=100");
  assert.equal(pageSizeHref(50, "page_size", "?page_size=100&filter_advanced=1"), "?page_size=50&filter_advanced=1");
  assert.equal(pageSizeHref(25, "page_size", ""), "?page_size=25");
});

test("id sort components are kept so the id column header toggles direction", () => {
  // Unlike parseSortPill, sortComponents keeps id so the header decorator can toggle it.
  assert.deepEqual(sortComponents("id"), [{ property: "id", descending: false }]);
  assert.deepEqual(sortComponents("-id"), [{ property: "id", descending: true }]);
  // Effective sort id → the id header flips to descending; -id → back to ascending.
  assert.equal(sortHref("id", sortComponents("id"), "sort", ""), "?sort=-id");
  assert.equal(sortHref("id", sortComponents("-id"), "sort", ""), "?sort=id");
});

test("a controller exposes no enumerable state that can contain continuation URLs", () => {
  const button = { addEventListener() {}, setAttribute() {} };
  const controller = new OptimadeTableController({}, {
    tbody: {}, previous: button, next: button, status: {}, pager: {}, columns: 1,
  }, configuration, {
    document: { baseURI: "https://site.example.test/", defaultView: {} },
    fetch: async () => { throw new Error("not called"); },
  });
  assert.deepEqual(Object.keys(controller), []);
  assert.equal(Object.values(controller).some((value) => Array.isArray(value) || String(value).includes("opaque=")), false);
});

test("the advanced disclosure opens only when its own submit marker is present", () => {
  const button = { addEventListener() {}, setAttribute() {} };
  const build = (search) => {
    const details = { open: false, querySelector: () => null };
    const shell = { querySelector: (selector) => (selector.includes("advanced") ? details : null) };
    new OptimadeTableController(shell, { tbody: {}, previous: button, next: button, status: {}, pager: {}, columns: 1 }, configuration, {
      document: { baseURI: "https://site.example.test/", defaultView: {} },
      location: { search },
      fetch: async () => { throw new Error("not called"); },
    });
    return details.open;
  };
  // The marker (filter_query is "filter" → "filter_advanced") drives open, alongside a filter or alone.
  assert.equal(build("?filter=nsites+%3E%3D+1&filter_advanced=1"), true);
  assert.equal(build("?filter_advanced=1"), true);
  // A bare sidebar-style filter with no marker stays closed.
  assert.equal(build("?filter=nsites+%3E%3D+1"), false);
  assert.equal(build("?other=1"), false);
});

test("changing the page-size dropdown navigates to the size-only URL, preserving other params", () => {
  const staticButton = { addEventListener() {}, setAttribute() {} };
  let changeHandler = null;
  const select = { value: "", addEventListener: (type, fn) => { if (type === "change") changeHandler = fn; } };
  const shell = { querySelector: (selector) => (selector.includes("page-size") ? select : null) };
  let assigned = null;
  const location = { search: "?filter=nsites+%3E%3D+2&sort=-nsites", assign: (href) => { assigned = href; } };
  const config = { ...configuration, page_size: 50, page_size_options: [25, 50, 100], page_size_query: "page_size" };
  new OptimadeTableController(shell, { tbody: {}, previous: staticButton, next: staticButton, status: {}, pager: {}, columns: 1 }, config, {
    document: { baseURI: "https://site.example.test/", defaultView: {} },
    location,
    fetch: async () => { throw new Error("not called"); },
  });
  // Install selects the effective (here authored) page size in the dropdown.
  assert.equal(select.value, "50");
  // A user change navigates via location.assign to the same URL with only the page-size param set.
  select.value = "100";
  changeHandler();
  assert.equal(assigned, pageSizeHref("100", "page_size", location.search));
  assert.equal(assigned, "?filter=nsites+%3E%3D+2&sort=-nsites&page_size=100");
});
