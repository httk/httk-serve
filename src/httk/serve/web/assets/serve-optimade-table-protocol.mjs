/**
 * Small, browser-only OPTIMADE transport for the table controller.
 *
 * The transport deliberately has no DOM, storage, or event dependency.  A
 * controller creates one instance per table, awaits discover(), then uses
 * fetchPage().  Continuation URLs returned by fetchPage() are intended to
 * stay in that controller's JavaScript memory only.
 */

/** A conservative limit for every response body read by this module. */
export const DEFAULT_BODY_LIMIT_BYTES = 1_048_576;

const SEMVER = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/;
const VERSION_MAJOR = /^(0|[1-9]\d*)$/;
const EXPLICIT_VERSION = /^v1(?:\.3(?:\.0)?)?$/;

/** A protocol failure suitable for an accessible, recoverable UI error. */
export class OptimadeProtocolError extends Error {
  constructor(code, message, options = {}) {
    super(message);
    this.name = "OptimadeProtocolError";
    this.code = code;
    if (options.cause !== undefined) this.cause = options.cause;
  }
}

/** A bounded, sanitized non-success HTTP response. */
export class OptimadeHttpError extends OptimadeProtocolError {
  constructor(status, title = null, detail = null) {
    const summary = [title, detail].filter(Boolean).join(": ");
    super("http", `OPTIMADE request failed (${status})${summary ? `: ${summary}` : ""}`);
    this.name = "OptimadeHttpError";
    this.status = status;
    this.title = title;
    this.detail = detail;
  }
}

/** Canonical HTTP(S) origin, including the URL API's default-port handling. */
export function canonicalOrigin(value, base) {
  let url;
  try {
    url = base === undefined ? new URL(value) : new URL(value, base);
  } catch {
    throw protocol("url", "OPTIMADE URL is invalid");
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw protocol("url", "OPTIMADE URL must use HTTP(S)");
  }
  if (url.username || url.password) {
    throw protocol("url", "OPTIMADE URL must not contain credentials");
  }
  return url.origin;
}

/** Parse the deliberately restricted, preference-ordered OPTIMADE versions CSV. */
export function parseVersionsCsv(text) {
  if (typeof text !== "string") throw protocol("versions", "/versions body must be text");
  if (text.includes('"')) throw protocol("versions", "/versions CSV must not contain quotes");
  if (text.replace(/\r\n/g, "").includes("\r")) {
    throw protocol("versions", "/versions CSV has an invalid line ending");
  }
  const normalized = text.replace(/\r\n/g, "\n");
  const rows = (normalized.endsWith("\n") ? normalized.slice(0, -1) : normalized).split("\n");
  if (!rows.length || rows[0].split(",", 1)[0] !== "version") {
    throw protocol("versions", "/versions CSV header must begin with version");
  }
  if (rows.length === 1) throw protocol("versions", "/versions CSV has no advertised versions");
  const seen = new Set();
  return rows.slice(1).map((row, index) => {
    const value = row.split(",", 1)[0];
    if (!row || !VERSION_MAJOR.test(value)) {
      throw protocol("versions", `/versions CSV has an invalid row at line ${index + 2}`);
    }
    const major = Number(value);
    if (seen.has(major)) throw protocol("versions", `/versions CSV duplicates major ${major}`);
    seen.add(major);
    return major;
  });
}

/**
 * Stateful OPTIMADE discovery and page transport.
 *
 * @param {object} configuration widget configuration emitted by httk-serve
 * @param {string} configuration.base_url OPTIMADE base URL (absolute or page-relative)
 * @param {string} configuration.entry_type entry endpoint name
 * @param {Array<string|{key: string}>} [configuration.columns] selected table columns
 * @param {string[]} [configuration.response_fields] selected OPTIMADE fields
 * @param {number} [configuration.page_size] requested page size (defaults to 1 for response_fields-only use)
 * @param {string[]} [configuration.allowed_origins] extra permitted origins
 * @param {string} [configuration.filter] complete OPTIMADE filter value
 * @param {string} [configuration.sort] complete OPTIMADE sort value
 * @param {object} options runtime dependencies, notably fetch and documentBase
 */
export class OptimadeTransport {
  constructor(configuration, options = {}) {
    if (!isObject(configuration)) throw protocol("configuration", "OPTIMADE configuration must be an object");
    this.fetch = options.fetch ?? globalThis.fetch;
    if (typeof this.fetch !== "function") throw protocol("configuration", "fetch is unavailable");
    this.bodyLimit = positiveInteger(options.bodyLimit ?? DEFAULT_BODY_LIMIT_BYTES, "bodyLimit");
    this.documentBase = options.documentBase ?? globalThis.document?.baseURI;
    if (typeof this.documentBase !== "string") throw protocol("configuration", "documentBase is required");

    this.requestedBase = checkedUrl(configuration.base_url, this.documentBase);
    this.requestedBase = stripTrailingSlash(this.requestedBase);
    this.entryType = nonemptyString(configuration.entry_type, "entry_type");
    this.pageSize = configuration.page_size === undefined && configuration.response_fields !== undefined
      ? 1
      : positiveInteger(configuration.page_size, "page_size");
    this.filter = optionalConfigurationString(configuration.filter, "filter");
    this.sort = optionalConfigurationString(configuration.sort, "sort");
    this.selectedFields = selectedFields(configuration.columns, configuration.response_fields);
    this.extraOrigins = new Set((configuration.allowed_origins ?? []).map((origin) => canonicalOrigin(origin)));
    this.allowedOrigins = new Set([...this.extraOrigins, this.requestedBase.origin]);
    this.apiBase = null;
    this.discovery = null;
  }

  /** Negotiate (when needed) and validate /info plus /info/<entry_type>. */
  async discover() {
    if (this.discovery !== null) return this.discovery;
    const explicit = EXPLICIT_VERSION.test(lastPathSegment(this.requestedBase));
    if (!explicit && /^v/i.test(lastPathSegment(this.requestedBase))) {
      throw protocol("versions", "OPTIMADE base URL has a malformed explicit version");
    }
    if (explicit) {
      this.apiBase = this.requestedBase;
    } else {
      const versions = await this.#request(appendPath(this.requestedBase, "versions"), "csv");
      const major = parseVersionsCsv(versions.text).find((item) => item === 1);
      if (major === undefined) throw protocol("versions", "server does not advertise supported OPTIMADE major 1");
      // Final URL validation remains defense in depth; redirects are rejected below.
      this.apiBase = appendPath(parentPath(versions.url), "v1");
    }
    this.allowedOrigins = new Set([...this.extraOrigins, this.apiBase.origin]);

    const info = await this.#request(appendPath(this.apiBase, "info"), "json");
    const apiVersion = validateInfo(parseJson(info.text, "/info"), this.entryType);
    const entryInfo = await this.#request(appendPath(this.apiBase, `info/${encodeURIComponent(this.entryType)}`), "json");
    const advertisedFields = validateEntryInfo(parseJson(entryInfo.text, `/info/${this.entryType}`), apiVersion, this.entryType, this.selectedFields);
    this.discovery = Object.freeze({
      apiBaseUrl: this.apiBase.href,
      apiVersion,
      entryType: this.entryType,
      advertisedFields: Object.freeze([...advertisedFields]),
    });
    return this.discovery;
  }

  /**
   * Fetch and validate one page. Pass a previously returned nextUrl to follow
   * it; otherwise filter and sort are each sent as one complete query value.
   */
  async fetchPage({ filter = this.filter, sort = this.sort, nextUrl = null, signal = undefined } = {}) {
    await this.discover();
    let requestUrl;
    if (nextUrl !== null) {
      requestUrl = checkedUrl(nextUrl, this.apiBase);
    } else {
      requestUrl = appendPath(this.apiBase, encodeURIComponent(this.entryType));
      const query = new URLSearchParams();
      const responseFields = this.selectedFields.filter((field) => field !== "id" && field !== "type");
      if (responseFields.length) query.set("response_fields", responseFields.join(","));
      query.set("page_limit", String(this.pageSize));
      if (filter !== null) query.set("filter", optionalString(filter, "filter"));
      if (sort !== null) query.set("sort", optionalString(sort, "sort"));
      requestUrl.search = query.toString();
    }
    const page = await this.#request(requestUrl, "json", signal);
    return validatePage(parseJson(page.text, "entry page"), {
      entryType: this.entryType,
      pageSize: this.pageSize,
      selectedFields: this.selectedFields,
      responseUrl: page.url,
      allowUrl: (url) => this.#allowedUrl(url),
    });
  }

  /** Fetch and validate one resource, including explicitly requested relations. */
  async fetchOne(id, { include = [], signal = undefined } = {}) {
    await this.discover();
    if (typeof id !== "string" || !id) throw protocol("configuration", "id must be a non-empty string");
    if (!Array.isArray(include) || include.some((item) => typeof item !== "string" || !item)) {
      throw protocol("configuration", "include must be an array of non-empty strings");
    }
    const requestUrl = appendPath(this.apiBase, encodeURIComponent(this.entryType));
    requestUrl.pathname += `/${encodeURIComponent(id)}`;
    const query = new URLSearchParams();
    const responseFields = this.selectedFields.filter((field) => field !== "id" && field !== "type");
    if (responseFields.length) query.set("response_fields", responseFields.join(","));
    if (include.length) query.set("include", include.join(","));
    requestUrl.search = query.toString();
    const response = await this.#request(requestUrl, "json", signal);
    const root = parseJson(response.text, "single entry");
    validateMeta(root, "single entry");
    let included = [];
    if (Object.hasOwn(root, "included")) {
      if (!Array.isArray(root.included)) throw protocol("single entry", "OPTIMADE included must be an array");
      included = root.included.map((item) => validateIncluded(item));
    }
    if (root.data === null) return null;
    const resource = validateResource(root.data, {
      entryType: this.entryType,
      selectedFields: this.selectedFields,
      label: "single entry",
      relationships: true,
    });
    return { resource, included };
  }

  #allowedUrl(value) {
    const url = checkedUrl(value, this.apiBase ?? this.requestedBase);
    if (!this.allowedOrigins.has(url.origin)) throw protocol("origin", "OPTIMADE URL targets a disallowed origin");
    return url;
  }

  async #request(value, expectedContentType, signal = undefined) {
    const requested = this.#allowedUrl(value);
    let response;
    try {
      // Browsers require native fetch to run with the global object as its receiver; a bare
      // this.fetch(...) call would throw "Illegal invocation". The stored reference is kept
      // unbound so it remains a stable discovery-cache key.
      response = await this.fetch.call(globalThis, requested.href, {
        method: "GET",
        credentials: "omit",
        redirect: "error",
        signal,
        headers: { Accept: expectedContentType === "csv" ? "text/csv" : "application/vnd.api+json, application/json" },
      });
    } catch (cause) {
      throw protocol("network", "OPTIMADE request could not be completed", cause);
    }
    if (!isObject(response) || typeof response.status !== "number") {
      throw protocol("network", "fetch returned an invalid response");
    }
    let finalUrl;
    try {
      finalUrl = this.#allowedUrl(response.url || requested.href);
    } catch (error) {
      await cancelBody(response);
      throw error;
    }
    const contentType = contentTypeOf(response);
    if (response.status < 200 || response.status >= 300) {
      throw await httpError(response, contentType, this.bodyLimit);
    }
    if (expectedContentType === "csv" ? contentType !== "text/csv" : !isJsonContentType(contentType)) {
      await cancelBody(response);
      throw protocol("content_type", `OPTIMADE response has unsupported content type ${contentType || "(missing)"}`);
    }
    return { text: await readBoundedText(response, this.bodyLimit), url: finalUrl };
  }
}

function protocol(code, message, cause) {
  return new OptimadeProtocolError(code, message, cause === undefined ? {} : { cause });
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function nonemptyString(value, label) {
  if (typeof value !== "string" || !value) throw protocol("configuration", `${label} must be a non-empty string`);
  return value;
}

function optionalString(value, label) {
  if (typeof value !== "string" || !value) throw protocol("configuration", `${label} must be a non-empty string when supplied`);
  return value;
}

function optionalConfigurationString(value, label) {
  if (value === undefined || value === null) return null;
  return optionalString(value, label);
}

function positiveInteger(value, label) {
  if (!Number.isSafeInteger(value) || value <= 0) throw protocol("configuration", `${label} must be a positive integer`);
  return value;
}

function checkedUrl(value, base) {
  if (typeof value !== "string" && !(value instanceof URL)) throw protocol("url", "OPTIMADE URL must be a string");
  let url;
  try {
    url = new URL(value, base);
  } catch {
    throw protocol("url", "OPTIMADE URL is invalid");
  }
  canonicalOrigin(url.href);
  return url;
}

function stripTrailingSlash(url) {
  const normalized = new URL(url.href);
  normalized.pathname = normalized.pathname.replace(/\/+$/, "") || "/";
  normalized.search = "";
  normalized.hash = "";
  return normalized;
}

function appendPath(base, segment) {
  const url = new URL(base.href);
  url.pathname = `${url.pathname.replace(/\/+$/, "")}/${segment}`;
  url.search = "";
  url.hash = "";
  return url;
}

function parentPath(url) {
  const result = new URL(url.href);
  const segments = result.pathname.replace(/\/+$/, "").split("/");
  segments.pop();
  result.pathname = segments.join("/") || "/";
  result.search = "";
  result.hash = "";
  return result;
}

function lastPathSegment(url) {
  const parts = url.pathname.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1];
}

function selectedFields(columns, responseFields) {
  if (responseFields !== undefined) {
    if (!Array.isArray(responseFields) || responseFields.some((field) => typeof field !== "string" || !/^[A-Za-z_][A-Za-z0-9_.]*$/.test(field))) {
      throw protocol("configuration", "response_fields must be an array of OPTIMADE identifiers");
    }
    return [...new Set(responseFields)];
  }
  if (!Array.isArray(columns) || !columns.length) throw protocol("configuration", "columns must be a non-empty array");
  const fields = columns.map((column) => typeof column === "string" ? column : column?.key);
  if (fields.some((field) => typeof field !== "string" || !field)) {
    throw protocol("configuration", "every column must have a string key");
  }
  return [...new Set(fields)];
}

function contentTypeOf(response) {
  const value = response.headers?.get?.("content-type");
  return typeof value === "string" ? value.split(";", 1)[0].trim().toLowerCase() : "";
}

function isJsonContentType(value) {
  return value === "application/vnd.api+json" || value === "application/json";
}

async function cancelBody(response) {
  try {
    await response.body?.cancel?.();
  } catch {
    // The original protocol error remains more useful than cancellation failure.
  }
}

async function readBoundedText(response, limit) {
  const contentLength = response.headers?.get?.("content-length");
  if (contentLength !== null && contentLength !== undefined && /^\d+$/.test(contentLength) && Number(contentLength) > limit) {
    await cancelBody(response);
    throw protocol("body_limit", "OPTIMADE response body exceeds the size limit");
  }
  if (!response.body?.getReader) throw protocol("body", "OPTIMADE response has no readable body");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let bytes = 0;
  let text = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!(value instanceof Uint8Array)) throw protocol("body", "OPTIMADE response stream yielded invalid bytes");
      bytes += value.byteLength;
      if (bytes > limit) {
        await reader.cancel();
        throw protocol("body_limit", "OPTIMADE response body exceeds the size limit");
      }
      text += decoder.decode(value, { stream: true });
    }
    return text + decoder.decode();
  } catch (error) {
    if (error instanceof OptimadeProtocolError) throw error;
    await reader.cancel().catch(() => undefined);
    throw protocol("body", "OPTIMADE response body is not valid UTF-8", error);
  } finally {
    reader.releaseLock?.();
  }
}

async function httpError(response, contentType, limit) {
  if (!isJsonContentType(contentType)) {
    await cancelBody(response);
    return new OptimadeHttpError(response.status);
  }
  try {
    const root = parseJson(await readBoundedText(response, limit), "error response");
    const first = Array.isArray(root.errors) ? root.errors[0] : null;
    const title = typeof first?.title === "string" ? boundedText(first.title, 160) : null;
    const detail = typeof first?.detail === "string" ? boundedText(first.detail, 320) : null;
    return new OptimadeHttpError(response.status, title, detail);
  } catch {
    return new OptimadeHttpError(response.status);
  }
}

function boundedText(value, maximum) {
  return value.replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, maximum) || null;
}

function parseJson(text, label) {
  try {
    const value = JSON.parse(text);
    if (!isObject(value)) throw new Error();
    return value;
  } catch {
    throw protocol("json", `${label} is not a JSON object`);
  }
}

function semver(value, label) {
  if (typeof value !== "string") throw protocol("discovery", `${label} must be a semantic version`);
  const match = SEMVER.exec(value);
  if (!match || Number(match[1]) !== 1) throw protocol("discovery", `${label} must declare OPTIMADE major 1`);
  return { value, minor: Number(match[2]) };
}

function stringArray(value, label) {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw protocol("discovery", `${label} must be an array of strings`);
  }
  return value;
}

function validateInfo(root, entryType) {
  const data = root.data;
  if (!isObject(data) || data.type !== "info" || data.id !== "/") {
    throw protocol("discovery", "/info data must identify the info resource");
  }
  const attributes = data.attributes;
  if (!isObject(attributes)) throw protocol("discovery", "/info attributes must be an object");
  const api = semver(attributes.api_version, "/info api_version");
  if (!stringArray(attributes.formats, "/info formats").includes("json")) {
    throw protocol("discovery", "/info must advertise JSON support");
  }
  let advertised = false;
  if (Object.hasOwn(attributes, "entry_types_by_format")) {
    if (!isObject(attributes.entry_types_by_format)) throw protocol("discovery", "/info entry_types_by_format must be an object");
    const entries = stringArray(attributes.entry_types_by_format.json, "/info entry_types_by_format.json");
    advertised ||= entries.includes(entryType);
  }
  if (Object.hasOwn(attributes, "available_endpoints")) {
    advertised ||= stringArray(attributes.available_endpoints, "/info available_endpoints").includes(entryType);
  }
  if (!advertised) throw protocol("discovery", `/info does not advertise entry type ${entryType}`);
  return api;
}

function validateEntryInfo(root, api, entryType, selected) {
  const data = root.data;
  if (!isObject(data)) throw protocol("discovery", `/info/${entryType} data must be an object`);
  if (api.minor >= 2 && (data.type !== "info" || data.id !== entryType)) {
    throw protocol("discovery", `/info/${entryType} data must identify its info resource`);
  }
  if (!isObject(data.properties)) throw protocol("discovery", `/info/${entryType} properties must be an object`);
  if (!stringArray(data.formats, `/info/${entryType} formats`).includes("json")) {
    throw protocol("discovery", `/info/${entryType} must advertise JSON support`);
  }
  if (!isObject(data.output_fields_by_format)) throw protocol("discovery", `/info/${entryType} output_fields_by_format must be an object`);
  const output = new Set(stringArray(data.output_fields_by_format.json, `/info/${entryType} output_fields_by_format.json`));
  for (const field of selected) {
    if (field === "id" || field === "type") continue;
    if (!Object.hasOwn(data.properties, field) || !output.has(field)) {
      throw protocol("discovery", `/info/${entryType} does not advertise selected field ${field}`);
    }
  }
  return output;
}

function validatePage(root, { entryType, pageSize, selectedFields, responseUrl, allowUrl }) {
  validateMeta(root, "page");
  if (!Array.isArray(root.data) || root.data.length > pageSize) {
    throw protocol("page", "OPTIMADE page data must be an array no larger than page_limit");
  }
  if (Object.hasOwn(root.meta, "data_returned") && (!Number.isSafeInteger(root.meta.data_returned) || root.meta.data_returned < 0 || root.meta.data_returned !== root.data.length)) {
    throw protocol("page", "OPTIMADE page has invalid meta.data_returned");
  }
  const more = Object.hasOwn(root.meta, "more_data_available") ? root.meta.more_data_available : false;
  if (typeof more !== "boolean") throw protocol("page", "OPTIMADE page has invalid meta.more_data_available");
  for (const resource of root.data) {
    validateResource(resource, { entryType, selectedFields, label: "page" });
  }
  let next = null;
  if (Object.hasOwn(root, "links")) {
    if (!isObject(root.links)) throw protocol("page", "OPTIMADE page links must be an object");
    if (Object.hasOwn(root.links, "next")) {
      const link = root.links.next;
      const href = typeof link === "string" ? link : isObject(link) ? link.href : null;
      if (link !== null && (typeof href !== "string" || !href || href !== href.trim())) {
        throw protocol("page", "OPTIMADE page links.next must be a usable string or link object href");
      }
      if (typeof href === "string") {
        next = allowUrl(new URL(href, responseUrl)).href;
      }
    }
  }
  if (more !== (next !== null)) throw protocol("page", "OPTIMADE page has inconsistent more_data_available and links.next");
  return Object.freeze({ resources: Object.freeze(root.data), nextUrl: next, moreDataAvailable: more, responseUrl: responseUrl.href });
}

function validateMeta(root, label) {
  if (!isObject(root.meta)) throw protocol(label, `OPTIMADE ${label} meta must be an object`);
  semver(root.meta.api_version, `${label} meta.api_version`);
}

function validateResource(resource, { entryType, selectedFields, label, relationships = false }) {
  if (!isObject(resource) || resource.type !== entryType || typeof resource.id !== "string" || !isObject(resource.attributes)) {
    throw protocol(label, `OPTIMADE ${label} contains an invalid resource`);
  }
  for (const field of selectedFields) {
    if (field !== "id" && field !== "type" && !Object.hasOwn(resource.attributes, field)) {
      throw protocol(label, `OPTIMADE resource omits selected field ${field}`);
    }
  }
  if (relationships && Object.hasOwn(resource, "relationships")) validateRelationships(resource.relationships, label);
  const result = { id: resource.id, type: resource.type, attributes: resource.attributes };
  if (Object.hasOwn(resource, "relationships")) result.relationships = resource.relationships;
  return result;
}

function validateIncluded(resource) {
  if (!isObject(resource) || typeof resource.type !== "string" || typeof resource.id !== "string" || !isObject(resource.attributes)) {
    throw protocol("single entry", "OPTIMADE included contains an invalid resource");
  }
  if (Object.hasOwn(resource, "relationships")) validateRelationships(resource.relationships, "single entry");
  const result = { id: resource.id, type: resource.type, attributes: resource.attributes };
  if (Object.hasOwn(resource, "relationships")) result.relationships = resource.relationships;
  return result;
}

function validateRelationships(value, label) {
  if (!isObject(value)) throw protocol(label, "OPTIMADE relationships must be an object");
  for (const relationship of Object.values(value)) {
    if (!isObject(relationship) || !Object.hasOwn(relationship, "data")) {
      throw protocol(label, "OPTIMADE relationship must contain data");
    }
    const data = relationship.data;
    if (data === null) continue;
    if (Array.isArray(data)) {
      if (data.some((item) => !isObject(item) || typeof item.type !== "string" || typeof item.id !== "string")) {
        throw protocol(label, "OPTIMADE relationship data is invalid");
      }
    } else if (!isObject(data) || typeof data.type !== "string" || typeof data.id !== "string") {
      throw protocol(label, "OPTIMADE relationship data is invalid");
    }
  }
}
