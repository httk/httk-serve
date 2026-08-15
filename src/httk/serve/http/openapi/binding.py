"""Bind declared OpenAPI operation inputs to plain handler parameters by name."""

import inspect
import re
import types
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, final, get_type_hints

from starlette.background import BackgroundTask

from .app import OpenAPIContractError, OpenAPIOperation, OpenAPIRequest, OpenAPIResponse

_ACRONYM_WORD = re.compile(r"(.)([A-Z][a-z]+)")
_LOWER_UPPER = re.compile(r"([a-z0-9])([A-Z])")
_REPEATED_UNDERSCORE = re.compile(r"_+")


def normalize_parameter_name(name: str) -> str:
    """Normalize an OpenAPI wire parameter name to a Python identifier form.

    Hyphens become underscores, ``camelCase`` and ``ACRONYMCase`` boundaries are
    split, the result is lowercased, repeated underscores collapse, and leading
    and trailing underscores are stripped. The returned string is not guaranteed
    to be a valid identifier; a wire name that does not normalize to one (for
    example ``filter[name]``) is not auto-bindable and requires an explicit alias.

    :param name: OpenAPI wire parameter name.
    :return: Normalized handler-parameter name candidate.
    """
    text = name.replace("-", "_")
    text = _ACRONYM_WORD.sub(r"\1_\2", text)
    text = _LOWER_UPPER.sub(r"\1_\2", text)
    text = _REPEATED_UNDERSCORE.sub("_", text.lower())
    return text.strip("_")


@final
@dataclass(frozen=True, slots=True)
class OperationBinding:
    """Declare how one operation's declared inputs bind to a handler callable.

    :param target: Callable implementing the operation. A bound method or
        module-level function is used directly; a plain function defined on a
        class is resolved against ``implementation`` when the application is
        created.
    :param aliases: Wire parameter name to handler parameter name overrides. The
        reserved wire name ``body`` remaps the request body.
    :param extras: Names of request-scope values this operation consumes.
    """

    target: Callable[..., Any]
    aliases: Mapping[str, str] = field(default_factory=dict)
    extras: tuple[str, ...] = ()


def operation(
    target: Callable[..., Any],
    *,
    aliases: Mapping[str, str] | None = None,
    extras: Sequence[str] = (),
) -> OperationBinding:
    """Declare an operation binding with optional aliases and request-scope extras.

    :param target: Callable implementing the operation.
    :param aliases: Wire parameter name to handler parameter name overrides.
    :param extras: Names of request-scope values this operation consumes.
    :return: The declared operation binding.
    """
    return OperationBinding(target, dict(aliases or {}), tuple(extras))


@dataclass
class OperationContext:
    """Carry per-request values between a request scope and one operation handler.

    The scope populates :attr:`extras` before the handler runs; the framework
    passes each extra the operation declares to the handler by name. After the
    handler returns, the scope may set :attr:`media_type`, :attr:`headers`, and
    :attr:`background`, which the framework folds into the response on normal
    completion only. The context is mutable by design so the scope can both
    supply inputs and collect response metadata.

    :param extras: Request-scope values keyed by the extra name each declares.
    :param media_type: Exact response media type the scope contributes, if any.
    :param headers: Additional response headers the scope contributes.
    :param background: Starlette background task the scope contributes, if any.
    """

    extras: dict[str, Any]
    media_type: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    background: BackgroundTask | None = None


@final
@dataclass(frozen=True, slots=True)
class BoundParameter:
    """Describe where one handler parameter's value is resolved from per request.

    :param param: Handler parameter name that receives the value.
    :param kind: Source kind: ``path``, ``query``, ``header``, ``body``,
        ``request``, or ``extra``.
    :param key: Lookup key within the source; the wire parameter name (lowercased
        for headers) for parameter sources, the scope name for ``extra``, and
        unused for ``body`` and ``request``.
    """

    param: str
    kind: str
    key: str


def _resolve_source(source: BoundParameter, request: OpenAPIRequest, scope: Mapping[str, Any]) -> tuple[bool, Any]:
    """Resolve one handler parameter value, reporting whether it is present."""
    if source.kind == "request":
        return True, request
    if source.kind == "body":
        return True, request.body
    if source.kind == "extra":
        return source.key in scope, scope.get(source.key)
    values = {"path": request.path_params, "query": request.query, "header": request.headers}[source.kind]
    value = values.get(source.key)
    return value is not None, value


@final
class BoundOperation:
    """A validated binding of one operation to its resolved handler callable.

    :param operation: The operation this binding serves.
    :param target: Resolved handler callable to invoke.
    :param sources: Per-request sources for the handler's bound parameters.
    """

    __slots__ = ("_operation", "_sources", "_target")

    def __init__(
        self, operation: OpenAPIOperation, target: Callable[..., Any], sources: Sequence[BoundParameter]
    ) -> None:
        self._operation = operation
        self._target = target
        self._sources = tuple(sources)

    @property
    def operation_id(self) -> str:
        """Return the bound operation's identifier.

        :return: The operation id.
        """
        return self._operation.operation_id

    async def __call__(self, request: OpenAPIRequest, scope: Mapping[str, Any] | None = None) -> OpenAPIResponse:
        """Bind request values by name, call the handler, and convert the result.

        :param request: Normalized request whose declared values are bound.
        :param scope: Request-scope values keyed by extra name.
        :return: The handler's converted response.
        :raises TypeError: If the handler returns an unsupported result type.
        """
        resolved: dict[str, Any] = {}
        for source in self._sources:
            present, value = _resolve_source(source, request, scope or {})
            if present:
                resolved[source.param] = value
        return await convert_result(self._operation.operation_id, self._target(**resolved))


async def convert_result(operation_id: str, result: Any) -> OpenAPIResponse:
    """Convert a handler return value into a constrained operation response.

    An awaitable is awaited first, so both synchronous and asynchronous handlers
    are supported. ``None`` becomes the bodyless success response, a mapping or
    list becomes a response body, and an
    :class:`~httk.serve.http.openapi.OpenAPIResponse` is used as is.

    :param operation_id: Operation identifier used in error messages.
    :param result: Raw handler return value or awaitable of one.
    :return: The constrained operation response.
    :raises TypeError: If the result is not a supported response value.
    """
    if inspect.isawaitable(result):
        result = await result
    if result is None:
        return OpenAPIResponse()
    if isinstance(result, OpenAPIResponse):
        return result
    if isinstance(result, (Mapping, list)):
        return OpenAPIResponse(body=result)
    raise TypeError(f"operation {operation_id!r} returned unsupported result type {type(result).__name__}")


def _resolve_target(operation_id: str, target: Callable[..., Any], implementation: object | None) -> Callable[..., Any]:
    """Resolve one operation entry to the callable that implements it."""
    if implementation is None:
        if not callable(target):
            raise OpenAPIContractError(f"operation {operation_id!r} target is not callable")
        return target
    if isinstance(target, (staticmethod, classmethod)):
        raise OpenAPIContractError(
            f"operation {operation_id!r} cannot resolve a staticmethod/classmethod against an implementation"
        )
    if inspect.ismethod(target):
        return target
    if not isinstance(target, types.FunctionType):
        raise OpenAPIContractError(
            f"operation {operation_id!r} entries resolved against an implementation must be plain functions, "
            f"got {type(target).__name__}"
        )
    try:
        resolved = getattr(implementation, target.__name__)
    except AttributeError as error:
        raise OpenAPIContractError(
            f"operation {operation_id!r} implementation has no attribute {target.__name__!r}"
        ) from error
    if not callable(resolved):
        raise OpenAPIContractError(
            f"operation {operation_id!r} implementation attribute {target.__name__!r} is not callable"
        )
    return resolved


def _handler_parameters(
    operation_id: str, target: Callable[..., Any]
) -> tuple[dict[str, inspect.Parameter], str | None]:
    """Collect name-bindable handler parameters and the request-injection parameter."""
    try:
        signature = inspect.signature(target)
    except (TypeError, ValueError) as error:
        raise OpenAPIContractError(f"operation {operation_id!r} handler has no introspectable signature") from error
    function = getattr(target, "__func__", target)
    try:
        hints = get_type_hints(function)
    except Exception:
        hints = {}
    parameters: dict[str, inspect.Parameter] = {}
    request_param: str | None = None
    for name, parameter in signature.parameters.items():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            raise OpenAPIContractError(f"operation {operation_id!r} handler *args cannot be bound by name")
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            raise OpenAPIContractError(f"operation {operation_id!r} handler **kwargs cannot be bound by name")
        if parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            raise OpenAPIContractError(
                f"operation {operation_id!r} positional-only parameter {name!r} cannot be bound by name"
            )
        annotation = hints.get(name, parameter.annotation)
        if annotation is OpenAPIRequest:
            if request_param is not None:
                raise OpenAPIContractError(
                    f"operation {operation_id!r} declares more than one OpenAPIRequest parameter"
                )
            request_param = name
            continue
        parameters[name] = parameter
    return parameters, request_param


def bind_operation(
    operation: OpenAPIOperation,
    entry: Callable[..., Any] | OperationBinding,
    *,
    implementation: object | None = None,
    scope_names: Collection[str] = (),
) -> BoundOperation:
    """Validate and bind one operation's declared inputs to its handler by name.

    :param operation: The declared operation to bind.
    :param entry: A bare handler callable, or an
        :class:`~httk.serve.http.openapi.OperationBinding`.
    :param implementation: Object whose methods resolve class-defined function
        entries; ``None`` uses each entry callable directly.
    :param scope_names: Request-scope value names available to ``extras``.
    :return: The validated per-request binding.
    :raises OpenAPIContractError: If the handler cannot satisfy the operation's
        declared inputs by name.
    """
    if isinstance(entry, OperationBinding):
        raw_target, aliases, extras = entry.target, dict(entry.aliases), tuple(entry.extras)
    else:
        raw_target, aliases, extras = entry, {}, ()
    operation_id = operation.operation_id
    target = _resolve_target(operation_id, raw_target, implementation)
    handler_params, request_param = _handler_parameters(operation_id, target)

    reserved = {parameter.name for parameter in operation.parameters}
    if operation.request_schema is not None:
        reserved.add("body")
    for wire in aliases:
        if wire not in reserved:
            raise OpenAPIContractError(f"operation {operation_id!r} alias names undeclared parameter {wire!r}")

    sources: list[BoundParameter] = []
    targets: dict[str, str] = {}
    if request_param is not None:
        targets[request_param] = "the request"
        sources.append(BoundParameter(request_param, "request", ""))

    for parameter in operation.parameters:
        if parameter.name in aliases:
            handler_name = aliases[parameter.name]
        else:
            handler_name = normalize_parameter_name(parameter.name)
            if not handler_name.isidentifier():
                if parameter.required:
                    raise OpenAPIContractError(
                        f"operation {operation_id!r} parameter {parameter.name!r} does not normalize to a valid "
                        "identifier; provide an alias"
                    )
                continue
        if handler_name in targets:
            raise OpenAPIContractError(
                f"operation {operation_id!r} parameters {targets[handler_name]!r} and {parameter.name!r} both bind "
                f"to handler parameter {handler_name!r}; add an alias"
            )
        targets[handler_name] = parameter.name
        if handler_name in handler_params:
            key = parameter.name.lower() if parameter.location == "header" else parameter.name
            sources.append(BoundParameter(handler_name, parameter.location, key))
        elif parameter.required:
            raise OpenAPIContractError(
                f"operation {operation_id!r} required {parameter.location} parameter {parameter.name!r} "
                f"has no handler parameter {handler_name!r}"
            )

    if operation.request_schema is not None:
        body_name = aliases.get("body", "body")
        if body_name in targets:
            raise OpenAPIContractError(
                f"operation {operation_id!r} parameter {targets[body_name]!r} collides with the request body "
                f"on handler parameter {body_name!r}"
            )
        if body_name not in handler_params:
            raise OpenAPIContractError(
                f"operation {operation_id!r} declares a request body but the handler does not accept {body_name!r}"
            )
        targets[body_name] = "the request body"
        sources.append(BoundParameter(body_name, "body", ""))

    for name in extras:
        if name not in handler_params:
            raise OpenAPIContractError(f"operation {operation_id!r} extra {name!r} is not a handler parameter")
        if name not in scope_names:
            raise OpenAPIContractError(
                f"operation {operation_id!r} extra {name!r} is not an available request-scope value"
            )
        if name in targets:
            raise OpenAPIContractError(
                f"operation {operation_id!r} extra {name!r} collides with binding source {targets[name]!r}"
            )
        targets[name] = f"extra {name!r}"
        sources.append(BoundParameter(name, "extra", name))

    for name, handler_parameter in handler_params.items():
        if name in targets:
            continue
        if handler_parameter.default is inspect.Parameter.empty:
            raise OpenAPIContractError(
                f"operation {operation_id!r} handler parameter {name!r} is not satisfied by any operation input"
            )
    return BoundOperation(operation, target, sources)


__all__ = [
    "BoundOperation",
    "BoundParameter",
    "OperationBinding",
    "OperationContext",
    "bind_operation",
    "convert_result",
    "normalize_parameter_name",
    "operation",
]
