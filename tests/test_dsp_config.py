"""Tests for immutable DSP provider configuration."""

from collections.abc import Mapping

import pytest

from httk.serve.dsp import HTTP_ENDPOINT_TYPE, DspProviderConfig


def config(**changes: object) -> DspProviderConfig:
    """Build a valid provider configuration with optional field replacements."""
    values: dict[str, object] = {
        "connector_root_url": "https://provider.example/connector",
        "service_id": "https://provider.example/service",
        "participant_id": "https://provider.example/participant",
        "catalog_id": "https://provider.example/catalog",
        "catalog_title": "A catalogue",
        "catalog_description": "A useful catalogue",
        "dataset": {
            "id": "https://provider.example/datasets/one",
            "title": "Dataset one",
            "description": "A dataset",
            "publisher_id": "https://provider.example/participants/provider",
            "publisher_name": "Provider",
        },
        "offer_id": "https://provider.example/offers/one",
        "distribution_id": "https://provider.example/distributions/one",
        "data_service_id": "https://provider.example/services/one",
        "data_service_title": "Data download",
        "access_url": "https://provider.example/data/one",
        "data_address": {
            "@type": "DataAddress",
            "endpointType": HTTP_ENDPOINT_TYPE,
            "endpoint": "https://provider.example/data/one",
        },
    }
    values.update(changes)
    return DspProviderConfig(**values)  # type: ignore[arg-type]


def test_config_coerces_dataset_and_freezes_data_address() -> None:
    """Configuration owns its dataset and nested data-address values."""
    address = {
        "@type": "DataAddress",
        "endpointType": HTTP_ENDPOINT_TYPE,
        "endpoint": "https://provider.example/data/one",
        "endpointProperties": [{"@type": "EndpointProperty", "name": "x", "value": "y"}],
    }
    value = config(data_address=address)

    address["endpoint"] = "https://other.example/data"

    assert value.dataset.id == "https://provider.example/datasets/one"
    assert isinstance(value.data_address, Mapping)
    assert value.data_address["endpoint"] == "https://provider.example/data/one"
    assert value.service_endpoint_url == "https://provider.example/connector/2025-1"
    with pytest.raises(TypeError):
        value.data_address["endpoint"] = "https://other.example/data"  # type: ignore[index]


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"connector_root_url": "http://provider.example/connector"}, "HTTPS"),
        ({"connector_root_url": "https://provider.example/connector/"}, "trailing slash"),
        ({"connector_root_url": "https://provider.example/connector?x=y"}, "HTTPS"),
        ({"service_id": "not-an-iri"}, "absolute IRI"),
        ({"service_id": "https://provider.example/%ZZ"}, "absolute IRI"),
        ({"participant_id": "urn:example|invalid"}, "absolute IRI"),
        ({"access_url": "https://exa mple/data"}, "HTTPS"),
        ({"access_url": "https://provider.example:bad/data"}, "HTTPS"),
        (
            {
                "data_address": {
                    "@type": "DataAddress",
                    "endpointType": "wrong",
                    "endpoint": "https://provider.example/data/one",
                }
            },
            "endpointType",
        ),
        (
            {
                "data_address": {
                    "@type": "DataAddress",
                    "endpointType": HTTP_ENDPOINT_TYPE,
                    "endpoint": "https://other.example/data",
                }
            },
            "equal",
        ),
        (
            {
                "data_address": {
                    "@type": "DataAddress",
                    "endpointType": HTTP_ENDPOINT_TYPE,
                    "endpoint": "https://provider.example/data/one",
                    "endpointProperties": [{"@type": "EndpointProperty", "name": "missing-value"}],
                }
            },
            "official DSP shape",
        ),
    ],
)
def test_config_rejects_invalid_urls_and_data_addresses(changes: dict[str, object], message: str) -> None:
    """Configuration fails eagerly for bad URL or data-address essentials."""
    with pytest.raises(ValueError, match=message):
        config(**changes)
