"""Exercise the OpenAPI-driven DSP HTTP adapter and mount composition."""

from starlette.testclient import TestClient
from test_dsp_config import config, publication

from httk.serve.dsp import DSP_CONTEXT, EU_FILE_TYPE_JSON, DspProvider, create_dsp_app
from httk.serve.dsp.api import DCAT_MEDIA_TYPE, openapi_operations


def provider(profile: str = "dcat-ap-3.0.1") -> DspProvider:
    return DspProvider(config(catalogue_profile=profile), datasets=(publication(),))


def test_openapi_exact_endpoint_inventory() -> None:
    assert {(operation.method.upper(), operation.path) for operation in openapi_operations()} == {
        ("GET", "/.well-known/dspace-version"),
        ("POST", "/2025-1/catalog/request"),
        ("GET", "/2025-1/catalog/datasets/{id}"),
        ("GET", "/2025-1/catalog"),
        ("GET", "/2025-1/negotiations/{providerPid}"),
        ("POST", "/2025-1/negotiations/request"),
        ("POST", "/2025-1/negotiations/{providerPid}/request"),
        ("POST", "/2025-1/negotiations/{providerPid}/events"),
        ("POST", "/2025-1/negotiations/{providerPid}/agreement/verification"),
        ("POST", "/2025-1/negotiations/{providerPid}/termination"),
        ("GET", "/2025-1/transfers/{providerPid}"),
        ("POST", "/2025-1/transfers/request"),
        ("POST", "/2025-1/transfers/{providerPid}/start"),
        ("POST", "/2025-1/transfers/{providerPid}/suspension"),
        ("POST", "/2025-1/transfers/{providerPid}/completion"),
        ("POST", "/2025-1/transfers/{providerPid}/termination"),
    }


def test_catalogue_http_contract_selects_configured_profile() -> None:
    with TestClient(create_dsp_app(provider()), base_url="https://provider.example") as client:
        catalogue = client.post(
            "/2025-1/catalog/request",
            json={"@context": [DSP_CONTEXT], "@type": "CatalogRequestMessage"},
        )
        assert catalogue.status_code == 200
        assert catalogue.json()["dataset"][0]["distribution"][0]["format"] == EU_FILE_TYPE_JSON
        alternate = client.get("/2025-1/catalog")
        assert alternate.headers["content-type"] == DCAT_MEDIA_TYPE

    with TestClient(create_dsp_app(provider("dcat")), base_url="https://provider.example") as client:
        alternate = client.get("/2025-1/catalog")
        assert alternate.status_code == 200
        assert alternate.headers["content-type"] == "application/ld+json"
