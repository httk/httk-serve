import assert from "node:assert/strict";
import test from "node:test";

import {
  discoveryCacheKey,
  effectiveFilter,
  effectiveSort,
  formatCellValue,
  OptimadeTableController,
} from "../src/httk/serve/web/assets/serve-optimade-table.mjs";

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
