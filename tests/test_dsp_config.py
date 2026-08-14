"""Shared fixtures and focused tests for the current DSP configuration API."""

import pytest
from httk.core import Dataset, DatasetDistribution, DatasetRecord, Service, ServiceRecord

from httk.serve.dsp import (
    DCAT_AP_3_0_1_PROFILE,
    DCAT_AP_MINIMAL_PROFILE,
    DspDatasetPublication,
    DspProviderConfig,
    DspPublicationRecord,
)


def publication(name: str = "one") -> DspDatasetPublication:
    """Build one publication while retaining stable protocol-test identifiers."""
    return DspDatasetPublication(
        dataset=Dataset(
            id=f"https://provider.example/datasets/{name}",
            title=f"Dataset {name}",
            description=f"Dataset {name} description",
            publisher_id="https://provider.example/participants/provider",
            publisher_name="Provider",
            distributions=(
                DatasetDistribution(
                    id=f"https://provider.example/distributions/{name}",
                    access_url=f"https://provider.example/data/{name}.json",
                    format_iri="http://publications.europa.eu/resource/authority/file-type/JSON",
                    media_type_iri="https://www.iana.org/assignments/media-types/application/json",
                ),
            ),
        ),
        offer_id=f"https://provider.example/offers/{name}",
    )


def config(**changes: object) -> DspProviderConfig:
    """Build valid global service configuration."""
    values: dict[str, object] = {
        "public_base_url": "https://provider.example",
        "dsp_mount": "/connector",
        "service_id": "https://provider.example/services/one",
        "service_title": "Data download one",
        "participant_id": "https://provider.example/participant",
        "catalog_id": "https://provider.example/catalog",
        "catalog_title": "A catalogue",
        "catalog_description": "A useful catalogue",
    }
    values.update(changes)
    return DspProviderConfig(**values)  # type: ignore[arg-type]


def multi_config(*_datasets: DspDatasetPublication, **changes: object) -> DspProviderConfig:
    """Build global service configuration for multi-publication tests."""
    return config(**changes)


def test_config_derives_connector_and_version_urls() -> None:
    value = config()
    assert value.connector_root_url == "https://provider.example/connector"
    assert value.service_endpoint_url == "https://provider.example/connector/2025-1"


def companion() -> Service:
    return Service(
        "https://provider.example/services/dcat-ap",
        "DCAT-AP catalogue",
        "https://provider.example/dcat-ap/catalogue/",
        (DCAT_AP_MINIMAL_PROFILE, DCAT_AP_3_0_1_PROFILE),
    )


def test_content_negotiation_is_validated_against_live_publications() -> None:
    value = config(dcat_ap_content_negotiation=True)
    assert value.dcat_ap_content_negotiation


def test_publication_envelope_requires_exactly_one_link_and_normalizes_mappings() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DspPublicationRecord()
    with pytest.raises(ValueError, match="exactly one"):
        DspPublicationRecord(dataset=publication(), service=companion())
    value = DspPublicationRecord.create({"dataset": publication()})
    assert value.dataset == publication()
    assert value.service is None
    service = DspPublicationRecord.create({"service": companion()})
    assert service.service == ServiceRecord.create(companion())


def test_public_constructors_accept_neutral_dataset_and_service_values() -> None:
    dataset_publication = DspDatasetPublication(
        Dataset(
            "https://example.test/datasets/one",
            "One",
            "One dataset",
            "https://example.test/publishers/one",
            "Publisher",
            (DatasetDistribution(access_url="https://example.test/files/one.csv"),),
        )
    )
    service_publication = DspPublicationRecord(
        service=Service(
            "https://example.test/services/one",
            "One service",
            "https://example.test/services/one/api",
            ("https://example.test/standards/one",),
        )
    )

    assert isinstance(dataset_publication.dataset, DatasetRecord)
    assert isinstance(service_publication.service, ServiceRecord)


def test_publication_accepts_a_root_relative_distribution_url() -> None:
    """Publication URLs may be resolved against the configured public origin later."""
    publication_value = DspDatasetPublication(
        Dataset(
            "https://example.test/datasets/relative",
            "Relative",
            "Root-relative distribution URL",
            "https://example.test/publishers/one",
            "Publisher",
            (DatasetDistribution(access_url="/files/relative.csv"),),
        )
    )
    assert publication_value.access_url == "/files/relative.csv"


def test_publication_defaults_ids_and_validates_file_metadata() -> None:
    value = DspDatasetPublication(
        Dataset(
            "https://example.test/d",
            "D",
            "D",
            "https://example.test/p",
            "P",
            (DatasetDistribution(access_url="https://example.test/files/data.csv"),),
        ),
    )
    assert value.offer_id == "https://example.test/d#offer"
    assert value.distribution_id == "https://example.test/d#distribution"
    with pytest.raises(ValueError, match="exactly one"):
        DspDatasetPublication(
            Dataset("https://example.test/d2", "D", "D", "https://example.test/p", "P"),
        )
    with pytest.raises(ValueError, match="exactly one"):
        DspDatasetPublication(
            Dataset(
                "https://example.test/d3",
                "D",
                "D",
                "https://example.test/p",
                "P",
                (
                    DatasetDistribution(access_url="https://example.test/files/one.csv"),
                    DatasetDistribution(access_url="https://example.test/files/two.csv"),
                ),
            ),
        )
