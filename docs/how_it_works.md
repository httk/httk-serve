# How it works

*httk-optimade* implements an OPTIMADE server as a pipeline of small,
independently testable layers. The package was ported from the OPTIMADE
implementation in httk v1 and reorganized around two explicit seams: a
web-framework seam and a storage-backend seam.

## Request flow

```
ASGI request
  └─ runtime/asgi.py     — adapts the HTTP request to a RawRequest
       └─ engine/processing.py — process(): dispatches to an endpoint
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

A future *httk₂* database module can plug in by implementing the protocols
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
  them,
- sorting of entry listings (the `sort` query parameter),
- relationships between entries and the `include` query parameter (compound
  documents with a top-level `included` field),
- the `references`, `files`, and `trajectories` entry types,
- per-property metadata (`meta.property_metadata` and the
  `x-optimade-metadata-definition` in property definitions),
- the partial data protocol (JSON Lines format and the `dimension_slices`
  query parameter) with the compact list representation for trajectories,
- the `license`, `available_licenses`, and `available_licenses_for_entries`
  base-info attributes, and the `warnings`, `last_id`, and `links.describedby`
  response fields via `OptimadeConfig`.

Optional parts of the specification that are not implemented: cross-source sort
merging, filtering on relationship `.target.*`/`.description`/`.role`
properties, the sparse JSON Lines layout, index meta-databases, transaction
mechanisms, and rejection of unrecognized query parameters.

## Serving additional entry types

The served entry types and their properties are described by a `ServedSchema`
(`schema/served.py`), built with `build_served_schema()`. A backend registers
extra entry types by passing them in and wiring an `EntrySource` for each:

- `build_served_schema(entries={...})` derives the endpoint/field tables from a
  per-entry list of served properties; `extra_entry_info=` injects entry-info
  blocks for entry types whose property definitions are generated rather than
  taken from the built-in `schema/entries.py` (this is how `trajectories` wraps
  the structures properties — see `schema/trajectories.py`).
- `BackendAdapter(schema=..., sources={entry: (EntrySource(...),)})` binds each
  served entry type to a queryable target and its field extractors.

The `examples/demo_server/` backend registers `references`, `files`, and
`trajectories` this way alongside `structures` and `calculations`.

## Large properties and slicing

Field extractors may return a `PartialValue` (`backend/partial.py`) instead of
a concrete value for large, dimensioned properties (e.g. a trajectory's
`cartesian_site_positions`). In a normal reply such a value is served as `null`
with a `meta.partial_data_links` entry pointing at
`partial_data/<type>/<id>/<property>`, which streams the data in the JSON Lines
format. A client may instead request an inline slice with the
`dimension_slices=name[start:stop:step]` query parameter (single-entry
endpoints only); note that `stop` is *inclusive* per the specification. Values
that are constant across a dimension (e.g. `nelements` across a trajectory's
frames) are served as single-item `constant` compact lists, which is legal
because those axes are declared `compactable`.

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
