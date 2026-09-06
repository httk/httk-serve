import assert from "node:assert/strict";
import test from "node:test";

import {
  composeFilter,
  discoveryCacheKey,
  effectiveFilter,
  effectivePageSize,
  effectiveSort,
  filterPillHref,
  formatCellValue,
  OptimadeTableController,
  pageSizeHref,
  parseFilterPills,
  parseSortPill,
  pillParts,
  renderSummary,
  sortComponents,
  sortHref,
  sortPillHref,
} from "../src/httk/serve/web/assets/serve-optimade-table.mjs";

function stubDocument() {
  const make = (tagName) => ({
    tagName,
    children: [],
    className: "",
    textContent: "",
    listeners: {},
    append(...nodes) { this.children.push(...nodes); },
    setAttribute(name, value) { this[name] = value; },
    addEventListener(type, handler) { this.listeners[type] = handler; },
  });
  return { createElement: (tagName) => make(tagName), createTextNode: (value) => ({ text: value }) };
}

/** Find a descendant matching predicate in the appended-children tree the stub document builds. */
function findNode(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children ?? []) {
    const found = findNode(child, predicate);
    if (found) return found;
  }
  return null;
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

test("every filter/sort pill gets a keyboard-accessible x that navigates via the onRemovePill callback", () => {
  const doc = stubDocument();
  const summary = { noun: "entries", fields: { nsites: { label: "Sites" }, elements: { label: "Elements", clears: ["elements"] } } };
  const filterClause = { property: "elements", op: "HAS", values: [{ type: "string", value: "Si" }] };
  const pills = { filter: [filterClause], sort: [{ property: "nsites", descending: true }] };
  const removed = [];
  const el = stubElement();
  renderSummary(el, doc, summary, pills, true, { dataReturned: 1, dataAvailable: 1 }, {
    filterQuery: "filter",
    sortQuery: "sort",
    search: "?filter=elements+HAS+%22Si%22&elements=Si&sort=-nsites",
    onRemovePill: (href) => removed.push(href),
  });

  // Each pill (index 1: filter, index 2: sort — index 0 is the count sentence) carries a
  // real <button> (keyboard-accessible: natively focusable and Enter/Space-activatable)
  // with an aria-label naming the pill, not a bare clickable span.
  const filterButton = findNode(el.children[1], (node) => node.tagName === "button");
  assert.ok(filterButton, "filter pill has a remove button");
  assert.equal(filterButton["aria-label"], "Remove filter Elements Si");

  const sortButton = findNode(el.children[2], (node) => node.tagName === "button");
  assert.ok(sortButton, "sort pill has a remove button");
  assert.match(sortButton["aria-label"], /Remove sort/);

  // Clicking the filter pill's x removes its predicate AND its configured clears param.
  filterButton.listeners.click();
  assert.equal(removed.length, 1);
  let params = new URLSearchParams(removed[0].slice(1));
  assert.equal(params.get("filter"), "");
  assert.equal(params.has("elements"), false);

  // Clicking the sort pill's x writes an explicit empty sort=, not param removal.
  sortButton.listeners.click();
  assert.equal(removed.length, 2);
  params = new URLSearchParams(removed[1].slice(1));
  assert.equal(params.get("sort"), "");
  assert.equal(params.get("filter"), 'elements HAS "Si"');
});

test("a pill renders with no x when its *_query isn't wired (no URL state to express removal in)", () => {
  const doc = stubDocument();
  const summary = { noun: "entries", fields: { nsites: { label: "Sites" } } };
  const pills = { filter: [{ property: "nsites", op: ">=", values: [{ type: "number", value: 1 }] }], sort: null };
  const el = stubElement();
  renderSummary(el, doc, summary, pills, true, { dataReturned: 1, dataAvailable: 1 });
  assert.equal(findNode(el.children[1], (node) => node.tagName === "button"), null);
});

test("sort pills drop id tiebreakers only; the authored default pills like any other sort (item 9)", () => {
  assert.deepEqual(parseSortPill("-a,id"), [{ property: "a", descending: true }]);
  assert.equal(parseSortPill("id"), null);
  // Reversed from the old hide-if-default behavior: an authored default sort still pills.
  assert.deepEqual(parseSortPill("-nsites"), [{ property: "nsites", descending: true }]);
  assert.deepEqual(parseSortPill("nsites,-energy"), [{ property: "nsites", descending: false }, { property: "energy", descending: true }]);
  assert.equal(parseSortPill(null), null);
  assert.equal(parseSortPill(""), null);
});

test("the resolved authored default sort renders as a removable pill, same as a URL-overridden sort", () => {
  // installOptimadeTable's contract: effectiveSort resolves the authored default through
  // sort_aliases exactly like a URL value, and parseSortPill no longer special-cases it.
  const config = { sort: "best", sort_aliases: { best: "-nsites,id" }, sort_query: "sort" };
  assert.deepEqual(parseSortPill(effectiveSort(config, { search: "?other=1" })), [{ property: "nsites", descending: true }]);
  assert.deepEqual(parseSortPill(effectiveSort(config, { search: "?sort=chemical_formula_reduced" })), [
    { property: "chemical_formula_reduced", descending: false },
  ]);
  // An explicit empty sort= is the ONLY thing that suppresses the default: effectiveSort
  // already resolves it to null before parseSortPill ever sees it (the tri-state).
  assert.equal(effectiveSort(config, { search: "?sort=" }), null);
  assert.equal(parseSortPill(effectiveSort(config, { search: "?sort=" })), null);
});

test("composeFilter rebuilds an equivalent filter string from surviving pill clauses", () => {
  const clauses = parseFilterPills('nsites >= 2 AND elements HAS ALL "Si", "O" AND name CONTAINS "Ca"');
  assert.equal(composeFilter(clauses), 'nsites >= 2 AND elements HAS ALL "Si", "O" AND name CONTAINS "Ca"');
  // Dropping the middle clause and recomposing round-trips through the parser.
  const withoutMiddle = [clauses[0], clauses[2]];
  assert.deepEqual(parseFilterPills(composeFilter(withoutMiddle)), withoutMiddle);
  assert.equal(composeFilter([]), "");
});

test("filterPillHref removes one predicate from filter_query and deletes its configured clears params", () => {
  const clauses = parseFilterPills('nsites >= 2 AND elements HAS "Si"');
  const fields = { elements: { clears: ["elements", "el_min"] } };
  // Removing the elements clause rewrites filter to just the nsites clause, and deletes
  // BOTH configured source params so a host form cannot resurrect the predicate.
  const href = filterPillHref(clauses, 1, fields, "filter", "?filter=x&elements=Si&el_min=1&other=1");
  const params = new URLSearchParams(href.slice(1));
  assert.equal(params.get("filter"), "nsites >= 2");
  assert.equal(params.has("elements"), false);
  assert.equal(params.has("el_min"), false);
  assert.equal(params.get("other"), "1");
  // Removing the only clause leaves an explicit empty filter param (no filter, not the authored one).
  const cleared = filterPillHref(clauses, 0, {}, "filter", "?filter=x");
  assert.equal(new URLSearchParams(cleared.slice(1)).get("filter"), 'elements HAS "Si"');
  // No filter_query wired: no URL state can express removal.
  assert.equal(filterPillHref(clauses, 0, {}, null, "?filter=x"), null);
});

test("sortPillHref sets an explicit empty sort= and is null when sort_query isn't wired", () => {
  assert.equal(sortPillHref("sort", "?sort=-nsites&filter=x"), "?sort=&filter=x");
  assert.equal(sortPillHref(null, "?sort=-nsites"), null);
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
