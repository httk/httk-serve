"""Shared fixtures and focused tests for the current DSP configuration API."""

import pytest
from httk.core import Dataset

from httk.serve.dsp import (
    DCAT_AP_3_0_1_PROFILE,
    DCAT_AP_MINIMAL_PROFILE,
    DcatDataService,
    DspDatasetPublication,
    DspProviderConfig,
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
        ),
        access_url=f"https://provider.example/data/{name}",
        file_format="http://publications.europa.eu/resource/authority/file-type/JSON",
        media_type="https://www.iana.org/assignments/media-types/application/json",
        offer_id=f"https://provider.example/offers/{name}",
        distribution_id=f"https://provider.example/distributions/{name}",
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


def companion() -> DcatDataService:
    return DcatDataService(
        "https://provider.example/services/dcat-ap",
        "DCAT-AP catalogue",
        "https://provider.example/dcat-ap/catalogue/",
        (DCAT_AP_MINIMAL_PROFILE, DCAT_AP_3_0_1_PROFILE),
    )


def test_content_negotiation_requires_a_conforming_companion() -> None:
    with pytest.raises(ValueError, match="requires dcat_ap_service"):
        config(dcat_ap_content_negotiation=True)
    value = config(dcat_ap_service=companion(), dcat_ap_content_negotiation=True)
    assert value.dcat_ap_service == companion()


def test_publication_defaults_ids_and_validates_file_metadata() -> None:
    value = DspDatasetPublication(
        Dataset("https://example.test/d", "D", "D", "https://example.test/p", "P"),
        "/files/data.csv",
    )
    assert value.offer_id == "https://example.test/d#offer"
    assert value.distribution_id == "https://example.test/d#distribution"
    with pytest.raises(ValueError, match="sha256"):
        DspDatasetPublication(value.dataset, value.access_url, sha256="invalid")
