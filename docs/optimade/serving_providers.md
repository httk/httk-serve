# Serving entry providers

*httk-serve* is a generic implementation of the OPTIMADE protocol: it carries
no knowledge of what it serves. Everything served — entry types, their
properties, and the records — is supplied through the neutral
`httk.core.EntryProvider` contract. This page shows how to write a provider,
serve it, and query it. For the internals, see
[How it works](how_it_works.md#entry-providers).

## Write a provider

A provider answers three questions: *what entry types do you serve* (each
described by a first-class `httk.core.EntryTypeDefinition`), *which record key
holds each property* (the property-key map must cover at least `id` and `type`, and
every served name must be described by the definition), and *what are the
records* (plain JSON-able mappings). Property definitions are generated from a
compact description with `PropertyDefinition.from_simple` (or loaded from the
vendored standards). A minimal provider serving a custom `widgets` entry type:

```python
from collections.abc import Iterable, Mapping
from typing import Any

from httk.core import EntryProvider, EntryTypeDefinition, PropertyDefinition


class WidgetProvider(EntryProvider):
    """A minimal provider serving a custom ``widgets`` entry type."""

    def __init__(self, widgets: list[dict[str, Any]]) -> None:
        self._widgets = widgets

    def entry_types(self) -> Mapping[str, EntryTypeDefinition]:
        return {
            "widgets": EntryTypeDefinition(
                "widgets",
                "A widgets entry.",
                {
                    "id": PropertyDefinition.from_simple("id", description="The widget id.", required_response=True),
                    "type": PropertyDefinition.from_simple("type", description="The entry type.", required_response=True),
                    "cogs": PropertyDefinition.from_simple("cogs", description="Number of cogs.", fulltype="integer"),
                    "tags": PropertyDefinition.from_simple("tags", description="Tag labels.", fulltype="list of string"),
                },
            )
        }

    def property_keys(self, entry_type: str) -> Mapping[str, str]:
        return {"id": "__id", "type": "type", "cogs": "cogs", "tags": "tags"}

    def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
        return self._widgets


provider = WidgetProvider(
    [
        {"__id": "w-1", "type": "widgets", "cogs": 3, "tags": ["red", "small"]},
        {"__id": "w-2", "type": "widgets", "cogs": 5, "tags": ["blue"]},
    ]
)
```

Each property's type drives which filter operations the engine offers for it
(comparisons for numbers, string matching for strings, `HAS` membership for
lists) — no handler code is needed. Custom (database-specific) properties must
carry a recognized prefix (`_httk_` is pre-registered; others via
`httk.core.register_definition_prefix`) and be merged into a standard
definition with `EntryTypeDefinition.extended({...})`.

## Serve it

`adapter_from_providers` turns one or more providers into a fully wired
backend adapter; `serve` runs a development server (or use `create_asgi_app`
with any ASGI server):

```python
from httk.serve.optimade import adapter_from_providers, serve

serve(adapter_from_providers([provider]), port=8080)
```

The API is then live, e.g.:

```console
curl 'http://localhost:8080/v1/widgets?filter=cogs=5'
```

```json
{
 "data": [
  {"attributes": {"cogs": 5, "tags": ["blue"]}, "id": "w-2", "type": "widgets"}
 ],
 "links": {"next": null},
 "meta": {"api_version": "1.3.0", "data_available": 2, "data_returned": 1, "...": "..."}
}
```

Filters compose over the described properties: `filter=cogs>3 AND tags HAS "blue"`,
`filter=id="w-1"`, and so on; `/v1/info` and `/v1/info/widgets` are generated
from the provider's descriptions. A runnable version of this example is in
`examples/optimade/provider_server/`, and `examples/optimade/demo_server/` shows the lower-level
wiring (custom `EntrySource`s, handler tables, and an `OptimadeConfig` with
provider links) that `adapter_from_providers` automates.

### Mounted deployments and browser access

When `create_asgi_app()` has no explicit `baseurl`, generated resource and
pagination links use the ASGI mount path. For example, mounting the app at
`/optimade` makes a request to `/optimade/v1/structures` generate links below
`/optimade/v1/`; an unversioned request remains below `/optimade/`. If the
public URL differs from ASGI's incoming scheme or host, pass `baseurl` as the
unversioned public API base instead. It remains authoritative, while a
versioned request still adds its single `/v1/` segment to generated links.

Cross-origin browser reads are disabled by default. To opt in, configure an
exact HTTP(S) origin allowlist; wildcard origins and credentials are not
supported:

```python
from httk.serve.optimade import OptimadeConfig, create_asgi_app

app = create_asgi_app(
    adapter,
    OptimadeConfig(cors_origins=("https://table.example",)),
)
```

Allowed origins receive CORS responses for `GET`, `HEAD`, and preflight
`OPTIONS` requests. Origins must not include paths (apart from a trailing
slash), credentials, queries, or fragments. Hostnames must be ASCII; configure
an internationalized domain name in browser-compatible punycode form so it
matches the browser's `Origin` header exactly.

## Query programmatically

For tests or in-process use, skip HTTP and drive the engine directly:

```python
from httk.serve.optimade import adapter_from_providers
from httk.serve.optimade.backend import execute_query
from httk.serve.optimade.filter import parse_optimade_filter

adapter = adapter_from_providers([provider])
results = execute_query(
    adapter, ["widgets"], ["id", "cogs"], [], 100, 0,
    parse_optimade_filter('tags HAS "red"'),
)
print([r.values["id"] for r in results])  # ['w-1']
```

## Relationships, `include`, and relationship filtering

A provider can declare related entries by overriding the optional
`EntryProvider.relationships(entry_type)` hook, which maps each entry id to a
flat tuple of `httk.core.RelatedEntry` objects (each naming the related entry
type and id, optionally with a per-identifier `description` and v1.3 `role`).
`adapter_from_providers` wires that into the served OPTIMADE **relationships**
block automatically (grouped by related type, metadata rendered as the
JSON:API `meta` object), so an `include=<type>` request embeds the related
resources (resolved from whichever provider serves that related type).
Filtering is auto-wired too: `references.id HAS "ref-1"` matches over the
declared ids, and depth-1 relationship-property filters such as
`references.title CONTAINS "study"` are resolved by filtering the related
entry type's own properties (each dotted filter node independently). A
provider that does not override the hook is unaffected — no relationships
block is emitted.

```python
from collections.abc import Iterable, Mapping
from typing import Any

from httk.core import EntryProvider, EntryTypeDefinition, PropertyDefinition, RelatedEntry, standard_entry_type
from httk.serve.optimade import adapter_from_providers
from httk.serve.optimade.backend import execute_query
from httk.serve.optimade.filter import parse_optimade_filter


class LinkedProvider(EntryProvider):
    def entry_types(self) -> Mapping[str, EntryTypeDefinition]:
        structures = EntryTypeDefinition(
            "structures",
            "Structures.",
            {
                "id": PropertyDefinition.from_simple("id", description="id", required_response=True),
                "type": PropertyDefinition.from_simple("type", description="type", required_response=True),
            },
        )
        return {"structures": structures, "references": standard_entry_type("references")}

    def property_keys(self, entry_type: str) -> Mapping[str, str]:
        if entry_type == "structures":
            return {"id": "__id", "type": "type"}
        return {"id": "__id", "type": "type", "title": "title"}

    def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
        if entry_type == "structures":
            return [{"__id": "s-1", "type": "structures"}]
        return [{"__id": "ref-1", "type": "references", "title": "A study"}]

    def relationships(self, entry_type: str) -> Mapping[str, tuple[RelatedEntry, ...]]:
        if entry_type == "structures":
            return {"s-1": (RelatedEntry("references", "ref-1", description="Cited for this structure"),)}
        return {}


adapter = adapter_from_providers([LinkedProvider()])
(row,) = list(execute_query(adapter, ["structures"], ["id", "type"], [], 100, 0))
assert row.relationships == {"references": [{"id": "ref-1", "description": "Cited for this structure"}]}

# Relationship filtering works without any handler wiring:
(row,) = list(
    execute_query(
        adapter, ["structures"], ["id"], [], 100, 0,
        parse_optimade_filter('references.title CONTAINS "study"'),
    )
)
assert row.values["id"] == "s-1"
```

A runnable server showcasing declared relationships (serving, `include`, and
both kinds of relationship filtering) is in `examples/optimade/provider_server/`.

## Discover registered providers

Provider packages can self-register a factory from a registration package
under the reserved entries tier (`httk.registry.entries.<module>`) via
`httk.core.register_entry_provider`. For example, *httk-store* registers
in-memory providers for the standard `references`/`files`/`calculations` entry
types (as `store-references`/`store-files`/`store-calculations`), and
*httk-atomistic* registers `atomistic-structures`. `providers_from_registry`
resolves everything registered in the current environment; since providers need
data, you instantiate them:

```python
from httk.serve.optimade import adapter_from_providers, providers_from_registry

factories = providers_from_registry()
provider = factories["atomistic-structures"](my_structures)  # requires httk-atomistic
adapter = adapter_from_providers([provider])
```

## Serving crystal structures (via *httk-atomistic*)

The materials mapping lives in *httk-atomistic*, not here: its
`StructureEntryProvider` maps `UnitcellStructure` objects to an OPTIMADE `structures`
entry type (`species`, `species_at_sites`, `lattice_vectors`,
`cartesian_site_positions`, `nsites`, `elements`, `nelements`,
`structure_features`). With *httk-atomistic* installed:

```python
from httk.atomistic import UnitcellStructure, StructureEntryProvider
from httk.serve.optimade import adapter_from_providers, serve

nacl = UnitcellStructure(
    cell=(5.64, 5.64, 5.64, 90.0, 90.0, 90.0),
    sites=[[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]],
    species=[
        {"name": "Na", "chemical_symbols": ["Na"], "concentration": [1.0]},
        {"name": "Cl", "chemical_symbols": ["Cl"], "concentration": [1.0]},
    ],
    species_at_sites=["Na", "Cl"],
)

serve(adapter_from_providers([StructureEntryProvider({"nacl": nacl})]))
```

```console
curl 'http://localhost:8080/v1/structures?filter=elements HAS "Na"'
```

Neither package imports the other — the contract in *httk-core* is the only
coupling, which is why the two install and evolve independently.
