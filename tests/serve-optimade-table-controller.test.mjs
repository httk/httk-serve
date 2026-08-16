import assert from "node:assert/strict";
import test from "node:test";

import {
  discoveryCacheKey,
  effectiveFilter,
  effectiveSort,
  formatCellValue,
  OptimadeTableController,
  parseFilterPills,
  parseSortPill,
  pillParts,
  renderSummary,
  resolveSortAlias,
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
