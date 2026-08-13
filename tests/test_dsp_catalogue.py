"""Tests for DSP and DCAT catalogue projections."""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

import pytest
from httk.core import EntryProvider
from test_dsp_config import config, multi_config, publication

from httk.serve.dsp import (
    DCAT_FILE_FORMAT,
    DSP_CONTEXT,
    DSP_TRANSFER_FORMAT,
    DcatDataService,
    DspEntryProviderDatasetSource,
    DspProtocolError,
    DspProvider,
)
from httk.serve.dsp.validation import validate_document


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


def test_multi_dataset_catalogue_and_exact_dataset_lookup() -> None:
    """Both projections include every publication and lookup selects one exact dataset."""
    service = DspProvider(multi_config(publication("one"), publication("two"), automatic_progression=False))

    dsp = service.dsp_catalogue(catalog_request())
    dcat = service.dcat_catalogue()

    assert [dataset["@id"] for dataset in dsp["dataset"]] == [
        "https://provider.example/datasets/one",
        "https://provider.example/datasets/two",
    ]
    assert [dataset["@id"] for dataset in dcat["dcat:dataset"]] == [
        "https://provider.example/datasets/one",
        "https://provider.example/datasets/two",
    ]
    assert len(dcat["dcat:service"]) == 2
    assert service.dsp_dataset("https://provider.example/datasets/two")["@id"].endswith("/two")
    validate_document("https://w3id.org/dspace/2025/1/catalog/catalog-schema.json", dsp)
    validate_document("https://schemas.httk.org/dsp/2025-1/dcat-ap-catalogue.json", dcat)


def test_public_optimade_service_is_additional_and_dcat_only() -> None:
    """Tier 1 advertises OPTIMADE without changing the DSP distribution access service."""
    optimade = DcatDataService(
        id="https://provider.example/services/optimade",
        title="Public materials OPTIMADE API",
        endpoint_url="https://provider.example/optimade/v1",
        conforms_to=("https://schemas.optimade.org/defs/v1.3/standards/optimade",),
        endpoint_description="https://www.optimade.org/specification/latest/",
    )
    service = DspProvider(
        multi_config(
            publication("one"),
            publication("two"),
            dcat_data_services=(optimade,),
            automatic_progression=False,
        )
    )

    dsp = service.dsp_catalogue(catalog_request())
    dcat = service.dcat_catalogue()
    dcat_services = {item["@id"]: item for item in dcat["dcat:service"]}
    public = dcat_services[optimade.id]

    assert "dcat:service" not in dsp
    assert all(
        dataset["distribution"][0]["accessService"]["@id"] == "https://provider.example/services/one"
        or dataset["distribution"][0]["accessService"]["@id"] == "https://provider.example/services/two"
        for dataset in dsp["dataset"]
    )
    assert public == {
        "@id": optimade.id,
        "@type": "DataService",
        "title": optimade.title,
        "endpointURL": {"@id": optimade.endpoint_url},
        "servesDataset": [
            {"@id": "https://provider.example/datasets/one"},
            {"@id": "https://provider.example/datasets/two"},
        ],
        "conformsTo": [
            {
                "@id": "https://schemas.optimade.org/defs/v1.3/standards/optimade",
                "@type": "dct:Standard",
            }
        ],
        "endpointDescription": {"@id": "https://www.optimade.org/specification/latest/"},
    }
    validate_document("https://schemas.httk.org/dsp/2025-1/dcat-ap-catalogue.json", dcat)


def test_public_dcat_services_must_reference_catalogue_datasets_and_distinct_ids() -> None:
    """Companion APIs cannot point outside the snapshot or impersonate the DSP service."""
    values = {
        "title": "Public API",
        "endpoint_url": "https://provider.example/api",
        "conforms_to": ("https://example.org/standards/api",),
    }
    with pytest.raises(ValueError, match="unknown dataset"):
        DspProvider(
            multi_config(
                publication("one"),
                dcat_data_services=(
                    DcatDataService(
                        id="https://provider.example/services/public",
                        serves_dataset_ids=("https://provider.example/datasets/missing",),
                        **values,
                    ),
                ),
            )
        )
    with pytest.raises(ValueError, match="distinct"):
        DspProvider(
            multi_config(
                publication("one"),
                dcat_data_services=(
                    DcatDataService(
                        id="https://provider.example/services/one",
                        **values,
                    ),
                ),
            )
        )


class RecordProvider(EntryProvider):
    """Minimal stand-in for a store-backed entry provider."""

    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = [{"name": "stored"}]
        self.reads = 0

    def entry_types(self) -> Mapping[str, Any]:
        """Return unused definitions for this focused adapter test."""
        return {}

    def property_keys(self, entry_type: str) -> Mapping[str, str]:
        """Return unused keys for this focused adapter test."""
        return {}

    def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
        """Return the current store rows and count snapshot reads."""
        assert entry_type == "published_datasets"
        self.reads += 1
        return tuple(self.rows)


def test_entry_provider_source_reads_store_records_once_at_startup() -> None:
    """A store-backed entry provider is adapted into a stable in-memory catalogue snapshot."""
    records = RecordProvider()
    source = DspEntryProviderDatasetSource(
        records,
        "published_datasets",
        lambda row: publication(str(row["name"])),
    )
    service = DspProvider(multi_config(dataset_source=source, automatic_progression=False))

    records.rows.append({"name": "late"})
    first = service.dsp_catalogue(catalog_request())
    second = service.dsp_catalogue(catalog_request())

    assert records.reads == 1
    assert [dataset["@id"] for dataset in first["dataset"]] == ["https://provider.example/datasets/stored"]
    assert second == first


def test_catalogue_rejects_duplicate_ids_and_mixed_publishers() -> None:
    """Ambiguous identifiers and multiple DCAT catalogue publishers fail at startup."""
    duplicate = publication("one")
    with pytest.raises(ValueError, match="dataset IDs"):
        DspProvider(multi_config(duplicate, duplicate))

    other = publication("two")
    other = replace(
        other,
        dataset=type(other.dataset)(
            other.dataset.id,
            other.dataset.title,
            other.dataset.description,
            "https://other.example/publisher",
            "Other",
        ),
    )
    with pytest.raises(ValueError, match="same publisher"):
        DspProvider(multi_config(publication("one"), other))
