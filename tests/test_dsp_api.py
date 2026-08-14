"""Exercise the OpenAPI-driven DSP HTTP adapter and mount composition."""

from starlette.testclient import TestClient
from test_dsp_config import companion, config, publication

from httk.serve.dsp import DSP_CONTEXT, EU_FILE_TYPE_JSON, DspProvider, create_dsp_app
from httk.serve.dsp.api import DCAT_MEDIA_TYPE, openapi_operations


def provider(*, alternate: bool = False) -> DspProvider:
    return DspProvider(
        config(
            dcat_ap_service=companion() if alternate else None,
            dcat_ap_content_negotiation=alternate,
        ),
        datasets=(publication(),),
    )


def test_openapi_exact_endpoint_inventory() -> None:
    assert {(operation.method.upper(), operation.path) for operation in openapi_operations()} == {
        ("GET", "/.well-known/dspace-version"),
        ("POST", "/2025-1/catalog/request"),
        ("GET", "/2025-1/catalog/datasets/{id}"),
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


def test_catalogue_http_contract_negotiates_only_explicit_json_ld() -> None:
    with TestClient(create_dsp_app(provider(alternate=True)), base_url="https://provider.example") as client:
        catalogue = client.post(
            "/2025-1/catalog/request",
            json={"@context": [DSP_CONTEXT], "@type": "CatalogRequestMessage"},
        )
        assert catalogue.status_code == 200
        assert catalogue.headers["content-type"] == "application/json"
        assert catalogue.json()["dataset"][0]["distribution"][0]["format"] == EU_FILE_TYPE_JSON
        alternate = client.post(
            "/2025-1/catalog/request",
            headers={"Accept": "application/ld+json"},
            json={"@context": [DSP_CONTEXT], "@type": "CatalogRequestMessage"},
        )
        assert alternate.headers["content-type"] == DCAT_MEDIA_TYPE
        assert alternate.headers["vary"] == "Accept"
        assert {k: v for k, v in catalogue.json().items() if k != "@context"} == {
            k: v for k, v in alternate.json().items() if k != "@context"
        }
        assert client.get("/2025-1/catalog").status_code == 404

    with TestClient(create_dsp_app(provider()), base_url="https://provider.example") as client:
        rejected = client.post(
            "/2025-1/catalog/request",
            headers={"Accept": "application/ld+json"},
            json={"@context": [DSP_CONTEXT], "@type": "CatalogRequestMessage"},
        )
        assert rejected.status_code == 406
