# How it works

*httk-optimade* implements an OPTIMADE server as a pipeline of small,
independently testable layers. The package was ported from the OPTIMADE
implementation in httk v1 and reorganized around two explicit seams: a
web-framework seam and a storage-backend seam.

## Request flow

```
ASGI request
  └─ runtime/asgi.py     — adapts the HTTP request to a RawRequest
       └─ engine/process.py    — process(): dispatches to an endpoint
            ├─ engine/validate.py    — validates URL, version, and query parameters
            ├─ filter/               — parses the OPTIMADE filter language to an AST
            ├─ query_function        — the backend seam (executes entry queries)
            └─ endpoints/            — builds the JSON:API reply documents
```

1. **Runtime** (`httk.optimade.runtime`): a Starlette application with a single
   catch-all GET route. It builds a `RawRequest` (base URL, representation,
   query string) and renders the resulting `EndpointResponse`. All errors are
   converted to OPTIMADE JSON:API error documents.

2. **Engine** (`httk.optimade.engine`): `validate_optimade_request()` resolves
   the endpoint (including versioned base URLs like `/v1.0.0/structures`),
   validates query parameters, and computes the response fields.
   `process()` routes the validated request to the matching endpoint reply
   generator, parsing the `filter=` parameter when present.

3. **Filter parsing** (`httk.optimade.filter`): the OPTIMADE filter grammar
   (shipped as `optimade_filter_grammar.ebnf`) is parsed with a vendored LR(1)
   parser into a nested-tuple abstract syntax tree.

4. **Backend** (`httk.optimade.backend`): the filter AST is translated into
   backend search expressions and executed. This is the storage seam.

## The backend seam

`process()` never touches storage directly; it calls a `QueryFunction`
callback. The standard implementation is provided by `BackendAdapter`:

- **`Store`/`Searcher` protocols** (`backend/protocols.py`) describe the query
  interface a storage backend must implement (mirroring the httk v1
  `httk.db` searcher API: `variable()`, `add()`, `count()`, set operations
  like `has_any`, SQL-`like` string matching, and the comparison operators).

- **`EntrySource`** pairs a queryable target (e.g. a table or type) with a
  mapping from OPTIMADE response fields to row extractors. An entry endpoint
  can be backed by several sources; results are concatenated and
  offset/limit are redistributed across them.

- **Field handlers** (`backend/handlers.py`) translate filter operations on
  OPTIMADE properties into search expressions over backend columns. The
  default tables encode the httk database schema; adapters for other schemas
  can supply their own.

A future httk v2 database module can plug in by implementing the protocols
and shipping a `make_..._adapter(store) -> BackendAdapter` factory — no
changes in httk-optimade are needed. Until then, the repository's
`examples/demo_server/` shows a complete in-memory implementation.

## OPTIMADE version support

The served API version is **1.3.0** (versioned base URLs `/v1`, `/v1.3`, `/v1.3.0`).
Relative to OPTIMADE v1.0.0, the implementation includes:

- boolean values (`TRUE`/`FALSE`) in the filter language (v1.2),
- the v1.2 entry listing info format: top-level `id`/`type` and properties
  presented as OPTIMADE Property Definitions (`schema/property_definitions.py`),
- extended `meta` information: `implementation` with `source_url` and
  `issue_tracker`, and the optional `schema`, `database`, and `request_delay`
  fields via `OptimadeConfig`,
- the structures properties added in v1.2 (space-group symmetry fields) and
  v1.3 (`fractional_site_positions`, `site_coordinate_span`,
  `optimization_type`, `wyckoff_positions`, ...) — recognized in filters and
  as response fields; they are served as `null` until a backend implements
  them.

Optional parts of the specification that are not implemented: the `files`,
`trajectories`, and `references` entry types, the partial data protocol,
per-property metadata, transaction mechanisms, and sorting.

## Differences from the httk v1 implementation

- The stdlib `httk.httkweb.webserver` binding was replaced by a Starlette
  ASGI app + uvicorn development server (`serve()`), and
  `create_asgi_app()` supports production ASGI deployment.
- `serve()` takes a `BackendAdapter` instead of a raw `httk.db` store.
- The client-side `validation/` subpackage (stale at OPTIMADE 0.9.5) was not
  ported; use the official `optimade-validator` instead.
- Several latent bugs were fixed (query-string derivation, caller-supplied
  endpoints, offsets beyond the result set) — see the regression tests in
  `tests/`.
- Timestamps in `meta` are now UTC.
