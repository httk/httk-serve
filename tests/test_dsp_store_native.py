"""Store-native DSP catalogue, profile, and file-metadata coverage."""

from dataclasses import replace

import pytest
from httk.core import Dataset
from httk.store.db import Database, SqlStore
from starlette.testclient import TestClient

from httk.serve.dsp import (
    DSP_CONTEXT,
    EU_FILE_TYPE_CSV,
    EU_FILE_TYPE_JSON,
    HTTP_ENDPOINT_TYPE,
    HTTP_PULL_PROFILE,
    IANA_MEDIA_TYPE_CSV,
    IANA_MEDIA_TYPE_JSON,
    DspDatasetPublication,
    DspProvider,
    DspProviderConfig,
    DspPublicationEntry,
    create_dsp_app,
)
from httk.serve.dsp.api import DCAT_MEDIA_TYPE


def publication(name: str, suffix: str = ".csv", **changes: object) -> DspDatasetPublication:
    values: dict[str, object] = {
        "dataset": Dataset(
            f"https://provider.example/datasets/{name}",
            f"Dataset {name}",
            f"Description {name}",
            "https://provider.example/publisher",
            "Publisher",
        ),
        "access_url": f"/files/{name}{suffix}",
    }
    values.update(changes)
    return DspDatasetPublication(**values)  # type: ignore[arg-type]


def config(profile: str = "dcat-ap-3.0.1", **changes: object) -> DspProviderConfig:
    values: dict[str, object] = {
        "public_base_url": "https://provider.example",
        "service_id": "https://provider.example/services/download",
        "service_title": "Downloads",
        "participant_id": "https://provider.example/participant",
        "catalog_id": "https://provider.example/catalog",
        "catalog_title": "Catalogue",
        "catalog_description": "Description",
        "catalogue_profile": profile,
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
        file_format="https://example.test/file-types/zip",
        media_type="https://www.iana.org/assignments/media-types/application/zip",
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
    store = SqlStore(database, entry_records={DspPublicationEntry: DspDatasetPublication})
    store.save(publication("one"))
    provider = DspProvider(config(), store=store)

    assert len(provider.dsp_catalogue(request())["dataset"]) == 1
    store.save(publication("two", ".json"))
    assert len(provider.dsp_catalogue(request())["dataset"]) == 2

    with TestClient(create_dsp_app(provider), base_url="https://provider.example") as client:
        assert client.get("/2025-1/catalog").status_code == 200
    store.save(publication("three"))
    assert len(provider.dsp_catalogue(request())["dataset"]) == 3


def test_store_source_requires_publication_family_and_revalidates_duplicates() -> None:
    missing = SqlStore(Database.sqlite(), entry_records={})
    with pytest.raises(ValueError, match="DspPublicationEntry"):
        DspProvider(config(), store=missing)

    store = SqlStore(Database.sqlite(), entry_records={DspPublicationEntry: DspDatasetPublication})
    first = publication("same")
    store.save(first)
    store.save(replace(first, access_url="/files/replacement.csv"))
    with pytest.raises(ValueError, match="dataset IDs"):
        DspProvider(config(), store=store).dsp_catalogue(request())


def test_strict_and_generic_catalogue_profiles() -> None:
    csv = publication("one", byte_size=12, sha256="b" * 64)
    json = publication("two", ".json")
    strict = DspProvider(config(), datasets=(csv, json))
    dsp = strict.dsp_catalogue(request())
    assert [item["distribution"][0]["format"] for item in dsp["dataset"]] == [
        EU_FILE_TYPE_CSV,
        EU_FILE_TYPE_JSON,
    ]
    distribution = strict.dcat_catalogue()["dcat:dataset"][0]["dcat:distribution"][0]
    assert distribution["mediaType"] == {"@id": IANA_MEDIA_TYPE_CSV, "@type": "dct:MediaType"}
    assert distribution["downloadURL"] == {"@id": "https://provider.example/files/one.csv"}
    assert distribution["byteSize"] == 12
    assert distribution["checksum"]["checksumValue"] == "b" * 64

    generic = DspProvider(config("dcat"), datasets=(csv, json))
    generic_dsp = generic.dsp_catalogue(request())
    assert {item["distribution"][0]["format"] for item in generic_dsp["dataset"]} == {HTTP_PULL_PROFILE}
    assert generic.profile.datasets[0].data_address == {
        "@type": "DataAddress",
        "endpointType": HTTP_ENDPOINT_TYPE,
        "endpoint": "https://provider.example/files/one.csv",
    }

    with TestClient(create_dsp_app(strict), base_url="https://provider.example") as client:
        assert client.get("/2025-1/catalog").headers["content-type"] == DCAT_MEDIA_TYPE
    with TestClient(create_dsp_app(generic), base_url="https://provider.example") as client:
        response = client.get("/2025-1/catalog")
        assert response.headers["content-type"] == "application/ld+json"
        assert "profile=" not in response.headers["content-type"]
