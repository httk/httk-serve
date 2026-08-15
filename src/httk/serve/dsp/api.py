"""Adapt the bundled DSP OpenAPI contract through the public OpenAPI adapter."""

from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from functools import partial
from typing import Any

from starlette.applications import Starlette
from starlette.background import BackgroundTask

from httk.serve.http.openapi import (
    OpenAPIRequest,
    OpenAPIRequestError,
    OpenAPIResponse,
    OperationBinding,
    OperationContext,
    create_openapi_app,
    operation,
)

from .catalogue import DCAT_MEDIA_TYPE, DCAT_PROFILE
from .models import DspProtocolError, ErrorKind
from .provider import DspProvider
from .validation import dsp_contract

_OPERATIONS: Mapping[str, OperationBinding] = {
    "version_discovery": operation(DspProvider.version_document),
    "catalog_request": operation(DspProvider.catalogue, aliases={"body": "request"}, extras=("representation",)),
    "dataset_request": operation(DspProvider.dsp_dataset, aliases={"id": "dataset_id"}),
    "negotiation_state": operation(DspProvider.get_negotiation),
    "negotiation_request": operation(
        DspProvider.request_negotiation, aliases={"body": "message"}, extras=("_automatic_batch",)
    ),
    "negotiation_counter_request": operation(DspProvider.counter_request, aliases={"body": "message"}),
    "negotiation_event": operation(DspProvider.negotiation_event, aliases={"body": "message"}),
    "agreement_verification": operation(
        DspProvider.verify_agreement, aliases={"body": "message"}, extras=("_automatic_batch",)
    ),
    "negotiation_termination": operation(DspProvider.receive_negotiation_termination, aliases={"body": "message"}),
    "transfer_state": operation(DspProvider.get_transfer),
    "transfer_request": operation(
        DspProvider.request_transfer, aliases={"body": "message"}, extras=("_automatic_batch",)
    ),
    "transfer_start": operation(DspProvider.resume_transfer, aliases={"body": "message"}),
    "transfer_suspension": operation(DspProvider.receive_transfer_suspension, aliases={"body": "message"}),
    "transfer_completion": operation(DspProvider.receive_transfer_completion, aliases={"body": "message"}),
    "transfer_termination": operation(DspProvider.receive_transfer_termination, aliases={"body": "message"}),
}


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


@asynccontextmanager
async def _scope(provider: DspProvider, request: OpenAPIRequest):
    """Supply per-request DSP inputs and defer automatic callbacks past a clean response."""
    batch = provider.automatic_batch()
    context = OperationContext(extras={"_automatic_batch": batch})
    if request.operation.operation_id == "catalog_request":
        representation = provider.select_catalogue_representation(request.header("accept"))
        context.extras["representation"] = representation
        context.media_type = representation.media_type
        context.headers = dict(representation.headers)
    yield context
    if provider.has_automatic_actions(batch):
        context.background = BackgroundTask(provider.release_automatic, batch)


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
    exception_handlers: Mapping[type[Exception], Callable[[Exception, OpenAPIRequest], OpenAPIResponse]] = {
        DspProtocolError: _adapt_protocol_error
    }
    app = create_openapi_app(
        contract,
        _OPERATIONS,
        implementation=provider,
        request_error_handler=_request_error,
        exception_handlers=exception_handlers,
        request_scope=partial(_scope, provider),
        scope_names=("_automatic_batch", "representation"),
        lifespan=lifespan,
        debug=debug,
        path_converters={"id": "path"},
    )
    app.state.dsp_provider = provider
    return app


__all__ = ["DCAT_MEDIA_TYPE", "DCAT_PROFILE", "create_dsp_app"]
