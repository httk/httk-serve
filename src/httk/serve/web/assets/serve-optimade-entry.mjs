/** Single-entry page: one OPTIMADE resource with its provenance, files, alternatives and properties. */
import { OptimadeHttpError, OptimadeTransport } from "./serve-optimade-table-protocol.mjs";
import {
  INTRINSIC,
  bestLabel,
  boundedText,
  capList,
  doiUrl,
  firstSentence,
  formatValue,
  humanSize,
  isIdentifier,
  linkPolicy,
  relationshipHeading,
  routeHref,
  splitAlternativeId,
  typeLabel,
  unitOf,
} from "./serve-optimade-present.mjs";

const MAX_ID_CHARS = 2_048;
const MAX_DEPTH = 4;
const SITES_OPEN_LIMIT = 20;
const ALTERNATIVES_PAGE = 20;
const RUN_FORWARD = ["_httk_has_input", "_httk_has_artifact", "_httk_has_output"];

function node(tag, className = "", text = null) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== null && text !== undefined) element.textContent = String(text);
  return element;
}

function readConfiguration(shell) {
  const script = document.getElementById(shell.getAttribute("data-config-id") ?? "");
  if (!script || script.type !== "application/json") throw new Error("Entry configuration is missing.");
  return JSON.parse(script.textContent ?? "");
}

function section(title, className = "") {
  const element = node("section", `httk-serve-optimade-explorer__section ${className}`.trim());
  element.append(node("h3", "", title));
  return element;
}

function definitionList(rows) {
  const list = node("dl", "httk-serve-optimade-explorer__fields");
  for (const [label, value, title] of rows) {
    if (value === null || value === undefined || value === "") continue;
    const dt = node("dt", "", label);
    if (title) dt.title = title;
    const dd = node("dd");
    if (value instanceof Node) dd.append(value);
    else dd.textContent = boundedText(value);
    list.append(dt, dd);
  }
  return list;
}

/** A link through the URL policy, or the policy's plain text. */
function policyLink(value, label = null, { download = false } = {}) {
  const policy = linkPolicy(value, document.baseURI, location.origin);
  if (policy.kind !== "link") return node("code", "", policy.text);
  const link = node("a", download ? "httk-serve-optimade-explorer__button" : "", label ?? policy.href);
  link.href = policy.href;
  link.rel = "noopener noreferrer";
  if (download) link.setAttribute("download", "");
  return link;
}

function entryLink(config, type, id, text) {
  const link = node("a", "", text);
  link.href = routeHref(config.entry_route, { type, id }, document.baseURI);
  return link;
}

function appendCapped(list, values, render) {
  const { items, more } = capList(values);
  for (const value of items) list.append(render(value));
  if (more) list.append(node("li", "httk-serve-optimade-explorer__more", `${more} more`));
  return list;
}

function identifiers(relationship) {
  const data = relationship?.data;
  return (Array.isArray(data) ? data : data ? [data] : []).filter((item) => typeof item?.type === "string" && typeof item?.id === "string");
}

function nonIntrinsic(attributes) {
  return Object.keys(attributes ?? {}).filter((name) => !INTRINSIC.has(name));
}

/** A bounded nested rendering of one served value. */
function valueNode(value, unit = "", depth = 0) {
  if (value === null || typeof value !== "object" || depth >= MAX_DEPTH) return document.createTextNode(formatValue(value, unit));
  if (Array.isArray(value)) {
    if (value.every((item) => item === null || typeof item !== "object")) return document.createTextNode(formatValue(value, unit));
    if (value.every((item) => Array.isArray(item) && item.every((cell) => cell === null || typeof cell !== "object"))) {
      const table = node("table", "httk-serve-optimade-explorer__matrix");
      const body = node("tbody");
      const { items, more } = capList(value);
      for (const row of items) {
        const tr = node("tr");
        for (const cell of capList(row, 32).items) tr.append(node("td", "", formatValue(cell)));
        body.append(tr);
      }
      table.append(body);
      if (unit) table.append(node("caption", "", unit));
      const wrapper = node("div");
      wrapper.append(table);
      if (more) wrapper.append(node("p", "httk-serve-optimade-explorer__more", `${more} more rows`));
      return wrapper;
    }
    const details = node("details");
    details.append(node("summary", "", `${value.length} ${value.length === 1 ? "item" : "items"}`));
    details.append(appendCapped(node("ol"), value, (item) => {
      const li = node("li");
      li.append(valueNode(item, "", depth + 1));
      return li;
    }));
    return details;
  }
  const keys = Object.keys(value);
  const details = node("details");
  details.append(node("summary", "", `${keys.length} ${keys.length === 1 ? "field" : "fields"}`));
  const list = node("dl", "httk-serve-optimade-explorer__fields");
  const { items, more } = capList(keys);
  for (const key of items) {
    const dd = node("dd");
    dd.append(valueNode(value[key], "", depth + 1));
    list.append(node("dt", "", boundedText(key, 128)), dd);
  }
  details.append(list);
  if (more) details.append(node("p", "httk-serve-optimade-explorer__more", `${more} more`));
  return details;
}

/** One-line summary of an included resource. */
function summaryNode(resource, context) {
  const attributes = resource?.attributes ?? {};
  const span = node("span", "httk-serve-optimade-explorer__summary");
  if (resource.type === "_httk_records") {
    const name = nonIntrinsic(attributes).find((key) => attributes[key] !== null && attributes[key] !== undefined);
    if (name) span.append(`${name} = ${formatValue(attributes[name], unitOf(context.recordProperties?.[name]))}`);
  } else if (resource.type === "files") {
    if (typeof attributes.name === "string") span.append(boundedText(attributes.name, 256), " ");
    if (humanSize(attributes.size)) span.append(`(${humanSize(attributes.size)}) `);
    if (typeof attributes.url === "string" && linkPolicy(attributes.url, document.baseURI, location.origin).kind === "link") {
      span.append(policyLink(attributes.url, "Download", { download: true }));
    }
  } else if (resource.type === "structures") {
    const formula = attributes.chemical_formula_descriptive ?? attributes.chemical_formula_reduced;
    if (typeof formula === "string") span.append(boundedText(formula, 256));
  } else if (resource.type === "_httk_runs") {
    const name = attributes._httk_name ?? attributes._httk_source_id;
    if (typeof name === "string") span.append(boundedText(name, 256));
  } else if (resource.type === "references") {
    if (typeof attributes.title === "string") span.append(boundedText(attributes.title, 512), " ");
    const doi = doiUrl(attributes.doi);
    if (doi) {
      const link = node("a", "", attributes.doi);
      link.href = doi;
      link.rel = "noopener noreferrer";
      span.append(link);
    }
  }
  return span.childNodes.length ? span : null;
}

function relatedItem(identifier, context, { nestRuns = false } = {}) {
  const li = node("li");
  const label = identifier.meta?._httk_label;
  if (typeof label === "string" && label) li.append(node("strong", "", boundedText(label, 256)), " ");
  const included = context.included.get(`${identifier.type}\n${identifier.id}`);
  const text = included ? bestLabel(included) : boundedText(identifier.id, 256);
  li.append(entryLink(context.config, identifier.type, identifier.id, text));
  li.append(" ", node("span", "httk-serve-optimade-explorer__type", typeLabel(identifier.type)));
  const summary = included ? summaryNode(included, context) : null;
  if (summary && summary.textContent.trim() !== text) li.append(" ", summary);
  if (nestRuns && included?.type === "_httk_runs") {
    const nested = node("ul", "httk-serve-optimade-explorer__nested");
    for (const key of RUN_FORWARD) {
      appendCapped(nested, identifiers(included.relationships?.[key]), (target) => {
        const item = node("li");
        const targetLabel = target.meta?._httk_label;
        item.append(`${relationshipHeading(key, "_httk_runs").heading}: `);
        if (typeof targetLabel === "string" && targetLabel) item.append(node("strong", "", boundedText(targetLabel, 256)), " ");
        item.append(entryLink(context.config, target.type, target.id, boundedText(target.id, 256)), " ", node("span", "httk-serve-optimade-explorer__type", typeLabel(target.type)));
        return item;
      });
    }
    if (nested.childNodes.length) li.append(nested);
  }
  return li;
}

function relationshipSections(resource, context) {
  const groups = { provenance: [], references: [], related: [] };
  for (const [key, relationship] of Object.entries(resource.relationships ?? {})) {
    const targets = identifiers(relationship);
    if (!targets.length) continue;
    const heading = relationshipHeading(key, resource.type);
    groups[heading.group].push({ key, heading, targets });
  }
  const sections = [];
  for (const [group, title] of [["provenance", "Provenance"], ["references", "References"], ["related", "Related"]]) {
    const entries = groups[group].sort((a, b) => a.heading.order - b.heading.order);
    if (!entries.length) continue;
    const element = section(title);
    for (const { heading, targets } of entries) {
      if (group !== "references") element.append(node("h4", "", heading.heading));
      const nestRuns = heading.heading === "Produced by" || heading.heading === "Used by";
      element.append(appendCapped(node("ul", "httk-serve-optimade-explorer__related"), targets, (target) => relatedItem(target, context, { nestRuns })));
    }
    sections.push(element);
  }
  return sections;
}

function latticeTable(vectors) {
  const table = node("table", "httk-serve-optimade-explorer__matrix");
  const head = node("tr");
  for (const label of ["", "x (Å)", "y (Å)", "z (Å)"]) head.append(node("th", "", label));
  const thead = node("thead");
  thead.append(head);
  const body = node("tbody");
  vectors.slice(0, 3).forEach((vector, index) => {
    const row = node("tr");
    row.append(node("th", "", "abc"[index]));
    for (const value of Array.isArray(vector) ? vector.slice(0, 3) : []) row.append(node("td", "", formatValue(value)));
    body.append(row);
  });
  table.append(thead, body);
  return table;
}

function sitesTable(attributes) {
  const species = Array.isArray(attributes.species_at_sites) ? attributes.species_at_sites : [];
  const positions = Array.isArray(attributes.cartesian_site_positions) ? attributes.cartesian_site_positions : [];
  const count = Math.max(species.length, positions.length);
  const details = node("details");
  details.open = count <= SITES_OPEN_LIMIT;
  details.append(node("summary", "", `Sites (${count})`));
  const table = node("table", "httk-serve-optimade-explorer__matrix");
  const head = node("tr");
  for (const label of ["#", "Species", "x (Å)", "y (Å)", "z (Å)"]) head.append(node("th", "", label));
  const thead = node("thead");
  thead.append(head);
  const body = node("tbody");
  const { items, more } = capList(Array.from({ length: count }, (_, index) => index));
  for (const index of items) {
    const row = node("tr");
    row.append(node("td", "", index + 1), node("td", "", formatValue(species[index] ?? null)));
    const position = Array.isArray(positions[index]) ? positions[index] : [];
    for (let axis = 0; axis < 3; axis += 1) row.append(node("td", "", formatValue(position[axis] ?? null)));
    body.append(row);
  }
  table.append(thead, body);
  details.append(table);
  if (more) details.append(node("p", "httk-serve-optimade-explorer__more", `${more} more sites`));
  return details;
}

function typePanel(resource, properties) {
  const attributes = resource.attributes;
  const title = (name) => (typeof properties?.[name]?.title === "string" ? boundedText(properties[name].title, 128) : name);
  if (resource.type === "_httk_records") {
    const names = nonIntrinsic(attributes).filter((name) => attributes[name] !== null && attributes[name] !== undefined);
    if (!names.length) return null;
    const panel = section("Values", "httk-serve-optimade-explorer__values");
    panel.append(definitionList(names.map((name) => [title(name), valueNode(attributes[name], unitOf(properties?.[name])), name])));
    return panel;
  }
  if (resource.type === "files") {
    const panel = section("File");
    const checksums = attributes.checksums && typeof attributes.checksums === "object" ? attributes.checksums : {};
    const sums = node("ul", "httk-serve-optimade-explorer__checksums");
    for (const [algorithm, digest] of Object.entries(checksums).slice(0, 16)) sums.append(node("li", "", `${algorithm}: ${boundedText(digest, 256)}`));
    const size = humanSize(attributes.size);
    const url = typeof attributes.url === "string" && attributes.url ? attributes.url : null;
    const downloadable = url !== null && linkPolicy(url, document.baseURI, location.origin).kind === "link";
    panel.append(definitionList([
      ["Name", attributes.name],
      ["Size", size ? `${size} (${attributes.size} bytes)` : null],
      ["Media type", attributes.media_type],
      ["Checksums", sums.childNodes.length ? sums : null],
      [downloadable ? "Download" : "Stored location", url === null ? null : policyLink(url, "Download file", { download: true })],
    ]));
    if (url !== null && !downloadable) {
      panel.append(node("p", "httk-serve-optimade-explorer__note", "This file has no downloadable URL here; its stored location is shown instead."));
    }
    return panel;
  }
  if (resource.type === "structures") {
    const panel = section("Structure");
    panel.append(definitionList([["Formula", attributes.chemical_formula_descriptive ?? attributes.chemical_formula_reduced], ["Sites", attributes.nsites]]));
    if (Array.isArray(attributes.lattice_vectors)) {
      panel.append(node("h4", "", "Lattice vectors"), latticeTable(attributes.lattice_vectors));
    }
    if (Array.isArray(attributes.species_at_sites) || Array.isArray(attributes.cartesian_site_positions)) panel.append(sitesTable(attributes));
    return panel;
  }
  if (resource.type === "_httk_runs") {
    const panel = section("Run");
    const uri = (value) => (typeof value === "string" && value ? policyLink(value) : null);
    panel.append(definitionList([
      ["Name", attributes._httk_name],
      ["Source", attributes._httk_source_id],
      ["Started", attributes._httk_started_at],
      ["Finished", attributes._httk_finished_at],
      ["Workflow declaration", uri(attributes._httk_workflow_declaration_uri)],
      ["Workflow definition", uri(attributes._httk_workflow_definition_uri)],
    ]));
    return panel;
  }
  if (resource.type === "references") {
    const doi = doiUrl(attributes.doi);
    if (!doi) return null;
    const link = node("a", "", attributes.doi);
    link.href = doi;
    link.rel = "noopener noreferrer";
    const panel = section("Reference");
    panel.append(definitionList([["Title", attributes.title], ["DOI", link]]));
    return panel;
  }
  return null;
}

function propertiesSection(resource, properties) {
  const panel = section("Properties");
  const { items: names, more } = capList(Object.keys(resource.attributes).sort());
  const list = node("dl", "httk-serve-optimade-explorer__fields httk-serve-optimade-explorer__properties");
  let empty = 0;
  for (const name of names) {
    const value = resource.attributes[name];
    const definition = properties?.[name];
    const dt = node("dt", "", typeof definition?.title === "string" ? boundedText(definition.title, 128) : name);
    dt.title = [name, firstSentence(definition?.description)].filter(Boolean).join(" — ");
    const dd = node("dd");
    dd.append(valueNode(value, unitOf(definition)));
    if (value === null || value === undefined) {
      empty += 1;
      dt.hidden = true;
      dd.hidden = true;
      dt.dataset.empty = dd.dataset.empty = "1";
    }
    list.append(dt, dd);
  }
  if (empty) {
    const label = node("label", "httk-serve-optimade-explorer__toggle");
    const toggle = node("input");
    toggle.type = "checkbox";
    toggle.addEventListener("change", () => {
      for (const element of list.querySelectorAll("[data-empty]")) element.hidden = !toggle.checked;
    });
    label.append(toggle, ` Show empty properties (${empty})`);
    panel.append(label);
  }
  panel.append(list);
  if (more) panel.append(node("p", "httk-serve-optimade-explorer__more", `${more} more`));
  return panel;
}

async function alternativesSection(config, type, id, discovery, info) {
  // httk advertises alternatives as `_httk_<type>~alts` (`<type>~alts` for _httk_ types).
  if (![`_httk_${type}~alts`, `${type}~alts`].some((endpoint) => info.endpoints.includes(endpoint))) return null;
  const lister = new OptimadeTransport({ base_url: config.base_url, entry_type: type, response_fields: [], page_size: ALTERNATIVES_PAGE }, { documentBase: document.baseURI, info });
  const query = new URLSearchParams({ page_limit: String(ALTERNATIVES_PAGE) });
  if (info.entryTypes.includes("files")) query.set("include", "files");
  // ponytail: first page only (20 alternatives) with a "more" note; page through if groups grow larger.
  const nextUrl = `${discovery.apiBaseUrl}/${encodeURIComponent(type)}/${encodeURIComponent(id)}/_httk_alts?${query}`;
  let page;
  try {
    page = await lister.fetchPage({ nextUrl });
  } catch {
    return null;
  }
  if (!page.resources.length) return null;
  const files = new Map(page.included.filter((item) => item.type === "files").map((item) => [item.id, item]));
  const panel = section("Alternatives");
  const list = node("ul", "httk-serve-optimade-explorer__related");
  for (const resource of page.resources) {
    const kind = typeof resource.attributes._httk_kind === "string" ? resource.attributes._httk_kind : splitAlternativeId(resource.id)?.kind ?? resource.id;
    const li = node("li");
    li.append(entryLink(config, type, resource.id, boundedText(kind, 128)));
    for (const file of identifiers(resource.relationships?.files).slice(0, 8)) {
      const included = files.get(file.id);
      li.append(" — input file ", entryLink(config, file.type, file.id, included ? bestLabel(included) : boundedText(file.id, 256)));
      const url = included?.attributes.url;
      if (typeof url === "string" && linkPolicy(url, document.baseURI, location.origin).kind === "link") {
        li.append(" ", policyLink(url, "Download", { download: true }));
      }
    }
    list.append(li);
  }
  panel.append(list);
  if (page.moreDataAvailable) panel.append(node("p", "httk-serve-optimade-explorer__more", "More alternatives are not shown."));
  return panel;
}

function showState(shell, message, browseHref) {
  const wrapper = node("div", "httk-serve-optimade-explorer__state");
  wrapper.append(node("p", "", message));
  const back = node("a", "", "Back to browse");
  back.href = browseHref;
  wrapper.append(back);
  shell.replaceChildren(wrapper);
  shell.setAttribute("aria-busy", "false");
}

async function loadEntry(shell) {
  const config = readConfiguration(shell);
  const params = new URLSearchParams(location.search);
  const type = params.get("type");
  const id = params.get("id");
  const typeValid = isIdentifier(type);
  const browseHref = routeHref(config.browse_route, typeValid ? { type } : {}, document.baseURI);
  if (!typeValid || typeof id !== "string" || !id || id.length > MAX_ID_CHARS || /[\u0000-\u001f\u007f]/.test(id)) {
    return showState(shell, "Choose an entry to view: no valid entry type and id were given.", browseHref);
  }
  let alternative = splitAlternativeId(id);
  const options = { documentBase: document.baseURI };
  const transport = new OptimadeTransport({ base_url: config.base_url, entry_type: type, response_fields: [] }, options);
  const info = await transport.discoverInfo();
  if (!info.entryTypes.includes(type) && !info.endpoints.includes(type)) {
    return showState(shell, `This service does not serve entries of type ${boundedText(type, 128)}.`, routeHref(config.browse_route, {}, document.baseURI));
  }
  const discovery = await transport.discover();
  const fetchEntry = (include) => (alternative
    ? transport.fetchAlternative(alternative.main, alternative.kind, { include })
    : transport.fetchOne(id, { include }));
  const fetchFirst = async () => {
    try {
      return await fetchEntry([]);
    } catch (error) {
      if (!(error instanceof OptimadeHttpError && error.status === 404)) throw error;
      return null;
    }
  };
  let result = await fetchFirst();
  if (result === null && alternative !== null) {
    // A foreign id may merely contain "~": retry it as a plain id.
    alternative = null;
    result = await fetchFirst();
  }
  if (result === null) return showState(shell, "The requested entry was not found.", browseHref);
  const { resource } = result;

  // Step 2: inline only the entry types its relationships actually name.
  const targets = Object.values(resource.relationships ?? {}).flatMap(identifiers);
  const include = [...new Set(targets.map((target) => target.type))].filter((target) => info.entryTypes.includes(target));
  const included = new Map();
  let note = null;
  if (include.length) {
    try {
      for (const item of (await fetchEntry(include))?.included ?? []) included.set(`${item.type}\n${item.id}`, item);
    } catch (error) {
      note = error?.code === "body_limit"
        ? "The related entries are too large to show inline; only their identifiers are listed."
        : "The related entries could not be loaded; only their identifiers are listed.";
    }
  }
  let recordProperties = type === "_httk_records" ? discovery.properties : null;
  if (recordProperties === null && [...included.values()].some((item) => item.type === "_httk_records")) {
    try {
      recordProperties = (await new OptimadeTransport({ base_url: config.base_url, entry_type: "_httk_records", response_fields: [] }, { ...options, info }).discover()).properties;
    } catch {
      recordProperties = null;
    }
  }
  const context = { config, included, recordProperties };

  const article = node("article", "httk-serve-optimade-explorer__entry");
  const header = node("header", "httk-serve-optimade-explorer__intro");
  header.append(node("p", "httk-serve-optimade-explorer__type", typeLabel(type)), node("h2", "", bestLabel(resource)));
  const raw = new URL(discovery.apiBaseUrl);
  raw.pathname += alternative
    ? `/${encodeURIComponent(type)}/${encodeURIComponent(alternative.main)}/_httk_alts/${encodeURIComponent(alternative.kind)}`
    : `/${encodeURIComponent(type)}/${encodeURIComponent(id)}`;
  header.append(definitionList([
    ["Type", type],
    ["ID", resource.id],
    ["Alternative of", alternative ? entryLink(config, type, alternative.main, boundedText(alternative.main, 256)) : null],
    ["Alternative kind", alternative?.kind],
    ["Immutable ID", resource.attributes.immutable_id],
    ["Last modified", resource.attributes.last_modified],
    ["OPTIMADE JSON", policyLink(raw.href, "Raw response")],
  ]));
  article.append(header);
  const panel = typePanel(resource, discovery.properties);
  if (panel) article.append(panel);
  if (!nonIntrinsic(resource.attributes).length) {
    article.append(node("p", "httk-serve-optimade-explorer__note", "Untyped record: this entry carries no properties beyond its identifiers."));
  }
  if (note) article.append(node("p", "httk-serve-optimade-explorer__note", note));
  article.append(...relationshipSections(resource, context));
  const alternativesSlot = node("div");
  article.append(alternativesSlot, propertiesSection(resource, discovery.properties));
  shell.replaceChildren(article);
  shell.setAttribute("aria-busy", "false");
  if (!alternative) {
    const alternatives = await alternativesSection(config, type, id, discovery, info);
    if (alternatives) alternativesSlot.replaceWith(alternatives);
  }
}

function start() {
  for (const shell of document.querySelectorAll("[data-httk-serve-optimade-entry]")) {
    loadEntry(shell).catch(() => {
      shell.setAttribute("aria-busy", "false");
      shell.replaceChildren(node("p", "httk-serve-optimade-explorer__error", "The OPTIMADE service could not be reached or returned an invalid response."));
    });
  }
}

if (globalThis.document) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
}
