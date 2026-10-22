import assert from "node:assert/strict";
import test from "node:test";

import {
  bestLabel,
  boundedText,
  capList,
  catalogOrder,
  compileCondition,
  compileFilter,
  doiUrl,
  elementsClause,
  firstSentence,
  formatValue,
  humanSize,
  isIdentifier,
  linkPolicy,
  numberLiteral,
  operatorsFor,
  queryableProperties,
  recordKinds,
  relationshipHeading,
  routeHref,
  splitAlternativeId,
  stringLiteral,
  typeLabel,
  withKind,
} from "../src/httk/serve/web/assets/serve-optimade-present.mjs";

const PAGE = "https://site.example.test/browse";
const ORIGIN = "https://site.example.test";

test("string literals escape backslash and double quote; numbers must be finite decimals", () => {
  assert.equal(stringLiteral('a"b\\c'), '"a\\"b\\\\c"');
  assert.equal(numberLiteral(" -1.5e3 "), "-1.5e3");
  assert.equal(numberLiteral(".5"), ".5");
  for (const bad of ["", "1e999", "NaN", "Infinity", "0x10", "1,5", "1 OR x"]) assert.throws(() => numberLiteral(bad));
});

test("operators follow the property fulltype and presence tests apply everywhere", () => {
  const presence = ["IS KNOWN", "IS UNKNOWN"];
  assert.deepEqual(operatorsFor({ "x-optimade-type": "string" }), ["=", "!=", "CONTAINS", "STARTS WITH", "ENDS WITH", ...presence]);
  for (const type of ["integer", "float", "timestamp"]) {
    assert.deepEqual(operatorsFor({ "x-optimade-type": type }), ["=", "!=", "<", "<=", ">", ">=", ...presence]);
  }
  assert.deepEqual(operatorsFor({ "x-optimade-type": "list" }), ["HAS", "HAS ALL", "HAS ANY", ...presence]);
  assert.deepEqual(operatorsFor({ "x-optimade-type": "dictionary" }), presence);
  // Without x-optimade-type the JSON-schema type decides.
  assert.deepEqual(operatorsFor({ type: ["array", "null"] }).slice(0, 3), ["HAS", "HAS ALL", "HAS ANY"]);
  assert.equal(operatorsFor({ type: ["string", "null"], format: "date-time" })[2], "<");
});

test("only properties whose query-support is not none are offered", () => {
  const properties = {
    name: { "x-optimade-type": "string", "x-optimade-requirements": { "query-support": "none" } },
    id: { "x-optimade-type": "string", "x-optimade-requirements": { "query-support": "all mandatory" } },
    nsites: { "x-optimade-type": "integer" },
    type: { "x-optimade-type": "string" },
  };
  assert.deepEqual(queryableProperties(properties).map((item) => item.name), ["id", "nsites"]);
});

test("conditions compile per fulltype, AND together, and carry the kind clause", () => {
  const string = { "x-optimade-type": "string" };
  const integer = { "x-optimade-type": "integer" };
  const timestamp = { "x-optimade-type": "timestamp" };
  const list = { "x-optimade-type": "list", items: { "x-optimade-type": "string" } };
  assert.equal(compileCondition({ property: "name", operator: "STARTS WITH", value: 'x"y', definition: string }), 'name STARTS WITH "x\\"y"');
  assert.equal(compileCondition({ property: "nsites", operator: ">=", value: "3", definition: integer }), "nsites >= 3");
  assert.equal(compileCondition({ property: "t", operator: "<", value: "2026-01-01T00:00:00Z", definition: timestamp }), 't < "2026-01-01T00:00:00Z"');
  assert.equal(compileCondition({ property: "elements", operator: "HAS ANY", value: "Si, O", definition: list }), 'elements HAS ANY "Si", "O"');
  assert.equal(compileCondition({ property: "x", operator: "IS UNKNOWN", definition: string }), "x IS UNKNOWN");
  assert.equal(compileCondition({ property: "", operator: "=", value: "1", definition: string }), null);
  assert.equal(compileCondition({ property: "name", operator: "=", value: " ", definition: string }), null);
  assert.throws(() => compileCondition({ property: "nsites", operator: "=", value: "1 OR 1=1", definition: integer }));
  assert.throws(() => compileCondition({ property: "nsites", operator: "CONTAINS", value: "1", definition: integer }));
  assert.throws(() => compileCondition({ property: "a b", operator: "IS KNOWN", definition: string }));

  assert.equal(elementsClause("Si O,Fe"), 'elements HAS ALL "Si", "O", "Fe"');
  assert.equal(elementsClause("  "), null);
  const rows = [{ property: "nsites", operator: ">", value: "2", definition: integer }, { property: "", operator: "", value: "" }];
  assert.equal(compileFilter(rows, { elements: "Si" }), 'nsites > 2 AND elements HAS ALL "Si"');
  assert.equal(compileFilter([], {}), null);
  assert.equal(compileFilter([], { kind: "_httk_total_energy" }), "_httk_total_energy IS KNOWN");
  assert.equal(compileFilter(rows, { kind: "_k" }), "_k IS KNOWN AND (nsites > 2)");
});

test("withKind ANDs the kind clause into raw filters exactly once", () => {
  assert.equal(withKind('a = 1 OR b = 2', "_k"), "_k IS KNOWN AND (a = 1 OR b = 2)");
  assert.equal(withKind("_k IS KNOWN AND (a = 1)", "_k"), "_k IS KNOWN AND (a = 1)");
  assert.equal(withKind('_k IS KNOWN AND (a = ")(")', "_k"), '_k IS KNOWN AND (a = ")(")');
  // AND binds tighter than OR: a leading clause alone does not restrict the whole filter.
  assert.equal(withKind("_k IS KNOWN AND a = 1 OR b = 2", "_k"), "_k IS KNOWN AND (_k IS KNOWN AND a = 1 OR b = 2)");
  assert.equal(withKind("_k IS KNOWN AND (a = 1) OR (b = 2)", "_k"), "_k IS KNOWN AND (_k IS KNOWN AND (a = 1) OR (b = 2))");
  // Idempotent on its own output, so the browser's normalizing reload happens at most once.
  const once = withKind("_k IS KNOWN AND (a = 1) OR (b = 2)", "_k");
  assert.equal(withKind(once, "_k"), once);
  assert.equal(withKind("_k IS KNOWN", "_k"), "_k IS KNOWN");
  assert.equal(withKind("", "_k"), "_k IS KNOWN");
  assert.equal(withKind(" a = 1 ", null), "a = 1");
  assert.equal(withKind("", null), null);
});

test("URL policy links only credential-free http(s) and same-origin root-relative URLs", () => {
  const link = (value) => linkPolicy(value, PAGE, ORIGIN);
  assert.deepEqual(link("https://ok.example/f"), { kind: "link", href: "https://ok.example/f" });
  assert.deepEqual(link("/data/ws/x"), { kind: "link", href: "https://site.example.test/data/ws/x" });
  assert.deepEqual(link("ni:///sha-256;abc"), { kind: "text", text: "sha-256;abc" });
  for (const value of ["/\\evil.example/x", "javascript:alert(1)", "data:text/html,x", "//evil.example", "data/ws/x", "https://u:p@ok.example/f", "ftp://ok.example/f", "https://ok.example/a b"]) {
    assert.equal(link(value).kind, "text", value);
  }
  assert.equal(link("javascript:alert(1)").text, "javascript:alert(1)");
});

test("DOI links come only from validated DOI strings, encoded per segment", () => {
  assert.equal(doiUrl("10.1103/PhysRevB.1#2"), "https://doi.org/10.1103/PhysRevB.1%232");
  assert.equal(doiUrl("10.1000/a/b c"), null);
  for (const bad of ["javascript:alert(1)", "11.1000/x", "10.12/x", 10.1]) assert.equal(doiUrl(bad), null);
});

test("alternative ids split at the last ~ with a valid kind", () => {
  assert.deepEqual(splitAlternativeId("httk-1-1~file_ab"), { main: "httk-1-1", kind: "file_ab" });
  assert.deepEqual(splitAlternativeId("a~b~conventional"), { main: "a~b", kind: "conventional" });
  for (const id of ["plain", "~kind", "a~", "a~Kind", "a~1x", "a~b-c", null]) assert.equal(splitAlternativeId(id), null);
});

test("relationship headings group provenance, references and the rest", () => {
  assert.equal(relationshipHeading("_httk_has_input", "_httk_runs").heading, "Inputs");
  assert.equal(relationshipHeading("_httk_has_artifact", "_httk_runs").heading, "Artifacts");
  assert.equal(relationshipHeading("_httk_has_output", "_httk_runs").heading, "Outputs");
  assert.equal(relationshipHeading("_httk_is_input", "_httk_runs").heading, "Followed by");
  assert.equal(relationshipHeading("_httk_is_input", "files").heading, "Used by");
  assert.equal(relationshipHeading("_httk_is_output", "files").heading, "Produced by");
  assert.equal(relationshipHeading("_httk_is_artifact", "files").heading, "Produced by");
  assert.equal(relationshipHeading("_httk_product_of", "_httk_records").heading, "Product of");
  assert.equal(relationshipHeading("_httk_has_product", "_httk_records").heading, "Products");
  assert.deepEqual(relationshipHeading("references", "structures"), { group: "references", heading: "References", order: 0 });
  assert.deepEqual(relationshipHeading("files", "structures"), { group: "related", heading: "files", order: 0 });
});

test("best label prefers name-like attributes, else the id", () => {
  assert.equal(bestLabel({ id: "x", attributes: { name: "OUTCAR", _httk_name: "relax" } }), "relax");
  assert.equal(bestLabel({ id: "x", attributes: { chemical_formula_reduced: "NaCl", title: " " } }), "NaCl");
  assert.equal(bestLabel({ id: "x", attributes: { title: 3 } }), "x");
});

test("values, sizes, text and lists are formatted and bounded", () => {
  assert.equal(formatValue(-1.5, "eV"), "-1.5 eV");
  assert.equal(formatValue(null, "eV"), "—");
  assert.equal(formatValue(true), "true");
  assert.equal(formatValue(["Si", "O"]), "Si, O");
  assert.equal(formatValue({ a: 1 }), '{"a":1}');
  assert.match(formatValue(Array.from({ length: 205 }, (_, index) => index)), /, … 5 more$/);
  assert.equal(humanSize(12), "12 B");
  assert.equal(humanSize(1536), "1.5 KiB");
  assert.equal(humanSize(-1), "");
  assert.equal(boundedText("abcdef", 4), "abc…");
  assert.equal(boundedText("a\u0000b"), "a b");
  assert.deepEqual(capList([1, 2, 3], 2), { items: [1, 2], more: 1 });
  assert.equal(firstSentence("Total energy. More text.\n\nSecond."), "Total energy.");
  assert.equal(firstSentence("v1.2 is fine and long"), "v1.2 is fine and long");
});

test("labels, catalog order, record kinds and identifiers", () => {
  assert.equal(typeLabel("_httk_runs"), "Runs (provenance)");
  assert.equal(typeLabel("_exmpl_things"), "_exmpl_things");
  assert.deepEqual(catalogOrder(["files", "zeta", "_httk_runs", "structures", "alpha", "_httk_records", "references", "calculations"]),
    ["_httk_records", "structures", "calculations", "references", "_httk_runs", "files", "alpha", "zeta"]);
  const kinds = recordKinds({
    id: {}, type: {}, immutable_id: {}, last_modified: {},
    _httk_total_energy: { description: "The total energy. Long.", "x-optimade-unit": "eV" },
    _httk_count: { description: "A count", "x-optimade-unit": "dimensionless" },
  });
  assert.deepEqual(kinds, [
    { name: "_httk_count", title: "A count", unit: "" },
    { name: "_httk_total_energy", title: "The total energy.", unit: "eV" },
  ]);
  assert.equal(isIdentifier("_httk_records"), true);
  for (const bad of ["Files", "a-b", "", "x".repeat(129), null]) assert.equal(isIdentifier(bad), false);
});

test("route links are same-origin and query-encoded", () => {
  assert.equal(routeHref("entry", { type: "files", id: "a&b", skip: null }, PAGE), "https://site.example.test/entry?type=files&id=a%26b");
  assert.throws(() => routeHref("https://evil.example/entry", {}, PAGE));
});
