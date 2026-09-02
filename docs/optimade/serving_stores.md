# Serving directly from an entry store

For a durable deployment, pass an `httk.store.EntryStore` directly to
`create_asgi_app`. This is the preferred path when records already live in a
store:

```python
from httk.atomistic import StructureEntry, UnitcellStructure, UnitcellStructureRecord
from httk.store import Backend, EntryIdScheme, SqlStore
from httk.serve.optimade import create_asgi_app

store = SqlStore(
    Backend.duckdb("materials.duckdb"),
    entry_records={StructureEntry: UnitcellStructureRecord},
    entry_ids=EntryIdScheme("materials.example", "1"),
)
store.save(UnitcellStructure(...))

app = create_asgi_app(store, baseurl="https://materials.example/optimade")
```

No provider snapshot or in-memory table is constructed. The application
discovers every family in `store.entry_layout` that has a registered OPTIMADE
entry-type definition, translates filters and sorting to the store backend,
applies offset/limit before hydrating records, and queries the store for every
request. A record saved after `app` is created is therefore visible to later
requests.

The store remains caller-owned. Closing the ASGI application does not close the
store or its database; the deployment's lifespan code should dispose them.

## Discovery and mixed stores

`adapter_from_store(store, **schema_options)` exposes the adapter explicitly
when an application needs to inspect or wrap it:

```python
from httk.serve.optimade import adapter_from_store, create_asgi_app

adapter = adapter_from_store(store)
print(adapter.schema.all_entries)
app = create_asgi_app(adapter)
```

Discovery uses the store's declared entry-family layout, not every private
dataclass table reachable from stored objects. Families without an OPTIMADE
definition are ignored. DSP publication declarations can consequently share a
store with structures without becoming an OPTIMADE endpoint.

Every discovered OPTIMADE family must have complete, valid
`StoredPropertyProjection` mappings for its served record classes. Adapter
construction validates that contract eagerly. For a mixed store that contains
defined families intended only for another service or not yet projection-ready,
construct an explicit `adapter_from_stores` source list containing only the
families this endpoint should publish.

All concrete record classes configured for one family are served together.
Each record class owns its exact response/query/sort behavior through
`StoredPropertyProjection`; properties lacking an exact query or sort mapping
are not silently approximated. Public IDs are minted lineage IDs; configured
`StoredEntrySource` federations may additionally prepend a public prefix.

## Entry ids and revisions

Defined entry families need an `EntryIdScheme` (or explicit record IDs). The
normal entry endpoint serves the latest revision for each lineage:

| URL | Result |
| --- | --- |
| `/<entry>` | Latest revision of every lineage. |
| `/<entry>/<id>` | Latest revision of one lineage. |
| `/<entry>/<id>/_httk_revs` | Every revision of that lineage. |
| `/<entry>/<id>/_httk_revs/<revision>` | One positive, canonical revision number. |
| `/_httk_<entry>~revs` | Every revision of every lineage. |
| `/_httk_<entry>~revs/<immutable_id>` | One revision by its complete immutable ID. |
| `/info/_httk_<entry>~revs` | Metadata for the revision endpoint. |
| `/<entry>/<id>/_httk_alts` | Every named alternative of that lineage (latest of each kind). |
| `/<entry>/<id>/_httk_alts/<kind>` | One named alternative by its kind token. |
| `/_httk_<entry>~alts` | Every named alternative of every lineage. |
| `/_httk_<entry>~alts/<id>~<kind>` | One named alternative by its composite ID. |
| `/info/_httk_<entry>~alts` | Metadata for the alternative endpoint. |

Revision collections support the usual `filter`, `sort`, and paging query
parameters. Their resource `id` is the immutable ID (for example,
`httk.mydb-1-42~3`) while `_httk_id` is the shared lineage ID
(`httk.mydb-1-42`). The ordinary endpoint continues to render `id` as the
lineage ID and any declared `immutable_id` property as the current immutable
revision. These revision URLs are available only for store-backed adapters.

Named alternatives are sibling representations of an entry (kind tokens matching
`[a-z][a-z0-9_]*`, for example `conventional` or `primitive`). Their resource
`id` is the composite `<id>~<kind>` addressing the latest revision of that
alternative, `_httk_id` is the shared lineage ID, and `_httk_kind` is the kind
token; both `_httk_id` and `_httk_kind` are filterable and sortable, and the
collections support the usual `filter`, `sort`, and paging query parameters. The
ordinary `/<entry>` endpoints serve mains only, and the revision endpoints are
likewise mains-only (an alternative's revision ID such as
`/_httk_<entry>~revs/<id>~<kind>~<n>` is never served). Like the revision URLs,
these alternative URLs are available only for store-backed adapters.

## Several stores

Use `adapter_from_stores` only when an endpoint must federate explicitly named
stores or needs public-ID prefixes:

```python
from httk.store.backend.sql import StoredEntrySource
from httk.serve.optimade import adapter_from_stores

adapter = adapter_from_stores(
    (
        StoredEntrySource(primary, StructureEntry, "primary", "p-"),
        StoredEntrySource(archive, StructureEntry, "archive", "a-"),
    )
)
```

Filtering and bounds remain store-side. Sorted pages are merged from bounded
candidate streams and only the final page is hydrated. Duplicate visible IDs
raise rather than choosing a record implicitly.

## Relationships from stored weak links

An exposed weak link (a `StorageInfo` `WeakLink(..., exposed_relationship=True)`
on a served record class) whose target family is also served is rendered as an
OPTIMADE relationship on the declaring resource. The relationship is keyed by
the target's wire entry type, its resource identifiers carry the linked lineage
IDs, and the link's `role`, `description`, and edge label render in each
identifier's `meta` (the label as the provider-prefixed `_httk_label`). A
resource with no such links carries no relationships, and `include=<wire type>`
inlines the related resources when their family is mounted.

## Wire naming

`EntryTypeDefinition.served_form()` is the single wire-naming transform applied
at the serving edge: a provider entry type and its provider properties are
served under their registered prefix (the runs family serves as `_httk_runs`
with `_httk_source_id` / `_httk_workflow_declaration_uri`), while standard
families and properties are served unchanged. Derived revision and alternative
endpoints keep a single prefix for an already-prefixed base — `_httk_runs~revs`
and `_httk_runs~alts`, never a doubled `_httk__httk_runs~revs`.

## When providers are still useful

`EntryProvider` and `adapter_from_providers` remain appropriate for small
generated datasets, compatibility adapters, and entry types that do not have a
durable storage representation. That path intentionally materializes provider
records in an in-memory query store. It should not be used merely to copy a
database into memory before serving it.

See `examples/optimade/store_server/` for a runnable server and
`examples/optimade/query_in_process.py` for a socket-free live-store example.
