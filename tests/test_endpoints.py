from typing import Any, Iterator

import pytest

from httk.optimade.endpoints import (
    generate_base_endpoint_reply,
    generate_entry_endpoint_reply,
    generate_entry_info_endpoint_reply,
    generate_info_endpoint_reply,
    generate_links_endpoint_reply,
    generate_single_entry_endpoint_reply,
    generate_versions_endpoint_reply,
)
from httk.optimade.model import (
    OptimadeConfig,
    OptimadeError,
    ResultRow,
    ValidatedParameters,
    ValidatedRequest,
)


class StubResults:
    def __init__(self, rows: list[dict[str, Any]], more_data_available: bool = False) -> None:
        self.rows = rows
        self.more_data_available = more_data_available

    def count(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[ResultRow]:
        return iter(ResultRow(values=row) for row in self.rows)


def make_validated(endpoint: str, **query_kwargs: Any) -> ValidatedRequest:
    return ValidatedRequest(
        baseurl="http://localhost/",
        representation="/" + endpoint,
        endpoint=endpoint,
        version="1.3.0",
        query=ValidatedParameters(**query_kwargs),
    )


def make_config() -> OptimadeConfig:
    config = OptimadeConfig()
    config.data_available = {"structures": 10, "calculations": 5}
    return config


def test_info_endpoint_reply() -> None:
    reply = generate_info_endpoint_reply(make_validated("info"), make_config())
    attributes = reply["data"]["attributes"]
    assert reply["data"]["type"] == "info"
    assert attributes["api_version"] == "1.3.0"
    assert {v["version"] for v in attributes["available_api_versions"]} == {"1.3.0"}
    assert attributes["entry_types_by_format"]["json"] == ["structures", "calculations"]
    assert attributes["available_endpoints"] == ["info", "links", "structures", "calculations"]
    assert attributes["is_index"] is False


def test_entry_info_endpoint_reply() -> None:
    reply = generate_entry_info_endpoint_reply(make_validated("info/structures"), make_config(), "structures")
    assert "elements" in reply["data"]["properties"]
    assert "description" in reply["data"]["properties"]["elements"]
    assert "elements" in reply["data"]["output_fields_by_format"]["json"]


def test_base_endpoint_reply() -> None:
    reply = generate_base_endpoint_reply(make_validated(""), make_config())
    assert reply.startswith("<!DOCTYPE html>")
    assert "OPTIMADE version:1.3.0" in reply


def test_versions_endpoint_reply() -> None:
    assert generate_versions_endpoint_reply(make_validated("versions"), make_config()) == "version\n1\n"


def test_links_endpoint_reply() -> None:
    config = make_config()
    config.links = [
        {
            "id": "index",
            "name": "test index",
            "base_url": "https://example.org",
        }
    ]
    reply = generate_links_endpoint_reply(make_validated("links"), config)
    assert len(reply["data"]) == 2
    entry = reply["data"][0]
    assert entry["id"] == "index"
    assert entry["attributes"]["name"] == "test index"
    assert "id" not in entry["attributes"]
    assert reply["data"][1]["id"] == "optimade"


def test_entry_endpoint_reply() -> None:
    rows = [
        {"id": "1", "type": "structures", "nelements": 2},
        {"id": "2", "type": "structures", "nelements": 3},
    ]
    reply = generate_entry_endpoint_reply(make_validated("structures"), make_config(), StubResults(rows))
    assert reply["links"]["next"] is None
    assert len(reply["data"]) == 2
    assert reply["data"][0]["id"] == "1"
    assert reply["data"][0]["attributes"] == {"nelements": 2}
    assert reply["meta"]["data_returned"] == 2
    assert reply["meta"]["data_available"] == 10
    assert reply["meta"]["more_data_available"] is False


def test_entry_endpoint_reply_pagination() -> None:
    rows = [{"id": str(i), "type": "structures"} for i in range(5)]
    request = make_validated("structures", page_limit=5, page_offset=10, filter="nelements=2")
    reply = generate_entry_endpoint_reply(request, make_config(), StubResults(rows, more_data_available=True))
    next_link = reply["links"]["next"]
    assert next_link is not None
    assert next_link.startswith("http://localhost/structures?")
    assert "page_offset=15" in next_link
    assert "page_limit=5" in next_link
    assert "filter=nelements%3D2" in next_link
    assert reply["meta"]["more_data_available"] is True


def test_single_entry_endpoint_reply() -> None:
    rows = [{"id": "1", "type": "structures", "nelements": 2}]
    reply = generate_single_entry_endpoint_reply(make_validated("structures"), make_config(), StubResults(rows))
    assert reply["data"]["id"] == "1"
    assert reply["meta"]["data_returned"] == 1


def test_single_entry_endpoint_reply_not_found() -> None:
    reply = generate_single_entry_endpoint_reply(make_validated("structures"), make_config(), StubResults([]))
    assert reply["data"] is None
    assert reply["meta"]["data_returned"] == 0


def test_single_entry_endpoint_reply_multiple_is_error() -> None:
    rows = [
        {"id": "1", "type": "structures"},
        {"id": "2", "type": "structures"},
    ]
    with pytest.raises(OptimadeError) as excinfo:
        generate_single_entry_endpoint_reply(make_validated("structures"), make_config(), StubResults(rows))
    assert excinfo.value.response_code == 500


def test_entry_info_endpoint_reply_v12_format() -> None:
    # OPTIMADE v1.2 requires top-level id/type in the entry listing info data,
    # and properties presented as OPTIMADE Property Definitions.
    reply = generate_entry_info_endpoint_reply(make_validated("info/structures"), make_config(), "structures")
    assert reply["data"]["id"] == "structures"
    assert reply["data"]["type"] == "info"
    nelements = reply["data"]["properties"]["nelements"]
    assert nelements["$id"] == "https://schemas.optimade.org/defs/v1.2/properties/optimade/structures/nelements"
    assert nelements["x-optimade-type"] == "integer"
    assert nelements["type"] == ["integer", "null"]
    assert nelements["x-optimade-definition"]["kind"] == "property"
    # id/type are required in responses and therefore not nullable:
    assert reply["data"]["properties"]["id"]["type"] == ["string"]
    # List properties carry inner item definitions:
    elements = reply["data"]["properties"]["elements"]
    assert elements["x-optimade-type"] == "list"
    assert elements["items"]["x-optimade-type"] == "string"
    lattice = reply["data"]["properties"]["lattice_vectors"]
    assert lattice["x-optimade-unit"] == "angstrom"
    assert lattice["items"]["items"]["x-optimade-type"] == "float"
    assert lattice["x-optimade-unit-definitions"][0]["symbol"] == "angstrom"


def test_entry_info_endpoint_reply_custom_property_definitions() -> None:
    reply = generate_entry_info_endpoint_reply(make_validated("info/calculations"), make_config(), "calculations")
    energy = reply["data"]["properties"]["_httk_total_energy"]
    assert energy["$id"].startswith("https://httk.org/")
    assert energy["x-optimade-type"] == "float"
    assert energy["type"] == ["number", "null"]
