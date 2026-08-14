"""Adapt the bundled DSP OpenAPI contract to a Starlette application."""

import json
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

import yaml
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .catalogue import DCAT_MEDIA_TYPE, DCAT_PROFILE, DspCatalogueRepresentation
from .models import DspProtocolError, ErrorKind
from .provider import DspProvider, _AutomaticBatch
from .validation import DspSchemaError, validate_document

type HandlerResult = dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class OpenAPIOperation:
    """Describe one supported operation extracted from the bundled contract.

    :param method: Lower-case HTTP method.
    :param path: OpenAPI path template.
    :param operation_id: Unique business-dispatch identifier.
    :param request_schema: Optional canonical request schema identifier.
    :param responses: Status-to-media-type-and-schema response contract.
    """

    method: str
    path: str
    operation_id: str
    request_schema: str | None
    responses: Mapping[int, tuple[tuple[str | None, str | None], ...]]


def openapi_document() -> dict[str, Any]:
    """Load the authoritative packaged OpenAPI document.

    :return: Parsed OpenAPI mapping.
    """
    resource = files("httk.serve.dsp").joinpath("schemas", "openapi.yaml")
    document = yaml.safe_load(resource.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("bundled OpenAPI document must be an object")
    return document


def _local_ref(document: Mapping[str, Any], value: object) -> object:
    """Resolve the deliberately supported local OpenAPI reference form."""
    while isinstance(value, Mapping) and set(value) == {"$ref"}:
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            break
        current: object = document
        for part in reference[2:].split("/"):
            if not isinstance(current, Mapping) or part not in current:
                raise RuntimeError(f"unresolvable local OpenAPI reference: {reference}")
            current = current[part]
        value = current
    return value


def _schema_ref(value: object) -> str:
    """Return the sole supported external schema reference."""
    if not isinstance(value, Mapping) or set(value) != {"$ref"}:
        raise RuntimeError("OpenAPI body schemas must be single external $ref objects")
    reference = value["$ref"]
    if not isinstance(reference, str) or reference.startswith("#"):
        raise RuntimeError("OpenAPI body schemas must reference a canonical bundled schema")
    return reference


def _body_contracts(document: Mapping[str, Any], body: object) -> tuple[tuple[str, str], ...]:
    """Extract supported media types and their external schema references."""
    body = _local_ref(document, body)
    if not isinstance(body, Mapping):
        raise RuntimeError("OpenAPI request/response body must be an object")
    content = body.get("content")
    if not isinstance(content, Mapping) or not content:
        raise RuntimeError("OpenAPI bodies must declare at least one media type")
    contracts = []
    for media_type, media in content.items():
        if not isinstance(media_type, str) or not isinstance(media, Mapping):
            raise RuntimeError("invalid OpenAPI media-type declaration")
        contracts.append((media_type, _schema_ref(media.get("schema"))))
    return tuple(contracts)


def openapi_operations() -> tuple[OpenAPIOperation, ...]:
    """Parse and validate the supported subset of the bundled OpenAPI contract.

    :return: Operations in document order.
    :raises RuntimeError: If the document uses an unsupported construct.
    """
    document = openapi_document()
    if document.get("openapi") != "3.1.0" or "webhooks" in document:
        raise RuntimeError("the DSP adapter requires an OpenAPI 3.1.0 path contract")
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise RuntimeError("OpenAPI paths must be an object")
    operations: list[OpenAPIOperation] = []
    identifiers: set[str] = set()
    for path, path_item in paths.items():
        if not isinstance(path, str) or not path.startswith("/") or not isinstance(path_item, Mapping):
            raise RuntimeError("OpenAPI path entries must be absolute path objects")
        unsupported = set(path_item) - {"get", "post"}
        if unsupported:
            raise RuntimeError(f"unsupported OpenAPI path constructs at {path}: {sorted(unsupported)}")
        for method, operation in path_item.items():
            if not isinstance(operation, Mapping):
                raise RuntimeError(f"OpenAPI operation {method} {path} must be an object")
            unknown_operation_fields = set(operation) - {
                "description",
                "operationId",
                "parameters",
                "requestBody",
                "responses",
                "summary",
                "tags",
            }
            if unknown_operation_fields:
                raise RuntimeError(
                    f"unsupported OpenAPI constructs in operation at {method} {path}: "
                    f"{sorted(unknown_operation_fields)}"
                )
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id or operation_id in identifiers:
                raise RuntimeError("OpenAPI operationId values must be non-empty and unique")
            identifiers.add(operation_id)
            if "callbacks" in operation or "security" in operation or "servers" in operation:
                raise RuntimeError(f"unsupported OpenAPI construct in operation {operation_id}")
            parameters = operation.get("parameters", [])
            if not isinstance(parameters, list):
                raise RuntimeError(f"parameters for {operation_id} must be an array")
            for parameter in parameters:
                parameter = _local_ref(document, parameter)
                if (
                    not isinstance(parameter, Mapping)
                    or parameter.get("in") != "path"
                    or parameter.get("required") is not True
                ):
                    raise RuntimeError(f"only required path parameters are supported in {operation_id}")
            request_schema = None
            if "requestBody" in operation:
                request_body = _local_ref(document, operation["requestBody"])
                if not isinstance(request_body, Mapping) or request_body.get("required") is not True:
                    raise RuntimeError(f"request body for {operation_id} must be required")
                request_contracts = _body_contracts(document, request_body)
                if len(request_contracts) != 1:
                    raise RuntimeError(f"DSP request body for {operation_id} must declare one media type")
                media_type, request_schema = request_contracts[0]
                if media_type != "application/json":
                    raise RuntimeError(f"DSP request body for {operation_id} must use application/json")
            declared_responses = operation.get("responses")
            if not isinstance(declared_responses, Mapping) or not declared_responses:
                raise RuntimeError(f"responses for {operation_id} must be declared")
            responses: dict[int, tuple[tuple[str | None, str | None], ...]] = {}
            for status_text, response in declared_responses.items():
                if not isinstance(status_text, str) or not status_text.isdigit():
                    raise RuntimeError(f"only exact numeric response codes are supported in {operation_id}")
                response = _local_ref(document, response)
                if not isinstance(response, Mapping):
                    raise RuntimeError(f"response {status_text} for {operation_id} must be an object")
                if "content" in response:
                    responses[int(status_text)] = _body_contracts(document, response)
                else:
                    responses[int(status_text)] = ((None, None),)
            operations.append(OpenAPIOperation(method, path, operation_id, request_schema, responses))
    return tuple(operations)


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


def _success_status(operation_id: str) -> int:
    """Return the success status assigned by the OpenAPI operation."""
    if operation_id in {"negotiation_request", "transfer_request"}:
        return 201
    return 200


def _route_endpoint(provider: DspProvider, operation: OpenAPIOperation) -> Callable[[Request], Awaitable[Response]]:
    """Build one request adapter closure from an OpenAPI operation."""

    async def endpoint(request: Request) -> Response:
        body: dict[str, object] | None = None
        path = {name: str(value) for name, value in request.path_params.items()}
        automatic_batch = provider.automatic_batch()
        try:
            catalogue_representation = (
                provider.select_catalogue_representation(request.headers.get("accept"))
                if operation.operation_id == "catalog_request"
                else None
            )
            if operation.request_schema is not None:
                content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json":
                    raise _schema_error(operation.operation_id, "Content-Type must be application/json", path, None)
                try:
                    parsed = json.loads(
                        (await request.body()).decode("utf-8"),
                        parse_constant=lambda value: (_ for _ in ()).throw(
                            ValueError(f"invalid JSON constant {value}")
                        ),
                    )
                except ValueError as error:
                    raise _schema_error(
                        operation.operation_id, "request body must be valid UTF-8 JSON", path, None
                    ) from error
                if not isinstance(parsed, dict):
                    raise _schema_error(operation.operation_id, "request body must be a JSON object", path, parsed)
                body = parsed
                try:
                    validate_document(operation.request_schema, body)
                except DspSchemaError as error:
                    raise _schema_error(operation.operation_id, str(error), path, body) from error
            result = await _dispatch(
                provider,
                operation.operation_id,
                path,
                body,
                automatic_batch,
                catalogue_representation=catalogue_representation,
            )
            status = _success_status(operation.operation_id)
            if result is None:
                response = Response(status_code=status)
            else:
                contracts = operation.responses[status]
                if operation.operation_id == "catalog_request":
                    assert catalogue_representation is not None
                    selected = next(
                        (contract for contract in contracts if contract[0] == catalogue_representation.media_type),
                        None,
                    )
                    if selected is None:
                        raise RuntimeError(
                            f"OpenAPI has no {catalogue_representation.media_type} response for catalog_request"
                        )
                    media_type, schema = selected
                else:
                    if len(contracts) != 1:
                        raise RuntimeError(f"OpenAPI response for {operation.operation_id} is ambiguous")
                    media_type, schema = contracts[0]
                if schema is None or media_type is None:
                    raise RuntimeError(f"OpenAPI success response for {operation.operation_id} has no body contract")
                validate_document(schema, result)
                headers = None
                if operation.operation_id == "catalog_request" and catalogue_representation is not None:
                    headers = dict(catalogue_representation.headers) or None
                response = JSONResponse(result, status_code=status, media_type=media_type, headers=headers)
            if provider.has_automatic_actions(automatic_batch):
                response.background = BackgroundTask(provider.release_automatic, automatic_batch)
            return response
        except DspProtocolError as error:
            document = _complete_error_document(error, path, body)
            contract = operation.responses.get(error.status_code)
            if contract is None:
                raise RuntimeError(
                    f"OpenAPI does not declare {error.status_code} for {operation.operation_id}"
                ) from error
            if len(contract) != 1:
                raise RuntimeError(f"OpenAPI error response for {operation.operation_id} is ambiguous")
            media_type, schema = contract[0]
            if media_type is None or schema is None:
                raise RuntimeError(
                    f"OpenAPI error response for {operation.operation_id} has no body contract"
                ) from error
            validate_document(schema, document)
            return JSONResponse(document, status_code=error.status_code, media_type=media_type)

    endpoint.__name__ = operation.operation_id
    return endpoint


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
    routes = []
    for operation in openapi_operations():
        route_path = operation.path.replace("{id}", "{id:path}")
        routes.append(Route(route_path, _route_endpoint(provider, operation), methods=[operation.method.upper()]))

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        try:
            yield
        finally:
            await provider.cancel_automatic()

    app = Starlette(debug=debug, routes=routes, lifespan=lifespan)
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
