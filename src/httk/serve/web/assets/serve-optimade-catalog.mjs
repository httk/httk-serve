/** Home-page catalog of the entry types an OPTIMADE service serves. */
import { OptimadeTransport } from "./serve-optimade-table-protocol.mjs";
import { boundedText, capList, catalogOrder, firstSentence, recordKinds, routeHref, typeLabel } from "./serve-optimade-present.mjs";

const COUNT_CONCURRENCY = 2;

function node(tag, className = "", text = null) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== null && text !== undefined) element.textContent = String(text);
  return element;
}

function readConfiguration(shell) {
  const script = document.getElementById(shell.getAttribute("data-config-id") ?? "");
  if (!script || script.type !== "application/json") throw new Error("Catalog configuration is missing.");
  return JSON.parse(script.textContent ?? "");
}

/** Run worker(item) over items with at most `limit` in flight. */
async function pool(items, limit, worker) {
  const queue = [...items];
  const run = async () => {
    while (queue.length) await worker(queue.shift());
  };
  await Promise.all(Array.from({ length: Math.min(limit, queue.length) }, run));
}

function countText(page) {
  const count = page.dataAvailable ?? page.dataReturned;
  return count === null ? "" : `${count.toLocaleString("en-US")} ${count === 1 ? "entry" : "entries"}`;
}

function kindList(kinds, browse) {
  const list = node("ul", "httk-serve-optimade-explorer__kinds");
  const { items, more } = capList(kinds);
  for (const kind of items) {
    const item = node("li");
    const link = node("a", "", kind.title);
    link.href = routeHref(browse, { type: "_httk_records", kind: kind.name }, document.baseURI);
    item.append(link);
    if (kind.unit) item.append(" ", node("span", "httk-serve-optimade-explorer__unit", kind.unit));
    item.append(" ", node("code", "", kind.name));
    list.append(item);
  }
  if (more) list.append(node("li", "httk-serve-optimade-explorer__more", `${more} more`));
  return list;
}

async function loadCatalog(shell) {
  const config = readConfiguration(shell);
  const options = { documentBase: document.baseURI };
  // discoverInfo() reads only /info; the entry_type placeholder is never requested.
  const info = await new OptimadeTransport({ base_url: config.base_url, entry_type: "info", response_fields: [] }, options).discoverInfo();
  const header = node("header", "httk-serve-optimade-explorer__intro");
  header.append(node("h2", "", info.provider?.name || "OPTIMADE database"));
  if (info.provider?.description) header.append(node("p", "", boundedText(info.provider.description, 600)));
  const api = node("p", "httk-serve-optimade-explorer__api");
  const base = new URL(config.base_url, document.baseURI);
  if (!base.pathname.endsWith("/")) base.pathname += "/";
  api.append("OPTIMADE API base URL: ", node("code", "", base.href));
  header.append(api);

  const grid = node("div", "httk-serve-optimade-explorer__cards");
  const types = catalogOrder(info.entryTypes);
  const cards = new Map();
  for (const type of types) {
    const card = node("article", "httk-serve-optimade-explorer__card");
    const title = node("h3");
    const link = node("a", "", typeLabel(type));
    link.href = routeHref(config.browse_route, { type }, document.baseURI);
    title.append(link);
    const description = node("p", "httk-serve-optimade-explorer__description");
    const count = node("p", "httk-serve-optimade-explorer__count", "…");
    card.append(title, node("p", "httk-serve-optimade-explorer__type", type), description, count);
    cards.set(type, { card, description, count });
    grid.append(card);
  }
  shell.replaceChildren(header, grid);
  if (!types.length) shell.append(node("p", "httk-serve-optimade-explorer__note", "This service advertises no entry types."));
  shell.setAttribute("aria-busy", "false");

  await pool(types, COUNT_CONCURRENCY, async (type) => {
    const { card, description, count } = cards.get(type);
    try {
      const transport = new OptimadeTransport({ base_url: config.base_url, entry_type: type, response_fields: [], page_size: 1 }, { ...options, info });
      const discovery = await transport.discover();
      description.textContent = firstSentence(discovery.description ?? "");
      if (type === "_httk_records") {
        const kinds = recordKinds(discovery.properties);
        if (kinds.length) {
          card.append(node("h4", "", "Record kinds"), kindList(kinds, config.browse_route));
          card.classList.add("httk-serve-optimade-explorer__card--wide");
        }
      }
      count.textContent = countText(await transport.fetchPage());
    } catch {
      count.textContent = "Count unavailable";
    }
  });
}

function start() {
  for (const shell of document.querySelectorAll("[data-httk-serve-optimade-catalog]")) {
    loadCatalog(shell).catch(() => {
      shell.setAttribute("aria-busy", "false");
      shell.replaceChildren(node("p", "httk-serve-optimade-explorer__error", "The OPTIMADE service could not be reached or returned an invalid response."));
    });
  }
}

if (globalThis.document) {
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
}
