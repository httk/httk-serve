"""Tests for DSP and DCAT catalogue projections."""

import pytest
from test_dsp_config import config

from httk.serve.dsp import DCAT_FILE_FORMAT, DSP_CONTEXT, DSP_TRANSFER_FORMAT, DspProtocolError, DspProvider


def provider() -> DspProvider:
    """Build a provider whose process callbacks are irrelevant to catalogue tests."""
    return DspProvider(config(automatic_progression=False))


def catalog_request(filter_value: object = None) -> dict[str, object]:
    """Build one DSP catalogue request with an optional filter field."""
    value: dict[str, object] = {"@context": [DSP_CONTEXT], "@type": "CatalogRequestMessage"}
    if filter_value is not None:
        value["filter"] = filter_value
    return value


def test_dsp_catalogue_has_one_unconditional_pull_dataset() -> None:
    """DSP projection has one dataset, offer, distribution, and embedded service."""
    document = provider().dsp_catalogue(catalog_request())

    assert document["@context"] == [DSP_CONTEXT]
    dataset = document["dataset"][0]
    assert len(document["dataset"]) == 1
    assert dataset["hasPolicy"] == [
        {"@id": "https://provider.example/offers/one", "@type": "Offer", "permission": [{"action": "use"}]}
    ]
    distribution = dataset["distribution"][0]
    assert distribution["format"] == DSP_TRANSFER_FORMAT
    assert distribution["dcat:accessURL"] == {"@id": "https://provider.example/data/one"}
    assert distribution["accessService"]["endpointURL"] == "https://provider.example/connector/2025-1"


def test_dcat_catalogue_uses_its_own_graph_and_iri_objects() -> None:
    """DCAT projection has the expected dataset, distribution, service, and publisher graph."""
    document = provider().dcat_catalogue()

    assert document["@context"]["dcat"] == "http://www.w3.org/ns/dcat#"
    assert document["@context"]["permission"]["@id"] == "odrl:permission"
    assert document["dct:publisher"] == {
        "@id": "https://provider.example/participants/provider",
        "@type": "Agent",
        "foaf:name": "Provider",
    }
    dataset = document["dcat:dataset"][0]
    distribution = dataset["dcat:distribution"][0]
    service = document["dcat:service"][0]
    assert dataset["dct:publisher"] == {"@id": "https://provider.example/participants/provider"}
    assert distribution["format"] == {"@id": DCAT_FILE_FORMAT, "@type": "dct:MediaTypeOrExtent"}
    assert distribution["accessURL"] == {"@id": "https://provider.example/data/one"}
    assert distribution["accessService"] == {"@id": service["@id"]}
    assert service["endpointURL"] == {"@id": "https://provider.example/connector/2025-1"}
    assert dataset["odrl:hasPolicy"][0]["permission"] == [{"action": "use"}]


def test_dcat_context_isolated_from_response_mutation() -> None:
    """Mutating nested JSON-LD context objects cannot poison later catalogue responses."""
    service = provider()
    first = service.dcat_catalogue()
    first["@context"]["format"]["@id"] = "https://attacker.example/format"

    second = service.dcat_catalogue()

    assert second["@context"]["format"] == {"@id": "dct:format", "@type": "@id"}


@pytest.mark.parametrize("filter_value", ["x = y", ["x = y"], {"x": "y"}])
def test_catalogue_rejects_nonempty_filters(filter_value: object) -> None:
    """The fixed profile accepts no query filtering."""
    with pytest.raises(DspProtocolError, match="filters") as raised:
        provider().dsp_catalogue(catalog_request(filter_value))

    assert raised.value.status_code == 400
    assert raised.value.kind == "catalog"


def test_dataset_requires_the_exact_configured_identifier() -> None:
    """Dataset projection is not a prefix or alias lookup."""
    service = provider()
    assert (
        service.dsp_dataset("https://provider.example/datasets/one")["@id"] == "https://provider.example/datasets/one"
    )

    with pytest.raises(DspProtocolError) as raised:
        service.dsp_dataset("https://provider.example/datasets/one/other")
    assert raised.value.status_code == 404
