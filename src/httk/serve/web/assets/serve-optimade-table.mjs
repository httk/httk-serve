import { OptimadeHttpError, OptimadeProtocolError, OptimadeTransport } from "./serve-optimade-table-protocol.mjs";

const MAX_FILTER_CHARS = 4_096;
const MAX_CELL_CHARS = 4_096;
const MAX_PREVIOUS_PAGES = 100;
let discoveryCaches = new WeakMap();
const controllers = new WeakMap();

/** Return the filter selected by the document URL, without composing filters. */
export function effectiveFilter(configuration, location = globalThis.location) {
  const authored = optionalString(configuration.filter, "filter");
  if (configuration.filter_query === null || configuration.filter_query === undefined) return authored;
  if (typeof configuration.filter_query !== "string" || !configuration.filter_query) {
    throw new Error("OPTIMADE table filter_query is invalid");
  }
  const parameters = new URLSearchParams(location?.search ?? "");
  if (!parameters.has(configuration.filter_query)) return authored;
  const value = parameters.get(configuration.filter_query) ?? "";
  if (value.length > MAX_FILTER_CHARS) throw new Error("The OPTIMADE filter in this URL is longer than 4096 characters.");
  return value || null;
}

/** Return the sort selected by the document URL, without composing sorts. */
/** Resolve a display sort value through the configured alias map before it reaches OPTIMADE. */
export function resolveSortAlias(value, aliases) {
  if (value === null || !aliases || typeof aliases !== "object") return value;
  return Object.prototype.hasOwnProperty.call(aliases, value) ? aliases[value] : value;
}

export function effectiveSort(configuration, location = globalThis.location) {
  const authored = optionalString(configuration.sort, "sort");
  if (configuration.sort_query === null || configuration.sort_query === undefined) {
    return resolveSortAlias(authored, configuration.sort_aliases);
  }
  if (typeof configuration.sort_query !== "string" || !configuration.sort_query) {
    throw new Error("OPTIMADE table sort_query is invalid");
  }
  const parameters = new URLSearchParams(location?.search ?? "");
  if (!parameters.has(configuration.sort_query)) return resolveSortAlias(authored, configuration.sort_aliases);
  const value = parameters.get(configuration.sort_query) ?? "";
  if (value.length > MAX_FILTER_CHARS) throw new Error("The OPTIMADE sort in this URL is longer than 4096 characters.");
  return resolveSortAlias(value || null, configuration.sort_aliases);
}

/** A stable key for work that discovers an OPTIMADE API, not a table page. */
export function discoveryCacheKey(configuration, documentBase = globalThis.document?.baseURI, bodyLimit = undefined) {
  const base = new URL(configuration.base_url, documentBase).href;
  const fields = configuration.columns.map((column) => typeof column === "string" ? column : column.key);
  const origins = [...(configuration.allowed_origins ?? [])].sort();
  return JSON.stringify([base, configuration.entry_type, fields, origins, bodyLimit ?? null]);
}

/** Presentation-only conversion for JSON values. It never changes source data. */
export function formatCellValue(value, maximum = MAX_CELL_CHARS) {
  let text;
  if (value === null || value === undefined) return { text: "—", missing: true, truncated: false };
  if (typeof value === "string") text = value;
  else if (typeof value === "number" || typeof value === "boolean") text = String(value);
  else {
    try {
      text = JSON.stringify(normalizeJson(value));
    } catch {
      text = "[unavailable value]";
    }
  }
  if (text.length > maximum) return { text: `${text.slice(0, Math.max(0, maximum - 1))}…`, missing: false, truncated: true };
  return { text, missing: false, truncated: false };
}

const SUMMARY_KEYWORDS = new Set(["AND", "OR", "NOT", "HAS", "ALL", "ANY", "ONLY", "CONTAINS", "STARTS", "ENDS", "WITH"]);
const SUMMARY_OP_PREFIX = { "!=": "≠ ", "<=": "≤ ", ">=": "≥ ", "<": "< ", ">": "> " };
const SUMMARY_PILL_MAX_CHARS = 256;

/** Shared presentation-only number formatting; returns null when scaling overflows. */
function formatNumberValue(value, format) {
  const scaled = value * format.scale;
  if (!Number.isFinite(scaled)) return null;
  return `${scaled.toFixed(format.digits)}${format.suffix}`;
}

/** Tokenize an OPTIMADE filter, honouring double-quoted strings; null if unrecognizable. */
function tokenizeFilter(filter) {
  const tokens = [];
  let i = 0;
  while (i < filter.length) {
    const c = filter[i];
    if (c === " " || c === "\t" || c === "\n" || c === "\r") { i += 1; continue; }
    if (c === '"') {
      let value = "";
      let closed = false;
      i += 1;
      while (i < filter.length) {
        const ch = filter[i];
        if (ch === "\\" && (filter[i + 1] === '"' || filter[i + 1] === "\\")) { value += filter[i + 1]; i += 2; continue; }
        if (ch === '"') { closed = true; i += 1; break; }
        value += ch; i += 1;
      }
      if (!closed) return null;
      tokens.push({ type: "string", value });
      continue;
    }
    if (c === "(" || c === ")") return null;
    if (c === ",") { tokens.push({ type: "comma" }); i += 1; continue; }
    const pair = filter.slice(i, i + 2);
    if (pair === "<=" || pair === ">=" || pair === "!=") { tokens.push({ type: "op", value: pair }); i += 2; continue; }
    if (c === "<" || c === ">" || c === "=") { tokens.push({ type: "op", value: c }); i += 1; continue; }
    const number = /^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?/.exec(filter.slice(i));
    if (number && /\d/.test(number[0])) { tokens.push({ type: "number", value: number[0] }); i += number[0].length; continue; }
    const word = /^[A-Za-z_][A-Za-z0-9_.]*/.exec(filter.slice(i));
    if (word) { tokens.push({ type: "word", value: word[0] }); i += word[0].length; continue; }
    return null;
  }
  return tokens;
}

function literalValue(token) {
  if (!token) return null;
  if (token.type === "string") return { type: "string", value: token.value };
  if (token.type === "number") {
    const n = Number(token.value);
    return Number.isFinite(n) ? { type: "number", value: n } : null;
  }
  return null;
}

function stringLiteral(token) {
  return token?.type === "string" ? { type: "string", value: token.value } : null;
}

function literalList(tokens) {
  if (!tokens.length) return null;
  const values = [];
  let wantLiteral = true;
  for (const token of tokens) {
    if (wantLiteral) {
      const value = literalValue(token);
      if (value === null) return null;
      values.push(value);
    } else if (token.type !== "comma") return null;
    wantLiteral = !wantLiteral;
  }
  return wantLiteral ? null : values;
}

function parseFilterClause(tokens) {
  const [first, ...rest] = tokens;
  if (!first || first.type !== "word" || SUMMARY_KEYWORDS.has(first.value)) return null;
  const property = first.value;
  const op = rest[0];
  if (!op) return null;
  if (op.type === "op") {
    const literal = literalValue(rest[1]);
    if (literal === null || rest.length !== 2) return null;
    return { property, op: op.value, values: [literal] };
  }
  if (op.type !== "word") return null;
  if (op.value === "CONTAINS") {
    const literal = stringLiteral(rest[1]);
    if (literal === null || rest.length !== 2) return null;
    return { property, op: "CONTAINS", values: [literal] };
  }
  if (op.value === "STARTS" || op.value === "ENDS") {
    if (rest[1]?.type !== "word" || rest[1].value !== "WITH" || rest.length !== 3) return null;
    const literal = stringLiteral(rest[2]);
    if (literal === null) return null;
    return { property, op: `${op.value} WITH`, values: [literal] };
  }
  if (op.value === "HAS") {
    const quantifier = rest[1]?.type === "word" && (rest[1].value === "ALL" || rest[1].value === "ANY" || rest[1].value === "ONLY") ? rest[1].value : null;
    const values = literalList(rest.slice(quantifier ? 2 : 1));
    if (values === null) return null;
    if (!quantifier) return values.length === 1 ? { property, op: "HAS", values } : null;
    return { property, op: `HAS ${quantifier}`, values };
  }
  return null;
}

/** Parse a filter into human-describable clauses, or null if any part is unrepresentable. */
export function parseFilterPills(filter) {
  if (filter === null || filter === undefined || filter === "") return [];
  const tokens = tokenizeFilter(filter);
  if (tokens === null) return null;
  if (!tokens.length) return [];
  const clauses = [[]];
  for (const token of tokens) {
    if (token.type === "word" && (token.value === "OR" || token.value === "NOT")) return null;
    if (token.type === "word" && token.value === "AND") { clauses.push([]); continue; }
    clauses.at(-1).push(token);
  }
  const pills = [];
  for (const clause of clauses) {
    const pill = parseFilterClause(clause);
    if (pill === null) return null;
    pills.push(pill);
  }
  return pills;
}

/** Parse a sort into described components, dropping id tiebreakers; null when nothing meaningful remains. */
export function parseSortPill(sort, defaultSort) {
  if (sort === null || sort === undefined || sort === "") return null;
  if (defaultSort !== null && defaultSort !== undefined && sort === defaultSort) return null;
  const components = [];
  for (const raw of sort.split(",")) {
    const token = raw.trim();
    if (!token) continue;
    const descending = token.startsWith("-");
    const property = descending ? token.slice(1) : token;
    if (!property || property === "id") continue;
    components.push({ property, descending });
  }
  return components.length ? components : null;
}

/** Split a sort into {property, descending} components, KEEPING id (unlike the pill parser). */
export function sortComponents(sort) {
  if (typeof sort !== "string") return [];
  const components = [];
  for (const raw of sort.split(",")) {
    const token = raw.trim();
    if (!token) continue;
    const descending = token.startsWith("-");
    const property = descending ? token.slice(1) : token;
    if (property) components.push({ property, descending });
  }
  return components;
}

/**
 * The relative href for clicking a column header to sort by it.
 *
 * Toggles direction when the column is already the primary sort (ascending →
 * descending, descending → ascending) and otherwise defaults to ascending. An
 * `,id` tiebreaker is always appended except for the `id` column itself. Only
 * the sort parameter is changed; every other URL parameter is preserved.
 */
export function sortHref(columnKey, currentSortComponents, sortQuery, search) {
  const primary = Array.isArray(currentSortComponents) ? currentSortComponents[0] : null;
  const descending = primary?.property === columnKey && primary.descending === false;
  const value = `${descending ? "-" : ""}${columnKey}${columnKey === "id" ? "" : ",id"}`;
  const params = new URLSearchParams(typeof search === "string" ? search : "");
  params.set(sortQuery, value);
  return `?${params.toString()}`;
}

function pillValue(literal, fieldSpec) {
  if (literal.type === "number") {
    if (fieldSpec?.format?.name === "number") {
      const text = formatNumberValue(literal.value, fieldSpec.format);
      if (text !== null) return text;
    }
    return String(literal.value);
  }
  const values = fieldSpec?.values;
  if (values && typeof values === "object" && Object.prototype.hasOwnProperty.call(values, literal.value)) return values[literal.value];
  return literal.value;
}

function present(count) {
  return count !== null && count !== undefined;
}

/** Compose the count sentence, or null when there is no meaningful count to show. */
function summaryCountSentence(filterActive, dataReturned, dataAvailable, noun) {
  if (filterActive) {
    if (present(dataReturned) && present(dataAvailable)) return `Showing ${dataReturned} of ${dataAvailable} ${noun}.`;
    if (present(dataReturned)) return `Showing ${dataReturned} ${noun}.`;
    return null;
  }
  const total = present(dataAvailable) ? dataAvailable : dataReturned;
  return present(total) ? `Showing all ${total} ${noun}.` : null;
}

/** Build one pill span with a bold label and a plain text value (createElement/text only). */
function summaryPill(document, label, text) {
  const pill = document.createElement("span");
  pill.className = "httk-serve-optimade-table__pill";
  const strong = document.createElement("strong");
  strong.append(document.createTextNode(label));
  pill.append(strong, document.createTextNode(` ${text}`));
  return pill;
}

/** Render a filter clause into a human label and value text using a summary field spec. */
export function pillParts(clause, fieldSpec) {
  const label = fieldSpec?.label ?? clause.property;
  const joined = clause.values.map((value) => pillValue(value, fieldSpec)).join(", ");
  // Cap pill text like a table cell so a crafted URL filter cannot inflate the widget.
  const text = formatCellValue(`${SUMMARY_OP_PREFIX[clause.op] ?? ""}${joined}`, SUMMARY_PILL_MAX_CHARS).text;
  return { label, text };
}

/** Rebuild the summary element idempotently from precomputed pills and page counts. */
export function renderSummary(element, document, summary, pills, filterActive, page) {
  const fields = summary.fields ?? {};
  element.replaceChildren();
  let rendered = false;
  const sentence = summaryCountSentence(filterActive, page.dataReturned, page.dataAvailable, summary.noun);
  if (sentence) {
    const line = document.createElement("p");
    line.className = "httk-serve-optimade-table__summary-count";
    line.textContent = sentence;
    element.append(line);
    rendered = true;
  }
  for (const clause of pills?.filter ?? []) {
    const { label, text } = pillParts(clause, fields[clause.property]);
    element.append(summaryPill(document, label, text));
    rendered = true;
  }
  if (pills?.sort) {
    const text = pills.sort
      .map((component) => `${fields[component.property]?.label ?? component.property} ${component.descending ? "↓" : "↑"}`)
      .join(", ");
    element.append(summaryPill(document, "Sorted by", text));
    rendered = true;
  }
  element.hidden = !rendered;
}

/** Install a controller for one shell. Calling this again returns the original controller. */
export function installOptimadeTable(shell, options = {}) {
  if (controllers.has(shell)) return controllers.get(shell);
  let view;
  let configuration;
  let summaryPills = null;
  try {
    view = requiredShell(shell);
    configuration = readConfiguration(shell, options.document ?? globalThis.document);
    validateConfiguration(configuration);
    const location = options.location ?? globalThis.location;
    // The authored default is alias-resolved just like the effective sort, so an
    // aliased default compares resolved-to-resolved and its pill is suppressed.
    const authoredSort = resolveSortAlias(configuration.sort ?? null, configuration.sort_aliases);
    const filter = effectiveFilter(configuration, location);
    const sort = effectiveSort(configuration, location);
    if (configuration.summary) summaryPills = { filter: parseFilterPills(filter), sort: parseSortPill(sort, authoredSort) };
    configuration = { ...configuration, filter, sort };
  } catch (error) {
    failInstallation(shell, view, messageFor(error));
    return null;
  }
  let controller;
  try {
    controller = new OptimadeTableController(shell, view, configuration, { ...options, summaryPills });
  } catch (error) {
    failInstallation(shell, view, messageFor(error));
    return null;
  }
  controllers.set(shell, controller);
  controller.reload();
  return controller;
}

/** Install every shell under root (or the document). */
export function installOptimadeTables(root = globalThis.document, options = {}) {
  if (!root?.querySelectorAll) return [];
  return [...root.querySelectorAll("[data-httk-serve-optimade-table]")]
    .map((shell) => installOptimadeTable(shell, options))
    .filter((controller) => controller !== null);
}

/** Test and enhancement hook; it intentionally reveals no page URLs. */
export function controllerFor(shell) {
  return controllers.get(shell) ?? null;
}

/** Isolate unit tests without making cache lifetime a public deployment feature. */
export function resetDiscoveryCacheForTests() {
  discoveryCaches = new WeakMap();
}

export class OptimadeTableController {
  #shell;
  #view;
  #configuration;
  #document;
  #fetch;
  #bodyLimit;
  #documentBase;
  #transport;
  #previousUrls;
  #currentUrl;
  #nextUrl;
  #pageIndex;
  #generation;
  #abortController;
  #lastRequest;
  #summaryElement;
  #summaryPills;
  #search;
  #headersDecorated;

  constructor(shell, view, configuration, options) {
    this.#shell = shell;
    this.#view = view;
    this.#configuration = configuration;
    this.#document = options.document ?? globalThis.document;
    this.#summaryPills = options.summaryPills ?? null;
    // A missing summary element degrades to no summary; it never kills the table.
    this.#summaryElement = configuration.summary ? (shell.querySelector?.("[data-httk-serve-optimade-summary]") ?? null) : null;
    this.#fetch = options.fetch ?? globalThis.fetch;
    this.#bodyLimit = options.bodyLimit;
    this.#documentBase = options.documentBase ?? this.#document?.baseURI;
    this.#transport = new OptimadeTransport(configuration, { fetch: this.#fetch, documentBase: this.#documentBase, bodyLimit: this.#bodyLimit });
    this.#previousUrls = [];
    this.#currentUrl = null;
    this.#nextUrl = null;
    this.#pageIndex = 0;
    this.#generation = 0;
    this.#abortController = null;
    this.#lastRequest = { kind: "initial", url: null };
    // The header sort links navigate to the current URL with only the sort param changed.
    this.#search = (options.location ?? globalThis.location)?.search ?? "";
    this.#headersDecorated = false;
    view.previous.addEventListener("click", () => this.previous());
    view.next.addEventListener("click", () => this.next());
    this.#setupAdvancedForm();
  }

  /** Prefill the advanced-filter input and carry the raw URL sort into its GET form. */
  #setupAdvancedForm() {
    try {
      const details = this.#shell.querySelector?.("[data-httk-serve-optimade-advanced]");
      if (!details) return;
      const input = details.querySelector?.("[data-httk-serve-optimade-advanced-filter]");
      // The effective filter is the URL param or authored value; assign only .value.
      if (input) input.value = this.#configuration.filter ?? "";
      const form = details.querySelector?.("form");
      const sortQuery = this.#configuration.sort_query;
      if (!form || !sortQuery) return;
      const params = new URLSearchParams(this.#search);
      // Preserve the user's raw alias in the URL, not the resolved sort.
      if (!params.has(sortQuery)) return;
      const hidden = this.#document.createElement("input");
      hidden.type = "hidden";
      hidden.name = sortQuery;
      hidden.value = params.get(sortQuery) ?? "";
      form.append(hidden);
    } catch {
      // The advanced form is an optional convenience; never fail install for it.
    }
  }

  reload() {
    this.#previousUrls = [];
    this.#currentUrl = null;
    this.#nextUrl = null;
    this.#pageIndex = 0;
    return this.#load({ kind: "initial", url: null });
  }

  next() {
    if (!this.#nextUrl) return Promise.resolve();
    return this.#load({ kind: "next", url: this.#nextUrl });
  }

  previous() {
    const url = this.#previousUrls.at(-1);
    if (!url) return Promise.resolve();
    return this.#load({ kind: "previous", url });
  }

  async #load(request) {
    this.#abortController?.abort();
    const abortController = new AbortController();
    this.#abortController = abortController;
    const generation = ++this.#generation;
    this.#lastRequest = request;
    this.#setLoading(generation);
    try {
      await this.#discover();
      if (!this.#current(generation)) return;
      const page = await this.#transport.fetchPage({
        filter: this.#configuration.filter,
        sort: this.#configuration.sort,
        nextUrl: request.url,
        signal: abortController.signal,
      });
      if (!this.#current(generation)) return;
      this.#commit(page, request, generation);
    } catch (error) {
      if (!this.#current(generation) || abortController.signal.aborted) return;
      this.#showError(messageFor(error), generation);
    }
  }

  async #discover() {
    const key = discoveryCacheKey(this.#configuration, this.#documentBase, this.#bodyLimit);
    const cache = cacheForFetch(this.#fetch);
    let promise = cache.get(key);
    if (!promise) {
      const discoveryTransport = new OptimadeTransport(this.#configuration, {
        fetch: this.#fetch, documentBase: this.#documentBase, bodyLimit: this.#bodyLimit,
      });
      promise = discoveryTransport.discover();
      cache.set(key, promise);
      promise.catch(() => {
        if (cache.get(key) === promise) cache.delete(key);
      });
    }
    const discovery = await promise;
    // The transport is deliberately public-stateful: hydrate this page-only transport
    // with reusable, validated discovery rather than binding discovery to an abort signal.
    this.#transport.apiBase = new URL(discovery.apiBaseUrl);
    this.#transport.discovery = Promise.resolve(discovery);
    this.#decorateHeaders(discovery);
  }

  /** Turn server-advertised sortable column headers into sort links, once. */
  #decorateHeaders(discovery) {
    if (this.#headersDecorated) return;
    this.#headersDecorated = true;
    try {
      const sortQuery = this.#configuration.sort_query;
      if (sortQuery === null || sortQuery === undefined) return;
      const sortable = new Set(discovery?.sortableFields ?? []);
      // Keep id components (parseSortPill drops them) so an id column header still toggles.
      const components = sortComponents(this.#configuration.sort ?? "");
      const primary = components[0] ?? null;
      const headers = this.#shell.querySelectorAll?.("thead th");
      if (!headers) return;
      const columns = this.#configuration.columns;
      for (let i = 0; i < columns.length; i += 1) {
        const th = headers[i];
        const key = columns[i]?.key;
        if (!th || typeof key !== "string" || !sortable.has(key)) continue;
        if (th.querySelector?.(".httk-serve-optimade-table__sort-link")) continue;
        const anchor = this.#document.createElement("a");
        anchor.className = "httk-serve-optimade-table__sort-link";
        anchor.href = sortHref(key, components, sortQuery, this.#search);
        while (th.firstChild) anchor.append(th.firstChild);
        th.append(anchor);
        if (primary?.property === key) th.setAttribute("aria-sort", primary.descending ? "descending" : "ascending");
      }
    } catch {
      // Header decoration is progressive enhancement; it must never fail the table.
    }
  }

  #commit(page, request, generation) {
    if (!this.#current(generation)) return;
    if (request.kind === "next" && this.#currentUrl) this.#rememberPrevious(this.#currentUrl);
    if (request.kind === "previous") this.#previousUrls.pop();
    if (request.kind === "next") this.#pageIndex += 1;
    if (request.kind === "previous") this.#pageIndex = Math.max(0, this.#pageIndex - 1);
    this.#currentUrl = page.responseUrl;
    this.#nextUrl = page.nextUrl;
    renderRows(this.#view.tbody, page.resources, this.#configuration, this.#document);
    if (!this.#current(generation)) return;
    this.#shell.setAttribute("aria-busy", "false");
    setButton(this.#view.previous, this.#previousUrls.length > 0);
    setButton(this.#view.next, this.#nextUrl !== null);
    removeRetry(this.#view);
    this.#view.status.textContent = page.resources.length ? `${page.resources.length} results loaded.` : "No OPTIMADE results found.";
    if (!page.resources.length) renderNotice(this.#view.tbody, this.#view.columns, "No OPTIMADE results found.", "empty", this.#document);
    if (this.#summaryElement) this.#renderSummary(page);
    if (!this.#current(generation)) return;
    const CustomEventForDocument = this.#document?.defaultView?.CustomEvent ?? globalThis.CustomEvent;
    this.#shell.dispatchEvent(new CustomEventForDocument("httk-serve:optimade-table-updated", {
      bubbles: true,
      detail: Object.freeze({ entryType: this.#configuration.entry_type, count: page.resources.length, pageIndex: this.#pageIndex, hasNext: this.#nextUrl !== null, hasPrevious: this.#previousUrls.length > 0 }),
    }));
  }

  #renderSummary(page) {
    const filter = this.#configuration.filter;
    const filterActive = filter !== null && filter !== undefined && filter !== "";
    renderSummary(this.#summaryElement, this.#document, this.#configuration.summary, this.#summaryPills, filterActive, page);
  }

  #rememberPrevious(url) {
    this.#previousUrls.push(url);
    if (this.#previousUrls.length > MAX_PREVIOUS_PAGES) this.#previousUrls.shift();
  }

  #setLoading(generation) {
    if (!this.#current(generation)) return;
    this.#shell.setAttribute("aria-busy", "true");
    setButton(this.#view.previous, false);
    setButton(this.#view.next, false);
    removeRetry(this.#view);
    this.#view.status.textContent = "Loading OPTIMADE results.";
  }

  #showError(message, generation) {
    if (!this.#current(generation)) return;
    renderProblem(this.#view, this.#view.columns, message, () => this.#load(this.#lastRequest));
    if (!this.#current(generation)) return;
    this.#shell.setAttribute("aria-busy", "false");
    setButton(this.#view.previous, this.#previousUrls.length > 0);
    setButton(this.#view.next, this.#nextUrl !== null);
  }

  #current(generation) {
    return generation === this.#generation;
  }
}

function readConfiguration(shell, document) {
  const id = shell.getAttribute("data-config-id");
  if (!id || !document?.getElementById) throw new Error("OPTIMADE table configuration is missing.");
  const script = document.getElementById(id);
  if (!script || script.tagName !== "SCRIPT" || script.type !== "application/json") {
    throw new Error("OPTIMADE table configuration is unavailable.");
  }
  try {
    const config = JSON.parse(script.textContent ?? "");
    if (!config || typeof config !== "object" || Array.isArray(config)) throw new Error();
    return config;
  } catch {
    throw new Error("OPTIMADE table configuration is invalid.");
  }
}

function validateConfiguration(config) {
  if (typeof config.base_url !== "string" || !config.base_url || typeof config.entry_type !== "string" || !config.entry_type || !Array.isArray(config.columns) || !config.columns.length || !Number.isSafeInteger(config.page_size) || config.page_size < 1) {
    throw new Error("OPTIMADE table configuration is incomplete.");
  }
  if (config.columns.some((column) => !column || typeof column.key !== "string" || !column.key)) {
    throw new Error("OPTIMADE table columns are invalid.");
  }
  for (const column of config.columns) {
    if (column.format === undefined) continue;
    if (column.format === "formula") continue;
    if (!column.format || typeof column.format !== "object" || Array.isArray(column.format)) {
      throw new Error("OPTIMADE table column format is invalid.");
    }
    if (column.format.name === "number") {
      if (!Number.isSafeInteger(column.format.digits) || column.format.digits < 0 || column.format.digits > 10 ||
          typeof column.format.scale !== "number" || !Number.isFinite(column.format.scale) || column.format.scale === 0 ||
          typeof column.format.suffix !== "string") throw new Error("OPTIMADE table number format is invalid.");
    } else if (column.format.name === "join") {
      if (typeof column.format.separator !== "string") throw new Error("OPTIMADE table join format is invalid.");
    } else throw new Error("OPTIMADE table column format is invalid.");
  }
  const aliases = config.sort_aliases;
  if (aliases !== undefined && aliases !== null) {
    if (typeof aliases !== "object" || Array.isArray(aliases) ||
        Object.values(aliases).some((sort) => typeof sort !== "string" || !sort)) {
      throw new Error("OPTIMADE table sort_aliases are invalid.");
    }
  }
}

function requiredShell(shell) {
  if (!shell?.querySelector) throw new Error("OPTIMADE table shell is unavailable.");
  const tbody = shell.querySelector("tbody");
  const previous = shell.querySelector("[data-httk-serve-optimade-previous]");
  const next = shell.querySelector("[data-httk-serve-optimade-next]");
  const status = shell.querySelector("[data-httk-serve-optimade-status]");
  const pager = shell.querySelector(".httk-serve-optimade-table__pager");
  const columns = shell.querySelectorAll("thead th").length;
  if (!tbody || !previous || !next || !status || !pager || !columns) throw new Error("OPTIMADE table shell is incomplete.");
  return { tbody, previous, next, status, pager, columns };
}

function setButton(button, enabled) {
  button.disabled = !enabled;
  button.setAttribute("aria-disabled", String(!enabled));
}

function renderRows(tbody, resources, configuration, document) {
  tbody.replaceChildren();
  for (const resource of resources) {
    const row = document.createElement("tr");
    for (const column of configuration.columns) {
      const value = column.key === "id" ? resource.id : column.key === "type" ? resource.type : resource.attributes[column.key];
      const rendered = formattedCellValue(value, column.format);
      const cell = document.createElement("td");
      cell.className = `httk-serve-optimade-table__cell httk-serve-optimade-table__cell--${column.align ?? "start"}`;
      if (rendered.missing) cell.setAttribute("aria-label", "No value");
      if (rendered.truncated) cell.title = "Value abbreviated for display";
      if (configuration.detail_route && configuration.detail_column === column.key) {
        const link = detailLink(configuration, resource.id, rendered, document);
        appendFormattedValue(link, rendered, document);
        cell.append(link);
      } else appendFormattedValue(cell, rendered, document);
      row.append(cell);
    }
    tbody.append(row);
  }
}

function detailLink(configuration, id, rendered, document) {
  const route = new URL(configuration.detail_route, document.baseURI);
  const local = new URL(document.baseURI);
  if (route.origin !== local.origin || !configuration.detail_query) throw new Error("OPTIMADE detail route is invalid.");
  route.searchParams.set(configuration.detail_query, id);
  const link = document.createElement("a");
  link.href = route.href;
  if (rendered.missing) link.textContent = id;
  if (rendered.missing) link.setAttribute("aria-label", `View ${id}`);
  return link;
}

function formattedCellValue(value, format) {
  if (format === "formula" && typeof value === "string") {
    const rendered = formatCellValue(value);
    return { ...rendered, formula: true };
  }
  if (format?.name === "number" && typeof value === "number" && Number.isFinite(value)) {
    const text = formatNumberValue(value, format);
    if (text !== null) return formatCellValue(text);
  }
  if (format?.name === "join" && Array.isArray(value) && value.every((item) => item === null || typeof item === "string" || typeof item === "number" || typeof item === "boolean")) {
    return formatCellValue(value.join(format.separator));
  }
  return formatCellValue(value);
}

function appendFormattedValue(parent, rendered, document) {
  if (!rendered.formula) {
    parent.textContent = rendered.text;
    return;
  }
  let start = 0;
  for (const match of rendered.text.matchAll(/\d+/g)) {
    if (match.index > start) parent.append(document.createTextNode(rendered.text.slice(start, match.index)));
    const sub = document.createElement("sub");
    sub.append(document.createTextNode(match[0]));
    parent.append(sub);
    start = match.index + match[0].length;
  }
  if (start < rendered.text.length) parent.append(document.createTextNode(rendered.text.slice(start)));
}

function renderNotice(tbody, columns, message, state, document) {
  tbody.replaceChildren();
  const row = document.createElement("tr");
  row.className = `httk-serve-optimade-table__${state}`;
  const cell = document.createElement("td");
  cell.colSpan = columns;
  cell.textContent = message;
  row.append(cell);
  tbody.append(row);
}

function renderProblem(view, columns, message, retry = null) {
  const document = view.tbody.ownerDocument;
  renderNotice(view.tbody, columns, message, "error", document);
  removeRetry(view);
  view.status.textContent = message;
  if (retry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "httk-serve-optimade-table__retry";
    button.setAttribute("data-httk-serve-optimade-retry", "1");
    button.textContent = "Retry";
    button.addEventListener("click", retry);
    view.pager.append(button);
  }
}

function removeRetry(view) {
  view.pager?.querySelector("[data-httk-serve-optimade-retry]")?.remove();
}

function failInstallation(shell, view, message) {
  if (!view) return;
  renderProblem(view, view.columns, message);
  shell.setAttribute("aria-busy", "false");
  setButton(view.previous, false);
  setButton(view.next, false);
}

function optionalString(value, name) {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string") throw new Error(`OPTIMADE table ${name} is invalid.`);
  return value;
}

function normalizeJson(value, depth = 0) {
  if (depth > 12) return "[depth limit]";
  if (Array.isArray(value)) return value.map((item) => normalizeJson(item, depth + 1));
  if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, normalizeJson(value[key], depth + 1)]));
  return value;
}

function messageFor(error) {
  if (error instanceof OptimadeHttpError) return `The OPTIMADE service returned HTTP ${error.status}.`;
  if (error instanceof OptimadeProtocolError) {
    if (error.code === "network") return "Network error: the OPTIMADE service could not be reached.";
    return bounded(`OPTIMADE protocol error: ${error.message}`);
  }
  return bounded(error instanceof Error ? error.message : "Could not load OPTIMADE results.");
}

function bounded(value) {
  return String(value).replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 480) || "Could not load OPTIMADE results.";
}

function cacheForFetch(fetch) {
  let cache = discoveryCaches.get(fetch);
  if (!cache) {
    cache = new Map();
    discoveryCaches.set(fetch, cache);
  }
  return cache;
}

function start() {
  installOptimadeTables();
}

if (globalThis.document) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
}
