# Serving entry providers

*httk-optimade* is a generic implementation of the OPTIMADE protocol: it carries
no knowledge of what it serves. Everything served — entry types, their
properties, and the records — is supplied through the neutral
`httk.core.EntryProvider` contract. This page shows how to write a provider,
serve it, and query it. For the internals, see
[How it works](how_it_works.md#entry-providers).

## Write a provider

A provider answers three questions: *what entry types do you serve* (with their
properties, described as plain dicts in the OPTIMADE property-definition
dialect), *which record column holds each property* (the column map must cover
at least `id` and `type`), and *what are the records* (plain JSON-able
mappings). A minimal provider serving a custom `widgets` entry type:

```python
from collections.abc import Iterable, Mapping
from typing import Any

from httk.core import EntryProvider


class WidgetProvider(EntryProvider):
    """A minimal provider serving a custom ``widgets`` entry type."""

    def __init__(self, widgets: list[dict[str, Any]]) -> None:
        self._widgets = widgets

    def entry_types(self) -> Mapping[str, dict[str, Any]]:
        return {
            "widgets": {
                "description": "A widgets entry.",
                "properties": {
                    "id": {"description": "The widget id.", "fulltype": "string"},
                    "type": {"description": "The entry type.", "fulltype": "string"},
                    "cogs": {"description": "Number of cogs.", "fulltype": "integer"},
                    "tags": {"description": "Tag labels.", "fulltype": "list of string"},
                },
            }
        }

    def columns(self, entry_type: str) -> Mapping[str, str]:
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

The `fulltype` of each property drives which filter operations the engine
offers for it (comparisons for numbers, string matching for strings, `HAS`
membership for lists) — no handler code is needed.

## Serve it

`adapter_from_providers` turns one or more providers into a fully wired
backend adapter; `serve` runs a development server (or use `create_asgi_app`
with any ASGI server):

```python
from httk.optimade import adapter_from_providers, serve

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
`examples/provider_server/`, and `examples/demo_server/` shows the lower-level
wiring (custom `EntrySource`s, handler tables, and an `OptimadeConfig` with
provider links) that `adapter_from_providers` automates.

## Query programmatically

For tests or in-process use, skip HTTP and drive the engine directly:

```python
from httk.optimade import adapter_from_providers
from httk.optimade.backend import execute_query
from httk.optimade.filter import parse_optimade_filter

adapter = adapter_from_providers([provider])
results = execute_query(
    adapter, ["widgets"], ["id", "cogs"], [], 100, 0,
    parse_optimade_filter('tags HAS "red"'),
)
print([r.values["id"] for r in results])  # ['w-1']
```

## Discover registered providers

Provider packages can self-register a factory under `httk.handlers.*` via
`httk.core.register_entry_provider`. `providers_from_registry` resolves
everything registered in the current environment; since providers need data,
you instantiate them:

```python
from httk.optimade import adapter_from_providers, providers_from_registry

factories = providers_from_registry()
provider = factories["atomistic-structures"](my_structures)  # requires httk-atomistic
adapter = adapter_from_providers([provider])
```

## Serving crystal structures (via *httk-atomistic*)

The materials mapping lives in *httk-atomistic*, not here: its
`StructureEntryProvider` maps `Structure` objects to an OPTIMADE `structures`
entry type (`species`, `species_at_sites`, `lattice_vectors`,
`cartesian_site_positions`, `nsites`, `elements`, `nelements`,
`structure_features`). With *httk-atomistic* installed:

```python
from httk.atomistic import Structure, StructureEntryProvider
from httk.optimade import adapter_from_providers, serve

nacl = Structure(
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
