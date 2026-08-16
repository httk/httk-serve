import assert from "node:assert/strict";
import test from "node:test";

import {
  OptimadeHttpError,
  OptimadeProtocolError,
  OptimadeTransport,
  canonicalOrigin,
  parseVersionsCsv,
} from "../src/httk/serve/web/assets/serve-optimade-table-protocol.mjs";

const ORIGIN = "https://api.example.test";
const BASE = `${ORIGIN}/db`;

function json(value, url, status = 200, headers = {}) {
  const response = new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/vnd.api+json; charset=utf-8", ...headers },
  });
  Object.defineProperty(response, "url", { value: url });
  return response;
}

function text(value, url, headers = {}) {
  const response = new Response(value, { status: 200, headers: { "content-type": "text/csv; charset=utf-8", ...headers } });
  Object.defineProperty(response, "url", { value: url });
  return response;
}

function streamResponse(chunks, url, headers = {}) {
  let cancelled = false;
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
    cancel() { cancelled = true; },
  });
  const response = new Response(stream, { status: 200, headers: { "content-type": "text/csv", ...headers } });
  Object.defineProperty(response, "url", { value: url });
  return { response, wasCancelled: () => cancelled };
}

function info(version = "1.3.0", { entries = ["structures"], endpoints = ["info", "structures"] } = {}) {
  return {
    data: {
      id: "/", type: "info",
      attributes: { api_version: version, formats: ["json"], entry_types_by_format: { json: entries }, available_endpoints: endpoints },
    },
  };
}

function entry(version = "1.3.0", { id = "structures", type = "info", fields = ["nsites"] } = {}) {
  const data = {
    properties: Object.fromEntries(fields.map((field) => [field, {}])),
    formats: ["json"], output_fields_by_format: { json: fields },
  };
  if (Number(version.split(".")[1]) >= 2) Object.assign(data, { id, type });
  return { data };
}

function page({ data = [{ id: "one", type: "structures", attributes: { nsites: 2 } }], meta = { api_version: "1.3.0", data_returned: 1, more_data_available: false }, links } = {}) {
  return { data, meta, ...(links === undefined ? {} : { links }) };
}

function routes(items) {
  const requests = [];
  return {
    requests,
    fetch: async (url, options) => {
      requests.push({ url, options });
      const response = typeof items[url] === "function" ? items[url]() : items[url];
      if (!response) throw new Error(`unexpected ${url}`);
      return response;
    },
  };
}

function transport(fetch, overrides = {}) {
  return new OptimadeTransport({
    base_url: BASE,
    entry_type: "structures",
    columns: ["id", "nsites"],
    page_size: 2,
    allowed_origins: [],
    ...overrides,
  }, { fetch, documentBase: "https://site.example.test/docs/page.html" });
}

test("restricted versions CSV preserves preference order and rejects unsafe grammar", () => {
  assert.deepEqual(parseVersionsCsv("version,comment\r\n2,preferred\r\n1,supported\r\n"), [2, 1]);
  assert.deepEqual(parseVersionsCsv("version\r\n1\r\n"), [1]);
  for (const value of ["versions\n1", "version\n01", "version\n1\n1", "version\n\"1\""]) {
    assert.throws(() => parseVersionsCsv(value), OptimadeProtocolError);
  }
});

test("explicit supported versions bypass /versions while unversioned bases negotiate v1", async () => {
  const explicit = routes({
    [`${ORIGIN}/v1.3/info`]: json(info(), `${ORIGIN}/v1.3/info`),
    [`${ORIGIN}/v1.3/info/structures`]: json(entry(), `${ORIGIN}/v1.3/info/structures`),
  });
  await transport(explicit.fetch, { base_url: `${ORIGIN}/v1.3` }).discover();
  assert.deepEqual(explicit.requests.map((request) => request.url), [`${ORIGIN}/v1.3/info`, `${ORIGIN}/v1.3/info/structures`]);

  const negotiated = routes({
    [`${BASE}/versions`]: text("version\n2\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
  });
  const discovery = await transport(negotiated.fetch).discover();
  assert.equal(discovery.apiBaseUrl, `${BASE}/v1`);
});

test("content types are strict and canonical origins normalize default ports", async () => {
  assert.equal(canonicalOrigin("HTTPS://API.EXAMPLE.TEST:443"), ORIGIN);
  const network = routes({ [`${BASE}/versions`]: json({ data: {} }, `${BASE}/versions`) });
  await assert.rejects(transport(network.fetch).discover(), (error) => error.code === "content_type");
});

test("redirects are rejected and a disallowed final URL cancels its unread body", async () => {
  const redirected = streamResponse([new TextEncoder().encode("version\n1\n")], "https://evil.example.test/versions");
  const redirects = routes({ [`${BASE}/versions`]: redirected.response });
  await assert.rejects(transport(redirects.fetch).discover(), (error) => error.code === "origin");
  assert.equal(redirects.requests[0].options.redirect, "error");
  assert.equal(redirected.wasCancelled(), true);

  const ready = routes({
    [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
  });
  const client = transport(ready.fetch);
  await client.discover();
  await assert.rejects(client.fetchPage({ nextUrl: "https://evil.example.test/cursor" }), (error) => error.code === "origin");
  assert.equal(ready.requests.length, 3);
});

test("streaming body limits do not rely on Content-Length and decode split multibyte chunks", async () => {
  const split = streamResponse([
    new Uint8Array([0x76, 0x65, 0x72, 0x73, 0x69, 0x6f, 0x6e, 0x0a, 0x31, 0x0a]),
  ], `${BASE}/versions`);
  const setup = routes({
    [`${BASE}/versions`]: split.response,
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
  });
  await transport(setup.fetch).discover();

  let cancelled = false;
  let chunk = 0;
  const oversizedResponse = new Response(new ReadableStream({
    pull(controller) { controller.enqueue(new Uint8Array(8)); chunk += 1; },
    cancel() { cancelled = true; },
  }), { headers: { "content-type": "text/csv" } });
  Object.defineProperty(oversizedResponse, "url", { value: `${BASE}/versions` });
  const tooLarge = routes({ [`${BASE}/versions`]: oversizedResponse });
  await assert.rejects(new OptimadeTransport({ base_url: BASE, entry_type: "structures", columns: ["nsites"], page_size: 1 }, {
    fetch: tooLarge.fetch, documentBase: "https://site.example.test/", bodyLimit: 10,
  }).discover(), (error) => error.code === "body_limit");
  assert.equal(chunk >= 2, true);
  assert.equal(cancelled, true);

  // A UTF-8 code point crossing chunks survives exactly in a bounded JSON error.
  const encoded = new TextEncoder().encode(JSON.stringify({ errors: [{ title: "€" }] }));
  const euro = new ReadableStream({ start(controller) { controller.enqueue(encoded.slice(0, encoded.indexOf(0xe2) + 2)); controller.enqueue(encoded.slice(encoded.indexOf(0xe2) + 2)); controller.close(); } });
  const response = new Response(euro, { status: 400, headers: { "content-type": "application/json" } });
  Object.defineProperty(response, "url", { value: `${BASE}/versions` });
  const multibyte = routes({ [`${BASE}/versions`]: response });
  await assert.rejects(transport(multibyte.fetch).discover(), (error) => error instanceof OptimadeHttpError && error.title === "€");

  const invalidUtf8 = new Response(new ReadableStream({ start(controller) { controller.enqueue(new Uint8Array([0xff])); controller.close(); } }), {
    headers: { "content-type": "text/csv" },
  });
  Object.defineProperty(invalidUtf8, "url", { value: `${BASE}/versions` });
  const malformed = routes({ [`${BASE}/versions`]: invalidUtf8 });
  await assert.rejects(transport(malformed.fetch).discover(), (error) => error.code === "body");
});

test("discovery validates /info and supports legacy and current entry-info grammar", async () => {
  const invalidInfo = routes({
    [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json({ data: { id: "wrong", type: "info", attributes: info().data.attributes } }, `${BASE}/v1/info`),
  });
  await assert.rejects(transport(invalidInfo.fetch).discover(), (error) => error.code === "discovery");

  const legacy = routes({
    [`${ORIGIN}/v1/info`]: json(info("1.1.0"), `${ORIGIN}/v1/info`),
    [`${ORIGIN}/v1/info/structures`]: json(entry("1.1.0"), `${ORIGIN}/v1/info/structures`),
  });
  await transport(legacy.fetch, { base_url: `${ORIGIN}/v1` }).discover();

  const current = routes({
    [`${ORIGIN}/v1/info`]: json(info("1.3.0"), `${ORIGIN}/v1/info`),
    [`${ORIGIN}/v1/info/structures`]: json(entry("1.3.0"), `${ORIGIN}/v1/info/structures`),
  });
  await transport(current.fetch, { base_url: `${ORIGIN}/v1` }).discover();
});

test("entry info rejects selected properties missing from either advertisement", async () => {
  const broken = entry();
  delete broken.data.properties.nsites;
  const network = routes({
    [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(broken, `${BASE}/v1/info/structures`),
  });
  await assert.rejects(transport(network.fetch).discover(), (error) => error.code === "discovery");
});

test("page query sends complete filter, sort, response fields, and page limit once", async () => {
  const first = `${BASE}/v1/structures?response_fields=nsites&page_limit=2&filter=nsites+%3E%3D+2&sort=-nsites`;
  const network = routes({
    [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
    [first]: json(page(), first),
  });
  const client = transport(network.fetch, { filter: "nsites >= 2", sort: "-nsites" });
  await client.fetchPage();
  const request = network.requests.at(-1);
  assert.equal(request.options.method, "GET");
  assert.equal(request.options.credentials, "omit");
  assert.equal(request.options.redirect, "error");
  assert.equal(request.options.headers.Accept, "application/vnd.api+json, application/json");
  const query = new URL(request.url).searchParams;
  assert.equal(query.get("filter"), "nsites >= 2");
  assert.equal(query.get("sort"), "-nsites");
  assert.equal(query.get("response_fields"), "nsites");
  assert.equal(query.get("page_limit"), "2");
  assert.equal([...query.keys()].filter((key) => key === "filter").length, 1);
});

test("page fetch forwards its AbortSignal without cancelling shared discovery", async () => {
  const first = `${BASE}/v1/structures?response_fields=nsites&page_limit=2`;
  const network = routes({
    [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
    [first]: json(page(), first),
  });
  const signal = new AbortController().signal;
  await transport(network.fetch).fetchPage({ signal });
  assert.equal(network.requests[0].options.signal, undefined);
  assert.equal(network.requests.at(-1).options.signal, signal);
});

test("page validation is strict, data counts are optional, and links accept relative href objects", async () => {
  const first = `${BASE}/v1/structures?response_fields=nsites&page_limit=2`;
  const final = `${BASE}/v1/structures?response_fields=nsites&page_limit=2`;
  // data_returned is the filtered total independent of pagination, so it may
  // exceed this single-resource page; data_available is the unfiltered total.
  const valid = page({ meta: { api_version: "1.3.0", more_data_available: true, data_returned: 7, data_available: 42 }, links: { next: { href: "?page_offset=2" } } });
  const network = routes({
    [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
    [first]: json(valid, final),
  });
  const result = await transport(network.fetch).fetchPage();
  assert.equal(result.nextUrl, `${BASE}/v1/structures?page_offset=2`);
  assert.equal(result.moreDataAvailable, true);
  assert.equal(result.dataReturned, 7);
  assert.equal(result.dataAvailable, 42);

  // Both counts are optional (SHOULD, not MUST); absent counts surface as null.
  const bare = page({ meta: { api_version: "1.3.0", more_data_available: false } });
  const bareNetwork = routes({
    [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
    [first]: json(bare, first),
  });
  const bareResult = await transport(bareNetwork.fetch).fetchPage();
  assert.equal(bareResult.dataReturned, null);
  assert.equal(bareResult.dataAvailable, null);

  for (const field of ["data_returned", "data_available"]) {
    for (const value of [-1, 1.5, true, "3"]) {
      const invalid = page({ data: [{ id: "one", type: "structures", attributes: {} }], meta: { api_version: "1.3.0", more_data_available: false, [field]: value } });
      const bad = routes({
        [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
        [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
        [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
        [first]: json(invalid, first),
      });
      await assert.rejects(transport(bad.fetch).fetchPage(), (error) => error.code === "page");
    }
  }

  const inconsistent = page({ meta: { api_version: "1.3.0", more_data_available: false, data_returned: 1 }, links: { next: "?page_offset=2" } });
  const mismatch = routes({
    [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
    [first]: json(inconsistent, first),
  });
  await assert.rejects(transport(mismatch.fetch).fetchPage(), (error) => error.code === "page");

  for (const next of [{}, { href: "" }, { href: " ?page_offset=2" }, { href: 2 }, []]) {
    const malformed = routes({
      [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
      [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
      [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
      [first]: json(page({ meta: { api_version: "1.3.0", more_data_available: true }, links: { next } }), first),
    });
    await assert.rejects(transport(malformed.fetch).fetchPage(), (error) => error.code === "page");
  }
});

test("non-2xx JSON errors are bounded and do not expose whole response bodies", async () => {
  const error = json({ errors: [{ title: "Bad request", detail: "x".repeat(500) }] }, `${BASE}/versions`, 400);
  const network = routes({ [`${BASE}/versions`]: error });
  await assert.rejects(transport(network.fetch).discover(), (caught) => {
    assert.ok(caught instanceof OptimadeHttpError);
    assert.equal(caught.status, 400);
    assert.equal(caught.title, "Bad request");
    assert.equal(caught.detail.length, 320);
    assert.ok(caught.message.length < 400);
    return true;
  });
});

test("fetchOne validates a single resource, included resources, relationships, and encoded ids", async () => {
  const id = "mp/one two";
  const one = `${BASE}/v1/structures/${encodeURIComponent(id)}?response_fields=nsites&include=references`;
  const root = {
    meta: { api_version: "1.3.0" },
    data: {
      id,
      type: "structures",
      attributes: { nsites: 2 },
      relationships: { references: { data: { type: "references", id: "ref/1" } }, empty: { data: null }, many: { data: [] } },
    },
    included: [{ type: "references", id: "ref/1", attributes: { title: "A" } }],
  };
  const network = routes({
    [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
    [one]: json(root, one),
  });
  const result = await transport(network.fetch, { response_fields: ["nsites"] }).fetchOne(id, { include: ["references"] });
  assert.deepEqual(result.resource, root.data);
  assert.deepEqual(result.included, root.included);
  assert.equal(network.requests.at(-1).options.credentials, "omit");
  assert.equal(network.requests.at(-1).options.redirect, "error");
});

test("fetchOne accepts data null, works without columns, and rejects invalid envelopes", async () => {
  const one = `${BASE}/v1/structures/id?response_fields=nsites`;
  const setup = (data, extra = {}) => routes({
    [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
    [one]: json({ meta: { api_version: "1.3.0" }, data, ...extra }, one),
  });
  const empty = setup(null);
  assert.equal(await transport(empty.fetch, { columns: undefined, response_fields: ["nsites"] }).fetchOne("id"), null);
  for (const data of [
    { id: "id", type: "other", attributes: { nsites: 1 } },
    { id: "id", type: "structures", attributes: {} },
    { id: "id", type: "structures", attributes: { nsites: 1 }, relationships: [] },
  ]) {
    const network = setup(data);
    await assert.rejects(transport(network.fetch).fetchOne("id"), (error) => error.code === "single entry");
  }
  const invalidIncluded = setup({ id: "id", type: "structures", attributes: { nsites: 1 } }, { included: [{}] });
  await assert.rejects(transport(invalidIncluded.fetch).fetchOne("id"), (error) => error.code === "single entry");
});

test("fetchOne enforces the bounded response body", async () => {
  const one = `${BASE}/v1/structures/id?response_fields=nsites`;
  const large = new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode("{".repeat(32))); controller.close(); },
  }), { headers: { "content-type": "application/vnd.api+json" } });
  Object.defineProperty(large, "url", { value: one });
  const network = routes({
    [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(entry(), `${BASE}/v1/info/structures`),
    [one]: large,
  });
  await assert.rejects(new OptimadeTransport({ base_url: BASE, entry_type: "structures", response_fields: ["nsites"], page_size: 1 }, {
    fetch: network.fetch, documentBase: "https://site.example.test/", bodyLimit: 10,
  }).fetchOne("id"), (error) => error.code === "body_limit");
});

test("discovery exposes exactly the strictly-sortable advertised fields", async () => {
  const fields = ["nsites", "nelements", "chemical_formula_reduced", "energy"];
  const entryInfo = {
    data: {
      id: "structures",
      type: "info",
      properties: {
        nsites: { sortable: true },
        nelements: { sortable: false },
        chemical_formula_reduced: {},
        energy: { sortable: "yes" },
      },
      formats: ["json"],
      output_fields_by_format: { json: fields },
    },
  };
  const network = routes({
    [`${BASE}/versions`]: text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: json(entryInfo, `${BASE}/v1/info/structures`),
  });
  const discovery = await transport(network.fetch, { columns: fields }).discover();
  assert.deepEqual([...discovery.sortableFields], ["nsites"]);
  assert.ok(Object.isFrozen(discovery.sortableFields));
});

test("the default fetch is invoked with the global receiver so native fetch does not raise Illegal invocation", async () => {
  const responses = {
    [`${BASE}/versions`]: () => text("version\n1\n", `${BASE}/versions`),
    [`${BASE}/v1/info`]: () => json(info(), `${BASE}/v1/info`),
    [`${BASE}/v1/info/structures`]: () => json(entry(), `${BASE}/v1/info/structures`),
  };
  // A native-like fetch: browsers throw "Illegal invocation" unless the receiver is the global object.
  const strictGlobalFetch = function fetch(url) {
    if (this !== globalThis) throw new TypeError("Illegal invocation");
    const make = responses[url];
    if (!make) throw new Error(`unexpected ${url}`);
    return Promise.resolve(make());
  };
  const previous = globalThis.fetch;
  globalThis.fetch = strictGlobalFetch;
  try {
    // No fetch option: this exercises the `?? globalThis.fetch` fallback, the real browser path.
    const client = new OptimadeTransport(
      { base_url: BASE, entry_type: "structures", columns: ["id", "nsites"], page_size: 2, allowed_origins: [] },
      { documentBase: "https://site.example.test/docs/page.html" },
    );
    const discovery = await client.discover();
    assert.equal(discovery.apiBaseUrl, `${BASE}/v1`);
  } finally {
    globalThis.fetch = previous;
  }
});
