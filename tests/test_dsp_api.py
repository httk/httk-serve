"""Exercise the OpenAPI-driven DSP HTTP adapter and mount composition."""

import asyncio
import json
from dataclasses import replace

from httk.core import Dataset
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from httk.serve import ASGIAppMount, compose_asgi_apps
from httk.serve.dsp import DspProvider, DspProviderConfig
from httk.serve.dsp.api import DCAT_MEDIA_TYPE, create_dsp_app, openapi_operations
from httk.serve.dsp.validation import validate_document

CONTEXT = ["https://w3id.org/dspace/2025/1/context.jsonld"]


def _provider() -> DspProvider:
    """Build a deterministic provider without automatic callbacks."""
    config = DspProviderConfig(
        connector_root_url="https://provider.example/dsp",
        service_id="https://provider.example/services/dsp",
        participant_id="https://provider.example/participants/provider",
        catalog_id="https://provider.example/catalogs/example",
        catalog_title="Example catalogue",
        catalog_description="A fixed catalogue.",
        dataset=Dataset(
            "https://provider.example/datasets/example",
            "Example dataset",
            "A static dataset.",
            "https://provider.example/participants/provider",
            "Example Provider",
        ),
        offer_id="https://provider.example/offers/example",
        distribution_id="https://provider.example/distributions/example",
        data_service_id="https://provider.example/services/data",
        data_service_title="Example DSP service",
        access_url="https://provider.example/data/example",
        data_address={
            "@type": "DataAddress",
            "endpointType": "https://w3id.org/idsa/v4.1/HTTP",
            "endpoint": "https://provider.example/data/example",
        },
        automatic_progression=False,
    )
    return DspProvider(config)


def test_openapi_exact_endpoint_inventory() -> None:
    """Keep the provider-side contract inventory exact."""
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


def test_version_catalogue_dataset_and_dcat_http_contracts() -> None:
    """Validate success responses and their distinct catalogue representations."""
    provider = _provider()
    app = create_dsp_app(provider)
    assert app.state.dsp_provider is provider
    client = TestClient(app)

    version = client.get("/.well-known/dspace-version")
    assert version.status_code == 200
    assert version.json()["protocolVersions"] == [
        {
            "version": "2025-1",
            "path": "/2025-1",
            "binding": "HTTPS",
            "serviceId": "https://provider.example/services/dsp",
        }
    ]
    validate_document("https://w3id.org/dspace/2025/1/common/protocol-version-schema.json", version.json())

    catalogue = client.post(
        "/2025-1/catalog/request",
        json={"@context": CONTEXT, "@type": "CatalogRequestMessage", "filter": []},
    )
    assert catalogue.status_code == 200
    assert catalogue.headers["content-type"] == "application/json"
    assert 'rel="alternate"' in catalogue.headers["link"]
    assert catalogue.json()["dataset"][0]["distribution"][0]["format"] == "HttpData-PULL"
    validate_document("https://w3id.org/dspace/2025/1/catalog/catalog-schema.json", catalogue.json())

    dataset = client.get("/2025-1/catalog/datasets/https://provider.example/datasets/example")
    assert dataset.status_code == 200
    validate_document("https://w3id.org/dspace/2025/1/catalog/dataset-schema.json", dataset.json())

    dcat = client.get("/2025-1/catalog")
    assert dcat.status_code == 200
    assert dcat.headers["content-type"] == DCAT_MEDIA_TYPE
    distribution = dcat.json()["dcat:dataset"][0]["dcat:distribution"][0]
    assert distribution["format"]["@id"].endswith("/JSON_LD")
    assert distribution["format"]["@id"] != "HttpData-PULL"
    assert dcat.json()["dcat:service"][0]["endpointURL"] == {"@id": "https://provider.example/dsp/2025-1"}
    validate_document("https://schemas.httk.org/dsp/2025-1/dcat-ap-catalogue.json", dcat.json())


def test_adapter_validates_requests_and_official_errors() -> None:
    """Reject wrong media types, schemas, filters, and unknown identifiers."""
    client = TestClient(create_dsp_app(_provider()))
    wrong_media = client.post("/2025-1/catalog/request", content="{}", headers={"content-type": "text/plain"})
    assert wrong_media.status_code == 400
    validate_document("https://w3id.org/dspace/2025/1/catalog/catalog-error-schema.json", wrong_media.json())

    bad_schema = client.post("/2025-1/catalog/request", json={"@type": "CatalogRequestMessage"})
    assert bad_schema.status_code == 400
    unsupported = client.post(
        "/2025-1/catalog/request",
        json={"@context": CONTEXT, "@type": "CatalogRequestMessage", "filter": [{"operandLeft": "x"}]},
    )
    assert unsupported.status_code == 400
    missing = client.get("/2025-1/catalog/datasets/https://provider.example/datasets/missing")
    assert missing.status_code == 404
    validate_document("https://w3id.org/dspace/2025/1/catalog/catalog-error-schema.json", missing.json())
    nonfinite = client.post(
        "/2025-1/catalog/request",
        content='{"@context": ["https://w3id.org/dspace/2025/1/context.jsonld"], '
        '"@type": "CatalogRequestMessage", "filter": NaN}',
        headers={"content-type": "application/json"},
    )
    assert nonfinite.status_code == 400
    validate_document("https://w3id.org/dspace/2025/1/catalog/catalog-error-schema.json", nonfinite.json())

    surrogate_pid = client.post(
        "/2025-1/negotiations/request",
        content=json.dumps(
            {
                "@context": CONTEXT,
                "@type": "ContractRequestMessage",
                "consumerPid": "\ud800",
                "callbackAddress": "https://consumer.example/callback",
                "offer": {
                    "@id": _provider().config.offer_id,
                    "@type": "Offer",
                    "target": _provider().config.dataset.id,
                    "permission": [{"action": "use"}],
                },
            }
        ),
        headers={"content-type": "application/json"},
    )
    assert surrogate_pid.status_code == 400
    validate_document(
        "https://w3id.org/dspace/2025/1/negotiation/contract-negotiation-error-schema.json",
        surrogate_pid.json(),
    )


def test_negotiation_creation_and_state_read() -> None:
    """Expose validated negotiation creation and state reads through HTTP."""
    provider = _provider()
    client = TestClient(create_dsp_app(provider))
    message = {
        "@context": CONTEXT,
        "@type": "ContractRequestMessage",
        "consumerPid": "urn:uuid:00000000-0000-0000-0000-000000000001",
        "callbackAddress": "https://consumer.example/callback",
        "offer": {
            "@id": provider.config.offer_id,
            "@type": "Offer",
            "target": provider.config.dataset.id,
            "permission": [{"action": "use"}],
        },
    }
    created = client.post("/2025-1/negotiations/request", json=message)
    assert created.status_code == 201
    assert created.json()["state"] == "REQUESTED"
    assert created.json()["providerPid"].startswith("urn:uuid:")
    validate_document("https://w3id.org/dspace/2025/1/negotiation/contract-negotiation-schema.json", created.json())
    state = client.get(f"/2025-1/negotiations/{created.json()['providerPid']}")
    assert state.status_code == 200
    assert state.json() == created.json()


def test_http_automatic_callbacks_start_only_after_the_specific_response_body() -> None:
    """The API acknowledges REQUESTED before its response-local agreement callback starts."""

    async def exercise() -> None:
        body_sent = asyncio.Event()
        callback_observed_body: list[bool] = []

        async def sender(_url: str, _document: dict[str, object]) -> int:
            callback_observed_body.append(body_sent.is_set())
            return 204

        provider = DspProvider(
            replace(_provider().config, automatic_progression=True),
            callback_sender=sender,
            uuid_factory=iter(["negotiation", "agreement"]).__next__,
        )
        app = create_dsp_app(provider)
        message = {
            "@context": CONTEXT,
            "@type": "ContractRequestMessage",
            "consumerPid": "consumer",
            "callbackAddress": "https://consumer.example/callback",
            "offer": {
                "@id": provider.config.offer_id,
                "@type": "Offer",
                "target": provider.config.dataset.id,
                "permission": [{"action": "use"}],
            },
        }
        request_body = json.dumps(message).encode("utf-8")
        received = False
        sent: list[dict[str, object]] = []

        async def receive() -> dict[str, object]:
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": request_body, "more_body": False}

        async def send(event: dict[str, object]) -> None:
            sent.append(event)
            if event["type"] == "http.response.body" and not event.get("more_body", False):
                body_sent.set()

        await app(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/2025-1/negotiations/request",
                "raw_path": b"/2025-1/negotiations/request",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "client": ("127.0.0.1", 10000),
                "server": ("provider.example", 443),
            },
            receive,
            send,
        )
        response_body = next(event["body"] for event in sent if event["type"] == "http.response.body")
        assert isinstance(response_body, bytes)
        created = json.loads(response_body)
        assert created["state"] == "REQUESTED"
        await provider.drain_automatic()
        assert callback_observed_body == [True]
        assert (await provider.get_negotiation(created["providerPid"]))["state"] == "AGREED"

    asyncio.run(exercise())


def test_dsp_mount_does_not_capture_sibling_or_root_routes() -> None:
    """Keep DSP independently mountable beside OPTIMADE and a root site."""

    async def text(_request):
        return PlainTextResponse("ok")

    dsp = create_dsp_app(_provider())
    optimade = Starlette(routes=[Route("/v1/info", text)])
    web = Starlette(routes=[Route("/", text)])
    app = compose_asgi_apps(
        [ASGIAppMount("/optimade", optimade), ASGIAppMount("/dsp", dsp)],
        root=ASGIAppMount("/", web),
    )
    with TestClient(app) as client:
        assert client.get("/dsp/.well-known/dspace-version").status_code == 200
        assert client.get("/optimade/v1/info").text == "ok"
        assert client.get("/").text == "ok"
        assert client.get("/optimade/.well-known/dspace-version").status_code == 404
