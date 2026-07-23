"""Tests for building a BackendAdapter from httk-core EntryProviders.

The toy provider imports only ``httk.core`` (now a hard dependency of
httk-optimade), exercising the generic provider path end to end without any
materials-science specifics.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from httk.core import EntryProvider
from starlette.testclient import TestClient

from httk.optimade import adapter_from_providers, create_asgi_app
from httk.optimade.backend import execute_query
from httk.optimade.filter import parse_optimade_filter


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


def test_missing_id_or_type_column_rejected() -> None:
    class BadProvider(EntryProvider):
        def entry_types(self) -> Mapping[str, dict[str, Any]]:
            return {"widgets": {"description": "x", "properties": {"id": {"fulltype": "string"}}}}

        def columns(self, entry_type: str) -> Mapping[str, str]:
            return {"id": "__id"}

        def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
            return []

    try:
        adapter_from_providers([BadProvider()])
    except ValueError as exc:
        assert "id" in str(exc) and "type" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for missing 'type' column")


def test_asgi_end_to_end_over_provider() -> None:
    adapter = adapter_from_providers([make_provider()])
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    client = TestClient(app, base_url="http://testserver")
    response = client.get("/widgets", params={"filter": "cogs >= 4"})
    assert response.status_code == 200
    payload = response.json()
    assert {d["id"] for d in payload["data"]} == {"w-2"}
    assert payload["data"][0]["attributes"]["tags"] == ["blue"]
