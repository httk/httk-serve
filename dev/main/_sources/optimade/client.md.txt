# Reading a remote OPTIMADE service

`OptimadeStore` is the synchronous, read-only client entry point. It discovers
the remote schema eagerly, keeps the exact redacted `/info/<entry>` documents
that describe every result, and exposes the neutral `Store`/`Searcher` query
profile from *httk-store*.

```python
from httk.serve.optimade import OptimadeStore

with OptimadeStore("https://example.org/optimade") as store:
    for entry_type in store.entry_types:
        print(entry_type.name, entry_type.definition_id)
```

For an unversioned base URL, construction first requests `/versions`, reads its
strict preference-ordered restricted CSV, and selects the first major version
supported by httk (currently major `1`). It then requests `/v1/info`, followed
by `/v1/info/<name>` for every advertised entry endpoint, in advertised order.
`requested_base_url` is the normalized URL supplied by the caller and
`base_url` is the negotiated effective versioned URL used for all discovery and
query requests. Both public fields redact URL credentials; authenticated
transport requests retain the supplied credentials privately. An explicit final
path segment of `v1`, `v1.2`, or `v1.2.3` skips `/versions` and is used
unchanged; an explicit unsupported major, malformed version-like segment, or
version suffix fails before discovery. Malformed `/versions` CSV, duplicate
major versions, an empty list, or no compatible advertised major raise
`OptimadeVersionNegotiationError` rather than falling back to unversioned
`/info`.

The base `/info` response's `data.attributes.api_version` selects the
entry-info grammar. OPTIMADE 1.0 and 1.1 put `description`, `properties`,
`formats`, and `output_fields_by_format` directly in `/info/<entry>.data` and
do not require `type` or `id` there. OPTIMADE 1.2 and later use the newer info
resource form, for which httk requires `data.type == "info"`. This is protocol
version support, not a permissive fallback: a service that declares 1.2 or
later is still validated against the newer form. `api_version` exposes the
version declared by the service, or `None` for a legacy test/service that
omits it.

`entry_types` is an immutable tuple of
`RemoteEntryType` descriptors; `entry_type(name)` and the immutable
`entry_types_by_name` mapping provide transport-name lookup. `refresh()` makes
a complete new generation and replaces the descriptors only after every
request and validation succeeds, so objects from an earlier generation retain
their original schema snapshot.

## Semantic recognition

An endpoint's transport spelling is never its meaning. A `RemoteEntryType` is
typed only when its `links.describedby` is an exact registered entry-definition
IRI, or, when that link is absent, when the advertised property-definition
IRIs identify exactly one local binding. Missing, malformed, ambiguous, or
contradictory IRIs leave the endpoint generic. In particular, an unknown
`describedby` does not fall back to endpoint or property names.

Recognized standard endpoints resolve to `OptimadeReference`, `OptimadeFile`,
or `OptimadeCalculation`; a recognized structures endpoint resolves to
`httk.atomistic.OptimadeStructure` when *httk-atomistic* is installed. Generic
resources use `httk.core.optimade.OptimadeResource`. All of these retain their original
immutable source resource:

For ordinary use, obtain the backend from a result (shown below), then choose a
view only when needed: `ReferenceView(backend)`, `FileView(backend)`, and
`CalculationView(backend)` provide lazy canonical record views. For a typed
structure, `UnitcellStructureView(optimade_structure)` is likewise lazy: an
incomplete remote structure remains inspectable and storable until a component
that needs unavailable structural data is requested.

Every backend has `unwrap()`; `OptimadeResource.unwrap()` returns the immutable
JSON:API resource mapping. The enclosing `OptimadeDocument` keeps raw response
text (with credential-bearing URLs redacted) and the `OptimadeSchemaSnapshot`
that was current when the resource was read. This makes `unwrap()` the escape
hatch for unknown extension fields and exact source provenance, rather than a
lossy conversion API.

## One-root portable queries

The remote query implementation deliberately supports one root endpoint per
query. Start from either a unique typed backend class or, where there may be
more than one endpoint of a type, the exact `RemoteEntryType` descriptor.

```python
from httk.serve.optimade import OptimadeStore

store = OptimadeStore("https://example.org/optimade")
references = store.entry_type("references")

search = store.searcher()
reference = search.variable(references)
search.add(reference.id.startswith("cod/"))
rows = search.results(reference=reference, identifier=reference.id)

for row in rows:
    print(row.identifier, row.reference.unwrap().document.source_url)
```

`id`, `type`, `immutable_id`, and `last_modified` are the portable core fields
where they are advertised. Typed definitions can expose additional portable
query-supported scalar and flat-list properties; the exact available fields
come from the discovered definition rather than from their remote spelling.
List fields support singular `has(value)`, `has_any(...)`, and
`has_only(...)`. Use `add()`, `add_sort()`, `set_limit()`, and `add_offset()`
to finish the single-root plan. Relationship traversal, joins, writes, and
asynchronous queries are outside this client.

`results()` produces a lazy, reusable `RemoteResultSet`. It supports iteration,
`first()` (`None` when empty), `one()` (raising `NoResultError` or
`MultipleResultsError`), `scalars()`, and scalar `column(name)` projections.
`search.count()` and `len(results)` use the server's filtered
`meta.data_returned` (the total for the current filter query, independent of
pagination); they raise `CountUnavailableError` when that exact count is absent
or invalid. `results[start:stop]` makes a derived lazy plan when
both bounds are nonnegative integers and the step is omitted or `1`; integer
indexing, negative bounds, and non-unit steps are unsupported. Cursor rows are
not implemented.

## Federating endpoints

`FederatedStore` (from *httk-store*) combines already-open stores into one
read-only, source-major union. Manage the remote connections yourself: the
federation borrows them and never closes either endpoint.

```python
from contextlib import ExitStack

from httk.atomistic import OptimadeStructure
from httk.store import FederatedStore
from httk.serve.optimade import OptimadeStore

with ExitStack() as stack:
    first = stack.enter_context(OptimadeStore("https://first.example/optimade"))
    second = stack.enter_context(OptimadeStore("https://second.example/optimade"))
    combined = FederatedStore({"first": first, "second": second})

    search = combined.searcher()
    structure = search.variable(OptimadeStructure)
    search.add(structure.elements.has("Li"))
    rows = search.results(record=structure, origin=search.origin)
    for row in rows:
        print(row.origin, row.record.id)
```

Only the strict intersection of source query support is portable: a source
failure or unsupported field raises rather than returning a partial result.
The federation is a union, not a deduplicating merge, so equal resource IDs
from different origins remain distinct. It pages sources sequentially in
constructor (source-major) order; offsets and limits apply globally after that
union. `count()` and `len(rows)` require exact child counts and can raise
`CountUnavailableError`; federation does not crawl pages to approximate them.
Global sorting is unsupported. When endpoint descriptors differ or a typed
backend is ambiguous at one endpoint, create an explicit per-source target with
`combined.target(...)` and each store's `entry_type(...)` descriptor.

### Response fields and exact literals

By default a whole-record query sends no `response_fields` parameter, so the
service chooses its normal response fields. Set
`response_fields` on `OptimadeStore` or on `store.searcher()` to request a
semantic field-name iterable for a typed endpoint. Use the identity sentinel
`ALL_ADVERTISED` to request every advertised field; generic endpoints accept
their exact advertised transport names for an explicit selection.

Adding a scalar projection to a whole-record result keeps that source record at
least as complete as the endpoint's advertised `response-default` fields and
makes the scalar explicit when necessary. If an endpoint does not advertise
that metadata, the conservative fallback requests every advertised field rather
than a thin scalar-only record. Record-only queries retain the literal
no-parameter default policy.

Numeric query literals are exact by policy. Use integers or `Decimal`; a
`Fraction` is accepted only when it has a finite decimal expansion. Binary
`float`, non-finite `Decimal`, and fractions such as `Fraction(1, 3)` are
rejected rather than silently approximated.

## Pagination, lifecycle, and errors

The client follows `links.next` synchronously while detecting cycles,
enforcing `max_pages`, and rejecting cross-origin continuations unless
`allow_cross_origin_pagination=True` was explicitly selected. A response that
claims `more_data_available` without a usable continuation raises
`OptimadePaginationError`.

When no client is supplied, `OptimadeStore` owns an `httpx.Client` and closes
it on `close()` or context-manager exit, including when `/versions` negotiation
or later discovery fails. An injected client is borrowed and is never closed.
`refresh()` reuses the already negotiated effective base URL and never repeats
`/versions`. Requests and `refresh()` after close raise `OptimadeClientError`.
Relevant failures are `OptimadeTransportError`, `OptimadeHTTPError`,
`OptimadeErrorDocumentError`, `OptimadeDiscoveryError`, `OptimadeResponseError`,
`OptimadeVersionNegotiationError`, `OptimadePaginationError`, and
`CountUnavailableError`; their diagnostics omit
credentials and sensitive query tokens.

Before a result page yields any item, the client verifies its JSON:API/OPTIMADE
envelope: object `meta`, an optional but (when present) nonnegative integer
`meta.data_returned` (the filtered total, independent of this page's length),
endpoint-matched resource types, and object-valued
`attributes`/`relationships` members when present. This
prevents a partially yielded typed page from being followed by a malformed
entry in the same response.

## Offline caching is explicit

Legacy services that predate property-definition IRIs remain generic: select
their exact `RemoteEntryType` descriptor and use its advertised transport field
names for queries. Results remain `OptimadeResource` objects rather than being
silently assigned a typed backend.

Remote reads never write local state. To retain a resource for offline work,
opt into the database capability and save the exact object yourself:

```python
from httk.store.db import Database, SqlStore

cache = SqlStore(Database.sqlite("optimade-cache.sqlite"), entry_records={})
backend = rows.one().reference  # a typed backend from a prior result
sid = cache.save(backend)
offline = cache.fetch(type(backend), sid)
raw_resource = offline.unwrap()
```

This reconstructs the same typed backend class and its exact raw resource.
`SqlStore.save()` deduplicates shared whole-page documents and schema snapshots
while retaining the resource index, so several resources from one response do
not duplicate their common source context. Saving `backend.unwrap()` directly
is also valid when only a generic `OptimadeResource` cache is wanted. These are
local cache operations, never OPTIMADE writeback.
