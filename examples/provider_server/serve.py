#!/usr/bin/env python3
"""A minimal OPTIMADE server fed by an httk-core entry provider.

This is the runnable version of the example in ``docs/serving_providers.md``:
a provider serving a custom ``widgets`` entry type, wired into a server with
``adapter_from_providers``. See ``examples/demo_server/`` for the lower-level
wiring this automates.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from httk.core import EntryProvider, EntryTypeDefinition, PropertyDefinition

from httk.optimade import adapter_from_providers, serve


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
                    "type": PropertyDefinition.from_simple(
                        "type", description="The entry type.", required_response=True
                    ),
                    "cogs": PropertyDefinition.from_simple("cogs", description="Number of cogs.", fulltype="integer"),
                    "tags": PropertyDefinition.from_simple(
                        "tags", description="Tag labels.", fulltype="list of string"
                    ),
                },
            )
        }

    def columns(self, entry_type: str) -> Mapping[str, str]:
        return {"id": "__id", "type": "type", "cogs": "cogs", "tags": "tags"}

    def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
        return self._widgets


def make_provider() -> WidgetProvider:
    return WidgetProvider(
        [
            {"__id": "w-1", "type": "widgets", "cogs": 3, "tags": ["red", "small"]},
            {"__id": "w-2", "type": "widgets", "cogs": 5, "tags": ["blue"]},
        ]
    )


if __name__ == "__main__":
    print("Serving on http://localhost:8080 - try:")
    print("  curl 'http://localhost:8080/v1/widgets?filter=cogs=5'")
    serve(adapter_from_providers([make_provider()]), port=8080)
