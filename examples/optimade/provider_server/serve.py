#!/usr/bin/env python3
"""A minimal OPTIMADE server fed by an httk-core entry provider.

This is the runnable version of the example in ``docs/serving_providers.md``:
a provider serving a custom ``widgets`` entry type alongside the standard
``references`` entry type, with declared relationships between them, wired
into a server with ``adapter_from_providers``. The declared
``RelatedEntry`` objects are served automatically as the OPTIMADE
relationships block (with per-identifier ``meta``), resolved by
``include=references``, and filterable by id (``references.id HAS "ref-1"``,
or equivalently the extension spelling ``_httk_relationships.references.id HAS
"ref-1"``) and by related properties (``references.title CONTAINS "study"``)
without any hand-wired filter handlers. See ``examples/optimade/demo_server/`` for the
lower-level wiring this automates.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from httk.core import (
    EntryProvider,
    EntryTypeDefinition,
    PropertyDefinition,
    RelatedEntry,
    standard_entry_type,
)

from httk.serve.optimade import adapter_from_providers, serve

# Run this example by hand (``python examples/optimade/provider_server/serve.py``): it
# binds port 8080 and serves until interrupted, so the examples smoke test in
# tests/test_examples.py must not launch it unattended.
HTTK_EXAMPLE_NO_AUTORUN = True


class WidgetProvider(EntryProvider):
    """A provider serving ``widgets`` linked to the standard ``references``."""

    def __init__(self, widgets: list[dict[str, Any]], references: list[dict[str, Any]]) -> None:
        self._widgets = widgets
        self._references = references

    def entry_types(self) -> Mapping[str, EntryTypeDefinition]:
        widgets = EntryTypeDefinition(
            "widgets",
            "A widgets entry.",
            {
                "id": PropertyDefinition.from_simple("id", description="The widget id.", required_response=True),
                "type": PropertyDefinition.from_simple("type", description="The entry type.", required_response=True),
                "cogs": PropertyDefinition.from_simple("cogs", description="Number of cogs.", fulltype="integer"),
                "tags": PropertyDefinition.from_simple("tags", description="Tag labels.", fulltype="list of string"),
            },
        )
        return {"widgets": widgets, "references": standard_entry_type("references")}

    def property_keys(self, entry_type: str) -> Mapping[str, str]:
        if entry_type == "widgets":
            return {"id": "__id", "type": "type", "cogs": "cogs", "tags": "tags"}
        return {"id": "__id", "type": "type", "title": "title", "doi": "doi"}

    def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
        if entry_type == "widgets":
            return self._widgets
        return self._references

    def relationships(self, entry_type: str) -> Mapping[str, tuple[RelatedEntry, ...]]:
        # Declared relationships are auto-wired by adapter_from_providers:
        # served in each widget's relationships block (with the meta below),
        # resolvable via ?include=references, and filterable via
        # `references.id HAS "ref-1"` or `references.title CONTAINS "study"`.
        if entry_type == "widgets":
            return {
                "w-1": (RelatedEntry("references", "ref-1", description="Widget design study", role="documentation"),),
            }
        return {}


def make_provider() -> WidgetProvider:
    return WidgetProvider(
        widgets=[
            {"__id": "w-1", "type": "widgets", "cogs": 3, "tags": ["red", "small"]},
            {"__id": "w-2", "type": "widgets", "cogs": 5, "tags": ["blue"]},
        ],
        references=[
            {"__id": "ref-1", "type": "references", "title": "A study of widgets", "doi": "10.1234/widgets.1"},
        ],
    )


if __name__ == "__main__":
    print("Serving on http://localhost:8080 - try:")
    print("  curl 'http://localhost:8080/v1/widgets?filter=cogs=5'")
    print("  curl 'http://localhost:8080/v1/widgets/w-1?include=references'")
    print('  curl \'http://localhost:8080/v1/widgets?filter=references.id HAS "ref-1"\'')
    print("  curl 'http://localhost:8080/v1/widgets?filter=references.title CONTAINS \"study\"'")
    serve(adapter_from_providers([make_provider()]), port=8080)
