"""Focused DSP/DCAT projection invariants not covered by store-native tests."""

from dataclasses import replace

import pytest
from httk.core import Service
from httk.store.storage_layout import EntryFamilyLayout
from test_dsp_config import config, publication

from httk.serve.dsp import (
    DCAT_AP_3_0_1_PROFILE,
    DCAT_AP_MINIMAL_PROFILE,
    DSP_CONTEXT,
    DspDatasetPublication,
    DspProvider,
    DspPublicationEntry,
    DspPublicationRecord,
)


def request() -> dict[str, object]:
    return {"@context": [DSP_CONTEXT], "@type": "CatalogRequestMessage"}


def test_companion_dcat_service_is_advertised_in_both_catalogues() -> None:
    companion = DspPublicationRecord(
        service={
            "id": "https://provider.example/services/dcat-ap",
            "title": "DCAT-AP",
            "endpoint_url": "https://provider.example/dcat-ap/catalogue/",
            "conforms_to": (DCAT_AP_MINIMAL_PROFILE, DCAT_AP_3_0_1_PROFILE),
        }
    )
    publications = (publication("one"), publication("two"))
    provider = DspProvider(
        config(),
        publications=tuple(DspPublicationRecord(dataset=item) for item in publications) + (companion,),
    )

    dsp = provider.dsp_catalogue(request())
    services = {item["@id"] for item in dsp["service"]}
    assert services == {config().service_id, companion.service.id}
    dcat = provider.dcat_catalogue()
    expected_conformance = [
        {"@id": config().dcat_ap_profile, "@type": "dct:Standard"},
        {"@id": DCAT_AP_3_0_1_PROFILE, "@type": "dct:Standard"},
    ]
    assert dsp["dct:conformsTo"] == expected_conformance
    assert dcat["dct:conformsTo"] == expected_conformance
    assert {key: value for key, value in dsp.items() if key != "@context"} == {
        key: value for key, value in dcat.items() if key != "@context"
    }


def test_snapshot_rejects_duplicate_ids_and_mixed_publishers() -> None:
    duplicate = publication("one")
    with pytest.raises(ValueError, match="dataset IDs"):
        replacement_distribution = replace(
            duplicate.distribution,
            access_url="https://provider.example/files/other.csv",
        )
        replacement = replace(
            duplicate,
            dataset=replace(duplicate.dataset, distributions=(replacement_distribution,)),
        )
        _ = DspProvider(
            config(),
            publications=tuple(
                DspPublicationRecord(dataset=item) for item in (duplicate, replacement)
            ),
        ).profile

    other = publication("two")
    other = replace(
        other,
        dataset=replace(other.dataset, publisher_id="https://other.example/publisher", publisher_name="Other"),
    )
    with pytest.raises(ValueError, match="same publisher"):
        _ = DspProvider(
            config(),
            publications=tuple(DspPublicationRecord(dataset=item) for item in (publication("one"), other)),
        ).profile


def _provider_with_services(*services: Service, content_negotiation: bool = False) -> DspProvider:
    return DspProvider(
        config(dcat_ap_content_negotiation=content_negotiation),
        publications=(
            DspPublicationRecord(dataset=publication("one")),
            DspPublicationRecord(dataset=publication("two")),
            *(DspPublicationRecord(service=item) for item in services),
        ),
    )


def test_snapshot_rejects_duplicate_catalogue_service_ids() -> None:
    first = Service(
        "https://provider.example/services/catalogue",
        "Catalogue one",
        "https://catalogue.example/one",
        (DCAT_AP_MINIMAL_PROFILE,),
    )
    second = Service(
        first.id,
        "Catalogue two",
        "https://catalogue.example/two",
        (DCAT_AP_3_0_1_PROFILE,),
    )
    with pytest.raises(ValueError, match="service IDs"):
        _provider_with_services(first, second).dsp_catalogue(request())


def test_snapshot_rejects_catalogue_service_id_collision_with_global_service() -> None:
    service = Service(config().service_id, "Collision", "https://catalogue.example/api", ("https://example.test/std",))
    with pytest.raises(ValueError, match="global DSP access service"):
        _provider_with_services(service).dsp_catalogue(request())


def test_snapshot_rejects_unknown_explicit_served_dataset() -> None:
    service = Service(
        "https://provider.example/services/catalogue",
        "Catalogue",
        "https://catalogue.example/api",
        ("https://example.test/std",),
        serves_dataset_ids=("https://provider.example/datasets/missing",),
    )
    with pytest.raises(ValueError, match="unknown dataset IDs"):
        _provider_with_services(service).dsp_catalogue(request())


def test_snapshot_rejects_non_https_service_endpoint() -> None:
    service = Service(
        "https://provider.example/services/catalogue",
        "Catalogue",
        "ftp://catalogue.example/api",
        ("https://example.test/std",),
    )
    with pytest.raises(ValueError, match="absolute HTTPS URL"):
        _provider_with_services(service).dsp_catalogue(request())


def test_snapshot_allows_cross_origin_https_service_and_emits_endpoint_unchanged() -> None:
    endpoint = "https://catalogue.example/api"
    service = Service(
        "https://provider.example/services/catalogue",
        "Catalogue",
        endpoint,
        ("https://example.test/std",),
    )
    provider = _provider_with_services(service)
    assert provider.profile.dcat_data_services[0].endpoint_url == endpoint
    assert provider.dsp_catalogue(request())["service"][-1]["endpointURL"] == endpoint


@pytest.mark.parametrize(
    "service",
    (
        Service(
            "https://provider.example/services/incomplete-conformance",
            "Incomplete conformance",
            "https://catalogue.example/conformance",
            (DCAT_AP_3_0_1_PROFILE,),
        ),
        Service(
            "https://provider.example/services/incomplete-datasets",
            "Incomplete datasets",
            "https://catalogue.example/datasets",
            (DCAT_AP_MINIMAL_PROFILE, DCAT_AP_3_0_1_PROFILE),
            serves_dataset_ids=("https://provider.example/datasets/one",),
        ),
    ),
)
def test_content_negotiation_requires_complete_conforming_service(service: Service) -> None:
    with pytest.raises(ValueError, match="qualifying published"):
        _provider_with_services(service, content_negotiation=True).dsp_catalogue(request())


def test_empty_and_service_only_sources_fail_at_snapshot() -> None:
    with pytest.raises(ValueError, match="no dataset publications"):
        DspProvider(config(), publications=()).dsp_catalogue(request())
    with pytest.raises(ValueError, match="no dataset publications"):
        DspProvider(
            config(),
            publications=(
                DspPublicationRecord(
                    service=Service(
                        "https://provider.example/services/catalogue",
                        "Catalogue",
                        "https://catalogue.example/api",
                        ("https://example.test/std",),
                    )
                ),
            ),
        ).dsp_catalogue(request())


def test_provider_rejects_wrong_publication_record_layout() -> None:
    class WrongLayoutStore:
        entry_layout = (
            EntryFamilyLayout(
                name="dsp-publications",
                family=DspPublicationEntry,
                definition_id=None,
                record_names=("old",),
                records=(DspDatasetPublication,),
                record_definition_ids=(None,),
            ),
        )

        def searcher(self, **_kwargs: object) -> object:
            raise AssertionError("wrong layout should be rejected before querying")

        def fetch(self, _cls: type, _sid: int) -> object:
            raise AssertionError("wrong layout should be rejected before fetching")

        def stored_property_plan(self, _family: type) -> object:
            raise AssertionError("wrong layout should be rejected before planning")

    with pytest.raises(ValueError, match="mapped solely to DspPublicationRecord"):
        DspProvider(config(), store=WrongLayoutStore())
