# How it works

*httk-serve* implements an OPTIMADE server as a pipeline of small,
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

1. **Runtime** (`httk.serve.optimade.runtime`): a Starlette application with a single
   catch-all GET route. It builds a `RawRequest` (base URL, representation,
   query string) and renders the resulting `EndpointResponse`. All errors are
   converted to OPTIMADE JSON:API error documents.

2. **Engine** (`httk.serve.optimade.engine`): `validate_optimade_request()` resolves
   the endpoint (including versioned base URLs like `/v1.0.0/structures`),
   validates query parameters, and computes the response fields.
   `process()` routes the validated request to the matching endpoint reply
   generator, parsing the `filter=` parameter when present.

3. **Filter parsing** (`httk.serve.optimade.filter`): the OPTIMADE filter grammar
   (shipped as `optimade_filter_grammar.ebnf`) is parsed with a vendored LR(1)
   parser into a nested-tuple abstract syntax tree.

4. **Backend** (`httk.serve.optimade.backend`): the filter AST is translated into
   backend search expressions and executed. This is the storage seam.

## The backend seam

`process()` never touches storage directly; it calls a `QueryFunction`
callback. The standard implementation is provided by `BackendAdapter`:

- **`Store`/`Searcher` protocols** (`backend/protocols.py`) describe the query
  interface a storage backend must implement (mirroring the httk v1
  `httk.db` searcher API: `variable()`, `add()`, `count()`, set operations
  like `has_any`, literal string matching (`contains`/`startswith`/`endswith`,
  which carry no pattern syntax), and the comparison operators).

- **`EntrySource`** pairs a queryable target (e.g. a table or type) with a
  mapping from OPTIMADE response fields to row extractors. An entry endpoint
  can be backed by several sources; results are concatenated and
  offset/limit are redistributed across them.

- **Field handlers** (`backend/handlers.py`) translate filter operations on
  OPTIMADE properties into search expressions over backend fields. A backend
  supplies a handler table per entry type; `simple_property_handlers()` derives
  one generically from a property-key map and the entry's property types. When a
  `BackendAdapter` is given no handlers, it derives them from its schema.

*httk-serve* is a generic implementation of the OPTIMADE *protocol*: it carries
no materials-science knowledge of its own. In a durable deployment, configured
entry families are discovered from `EntryStore` and queried lazily through
`adapter_from_store`; see [Serving directly from an entry
store](serving_stores.md). A record saved after application construction is
visible without rebuilding an adapter. `EntryProvider` is the separate
in-memory ingestion path for generated and compatibility datasets.

(entry-providers)=
## Entry providers

For a worked usage guide with runnable examples, see
[Serving entry providers](serving_providers.md).

The materials-science mapping lives *outside* *httk-serve*, behind the neutral
`httk.core.EntryProvider` contract (defined in *httk-core*, so neither package
depends on the other). A provider describes its entry types (as first-class
`httk.core.EntryTypeDefinition` objects — vendored standards or definitions built
with `from_optimade`/`from_simple`), states how each served property maps to a
record key, and yields the records:

- `entry_types()` → entry-type name to an `EntryTypeDefinition`;
- `property_keys(entry_type)` → served-property to record-key map (at least `id`/`type`;
  every served name must be described by the definition);
- `records(entry_type)` → an iterable of plain JSON-able record dicts;
- `relationships(entry_type)` → *optional* map of entry id to a flat tuple of
  related entries (the serving layer groups them by `relationship or entry_type`);
- `reverse_relationships()` → *optional*, taking no argument: a nested mapping
  (target entry type → target id → related-entry tuple) of derived reverse edges,
  which `adapter_from_providers` append-merges onto the targets' forward blocks
  in a post-loop pass so both directions are served.

`adapter_from_providers([...])` (`backend/providers.py`) turns one or more
providers into a fully wired `BackendAdapter` over an in-memory store: it builds
the `ServedSchema` from the definitions (validating each served property
against the definition), the filter handlers from the property keys, and the
response-field extractors from the property keys, then loads the records.

`adapter_from_store(store)` (`backend/stores.py`) instead reads the store's
declared entry layout, ignores families without an OPTIMADE definition, and
builds a `StoredBackendAdapter`. Its query callback delegates filtering,
sorting, counting, pagination, and record hydration to durable entry
federations. It never calls `EntryProvider.records()` and never copies the
database into an `InMemoryStore`.

The materials provider itself lives in *httk-atomistic*
(`httk.atomistic.entries.structures.StructureEntryProvider`), which serves
OPTIMADE `structures` — `species`, `species_at_sites`, `lattice_vectors`,
`cartesian_site_positions`, ... — from `httk.atomistic.UnitcellStructure` objects.
Providers self-register a factory via `httk.core.register_entry_provider`, so
`providers_from_registry()` can enumerate the installed ones (applications
instantiate them with their data).

## OPTIMADE version support

The served API version is **1.3.0** (versioned base URLs `/v1`, `/v1.3`, `/v1.3.0`).
Relative to OPTIMADE v1.0.0, the implementation includes:

- boolean values (`TRUE`/`FALSE`) in the filter language (v1.2),
- the v1.2 entry listing info format: top-level `id`/`type` and properties
  presented as OPTIMADE Property Definitions (the property-definition model and
  generator live in *httk-core*, `httk.core.property_definitions`),
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
  documents with a top-level `included` field), with per-identifier
  relationship `meta` (`description`, the v1.3 `role`, and the httk-specific
  `_httk_label`),
- filtering on relationships: `<type>.id HAS ...` and depth-1
  relationship-property filters such as `references.doi CONTAINS "10.1"`
  (resolved by a two-phase semi-join over the related entry type; each dotted
  filter node is resolved independently, matching the reference
  implementation's semantics), plus the httk-specific `_httk_relationships.<key>.id`
  filter extension (see below) that also filters by the semantic provenance keys.
  Coverage is route-aware: the in-memory provider route supports both `<type>.id`
  filtering and the depth-1 related-property filters; the durable stored route
  gained `<type>.id` relationship filtering in this series (previously bare
  `<type>.id` there matched nothing), while its depth-1 related-property filters
  still match nothing (deferred),
- the `references`, `files`, and `trajectories` entry types,
- per-property metadata (`meta.property_metadata` and the
  `x-optimade-metadata-definition` in property definitions),
- the partial data protocol (JSON Lines format and the `dimension_slices`
  query parameter) with the compact list representation for trajectories,
- the `license`, `available_licenses`, and `available_licenses_for_entries`
  base-info attributes, and the `warnings`, `last_id`, and `links.describedby`
  response fields via `OptimadeConfig`.

Optional parts of the specification that are not implemented: cross-source sort
merging, filtering on relationship paths nested deeper than one level
(`references.structures.x`), on relationship `meta`
(`.description`/`.role`), and dotted `LENGTH` filters, the sparse JSON Lines
layout, transaction mechanisms, and rejection of unrecognized query parameters.

## The `_httk_relationships` filter extension

httk adds one provider-specific extension to the OPTIMADE filter grammar for
filtering entries by their served relationships:

```
filter=_httk_relationships.<key>.id HAS "<related id>"
```

`<key>` is any key that appears in the entry's `relationships` object — either a
type-keyed block (`references`, `structures`, ...) or one of the semantic
provenance keys (the forward `_httk_has_input` / `_httk_has_artifact` /
`_httk_has_output` on runs, and the derived reverse `_httk_is_input` /
`_httk_is_artifact` / `_httk_is_output` on the targeted entries). For the
type-keyed blocks this is exactly equivalent to the standard `<type>.id HAS ...`
spelling; the semantic keys are reachable only through the extension, since they
have no standard spelling. On the in-memory provider route the bare `<type>.id`
spelling has always worked; on the durable stored route the bare typed spelling
only now really filters (a conformance fix landed in this series — previously
`references.id` there silently matched nothing and `_httk_runs.id` returned a
`400`).

The full `HAS` family is supported with the usual set semantics — `HAS`,
`HAS ALL`, `HAS ANY`, and `HAS ONLY` (vacuously true for an entry with no
related entries of that key). Forward keys evaluate the filtered row's own
revision-pinned edges; reverse keys evaluate the current latest-main runs that
reference the row (and never match on an entry's `~alts` alternative cells).

`_httk_relationships` is a *filter-grammar* extension, not a property: it never
appears as a response field, in `sort=`, or on the `/info` endpoints, and it has
no property definition. Filtering on an unknown key under the extension (for
example `_httk_relationships.bogus.id`) is a `400 Bad Request` naming the full
dotted identifier. The set of filterable keys is derived from the mounted
backing schemas on the durable stored route (a declared key with no matching
data simply filters to an empty result), and from the observed relationship data
on the in-memory provider route (only keys present in the served data are
filterable, so an empty in-memory dataset `400`s on any key).

A few boundaries apply. Non-`HAS` operators against the extension are a
`400 Bad Request` naming only the `_httk_relationships` root (cosmetic). The
semantic provenance relationships are served by any provider that declares them
(the in-memory provider route, through the hook), by the SQL provider path, and
by the durable stored federation; Mongo covers the provider path only, and Mongo
federation serves no relationships (so there is no `_httk_relationships`
filtering on a Mongo-federated route). The forward-only `_httk_has_product`
relationship — a data→data curation edge between data entries (`ProductLink`),
whose wire prefix is merely anchored on the runs definition, not a run edge — is
emitted, and filterable, on the provider path only, with no derived reverse.

## Index meta-databases and composition

An index is configured separately from a normal backend-backed OPTIMADE
service. Its links contain one `root` and any `child`, `external`, or
`providers` entries; `default_link_id` optionally selects a child:

```python
from httk.serve import ASGIAppMount, compose_asgi_apps
from httk.serve.optimade import OptimadeIndexConfig, create_index_asgi_app

index = create_index_asgi_app(
    OptimadeIndexConfig(links=[root_link, amdb_link], default_link_id="amdb"),
    baseurl="https://example.org/optimade/index/",
)
app = compose_asgi_apps(
    [ASGIAppMount("/optimade/index", index), ASGIAppMount("/optimade/amdb", amdb_app)],
    root=ASGIAppMount("/", website_app),
)
```

`compose_asgi_apps` orders nested mounts from most specific to least specific
and coordinates the Starlette lifespan of every child. The index has no
backend adapter: its `/info`, `/links`, and unversioned `/versions` responses
are produced by the same request, version, rendering, reporting, and CORS
pipeline as an ordinary service.

## Serving additional entry types

The served entry types and their properties are described by a `ServedSchema`
(`schema/served.py`), built with `build_served_schema()`. A backend registers
extra entry types by passing them in and wiring an `EntrySource` for each:

- `build_served_schema(definitions, served)` derives the endpoint/field tables
  from a mapping of entry type to `EntryTypeDefinition` and the per-entry list of
  served property names (defaulting to every described property). The
  `trajectories` entry type is generated by frame-wrapping the `structures`
  properties (`schema/trajectories.py`, which takes the structures
  `EntryTypeDefinition`) and turning the result into a definition via
  `entry_type_definition_from_simple` (in `schema.served`).
- `BackendAdapter(schema=..., sources={entry: (EntrySource(...),)})` binds each
  served entry type to a queryable target and its field extractors.

The `examples/optimade/demo_server/` backend registers `references`, `files`, and
`trajectories` this way alongside `structures` and `calculations`.

Store-backed schemas additionally route revision paths through their derived
`_httk_<entry>~revs` endpoint and named-alternative paths through their derived
`_httk_<entry>~alts` endpoint, while preserving the base entry type for data
federation and response resource types.

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
