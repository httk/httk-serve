"""Serve a minimal public DCAT-AP catalogue and discovery document."""

import hashlib
import json
from collections.abc import Mapping
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from httk.serve.dsp.api import DCAT_MEDIA_TYPE
from httk.serve.dsp.provider import DspProvider

DISCOVERY_VERSION = "3.0.1"
DCAT_AP_MINIMAL_DISCOVERY_PATH = "/.well-known/dcat-ap-minimal"


def _json_response(
    document: Mapping[str, object],
    request: Request,
    *,
    media_type: str,
    profile: str | None = None,
) -> Response:
    """Build one cacheable public JSON response with conditional GET support."""
    content = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    etag = f'"{hashlib.sha256(content).hexdigest()}"'
    headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "public, max-age=60",
        "ETag": etag,
    }
    if profile is not None:
        headers["Link"] = f'<{profile}>; rel="profile"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content, media_type=media_type, headers=headers)


def _accepts_json_ld(header: str | None) -> bool:
    """Return whether an HTTP Accept field permits JSON-LD."""
    if header is None or not header.strip():
        return True
    for item in header.split(","):
        normalized = item.strip().lower().replace(" ", "")
        media_range = normalized.split(";", 1)[0]
        if media_range in {"*/*", "application/*", "application/ld+json"} and "q=0" not in normalized:
            return True
    return False


def create_dcat_ap_app(
    provider: DspProvider,
    *,
    discovery_path: str = DCAT_AP_MINIMAL_DISCOVERY_PATH,
    debug: bool = False,
) -> Starlette:
    """Create a DCAT-AP minimal discovery and catalogue application.

    The provider must configure exactly one ``dcat_ap_service``. Its endpoint
    URL selects the catalogue route. ``discovery_path`` lets an external
    profile select its own well-known identifier without coupling that
    identity to this reusable feature set. The caller retains ownership of the
    provider and store.

    :param provider: Live publication provider shared with DSP serving.
    :param discovery_path: Canonical root-relative discovery route.
    :param debug: Whether Starlette debug responses are enabled.
    :return: A root-mountable Starlette application.
    """
    if not isinstance(provider, DspProvider):
        raise TypeError("provider must be a DspProvider")
    service = provider.config.dcat_ap_service
    if service is None:
        raise ValueError("provider must configure dcat_ap_service")
    if (
        not isinstance(discovery_path, str)
        or not discovery_path.startswith("/")
        or discovery_path == "/"
        or discovery_path.endswith("/")
        or "//" in discovery_path
        or "?" in discovery_path
        or "#" in discovery_path
        or "%" in discovery_path
        or any(part in {"", ".", ".."} for part in discovery_path[1:].split("/"))
    ):
        raise ValueError("discovery_path must be a canonical root-relative path")
    parsed = urlsplit(service.endpoint_url)
    catalogue_path = parsed.path or "/"
    if catalogue_path == discovery_path:
        raise ValueError("the catalogue endpoint must differ from the discovery endpoint")

    async def discovery(request: Request) -> Response:
        document: dict[str, object] = {
            "services": [
                {
                    "version": DISCOVERY_VERSION,
                    "profile": provider.config.dcat_ap_profile,
                    "endpoint": service.endpoint_url,
                    "catalogueId": provider.config.catalog_id,
                    "serviceId": service.id,
                    "dspVersionDiscovery": (f"{provider.config.connector_root_url}/.well-known/dspace-version"),
                }
            ]
        }
        return _json_response(document, request, media_type="application/json")

    async def catalogue(request: Request) -> Response:
        if not _accepts_json_ld(request.headers.get("accept")):
            return Response(
                "Not Acceptable",
                status_code=406,
                media_type="text/plain",
                headers={"Access-Control-Allow-Origin": "*"},
            )
        return _json_response(
            provider.dcat_catalogue(),
            request,
            media_type=DCAT_MEDIA_TYPE,
            profile=provider.config.dcat_ap_profile,
        )

    app = Starlette(
        debug=debug,
        routes=[
            Route(discovery_path, discovery, methods=["GET"]),
            Route(catalogue_path, catalogue, methods=["GET"]),
        ],
    )
    app.state.dcat_ap_provider = provider
    return app


__all__ = [
    "DCAT_AP_MINIMAL_DISCOVERY_PATH",
    "DISCOVERY_VERSION",
    "create_dcat_ap_app",
]
