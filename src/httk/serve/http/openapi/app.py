"""The deliberately small OpenAPI 3.1-to-Starlette adapter."""

import contextlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .schemas import OpenAPISchemaError, OpenAPISchemaRegistry

if TYPE_CHECKING:
    # Deferred to break the import cycle: contract.py imports OpenAPIContractError,
    # OpenAPIOperation, and parse_openapi_operations from this module at module level,
    # so this module must not import contract.py at module level in turn. The runtime
    # check in create_openapi_app uses a local import instead; this one is type-only.
    # binding.py imports this module at module level, so it too is referenced type-only here.
    from .binding import BoundOperation, OperationBinding, OperationContext
    from .contract import OpenAPIContract

type RequestErrorHandler = Callable[["OpenAPIRequestError"], "OpenAPIResponse"]
type ExceptionHandler = Callable[[Exception, "OpenAPIRequest"], "OpenAPIResponse | Awaitable[OpenAPIResponse]"]
type RequestScope = Callable[["OpenAPIRequest"], contextlib.AbstractAsyncContextManager["OperationContext"]]


class OpenAPIContractError(ValueError):
    """Report an unsupported or internally inconsistent OpenAPI contract."""


class OpenAPIRequestError(ValueError):
    """Report adapter-generated request parsing or validation failure.

    :param operation: The matching operation.
    :param detail: Human-readable request failure detail.
    :param request: Partial normalized request values.
    """

    def __init__(self, operation: "OpenAPIOperation", detail: str, request: "OpenAPIRequest") -> None:
        super().__init__(detail)
        self.operation = operation
        self.detail = detail
        self.request = request


@dataclass(frozen=True, slots=True)
class OpenAPIParameter:
    """Describe one supported string OpenAPI parameter.

    :param name: Parameter name as declared by OpenAPI.
    :param location: ``path``, ``query``, or ``header``.
    :param required: Whether the parameter must be sent.
    :param enum: Optional exact set of accepted string values.
    """

    name: str
    location: str
    required: bool
    enum: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class OpenAPIOperation:
    """Describe one supported OpenAPI operation.

    :param method: Lowercase HTTP method.
    :param path: OpenAPI path template.
    :param operation_id: Unique operation identifier.
    :param parameters: Supported path, query, and header parameter contracts.
    :param request_schema: Required JSON request schema identifier, if any.
    :param responses: Exact status, media type, and schema response contracts.
    :param success_status: The single declared 2xx status, or ``None`` when the
        operation declares zero or more than one.
    """

    method: str
    path: str
    operation_id: str
    parameters: tuple[OpenAPIParameter, ...]
    request_schema: str | None
    responses: Mapping[int, tuple[tuple[str | None, str | None], ...]]
    success_status: int | None

    @property
    def success_contracts(self) -> tuple[tuple[str | None, str | None], ...]:
        """Return the media type and schema contracts declared for the success status.

        :return: The declared ``(media type, schema id)`` pairs for
            :attr:`success_status`, or an empty tuple when there is no single
            declared success status.
        """
        if self.success_status is None:
            return ()
        return self.responses[self.success_status]

    def response_contracts(self, status: int) -> tuple[tuple[str | None, str | None], ...]:
        """Return the media type and schema contracts declared for one status.

        :param status: Exact HTTP status to look up.
        :return: The declared ``(media type, schema id)`` pairs for ``status``,
            or an empty tuple when the status is not declared.
        """
        return self.responses.get(status, ())


@dataclass(frozen=True, slots=True)
class OpenAPIRequest:
    """Normalized values passed to an OpenAPI operation handler.

    :param operation: Matched OpenAPI operation.
    :param path_params: Normalized route parameters.
    :param query: Query parameters, retaining Starlette's last-value semantics.
    :param headers: Lowercase HTTP header names and values.
    :param body: Validated JSON body, or ``None`` for bodyless operations.
    """

    operation: OpenAPIOperation
    path_params: Mapping[str, str]
    query: Mapping[str, str]
    headers: Mapping[str, str]
    body: Any = None

    def header(self, name: str) -> str | None:
        """Return one request header case-insensitively.

        :param name: Header name.
        :return: Header value, if sent.
        """
        return self.headers.get(name.lower())


@dataclass(frozen=True, slots=True)
class OpenAPIResponse:
    """A handler response constrained by the matched OpenAPI operation.

    :param status: Exact declared HTTP status, or ``None`` to use the matched
        operation's declared success status.
    :param body: Optional JSON-compatible response body.
    :param media_type: Exact declared media type; inferred only when unambiguous.
    :param headers: Additional HTTP response headers.
    :param background: Optional Starlette background task.
    """

    status: int | None = None
    body: Any = None
    media_type: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    background: BackgroundTask | None = None


def _local_ref(document: Mapping[str, Any], value: object) -> object:
    """Resolve the supported local ``#/`` reference form."""
    seen: set[str] = set()
    while isinstance(value, Mapping) and set(value) == {"$ref"}:
        reference = value["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            break
        if reference in seen:
            raise OpenAPIContractError(f"cyclic local OpenAPI reference: {reference}")
        seen.add(reference)
        current: object = document
        for encoded in reference[2:].split("/"):
            part = encoded.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, Mapping) or part not in current:
                raise OpenAPIContractError(f"unresolvable local OpenAPI reference: {reference}")
            current = current[part]
        value = current
    return value


def _schema_ref(value: object) -> str:
    """Extract the sole supported external schema reference."""
    if not isinstance(value, Mapping) or set(value) != {"$ref"}:
        raise OpenAPIContractError("OpenAPI body schemas must be single external $ref objects")
    reference = value["$ref"]
    if not isinstance(reference, str) or reference.startswith("#"):
        raise OpenAPIContractError("OpenAPI body schemas must use external schema $refs")
    return reference


def _body_contracts(document: Mapping[str, Any], body: object) -> tuple[tuple[str, str], ...]:
    """Extract response media types and external schema references."""
    body = _local_ref(document, body)
    if not isinstance(body, Mapping):
        raise OpenAPIContractError("OpenAPI request/response body must be an object")
    content = body.get("content")
    if not isinstance(content, Mapping) or not content:
        raise OpenAPIContractError("OpenAPI bodies must declare at least one media type")
    contracts: list[tuple[str, str]] = []
    for media_type, media in content.items():
        if not isinstance(media_type, str) or not isinstance(media, Mapping) or set(media) != {"schema"}:
            raise OpenAPIContractError("OpenAPI media types must contain only a schema")
        contracts.append((media_type, _schema_ref(media["schema"])))
    return tuple(contracts)


def _template_variables(path: str) -> tuple[str, ...]:
    """Extract and validate the simple brace variables in an OpenAPI path."""
    variables = tuple(re.findall(r"\{([^{}]+)\}", path))
    if "{" in re.sub(r"\{[^{}]+\}", "", path) or "}" in re.sub(r"\{[^{}]+\}", "", path):
        raise OpenAPIContractError(f"invalid path template: {path}")
    if len(set(variables)) != len(variables):
        raise OpenAPIContractError(f"path template variables must be unique: {path}")
    return variables


def _parameter_contract(raw_parameter: Mapping[str, Any], operation_id: str) -> OpenAPIParameter:
    """Parse one deliberately constrained string parameter contract."""
    allowed = {"description", "in", "name", "required", "schema"}
    unknown = set(raw_parameter) - allowed
    if unknown:
        raise OpenAPIContractError(f"unsupported parameter constructs in {operation_id}: {sorted(unknown)}")
    location, name = raw_parameter.get("in"), raw_parameter.get("name")
    if location not in {"path", "query", "header"} or not isinstance(name, str) or not name:
        raise OpenAPIContractError(f"unsupported parameter in {operation_id}")
    required = raw_parameter.get("required", False)
    if not isinstance(required, bool):
        raise OpenAPIContractError(f"parameter required flag must be boolean in {operation_id}")
    if location == "path" and not required:
        raise OpenAPIContractError(f"path parameters must be required in {operation_id}")
    schema = raw_parameter.get("schema")
    if not isinstance(schema, Mapping) or set(schema) - {"type", "enum"} or schema.get("type") != "string":
        raise OpenAPIContractError(f"parameters must use a simple string schema in {operation_id}")
    raw_enum = schema.get("enum")
    if raw_enum is None:
        return OpenAPIParameter(name, location, required)
    if not isinstance(raw_enum, list) or not raw_enum or not all(isinstance(value, str) for value in raw_enum):
        raise OpenAPIContractError(f"parameter enum must be a non-empty string array in {operation_id}")
    return OpenAPIParameter(name, location, required, tuple(raw_enum))


def _is_json_media_type(media_type: str) -> bool:
    """Return whether a declared media type is JSON or structured-suffix JSON."""
    base = media_type.split(";", 1)[0].strip().lower()
    return base == "application/json" or base.endswith("+json")


def parse_openapi_operations(document: Mapping[str, Any]) -> tuple[OpenAPIOperation, ...]:
    """Parse the supported OpenAPI 3.1 path subset.

    Local references may be used for path items, operations, parameters, request
    bodies, and responses. Bodies use external JSON Schema references. Supported
    parameters are path, query, and header parameters with a simple schema.

    :param document: Caller-owned OpenAPI document mapping.
    :return: Operations in document order.
    :raises OpenAPIContractError: If the document uses an unsupported construct.
    """
    if document.get("openapi") != "3.1.0" or "webhooks" in document:
        raise OpenAPIContractError("adapter requires an OpenAPI 3.1.0 paths contract")
    if "security" in document:
        raise OpenAPIContractError("global OpenAPI security is not supported")
    components = document.get("components")
    if isinstance(components, Mapping) and "securitySchemes" in components:
        raise OpenAPIContractError("OpenAPI security schemes are not supported")
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise OpenAPIContractError("OpenAPI paths must be an object")
    operations: list[OpenAPIOperation] = []
    identifiers: set[str] = set()
    for path, raw_path_item in paths.items():
        path_item = _local_ref(document, raw_path_item)
        if not isinstance(path, str) or not path.startswith("/") or not isinstance(path_item, Mapping):
            raise OpenAPIContractError("OpenAPI path entries must be absolute path objects")
        path_variables = _template_variables(path)
        path_parameters = path_item.get("parameters", [])
        if not isinstance(path_parameters, list):
            raise OpenAPIContractError(f"path parameters at {path} must be an array")
        unsupported = set(path_item) - {"get", "post", "parameters"}
        if unsupported:
            raise OpenAPIContractError(f"unsupported OpenAPI path constructs at {path}: {sorted(unsupported)}")
        for method, raw_operation in path_item.items():
            if method == "parameters":
                continue
            operation = _local_ref(document, raw_operation)
            if not isinstance(operation, Mapping):
                raise OpenAPIContractError(f"OpenAPI operation {method} {path} must be an object")
            allowed = {"description", "operationId", "parameters", "requestBody", "responses", "summary", "tags"}
            unknown = set(operation) - allowed
            if unknown:
                raise OpenAPIContractError(
                    f"unsupported OpenAPI constructs in operation at {method} {path}: {sorted(unknown)}"
                )
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id or operation_id in identifiers:
                raise OpenAPIContractError("OpenAPI operationId values must be non-empty and unique")
            identifiers.add(operation_id)
            operation_parameters = operation.get("parameters", [])
            if not isinstance(operation_parameters, list):
                raise OpenAPIContractError(f"parameters for {operation_id} must be an array")
            parameters = [*path_parameters, *operation_parameters]
            if not all(isinstance(parameter, Mapping) for parameter in parameters):
                raise OpenAPIContractError(f"parameters for {operation_id} must be an array")
            parameter_contracts: list[OpenAPIParameter] = []
            for raw_parameter in parameters:
                parameter = _local_ref(document, raw_parameter)
                if not isinstance(parameter, Mapping):
                    raise OpenAPIContractError(f"invalid parameter in {operation_id}")
                parameter_contracts.append(_parameter_contract(parameter, operation_id))
            parameter_keys = {
                (parameter.location, parameter.name.lower() if parameter.location == "header" else parameter.name)
                for parameter in parameter_contracts
            }
            if len(parameter_keys) != len(parameter_contracts):
                raise OpenAPIContractError(f"duplicate parameter in {operation_id}")
            path_parameter_names = {parameter.name for parameter in parameter_contracts if parameter.location == "path"}
            if path_parameter_names != set(path_variables):
                raise OpenAPIContractError(f"path parameters must exactly match template variables in {operation_id}")
            request_schema: str | None = None
            if "requestBody" in operation:
                request_body = _local_ref(document, operation["requestBody"])
                if not isinstance(request_body, Mapping) or request_body.get("required") is not True:
                    raise OpenAPIContractError(f"request body for {operation_id} must be required")
                contracts = _body_contracts(document, request_body)
                if len(contracts) != 1 or contracts[0][0].lower() != "application/json":
                    raise OpenAPIContractError(f"request body for {operation_id} must declare application/json once")
                request_schema = contracts[0][1]
            raw_responses = operation.get("responses")
            if not isinstance(raw_responses, Mapping) or not raw_responses:
                raise OpenAPIContractError(f"responses for {operation_id} must be declared")
            responses: dict[int, tuple[tuple[str | None, str | None], ...]] = {}
            for status_text, raw_response in raw_responses.items():
                if not isinstance(status_text, str) or not status_text.isdigit():
                    raise OpenAPIContractError(f"only exact numeric response codes are supported in {operation_id}")
                response = _local_ref(document, raw_response)
                if not isinstance(response, Mapping):
                    raise OpenAPIContractError(f"response {status_text} for {operation_id} must be an object")
                unknown_response = set(response) - {"description", "headers", "content"}
                if unknown_response:
                    raise OpenAPIContractError(
                        f"unsupported response constructs in {operation_id}: {sorted(unknown_response)}"
                    )
                responses[int(status_text)] = (
                    _body_contracts(document, response) if "content" in response else ((None, None),)
                )
            success_statuses = [status for status in responses if 200 <= status < 300]
            success_status = success_statuses[0] if len(success_statuses) == 1 else None
            operations.append(
                OpenAPIOperation(
                    method,
                    path,
                    operation_id,
                    tuple(parameter_contracts),
                    request_schema,
                    MappingProxyType(responses),
                    success_status,
                )
            )
    return tuple(operations)


def _normal_request(operation: OpenAPIOperation, request: Request, body: Any = None) -> OpenAPIRequest:
    """Copy Starlette request values into the immutable public request value."""
    return OpenAPIRequest(
        operation,
        MappingProxyType({name: str(value) for name, value in request.path_params.items()}),
        MappingProxyType(dict(request.query_params)),
        MappingProxyType({name.lower(): value for name, value in request.headers.items()}),
        body,
    )


def _validate_parameters(request: OpenAPIRequest) -> None:
    """Enforce required string parameters and their optional enum values."""
    for parameter in request.operation.parameters:
        values = {
            "path": request.path_params,
            "query": request.query,
            "header": request.headers,
        }[parameter.location]
        name = parameter.name.lower() if parameter.location == "header" else parameter.name
        value = values.get(name)
        if value is None:
            if parameter.required:
                raise OpenAPIRequestError(
                    request.operation, f"required {parameter.location} parameter missing: {parameter.name}", request
                )
            continue
        if parameter.enum is not None and value not in parameter.enum:
            raise OpenAPIRequestError(
                request.operation, f"invalid {parameter.location} parameter: {parameter.name}", request
            )


async def _read_request_body(operation: OpenAPIOperation, request: Request) -> OpenAPIRequest:
    """Read required JSON and report strict parsing failures as request errors."""
    normalized = _normal_request(operation, request)
    if operation.request_schema is None:
        return normalized
    content_type = normalized.header("content-type")
    if content_type is None or content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise OpenAPIRequestError(operation, "Content-Type must be application/json", normalized)
    try:
        body = json.loads(
            (await request.body()).decode("utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {value}")),
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise OpenAPIRequestError(operation, "request body must be valid UTF-8 JSON", normalized) from error
    return _normal_request(operation, request, body)


async def _call(handler: ExceptionHandler, *args: Any) -> OpenAPIResponse:
    """Await an exception adapter only when it returned an awaitable result."""
    result = handler(*args)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, OpenAPIResponse):
        raise TypeError("OpenAPI handlers must return OpenAPIResponse")
    return result


def _response(operation: OpenAPIOperation, value: OpenAPIResponse, schemas: OpenAPISchemaRegistry) -> Response:
    """Validate and serialize one operation response."""
    status = operation.success_status if value.status is None else value.status
    if status is None:
        raise OpenAPIContractError(
            f"{operation.operation_id} does not declare exactly one 2xx status; "
            "the response must state a status explicitly"
        )
    contracts = operation.responses.get(status)
    if contracts is None:
        raise OpenAPIContractError(f"OpenAPI does not declare {status} for {operation.operation_id}")
    if value.body is None:
        if contracts != ((None, None),):
            raise OpenAPIContractError(f"response body is required for {operation.operation_id} {status}")
        if value.media_type is not None:
            raise OpenAPIContractError("bodyless response cannot declare a media type")
        return Response(status_code=status, headers=dict(value.headers), background=value.background)
    body_contracts = tuple(contract for contract in contracts if contract[0] is not None)
    media_type = value.media_type
    if media_type is None:
        if len(body_contracts) != 1:
            raise OpenAPIContractError(f"response media type is ambiguous for {operation.operation_id} {status}")
        media_type = body_contracts[0][0]
    assert media_type is not None
    contract = next((item for item in body_contracts if item[0] == media_type), None)
    if contract is None:
        raise OpenAPIContractError(f"undeclared response media type {media_type} for {operation.operation_id}")
    assert contract[1] is not None
    try:
        schemas.validate(contract[1], value.body)
    except OpenAPISchemaError as error:
        raise OpenAPIContractError(f"invalid response for {operation.operation_id}: {error}") from error
    if not _is_json_media_type(media_type):
        raise OpenAPIContractError(f"only JSON response media types are supported: {media_type}")
    return JSONResponse(
        value.body,
        status_code=status,
        media_type=media_type,
        headers=dict(value.headers),
        background=value.background,
    )


def _preflight_schemas(operations: tuple[OpenAPIOperation, ...], schemas: OpenAPISchemaRegistry) -> None:
    """Reject unavailable schemas and unsupported response media types before routing."""
    for operation in operations:
        identifiers: list[str] = []
        if operation.request_schema is not None:
            identifiers.append(operation.request_schema)
        for contracts in operation.responses.values():
            for media_type, identifier in contracts:
                if media_type is not None and not _is_json_media_type(media_type):
                    raise OpenAPIContractError(
                        f"only JSON response media types are supported: {media_type} in {operation.operation_id}"
                    )
                if identifier is not None:
                    identifiers.append(identifier)
        for identifier in identifiers:
            try:
                schemas.lookup(identifier)
            except OpenAPISchemaError as error:
                raise OpenAPIContractError(
                    f"schema referenced by {operation.operation_id} is not registered: {identifier}"
                ) from error


def _merge_context(response: OpenAPIResponse, context: "OperationContext") -> OpenAPIResponse:
    """Fold a request scope's response metadata into a successful handler response.

    The handler wins on every field it set: its ``media_type`` when not ``None``,
    its ``background`` when not ``None``, and its headers key-by-key. The scope
    supplies whatever the handler left unset. A handler that returned a raw value
    set none of these, so the scope becomes the sole source.

    :param response: The converted handler response.
    :param context: The request scope's populated per-request context.
    :return: The response with the scope's metadata folded in.
    """
    return OpenAPIResponse(
        status=response.status,
        body=response.body,
        media_type=response.media_type if response.media_type is not None else context.media_type,
        headers={**context.headers, **response.headers},
        background=response.background if response.background is not None else context.background,
    )


def create_openapi_app(
    contract: "OpenAPIContract | Mapping[str, Any]",
    operations: "Mapping[str, Callable[..., Any] | OperationBinding]",
    *,
    implementation: object | None = None,
    schemas: OpenAPISchemaRegistry | None = None,
    request_error_handler: RequestErrorHandler,
    exception_handlers: Mapping[type[Exception], ExceptionHandler] | None = None,
    request_scope: "RequestScope | None" = None,
    scope_names: Sequence[str] = (),
    lifespan: Callable[[Starlette], Any] | None = None,
    debug: bool = False,
    path_converters: Mapping[str, str] | None = None,
) -> Starlette:
    """Create a Starlette app from a constrained OpenAPI 3.1 contract.

    ``contract`` accepts either an :class:`~httk.serve.http.openapi.OpenAPIContract`,
    which already bundles its offline schema registry, or a plain OpenAPI document
    mapping paired with a separate ``schemas`` registry.

    Each ``operations`` entry is a bare handler callable or an
    :class:`~httk.serve.http.openapi.OperationBinding`. The framework binds the
    operation's declared path, query, and header parameters, the validated request
    body, and any whole-request injection to the handler's parameters by name; see
    :func:`~httk.serve.http.openapi.operation`.

    :param contract: Parsed contract, or a caller-owned OpenAPI path document.
    :param operations: Operation-id-to-handler mapping.
    :param implementation: Object whose methods resolve class-defined function
        entries; ``None`` uses each entry callable directly.
    :param schemas: Offline JSON Schema registry for external body references.
        Required and used when ``contract`` is a plain mapping; must not be
        supplied when ``contract`` is an
        :class:`~httk.serve.http.openapi.OpenAPIContract`.
    :param request_error_handler: Converts request parsing or schema errors to a response.
    :param exception_handlers: Exact protocol exception classes converted to responses.
    :param request_scope: Optional per-request async context manager entered around
        each handler call. It populates the :class:`~httk.serve.http.openapi.OperationContext`
        extras before the handler runs and may set response metadata after it
        returns; that metadata is folded into the response on normal completion
        only, never onto an error or adapted-exception response.
    :param scope_names: Names of the request-scope values the scope may supply,
        against which each operation's declared extras are validated.
    :param lifespan: Optional Starlette lifespan callable.
    :param debug: Whether Starlette debug responses are enabled.
    :param path_converters: OpenAPI path parameter to Starlette converter mapping.
    :return: Mountable Starlette application.
    :raises OpenAPIContractError: If the operations or the contract are incomplete
        or unsupported, if a handler cannot satisfy an operation's declared inputs
        by name, or if ``contract`` and ``schemas`` disagree about which schema
        registry to use.
    """
    from .binding import bind_operation  # local: see the TYPE_CHECKING import above
    from .contract import OpenAPIContract  # local: see the TYPE_CHECKING import above

    if isinstance(contract, OpenAPIContract):
        if schemas is not None:
            raise OpenAPIContractError("schemas must not be supplied together with an OpenAPIContract")
        parsed_operations = contract.operations
        schemas = contract.schemas
    else:
        if schemas is None:
            raise OpenAPIContractError("schemas is required when contract is a plain OpenAPI mapping")
        parsed_operations = parse_openapi_operations(contract)
    _preflight_schemas(parsed_operations, schemas)
    expected = {operation.operation_id for operation in parsed_operations}
    missing, unknown = expected - set(operations), set(operations) - expected
    if missing or unknown:
        detail = []
        if missing:
            detail.append(f"missing operations: {sorted(missing)}")
        if unknown:
            detail.append(f"unknown operations: {sorted(unknown)}")
        raise OpenAPIContractError("; ".join(detail))
    bound_operations = {
        operation.operation_id: bind_operation(
            operation, operations[operation.operation_id], implementation=implementation, scope_names=scope_names
        )
        for operation in parsed_operations
    }
    converters = path_converters or {}
    adapted_exceptions = exception_handlers or {}
    routes: list[Route] = []
    for operation in parsed_operations:
        route_path = operation.path
        for name, converter in converters.items():
            route_path = route_path.replace("{" + name + "}", "{" + name + ":" + converter + "}")
        invoke = bound_operations[operation.operation_id]

        async def endpoint(
            request: Request, operation: OpenAPIOperation = operation, invoke: "BoundOperation" = invoke
        ) -> Response:
            normalized = _normal_request(operation, request)
            try:
                _validate_parameters(normalized)
                normalized = await _read_request_body(operation, request)
                if operation.request_schema is not None:
                    try:
                        schemas.validate(operation.request_schema, normalized.body)
                    except OpenAPISchemaError as error:
                        raise OpenAPIRequestError(operation, str(error), normalized) from error
                if request_scope is None:
                    result = await invoke(normalized)
                else:
                    async with request_scope(normalized) as context:
                        result = await invoke(normalized, context.extras)
                    result = _merge_context(result, context)
            except OpenAPIRequestError as error:
                result = request_error_handler(error)
                if not isinstance(result, OpenAPIResponse):
                    raise TypeError("request_error_handler must return OpenAPIResponse")
            except tuple(adapted_exceptions) as error:
                adapter = next(
                    (handler for kind, handler in adapted_exceptions.items() if isinstance(error, kind)), None
                )
                assert adapter is not None
                result = await _call(adapter, error, normalized)
            return _response(operation, result, schemas)

        endpoint.__name__ = operation.operation_id
        routes.append(Route(route_path, endpoint, methods=[operation.method.upper()]))
    return Starlette(debug=debug, routes=routes, lifespan=lifespan)


__all__ = [
    "ExceptionHandler",
    "OpenAPIContractError",
    "OpenAPIOperation",
    "OpenAPIParameter",
    "OpenAPIRequest",
    "OpenAPIRequestError",
    "OpenAPIResponse",
    "RequestErrorHandler",
    "RequestScope",
    "create_openapi_app",
    "parse_openapi_operations",
]
