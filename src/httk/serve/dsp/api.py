"""Adapt the bundled DSP OpenAPI contract through the public OpenAPI adapter."""

from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.background import BackgroundTask

from httk.serve.http.openapi import (
    OpenAPIOperation,
    OpenAPIRequest,
    OpenAPIRequestError,
    OpenAPIResponse,
    create_openapi_app,
)

from .catalogue import DCAT_MEDIA_TYPE, DCAT_PROFILE, DspCatalogueRepresentation
from .models import DspProtocolError, ErrorKind
from .provider import DspProvider, _AutomaticBatch
from .validation import dsp_contract

type HandlerResult = dict[str, Any] | None


def openapi_document() -> dict[str, Any]:
    """Load the authoritative packaged OpenAPI document.

    :return: Parsed OpenAPI mapping.
    """
    return dsp_contract().document()


def openapi_operations() -> tuple[OpenAPIOperation, ...]:
    """Parse and validate the supported subset of the bundled OpenAPI contract.

    :return: Operations in document order.
    :raises RuntimeError: If the document uses an unsupported construct.
    """
    return dsp_contract().operations


async def _dispatch(
    provider: DspProvider,
    operation_id: str,
    path: Mapping[str, str],
    body: dict[str, object] | None,
    automatic_batch: _AutomaticBatch | None = None,
    catalogue_representation: DspCatalogueRepresentation | None = None,
) -> HandlerResult:
    """Dispatch one validated operation to its provider business method."""
    if operation_id == "version_discovery":
        return provider.version_document()
    elif operation_id == "catalog_request":
        if catalogue_representation is None:
            raise RuntimeError("catalog_request requires a selected catalogue representation")
        return provider.catalogue(body or {}, catalogue_representation)
    elif operation_id == "dataset_request":
        return provider.dsp_dataset(path["id"])
    elif operation_id == "negotiation_state":
        return await provider.get_negotiation(path["providerPid"])
    elif operation_id == "negotiation_request":
        return await provider.request_negotiation(body or {}, _automatic_batch=automatic_batch)
    elif operation_id == "negotiation_counter_request":
        await provider.counter_request(path["providerPid"], body or {})
        return None
    elif operation_id == "negotiation_event":
        await provider.negotiation_event(path["providerPid"], body or {})
        return None
    elif operation_id == "agreement_verification":
        await provider.verify_agreement(path["providerPid"], body or {}, _automatic_batch=automatic_batch)
        return None
    elif operation_id == "negotiation_termination":
        await provider.receive_negotiation_termination(path["providerPid"], body or {})
        return None
    elif operation_id == "transfer_state":
        return await provider.get_transfer(path["providerPid"])
    elif operation_id == "transfer_request":
        return await provider.request_transfer(body or {}, _automatic_batch=automatic_batch)
    elif operation_id == "transfer_start":
        await provider.resume_transfer(path["providerPid"], body or {})
        return None
    elif operation_id == "transfer_suspension":
        await provider.receive_transfer_suspension(path["providerPid"], body or {})
        return None
    elif operation_id == "transfer_completion":
        await provider.receive_transfer_completion(path["providerPid"], body or {})
        return None
    elif operation_id == "transfer_termination":
        await provider.receive_transfer_termination(path["providerPid"], body or {})
        return None
    else:
        raise RuntimeError(f"no DSP handler is registered for {operation_id}")


def _error_kind(operation_id: str) -> ErrorKind:
    """Classify an operation for its official DSP error document."""
    if operation_id.startswith("catalog") or operation_id == "dataset_request":
        return "catalog"
    if operation_id.startswith("negotiation") or operation_id == "agreement_verification":
        return "negotiation"
    return "transfer"


def _schema_error(operation_id: str, detail: str, path: Mapping[str, str], body: object) -> DspProtocolError:
    """Convert adapter validation errors into official protocol error objects."""
    mapping = body if isinstance(body, Mapping) else {}
    provider_pid = path.get("providerPid") or mapping.get("providerPid")
    consumer_pid = mapping.get("consumerPid")
    return DspProtocolError(
        _error_kind(operation_id),
        400,
        detail,
        code="invalid-message",
        provider_pid=provider_pid if isinstance(provider_pid, str) else None,
        consumer_pid=consumer_pid if isinstance(consumer_pid, str) else None,
    )


def _complete_error_document(error: DspProtocolError, path: Mapping[str, str], body: object) -> dict[str, Any]:
    """Fill mandatory process correlation fields when malformed input omitted them."""
    document = error.as_document()
    if error.kind in {"negotiation", "transfer"}:
        mapping = body if isinstance(body, Mapping) else {}
        document.setdefault("providerPid", path.get("providerPid") or mapping.get("providerPid") or "")
        document.setdefault("consumerPid", mapping.get("consumerPid") or "")
        for field_name in ("providerPid", "consumerPid"):
            value = document[field_name]
            if not isinstance(value, str) or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
                document[field_name] = ""
    return document


def _handler(provider: DspProvider, operation_id: str) -> Callable[[OpenAPIRequest], Awaitable[OpenAPIResponse]]:
    """Build one DSP business handler for the neutral request contract."""

    async def handler(request: OpenAPIRequest) -> OpenAPIResponse:
        if request.body is not None and not isinstance(request.body, dict):
            raise _schema_error(operation_id, "request body must be a JSON object", request.path_params, request.body)
        automatic_batch = provider.automatic_batch()
        catalogue_representation = (
            provider.select_catalogue_representation(request.header("accept"))
            if operation_id == "catalog_request"
            else None
        )
        result = await _dispatch(
            provider,
            operation_id,
            request.path_params,
            request.body,
            automatic_batch,
            catalogue_representation=catalogue_representation,
        )
        background = (
            BackgroundTask(provider.release_automatic, automatic_batch)
            if provider.has_automatic_actions(automatic_batch)
            else None
        )
        if result is None:
            return OpenAPIResponse(background=background)
        media_type = catalogue_representation.media_type if catalogue_representation is not None else None
        headers = dict(catalogue_representation.headers) if catalogue_representation is not None else {}
        return OpenAPIResponse(body=result, media_type=media_type, headers=headers, background=background)

    handler.__name__ = operation_id
    return handler


def _request_error(error: OpenAPIRequestError) -> OpenAPIResponse:
    """Convert neutral request failures to DSP's schema-validated error response."""
    protocol_error = _schema_error(
        error.operation.operation_id, error.detail, error.request.path_params, error.request.body
    )
    return _protocol_error(protocol_error, error.request)


def _protocol_error(error: DspProtocolError, request: OpenAPIRequest) -> OpenAPIResponse:
    """Convert a DSP protocol exception to the neutral response contract."""
    return OpenAPIResponse(
        error.status_code,
        _complete_error_document(error, request.path_params, request.body),
        media_type="application/json",
    )


def _adapt_protocol_error(error: Exception, request: OpenAPIRequest) -> OpenAPIResponse:
    """Narrow the generic exception hook to DSP protocol exceptions."""
    if not isinstance(error, DspProtocolError):
        raise TypeError("DSP exception adapter received a non-DSP exception")
    return _protocol_error(error, request)


def create_dsp_app(provider: DspProvider, *, debug: bool = False) -> Starlette:
    """Create a mountable Starlette application for one DSP provider.

    Routes, methods, body schemas, status codes, and media types are loaded from
    the packaged OpenAPI 3.1 contract. Request and response validation resolves
    only the packaged offline schema registry.

    :param provider: In-memory provider whose business operations are exposed.
    :param debug: Whether Starlette debug responses are enabled for unexpected failures.
    :return: Mountable Starlette application with the provider on ``app.state``.
    :raises TypeError: If ``provider`` is not a :class:`DspProvider`.
    :raises RuntimeError: If the packaged contract uses an unsupported construct.
    """
    if not isinstance(provider, DspProvider):
        raise TypeError("provider must be a DspProvider")

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        try:
            yield
        finally:
            await provider.cancel_automatic()

    contract = dsp_contract()
    operations = contract.operations
    exception_handlers: Mapping[type[Exception], Callable[[Exception, OpenAPIRequest], OpenAPIResponse]] = {
        DspProtocolError: _adapt_protocol_error
    }
    app = create_openapi_app(
        contract,
        {operation.operation_id: _handler(provider, operation.operation_id) for operation in operations},
        request_error_handler=_request_error,
        exception_handlers=exception_handlers,
        lifespan=lifespan,
        debug=debug,
        path_converters={"id": "path"},
    )
    app.state.dsp_provider = provider
    return app


__all__ = [
    "DCAT_MEDIA_TYPE",
    "DCAT_PROFILE",
    "OpenAPIOperation",
    "create_dsp_app",
    "openapi_document",
    "openapi_operations",
]
