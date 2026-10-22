/**
 * Pure presentation helpers shared by the OPTIMADE catalog, browser and entry widgets.
 *
 * No DOM, storage or network access: everything here is unit-testable in Node.
 */

/** Largest number of items rendered from any one served list. */
export const MAX_LIST_ITEMS = 200;
/** Largest number of characters rendered from any one served string. */
export const MAX_TEXT_CHARS = 4_096;
/** Entry and record-kind names accepted from URLs. */
export const IDENTIFIER = /^[a-z_][a-z0-9_]*$/;
/** The httk alternative-kind suffix grammar. */
export const ALTERNATIVE_KIND = /^[a-z][a-z0-9_]*$/;
/** Properties every entry carries; anything else is the entry's own data. */
export const INTRINSIC = new Set(["id", "type", "immutable_id", "last_modified"]);

const TYPE_LABELS = {
  structures: "Structures",
  calculations: "Calculations",
  references: "References",
  files: "Files",
  _httk_runs: "Runs (provenance)",
  _httk_records: "Result records",
};
const CATALOG_ORDER = ["_httk_records", "structures", "calculations", "references", "_httk_runs", "files"];
const STRING_OPERATORS = ["=", "!=", "CONTAINS", "STARTS WITH", "ENDS WITH"];
const ORDERED_OPERATORS = ["=", "!=", "<", "<=", ">", ">="];
const LIST_OPERATORS = ["HAS", "HAS ALL", "HAS ANY"];
const PRESENCE_OPERATORS = ["IS KNOWN", "IS UNKNOWN"];
const DECIMAL = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$/;
const DOI = /^10\.\d{4,9}\/\S+$/;
const LABEL_FIELDS = ["_httk_name", "name", "title", "chemical_formula_descriptive", "chemical_formula_reduced"];
const UNITLESS = new Set(["", "inapplicable", "dimensionless"]);

/** True for a bounded URL-safe entry/kind identifier. */
export function isIdentifier(value) {
  return typeof value === "string" && value.length <= 128 && IDENTIFIER.test(value);
}

/** Human label for an entry type; unknown types keep their name. */
export function typeLabel(type) {
  return Object.hasOwn(TYPE_LABELS, type) ? TYPE_LABELS[type] : String(type);
}

/** Catalog order: result records, structures, calculations, references, runs, files, then others by name. */
export function catalogOrder(types) {
  const rank = (type) => {
    const index = CATALOG_ORDER.indexOf(type);
    return index < 0 ? CATALOG_ORDER.length : index;
  };
  return [...new Set(types)].sort((a, b) => rank(a) - rank(b) || (a < b ? -1 : a > b ? 1 : 0));
}

/** Bound a value to plain display text without control characters. */
export function boundedText(value, maximum = MAX_TEXT_CHARS) {
  const text = String(value ?? "").replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, " ");
  return text.length > maximum ? `${text.slice(0, maximum - 1)}…` : text;
}

/** Cap a served list: the first MAX_LIST_ITEMS items plus how many were left out. */
export function capList(values, maximum = MAX_LIST_ITEMS) {
  const list = Array.isArray(values) ? values : [];
  return { items: list.slice(0, maximum), more: Math.max(0, list.length - maximum) };
}

/** The first sentence (or first paragraph line) of a description, bounded. */
export function firstSentence(text, maximum = 240) {
  if (typeof text !== "string") return "";
  const paragraph = text.trim().split(/\n\s*\n/, 1)[0].replace(/\s+/g, " ");
  const match = /^.*?[.!?](?=\s|$)/.exec(paragraph);
  return boundedText(match ? match[0] : paragraph, maximum);
}

/** The display unit of a property definition, or "" when it has none. */
export function unitOf(definition) {
  const unit = definition?.["x-optimade-unit"];
  return typeof unit === "string" && !UNITLESS.has(unit) ? boundedText(unit, 64) : "";
}

/** The OPTIMADE fulltype of a property definition (x-optimade-type, else the JSON-schema type). */
export function fulltypeOf(definition) {
  const declared = definition?.["x-optimade-type"];
  if (typeof declared === "string") return declared;
  const types = [definition?.type].flat().filter((type) => typeof type === "string" && type !== "null");
  const type = types[0];
  if (type === "array") return "list";
  if (type === "object") return "dictionary";
  if (type === "number") return "float";
  if (type === "string" && definition?.format === "date-time") return "timestamp";
  return type ?? "unknown";
}

/** Whether a property may be filtered on: its query-support is anything but "none". */
export function isQueryable(definition) {
  return definition?.["x-optimade-requirements"]?.["query-support"] !== "none";
}

/** Filter operators offered for one property definition. */
export function operatorsFor(definition) {
  const fulltype = fulltypeOf(definition);
  if (fulltype === "string") return [...STRING_OPERATORS, ...PRESENCE_OPERATORS];
  if (fulltype === "integer" || fulltype === "float" || fulltype === "timestamp") return [...ORDERED_OPERATORS, ...PRESENCE_OPERATORS];
  if (fulltype === "list") return [...LIST_OPERATORS, ...PRESENCE_OPERATORS];
  return [...PRESENCE_OPERATORS];
}

/** The queryable properties of an /info/<type> properties object, sorted, with their operators. */
export function queryableProperties(properties) {
  if (!properties || typeof properties !== "object") return [];
  return Object.keys(properties)
    .filter((name) => name !== "type" && isQueryable(properties[name]))
    .sort()
    .map((name) => ({ name, definition: properties[name], operators: operatorsFor(properties[name]) }));
}

/** Record kinds: the non-intrinsic properties of /info/_httk_records, with title and unit. */
export function recordKinds(properties) {
  if (!properties || typeof properties !== "object") return [];
  return Object.keys(properties)
    .filter((name) => !INTRINSIC.has(name) && isIdentifier(name))
    .sort()
    .map((name) => ({ name, title: firstSentence(properties[name]?.description) || name, unit: unitOf(properties[name]) }));
}

/** An OPTIMADE string literal with backslash and double quote escaped. */
export function stringLiteral(value) {
  return `"${String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

/** A finite decimal OPTIMADE number literal from user text; throws on anything else. */
export function numberLiteral(value) {
  const text = String(value ?? "").trim();
  if (!DECIMAL.test(text) || !Number.isFinite(Number(text))) throw new Error(`"${boundedText(text, 40)}" is not a finite number.`);
  return text;
}

function literalFor(value, fulltype) {
  return fulltype === "integer" || fulltype === "float" ? numberLiteral(value) : stringLiteral(value);
}

function listValues(value) {
  return String(value ?? "").split(",").map((item) => item.trim()).filter(Boolean);
}

/**
 * Compile one condition row `{property, operator, value, definition}` to an OPTIMADE clause.
 * Returns null for an incomplete row; throws for an invalid value or operator.
 */
export function compileCondition({ property, operator, value = "", definition = {} }) {
  if (!property) return null;
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(property)) throw new Error("Invalid property name.");
  if (!operatorsFor(definition).includes(operator)) throw new Error(`Operator ${operator} does not apply to ${property}.`);
  if (PRESENCE_OPERATORS.includes(operator)) return `${property} ${operator}`;
  const text = String(value ?? "").trim();
  if (!text) return null;
  if (LIST_OPERATORS.includes(operator)) {
    const itemType = fulltypeOf(definition?.items ?? {});
    const values = operator === "HAS" ? [text] : listValues(text);
    if (!values.length) return null;
    return `${property} ${operator} ${values.map((item) => literalFor(item, itemType)).join(", ")}`;
  }
  return `${property} ${operator} ${literalFor(text, fulltypeOf(definition))}`;
}

/** The "Elements" shortcut: HAS ALL over comma- or space-separated symbols, or null when empty. */
export function elementsClause(value) {
  const symbols = String(value ?? "").split(/[\s,]+/).filter(Boolean);
  return symbols.length ? `elements HAS ALL ${symbols.map(stringLiteral).join(", ")}` : null;
}

/** True when text is one parenthesized group: its opening "(" closes only at the last character. */
function wholeGroup(text) {
  if (!text.startsWith("(") || !text.endsWith(")")) return false;
  let depth = 0;
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === "\\") index += 1;
      else if (char === '"') quoted = false;
    } else if (char === '"') quoted = true;
    else if (char === "(") depth += 1;
    else if (char === ")" && (depth -= 1) === 0 && index < text.length - 1) return false;
  }
  return depth === 0 && !quoted;
}

/** AND a record-kind presence clause into a filter, recognising only the exact forms this emits. */
export function withKind(filter, kind) {
  const text = typeof filter === "string" ? filter.trim() : "";
  if (!kind) return text || null;
  const clause = `${kind} IS KNOWN`;
  const prefix = `${clause} AND `;
  if (!text || text === clause) return clause;
  if (text.startsWith(prefix) && wholeGroup(text.slice(prefix.length))) return text;
  return `${clause} AND (${text})`;
}

/** Compile condition rows (AND), the elements shortcut, and the kind clause into one filter or null. */
export function compileFilter(conditions, { elements = "", kind = null } = {}) {
  const clauses = conditions.map(compileCondition).filter((clause) => clause !== null);
  const shortcut = elementsClause(elements);
  if (shortcut !== null) clauses.push(shortcut);
  return withKind(clauses.join(" AND "), kind);
}

/**
 * Decide how a served URL is shown: `{kind: "link", href}` for http(s) URLs (credential-free,
 * relative inputs only when root-relative and same-origin) or `{kind: "text", text}` otherwise;
 * `ni:` content URIs show their hash.
 */
export function linkPolicy(value, base, origin) {
  if (typeof value !== "string" || !value) return { kind: "text", text: "" };
  const text = boundedText(value, 2_048);
  if (/^ni:/i.test(value)) return { kind: "text", text: boundedText(value.replace(/^ni:(?:\/\/[^/]*\/)?/i, ""), 2_048) };
  if (value.includes("\\") || /[\u0000- \u007f]/.test(value)) return { kind: "text", text };
  const absolute = /^[A-Za-z][A-Za-z0-9+.-]*:/.test(value);
  if (!absolute && (!value.startsWith("/") || value.startsWith("//"))) return { kind: "text", text };
  let url;
  try {
    url = new URL(value, base);
  } catch {
    return { kind: "text", text };
  }
  if ((url.protocol !== "http:" && url.protocol !== "https:") || url.username || url.password) return { kind: "text", text };
  if (!absolute && url.origin !== origin) return { kind: "text", text };
  return { kind: "link", href: url.href };
}

/** A https://doi.org link for a validated DOI string, else null. */
export function doiUrl(value) {
  if (typeof value !== "string" || !DOI.test(value)) return null;
  return `https://doi.org/${value.split("/").map(encodeURIComponent).join("/")}`;
}

/** Split `<main>~<kind>` at the last `~`; null unless both parts are valid. */
export function splitAlternativeId(id) {
  if (typeof id !== "string") return null;
  const index = id.lastIndexOf("~");
  if (index <= 0) return null;
  const kind = id.slice(index + 1);
  return ALTERNATIVE_KIND.test(kind) ? { main: id.slice(0, index), kind } : null;
}

/** Section group and heading for one relationship key on an entry of the given type. */
export function relationshipHeading(key, entryType) {
  switch (key) {
    case "_httk_has_input": return { group: "provenance", heading: "Inputs", order: 0 };
    case "_httk_has_artifact": return { group: "provenance", heading: "Artifacts", order: 1 };
    case "_httk_has_output": return { group: "provenance", heading: "Outputs", order: 2 };
    case "_httk_is_output":
    case "_httk_is_artifact": return { group: "provenance", heading: "Produced by", order: 3 };
    case "_httk_is_input":
      return entryType === "_httk_runs"
        ? { group: "provenance", heading: "Followed by", order: 4 }
        : { group: "provenance", heading: "Used by", order: 4 };
    case "_httk_product_of": return { group: "provenance", heading: "Product of", order: 5 };
    case "_httk_has_product": return { group: "provenance", heading: "Products", order: 6 };
    case "references": return { group: "references", heading: "References", order: 0 };
    default: return { group: "related", heading: boundedText(key, 128), order: 0 };
  }
}

/** The best human label of a resource: name-like attributes first, else its id. */
export function bestLabel(resource) {
  const attributes = resource?.attributes ?? {};
  for (const field of LABEL_FIELDS) {
    if (typeof attributes[field] === "string" && attributes[field].trim()) return boundedText(attributes[field].trim(), 256);
  }
  return boundedText(resource?.id ?? "", 256);
}

/** Compact binary byte size ("" when unavailable). */
export function humanSize(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${size} B` : `${size.toFixed(1)} ${units[unit]}`;
}

/** Scalar display text with an optional unit; lists of scalars are joined; null is an em dash. */
export function formatValue(value, unit = "") {
  if (value === null || value === undefined) return "—";
  let text;
  if (typeof value === "number") text = Number.isFinite(value) ? String(value) : "—";
  else if (typeof value === "boolean") text = value ? "true" : "false";
  else if (typeof value === "string") text = value;
  else if (Array.isArray(value) && value.every((item) => item === null || typeof item !== "object")) {
    const { items, more } = capList(value);
    text = items.map((item) => formatValue(item)).join(", ") + (more ? `, … ${more} more` : "");
  } else text = JSON.stringify(value);
  const suffix = unit && text !== "—" ? ` ${unit}` : "";
  return boundedText(text + suffix);
}

/** A same-origin site URL `route?type=…&id=…` (or any parameters), resolved against base. */
export function routeHref(route, parameters, base) {
  const url = new URL(route, base);
  if (url.origin !== new URL(base).origin) throw new Error("Site route is not same-origin.");
  for (const [name, value] of Object.entries(parameters)) {
    if (value !== null && value !== undefined) url.searchParams.set(name, value);
  }
  return url.href;
}
