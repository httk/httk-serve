"""Tests for building a BackendAdapter from httk-core EntryProviders.

The toy provider imports only ``httk.core`` (now a hard dependency of
httk-optimade), exercising the generic provider path end to end without any
materials-science specifics.
"""

import asyncio
from collections.abc import Iterable, Mapping
from typing import Any

import httpx
import pytest
from httk.core import EntryProvider, EntryTypeDefinition, PropertyDefinition, RelatedEntry
from starlette.testclient import TestClient

from httk.optimade import adapter_from_providers, create_asgi_app
from httk.optimade.backend import execute_query
from httk.optimade.filter import parse_optimade_filter


def _widget_definition() -> EntryTypeDefinition:
    return EntryTypeDefinition(
        "widgets",
        "A widgets entry.",
        {
            "id": PropertyDefinition.from_simple("id", description="The widget id.", required_response=True),
            "type": PropertyDefinition.from_simple("type", description="The entry type.", required_response=True),
            "cogs": PropertyDefinition.from_simple("cogs", description="Number of cogs.", fulltype="integer"),
            "tags": PropertyDefinition.from_simple("tags", description="Tag labels.", fulltype="list of string"),
        },
    )


class WidgetProvider(EntryProvider):
    """A minimal provider serving a custom ``widgets`` entry type."""

    def __init__(self, widgets: list[dict[str, Any]]) -> None:
        self._widgets = widgets

    def entry_types(self) -> Mapping[str, EntryTypeDefinition]:
        return {"widgets": _widget_definition()}

    def property_keys(self, entry_type: str) -> Mapping[str, str]:
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


def test_adapter_serves_provider_schema_and_records() -> None:
    adapter = adapter_from_providers([make_provider()])
    assert adapter.schema.all_entries == ("widgets",)
    # All served properties are default-response (id/type get the standard flags):
    assert set(adapter.schema.default_response_fields["widgets"]) == {"id", "type", "cogs", "tags"}
    results = list(execute_query(adapter, ["widgets"], ["id", "type", "cogs", "tags"], [], 100, 0))
    assert {r.values["id"] for r in results} == {"w-1", "w-2"}
    w1 = next(r for r in results if r.values["id"] == "w-1")
    assert w1.values["cogs"] == 3
    assert w1.values["tags"] == ["red", "small"]


def test_adapter_numeric_filter() -> None:
    adapter = adapter_from_providers([make_provider()])
    results = list(execute_query(adapter, ["widgets"], ["id"], [], 100, 0, parse_optimade_filter("cogs = 5")))
    assert [r.values["id"] for r in results] == ["w-2"]


def test_adapter_list_membership_filter() -> None:
    adapter = adapter_from_providers([make_provider()])
    results = list(execute_query(adapter, ["widgets"], ["id"], [], 100, 0, parse_optimade_filter('tags HAS "red"')))
    assert [r.values["id"] for r in results] == ["w-1"]


def test_adapter_id_filter_matches_normalized_id() -> None:
    adapter = adapter_from_providers([make_provider()])
    results = list(execute_query(adapter, ["widgets"], ["id"], [], 100, 0, parse_optimade_filter('id = "w-1"')))
    assert [r.values["id"] for r in results] == ["w-1"]


def test_provider_declared_sortable_property_sorts_end_to_end() -> None:
    """A provider-declared sortable property must build AND actually sort.

    ``adapter_from_providers`` used to build its ``EntrySource`` with no sort
    mapping at all, so declaring any property sortable raised ValueError from
    ``BackendAdapter.__post_init__``; the provider's property-key map is now
    passed through as ``sort_keys``.
    """
    adapter = adapter_from_providers([make_provider()], sortable={"widgets": ["id", "cogs"]})
    assert adapter.sources["widgets"][0].sort_keys["cogs"] == "cogs"
    ascending = list(execute_query(adapter, ["widgets"], ["id"], [], 100, 0, sort=[("cogs", False)]))
    assert [r.values["id"] for r in ascending] == ["w-1", "w-2"]
    descending = list(execute_query(adapter, ["widgets"], ["id"], [], 100, 0, sort=[("cogs", True)]))
    assert [r.values["id"] for r in descending] == ["w-2", "w-1"]


def test_missing_id_or_type_property_key_rejected() -> None:
    class BadProvider(EntryProvider):
        def entry_types(self) -> Mapping[str, EntryTypeDefinition]:
            return {
                "widgets": EntryTypeDefinition(
                    "widgets",
                    "x",
                    {"id": PropertyDefinition.from_simple("id", description="id", required_response=True)},
                )
            }

        def property_keys(self, entry_type: str) -> Mapping[str, str]:
            return {"id": "__id"}

        def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
            return []

    with pytest.raises(ValueError) as excinfo:
        adapter_from_providers([BadProvider()])
    assert "id" in str(excinfo.value) and "type" in str(excinfo.value)


def test_property_key_not_in_definition_rejected() -> None:
    class MismatchProvider(EntryProvider):
        def entry_types(self) -> Mapping[str, EntryTypeDefinition]:
            return {
                "widgets": EntryTypeDefinition(
                    "widgets",
                    "x",
                    {
                        "id": PropertyDefinition.from_simple("id", description="id", required_response=True),
                        "type": PropertyDefinition.from_simple("type", description="type", required_response=True),
                    },
                )
            }

        def property_keys(self, entry_type: str) -> Mapping[str, str]:
            # 'sprockets' is not described by the definition:
            return {"id": "__id", "type": "type", "sprockets": "sprockets"}

        def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
            return []

    with pytest.raises(ValueError) as excinfo:
        adapter_from_providers([MismatchProvider()])
    assert "sprockets" in str(excinfo.value) and "widgets" in str(excinfo.value)


def test_unprefixed_custom_property_rejected_by_extended() -> None:
    from httk.core import standard_entry_type

    energy = PropertyDefinition.from_simple("cogwheels", description="w", fulltype="integer")
    with pytest.raises(ValueError):
        standard_entry_type("calculations").extended({"cogwheels": energy})


class HttkCalcProvider(EntryProvider):
    """A calculations provider serving a custom ``_httk_`` property end to end."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def entry_types(self) -> Mapping[str, EntryTypeDefinition]:
        from httk.core import standard_entry_type

        energy = PropertyDefinition.from_simple("_httk_total_energy", description="Total energy", fulltype="float")
        return {"calculations": standard_entry_type("calculations").extended({"_httk_total_energy": energy})}

    def property_keys(self, entry_type: str) -> Mapping[str, str]:
        return {"id": "__id", "type": "type", "_httk_total_energy": "_httk_total_energy"}

    def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
        return self._rows


def test_custom_httk_property_served_and_defined() -> None:
    provider = HttkCalcProvider(
        [
            {"__id": "c-1", "type": "calculations", "_httk_total_energy": -1.5},
            {"__id": "c-2", "type": "calculations", "_httk_total_energy": -2.5},
        ]
    )
    adapter = adapter_from_providers([provider])
    # Served in responses (default-response) and filterable:
    results = list(
        execute_query(
            adapter,
            ["calculations"],
            ["id", "_httk_total_energy"],
            [],
            100,
            0,
            parse_optimade_filter("_httk_total_energy < -2"),
        )
    )
    assert [r.values["id"] for r in results] == ["c-2"]
    # Defined on the /info endpoint with its httk.org $id:
    definition = adapter.schema.property_definitions["calculations"]["_httk_total_energy"]
    assert definition["$id"].startswith("https://schemas.httk.org/ad-hoc/defs/properties/")
    assert definition["x-optimade-type"] == "float"


def test_asgi_end_to_end_over_provider() -> None:
    adapter = adapter_from_providers([make_provider()])
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    client = TestClient(app, base_url="http://testserver")
    response = client.get("/widgets", params={"filter": "cogs >= 4"})
    assert response.status_code == 200
    payload = response.json()
    assert {d["id"] for d in payload["data"]} == {"w-2"}
    assert payload["data"][0]["attributes"]["tags"] == ["blue"]


def test_asgi_known_unknown_filters_inspect_each_resource_value() -> None:
    provider = WidgetProvider(
        [
            {"__id": "known", "type": "widgets", "cogs": 3, "tags": []},
            {"__id": "null", "type": "widgets", "cogs": None, "tags": []},
            {"__id": "missing", "type": "widgets", "tags": []},
        ]
    )
    app = create_asgi_app(adapter_from_providers([provider]), baseurl="http://testserver")

    async def request(filter_string: str) -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/widgets", params={"filter": filter_string})

    known = asyncio.run(request("cogs IS KNOWN"))
    unknown = asyncio.run(request("cogs IS UNKNOWN"))

    assert known.status_code == 200
    assert unknown.status_code == 200
    assert [entry["id"] for entry in known.json()["data"]] == ["known"]
    assert {entry["id"] for entry in unknown.json()["data"]} == {"null", "missing"}


class LinkedProvider(EntryProvider):
    """A provider serving ``structures`` linked to ``references`` via the relationships hook."""

    def entry_types(self) -> Mapping[str, EntryTypeDefinition]:
        from httk.core import standard_entry_type

        structures = EntryTypeDefinition(
            "structures",
            "Structures.",
            {
                "id": PropertyDefinition.from_simple("id", description="id", required_response=True),
                "type": PropertyDefinition.from_simple("type", description="type", required_response=True),
                "nelements": PropertyDefinition.from_simple("nelements", description="n", fulltype="integer"),
            },
        )
        return {"structures": structures, "references": standard_entry_type("references")}

    def property_keys(self, entry_type: str) -> Mapping[str, str]:
        if entry_type == "structures":
            return {"id": "__id", "type": "type", "nelements": "nelements"}
        return {"id": "__id", "type": "type", "title": "title"}

    def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
        if entry_type == "structures":
            return [{"__id": "s-1", "type": "structures", "nelements": 2}]
        return [
            {"__id": "ref-1", "type": "references", "title": "A study"},
            {"__id": "ref-2", "type": "references", "title": "Another study"},
        ]

    def relationships(self, entry_type: str) -> Mapping[str, tuple[RelatedEntry, ...]]:
        if entry_type == "structures":
            return {
                "s-1": (RelatedEntry("references", "ref-1", description="Cited for this structure", role="citation"),)
            }
        return {}


def test_provider_relationships_served_and_included() -> None:
    adapter = adapter_from_providers([LinkedProvider()])
    # The structures source carries a relationships extractor; references does not.
    assert adapter.sources["structures"][0].relationships is not None
    assert adapter.sources["references"][0].relationships is None

    app = create_asgi_app(adapter, baseurl="http://testserver/")
    client = TestClient(app, base_url="http://testserver")
    response = client.get("/structures/s-1", params={"include": "references"})
    assert response.status_code == 200
    payload = response.json()
    rels = payload["data"]["relationships"]["references"]["data"]
    # The RelatedEntry metadata flows end to end into the JSON:API meta object:
    assert rels[0] == {
        "type": "references",
        "id": "ref-1",
        "meta": {"description": "Cited for this structure", "role": "citation"},
    }
    included = payload["included"]
    assert [obj["id"] for obj in included] == ["ref-1"]
    assert included[0]["type"] == "references"


def test_provider_relationship_filter_auto_registered() -> None:
    # Declared relationships auto-register '<type>.id' filter handlers over the
    # synthetic __rel_<type> fields: no manual handler wiring, no 501.
    adapter = adapter_from_providers([LinkedProvider()])
    assert "references.id" in adapter.field_handlers["structures"]
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    client = TestClient(app, base_url="http://testserver")
    response = client.get("/structures", params={"filter": 'references.id HAS "ref-1"'})
    assert response.status_code == 200
    assert [entry["id"] for entry in response.json()["data"]] == ["s-1"]
    response = client.get("/structures", params={"filter": 'references.id HAS "ref-2"'})
    assert response.status_code == 200
    assert response.json()["data"] == []


def test_provider_relationships_without_meta_have_no_meta_object() -> None:
    class BareLinkedProvider(LinkedProvider):
        def relationships(self, entry_type: str) -> Mapping[str, tuple[RelatedEntry, ...]]:
            if entry_type == "structures":
                return {"s-1": (RelatedEntry("references", "ref-1"),)}
            return {}

    adapter = adapter_from_providers([BareLinkedProvider()])
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    client = TestClient(app, base_url="http://testserver")
    payload = client.get("/structures/s-1").json()
    assert payload["data"]["relationships"]["references"]["data"] == [{"type": "references", "id": "ref-1"}]


def test_absent_relationships_hook_unchanged() -> None:
    # A provider that does not override relationships yields no relationships block.
    adapter = adapter_from_providers([make_provider()])
    assert adapter.sources["widgets"][0].relationships is None
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    client = TestClient(app, base_url="http://testserver")
    payload = client.get("/widgets/w-1").json()
    assert not payload["data"].get("relationships")
