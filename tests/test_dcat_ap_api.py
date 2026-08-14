"""DCAT-AP minimal GET protocol HTTP behavior."""

from httk.store.db import Database, SqlStore
from starlette.testclient import TestClient
from test_dsp_config import companion, config, publication

from httk.serve.dcat_ap import create_dcat_ap_app
from httk.serve.dsp import (
    DCAT_AP_MINIMAL_PROFILE,
    DSP_CONTEXT,
    DspDatasetPublication,
    DspProvider,
    DspPublicationEntry,
)
from httk.serve.dsp.api import DCAT_MEDIA_TYPE


def provider(*, store: SqlStore | None = None) -> DspProvider:
    settings = config(dcat_ap_service=companion(), dcat_ap_content_negotiation=True)
    if store is not None:
        return DspProvider(settings, store=store)
    return DspProvider(settings, datasets=(publication(),))


def test_discovery_and_catalogue_get_head_cache_cors_and_accept() -> None:
    app = create_dcat_ap_app(provider())
    with TestClient(app, base_url="https://provider.example") as client:
        discovery = client.get("/.well-known/dcat-ap-minimal")
        assert discovery.status_code == 200
        assert discovery.headers["content-type"] == "application/json"
        assert discovery.headers["access-control-allow-origin"] == "*"
        assert discovery.json() == {
            "services": [
                {
                    "version": "3.0.1",
                    "profile": DCAT_AP_MINIMAL_PROFILE,
                    "endpoint": "https://provider.example/dcat-ap/catalogue/",
                    "catalogueId": "https://provider.example/catalog",
                    "serviceId": "https://provider.example/services/dcat-ap",
                    "dspVersionDiscovery": "https://provider.example/connector/.well-known/dspace-version",
                }
            ]
        }
        head = client.head("/.well-known/dcat-ap-minimal")
        assert head.status_code == 200
        assert head.content == b""
        assert head.headers["etag"] == discovery.headers["etag"]
        cached = client.get("/.well-known/dcat-ap-minimal", headers={"If-None-Match": discovery.headers["etag"]})
        assert cached.status_code == 304

        catalogue = client.get("/dcat-ap/catalogue/", headers={"Accept": "application/ld+json"})
        assert catalogue.status_code == 200
        assert catalogue.headers["content-type"] == DCAT_MEDIA_TYPE
        assert catalogue.headers["access-control-allow-origin"] == "*"
        assert 'rel="profile"' in catalogue.headers["link"]
        catalogue_head = client.head("/dcat-ap/catalogue/")
        assert catalogue_head.status_code == 200
        assert catalogue_head.content == b""
        assert catalogue_head.headers["content-length"] == catalogue.headers["content-length"]
        assert client.get("/dcat-ap/catalogue/", headers={"Accept": "text/turtle"}).status_code == 406


def test_get_and_dsp_negotiated_representations_share_the_common_catalogue_value() -> None:
    value = provider()
    with TestClient(create_dcat_ap_app(value), base_url="https://provider.example") as get_client:
        get_document = get_client.get("/dcat-ap/catalogue/").json()
    dsp_document = value.dsp_catalogue({"@context": [DSP_CONTEXT], "@type": "CatalogRequestMessage"})
    assert {key: child for key, child in get_document.items() if key != "@context"} == {
        key: child for key, child in dsp_document.items() if key != "@context"
    }


def test_get_catalogue_reads_live_store_and_does_not_close_it() -> None:
    store = SqlStore(
        Database.sqlite(),
        entry_records={DspPublicationEntry: DspDatasetPublication},
    )
    store.save(publication("one"))
    app = create_dcat_ap_app(provider(store=store))
    with TestClient(app, base_url="https://provider.example") as client:
        assert len(client.get("/dcat-ap/catalogue/").json()["dataset"]) == 1
        store.save(publication("two"))
        assert len(client.get("/dcat-ap/catalogue/").json()["dataset"]) == 2
    store.save(publication("three"))
    assert len(app.state.dcat_ap_provider.profile.datasets) == 3
