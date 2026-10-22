/**
 * Browse page controller: the type/kind picker and the condition builder around a
 * server-rendered optimade_table. It only rewrites `?type=&kind=&filter=` and reloads.
 */
import { OptimadeTransport } from "./serve-optimade-table-protocol.mjs";
import {
  catalogOrder,
  compileFilter,
  queryableProperties,
  recordKinds,
  typeLabel,
  withKind,
} from "./serve-optimade-present.mjs";

function node(tag, className = "", text = null) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== null && text !== undefined) element.textContent = String(text);
  return element;
}

function option(value, label, selected = false) {
  const element = node("option", "", label);
  element.value = value;
  element.selected = selected;
  return element;
}

function readConfiguration(shell) {
  const script = document.getElementById(shell.getAttribute("data-config-id") ?? "");
  if (!script || script.type !== "application/json") throw new Error("Browser configuration is missing.");
  return JSON.parse(script.textContent ?? "");
}

function replaceSearch(params) {
  location.replace(`${location.pathname}?${params}${location.hash}`);
}

/** Carry type, kind and the column fallback through the table's raw-filter GET form. */
function preserveInAdvancedForm(shell, params) {
  const form = shell.querySelector("[data-httk-serve-optimade-advanced] form");
  if (!form) return;
  for (const name of ["type", "kind"]) {
    if (!params.get(name)) continue;
    const hidden = node("input");
    hidden.type = "hidden";
    hidden.name = name;
    hidden.value = params.get(name);
    form.append(hidden);
  }
}

async function setupPicker(shell, config, info, options) {
  const typeSelect = shell.querySelector("[data-httk-serve-optimade-type]");
  const kindSelect = shell.querySelector("[data-httk-serve-optimade-kind]");
  const kindLabel = shell.querySelector("[data-httk-serve-optimade-kind-label]");
  if (!typeSelect || !kindSelect || !kindLabel) return;
  const types = catalogOrder(info.entryTypes);
  typeSelect.replaceChildren(option("", "Choose…", !config.entry_type), ...types.map((type) => option(type, typeLabel(type), type === config.entry_type)));
  let kinds = null;
  const refreshKinds = async () => {
    const records = typeSelect.value === "_httk_records";
    kindLabel.hidden = !records;
    kindSelect.disabled = !records;
    if (!records || kinds !== null) return;
    try {
      const transport = new OptimadeTransport({ base_url: config.base_url, entry_type: "_httk_records", response_fields: [] }, { ...options, info });
      kinds = recordKinds((await transport.discover()).properties);
    } catch {
      kinds = [];
    }
    kindSelect.replaceChildren(option("", "All records", !config.kind), ...kinds.map((kind) => option(kind.name, kind.unit ? `${kind.title} (${kind.unit})` : kind.title, kind.name === config.kind)));
  };
  typeSelect.addEventListener("change", () => {
    kindSelect.value = "";
    refreshKinds();
  });
  await refreshKinds();
}

function conditionRow(properties, onChange) {
  const row = node("div", "httk-serve-optimade-explorer__condition");
  const property = node("select");
  property.setAttribute("aria-label", "Property");
  property.append(option("", "Property…"), ...properties.map((item) => option(item.name, item.name)));
  const operator = node("select");
  operator.setAttribute("aria-label", "Operator");
  const value = node("input");
  value.type = "text";
  value.setAttribute("aria-label", "Value");
  value.autocomplete = "off";
  const remove = node("button", "", "Remove");
  remove.type = "button";
  const definitionFor = () => properties.find((item) => item.name === property.value);
  const syncOperators = () => {
    const item = definitionFor();
    operator.replaceChildren(...(item?.operators ?? []).map((name) => option(name, name)));
    syncValue();
  };
  const syncValue = () => {
    value.hidden = operator.value === "IS KNOWN" || operator.value === "IS UNKNOWN";
    value.placeholder = operator.value === "HAS ALL" || operator.value === "HAS ANY" ? "a, b, c" : "";
  };
  property.addEventListener("change", () => { syncOperators(); onChange(); });
  operator.addEventListener("change", () => { syncValue(); onChange(); });
  value.addEventListener("input", onChange);
  remove.addEventListener("click", () => { row.remove(); onChange(); });
  row.append(property, operator, value, remove);
  row.condition = () => ({ property: property.value, operator: operator.value, value: value.value, definition: definitionFor()?.definition });
  return row;
}

function setupConditions(shell, config, properties) {
  const form = shell.querySelector("[data-httk-serve-optimade-conditions]");
  if (!form) return;
  const rows = form.querySelector("[data-httk-serve-optimade-condition-rows]");
  const preview = form.querySelector("[data-httk-serve-optimade-preview]");
  const problem = form.querySelector("[data-httk-serve-optimade-condition-error]");
  const elementsLabel = form.querySelector("[data-httk-serve-optimade-elements-label]");
  const elements = form.querySelector("[data-httk-serve-optimade-elements]");
  const queryable = queryableProperties(properties);
  if (elementsLabel && queryable.some((item) => item.name === "elements")) elementsLabel.hidden = false;
  const compile = () => compileFilter([...rows.children].map((row) => row.condition()), {
    elements: elementsLabel?.hidden ? "" : elements?.value ?? "",
    kind: config.kind,
  });
  const update = () => {
    try {
      preview.textContent = compile() ?? "(no filter: every entry)";
      problem.hidden = true;
      return true;
    } catch (error) {
      problem.textContent = error instanceof Error ? error.message : "Invalid condition.";
      problem.hidden = false;
      return false;
    }
  };
  const add = () => rows.append(conditionRow(queryable, update));
  form.querySelector("[data-httk-serve-optimade-add-condition]")?.addEventListener("click", () => { add(); update(); });
  elements?.addEventListener("input", update);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!update()) return;
    const url = new URL(location.href);
    url.searchParams.set("type", config.entry_type);
    if (config.kind) url.searchParams.set("kind", config.kind);
    const filter = compile();
    if (filter === null) url.searchParams.delete("filter");
    else url.searchParams.set("filter", filter);
    url.searchParams.delete("filter_advanced");
    location.assign(url.href);
  });
  add();
  update();
  form.hidden = false;
}

async function loadBrowser(shell) {
  const config = readConfiguration(shell);
  const params = new URLSearchParams(location.search);
  // A raw or pasted filter must still select the record kind: AND it in and reload once.
  if (config.kind && params.has("filter")) {
    const normalized = withKind(params.get("filter"), config.kind);
    if (normalized !== params.get("filter")) {
      params.set("filter", normalized);
      return replaceSearch(params);
    }
  }
  preserveInAdvancedForm(shell, params);
  const options = { documentBase: document.baseURI };
  // discoverInfo() reads only /info; the entry_type placeholder is never requested.
  const info = await new OptimadeTransport({ base_url: config.base_url, entry_type: "info", response_fields: [] }, options).discoverInfo();
  await setupPicker(shell, config, info, options);
  if (!config.entry_type) return;
  if (!info.entryTypes.includes(config.entry_type) && !info.endpoints.includes(config.entry_type)) {
    return showStatus(shell, `This server does not serve ${config.entry_type}.`);
  }
  const transport = new OptimadeTransport({ base_url: config.base_url, entry_type: config.entry_type, response_fields: [] }, { ...options, info });
  setupConditions(shell, config, (await transport.discover()).properties);
}

function showStatus(shell, message) {
  const note = shell.querySelector("[data-httk-serve-optimade-browser-status]");
  if (!note) return;
  note.textContent = message;
  note.hidden = false;
}

function start() {
  for (const shell of document.querySelectorAll("[data-httk-serve-optimade-browser]")) {
    loadBrowser(shell)
      .catch(() => showStatus(shell, "The OPTIMADE service could not be reached or returned an invalid response."))
      .finally(() => shell.setAttribute("aria-busy", "false"));
  }
}

if (globalThis.document) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
}
