"""Store-native DSP catalogue, profile, and file-metadata coverage."""

from dataclasses import replace

import pytest
from httk.core import Dataset, DatasetDistribution, DatasetRecord, Service, ServiceRecord
from httk.store.db import Database, SqlStore
from starlette.testclient import TestClient

from httk.serve.dsp import (
    DCAT_AP_3_0_1_PROFILE,
    DCAT_AP_MINIMAL_PROFILE,
    DSP_CONTEXT,
    EU_FILE_TYPE_CSV,
    EU_FILE_TYPE_JSON,
    HTTP_ENDPOINT_TYPE,
    IANA_MEDIA_TYPE_CSV,
    IANA_MEDIA_TYPE_JSON,
    DspDatasetPublication,
    DspProvider,
    DspProviderConfig,
    DspPublicationEntry,
    DspPublicationRecord,
    create_dsp_app,
)


def publication(
    name: str,
    suffix: str = ".csv",
    *,
    distribution_id: str | None = None,
    format_iri: str | None = None,
    media_type_iri: str | None = None,
    byte_size: int | None = None,
    sha256: str | None = None,
    offer_id: str | None = None,
) -> DspDatasetPublication:
    distribution = DatasetDistribution(
        id=distribution_id,
        access_url=f"https://provider.example/files/{name}{suffix}",
        format_iri=format_iri,
        media_type_iri=media_type_iri,
        byte_size=byte_size,
        sha256=sha256,
    )
    return DspDatasetPublication(
        Dataset(
            f"https://provider.example/datasets/{name}",
            f"Dataset {name}",
            f"Description {name}",
            "https://provider.example/publisher",
            "Publisher",
            (distribution,),
        ),
        offer_id=offer_id,
    )


def config(**changes: object) -> DspProviderConfig:
    values: dict[str, object] = {
        "public_base_url": "https://provider.example",
        "service_id": "https://provider.example/services/download",
        "service_title": "Downloads",
        "participant_id": "https://provider.example/participant",
        "catalog_id": "https://provider.example/catalog",
        "catalog_title": "Catalogue",
        "catalog_description": "Description",
    }
    values.update(changes)
    return DspProviderConfig(**values)  # type: ignore[arg-type]


def request() -> dict[str, object]:
    return {"@context": [DSP_CONTEXT], "@type": "CatalogRequestMessage"}


def test_publication_inference_defaults_and_explicit_metadata() -> None:
    csv = publication("one", byte_size=0, sha256="a" * 64)
    json = publication("two", ".json")

    assert (csv.file_format, csv.media_type) == (EU_FILE_TYPE_CSV, IANA_MEDIA_TYPE_CSV)
    assert (json.file_format, json.media_type) == (EU_FILE_TYPE_JSON, IANA_MEDIA_TYPE_JSON)
    assert csv.offer_id == f"{csv.dataset.id}#offer"
    assert csv.distribution_id == f"{csv.dataset.id}#distribution"
    assert csv.byte_size == 0
    assert csv.sha256 == "a" * 64

    with pytest.raises(ValueError, match="explicit"):
        publication("other", ".zip")
    custom = publication(
        "other",
        ".zip",
        format_iri="https://example.test/file-types/zip",
        media_type_iri="https://www.iana.org/assignments/media-types/application/zip",
    )
    assert custom.file_format.endswith("/zip")
    with pytest.raises(ValueError, match="lowercase"):
        publication("bad", sha256="A" * 64)


@pytest.mark.parametrize("base", ["http://provider.example", "https://provider.example/path", "https://u@x.test"])
def test_config_rejects_non_https_origins(base: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        config(public_base_url=base)


@pytest.mark.parametrize("mount", ["dsp", "/", "/dsp/", "//dsp", "/a/../dsp"])
def test_config_rejects_noncanonical_mounts(mount: str) -> None:
    with pytest.raises(ValueError, match="canonical"):
        config(dsp_mount=mount)


def test_store_catalogue_is_live_and_store_is_caller_owned() -> None:
    database = Database.sqlite()
    store = SqlStore(database, entry_records={DspPublicationEntry: DspPublicationRecord})
    store.save(DspPublicationRecord(dataset=publication("one")))
    provider = DspProvider(config(), store=store)

    assert len(provider.dsp_catalogue(request())["dataset"]) == 1
    store.save(DspPublicationRecord(dataset=publication("two", ".json")))
    assert len(provider.dsp_catalogue(request())["dataset"]) == 2

    with TestClient(create_dsp_app(provider), base_url="https://provider.example") as client:
        assert client.get("/.well-known/dspace-version").status_code == 200
    store.save(DspPublicationRecord(dataset=publication("three")))
    assert len(provider.dsp_catalogue(request())["dataset"]) == 3


def test_store_hydrates_dataset_and_service_envelopes_and_validates_services_live() -> None:
    store = SqlStore(Database.sqlite(), entry_records={DspPublicationEntry: DspPublicationRecord})
    store.save(DspPublicationRecord(dataset=publication("one")))
    provider = DspProvider(config(dcat_ap_content_negotiation=True), store=store)
    with pytest.raises(ValueError, match="qualifying published"):
        provider.dsp_catalogue(request())

    service = Service(
        "https://catalogue.example/services/dcat-ap",
        "DCAT-AP",
        "https://catalogue.example/api",
        (DCAT_AP_MINIMAL_PROFILE, DCAT_AP_3_0_1_PROFILE),
    )
    store.save(DspPublicationRecord(service=service))
    profile = provider.profile
    assert profile.dcat_data_services[0].id == service.id
    assert profile.dcat_data_services[0].serves_dataset_ids == (publication("one").dataset.id,)

    searcher = store.searcher()
    envelope = searcher.variable(DspPublicationRecord)
    searcher.output(envelope, "envelope")
    values = tuple(row.values[0] for row in searcher)
    assert values[0].dataset == publication("one")
    assert isinstance(values[0].dataset.dataset, DatasetRecord)
    assert values[1].service == ServiceRecord.create(service)


def test_store_source_requires_publication_family_and_revalidates_duplicates() -> None:
    missing = SqlStore(Database.sqlite(), entry_records={})
    with pytest.raises(ValueError, match="DspPublicationEntry"):
        DspProvider(config(), store=missing)

    store = SqlStore(Database.sqlite(), entry_records={DspPublicationEntry: DspPublicationRecord})
    first = publication("same")
    store.save(DspPublicationRecord(dataset=first))
    replacement_distribution = replace(first.distribution, access_url="https://provider.example/files/replacement.csv")
    store.save(
        DspPublicationRecord(
            dataset=replace(first, dataset=replace(first.dataset, distributions=(replacement_distribution,)))
        )
    )
    with pytest.raises(ValueError, match="dataset IDs"):
        DspProvider(config(), store=store).dsp_catalogue(request())


def test_minimal_catalogue_uses_per_distribution_file_formats() -> None:
    csv = publication("one", byte_size=12, sha256="b" * 64)
    json = publication("two", ".json")
    provider = DspProvider(
        config(),
        publications=(DspPublicationRecord(dataset=csv), DspPublicationRecord(dataset=json)),
    )
    dsp = provider.dsp_catalogue(request())
    assert [item["distribution"][0]["format"] for item in dsp["dataset"]] == [
        EU_FILE_TYPE_CSV,
        EU_FILE_TYPE_JSON,
    ]
    distribution = provider.dcat_catalogue()["dataset"][0]["distribution"][0]
    assert distribution["dcat:mediaType"] == {"@id": IANA_MEDIA_TYPE_CSV, "@type": "dct:MediaType"}
    assert distribution["dcat:downloadURL"] == {"@id": "https://provider.example/files/one.csv"}
    assert distribution["dcat:byteSize"] == {
        "@value": "12",
        "@type": "http://www.w3.org/2001/XMLSchema#nonNegativeInteger",
    }
    assert (
        distribution["http://spdx.org/rdf/terms#checksum"]["http://spdx.org/rdf/terms#checksumValue"]["@value"]
        == "b" * 64
    )
    assert provider.profile.datasets[0].data_address == {
        "@type": "DataAddress",
        "endpointType": HTTP_ENDPOINT_TYPE,
        "endpoint": "https://provider.example/files/one.csv",
    }
