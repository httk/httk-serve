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

/** Install a controller for one shell. Calling this again returns the original controller. */
export function installOptimadeTable(shell, options = {}) {
  if (controllers.has(shell)) return controllers.get(shell);
  let view;
  let configuration;
  try {
    view = requiredShell(shell);
    configuration = readConfiguration(shell, options.document ?? globalThis.document);
    validateConfiguration(configuration);
    configuration = { ...configuration, filter: effectiveFilter(configuration, options.location ?? globalThis.location) };
  } catch (error) {
    failInstallation(shell, view, messageFor(error));
    return null;
  }
  let controller;
  try {
    controller = new OptimadeTableController(shell, view, configuration, options);
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

  constructor(shell, view, configuration, options) {
    this.#shell = shell;
    this.#view = view;
    this.#configuration = configuration;
    this.#document = options.document ?? globalThis.document;
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
    view.previous.addEventListener("click", () => this.previous());
    view.next.addEventListener("click", () => this.next());
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
    if (!this.#current(generation)) return;
    const CustomEventForDocument = this.#document?.defaultView?.CustomEvent ?? globalThis.CustomEvent;
    this.#shell.dispatchEvent(new CustomEventForDocument("httk-serve:optimade-table-updated", {
      bubbles: true,
      detail: Object.freeze({ entryType: this.#configuration.entry_type, count: page.resources.length, pageIndex: this.#pageIndex, hasNext: this.#nextUrl !== null, hasPrevious: this.#previousUrls.length > 0 }),
    }));
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
      const rendered = formatCellValue(value);
      const cell = document.createElement("td");
      cell.className = `httk-serve-optimade-table__cell httk-serve-optimade-table__cell--${column.align ?? "start"}`;
      if (rendered.missing) cell.setAttribute("aria-label", "No value");
      if (rendered.truncated) cell.title = "Value abbreviated for display";
      if (configuration.detail_route && configuration.detail_column === column.key) {
        const link = detailLink(configuration, resource.id, rendered, document);
        cell.append(link);
      } else cell.textContent = rendered.text;
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
  link.textContent = rendered.missing ? id : rendered.text;
  if (rendered.missing) link.setAttribute("aria-label", `View ${id}`);
  return link;
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
