"""Neutral synchronous query/result protocols over a remote OPTIMADE service.

Attribute access on a bound query variable (``variable.some_field``) resolves
against the endpoint's declared field map, including OPTIMADE provider-prefixed
properties such as ``variable._anyterial_max_spin_splitting`` -- on a generic,
unregistered entry type the local field names are the wire names verbatim, and
provider prefixes are the norm there.  A double-leading-underscore name (a
dunder, e.g. ``__deepcopy__``) is always rejected with a bare
``AttributeError`` before the field map is even consulted, so interpreter and
library introspection stay cheap and can never collide with a field name.  A
single-leading-underscore name that is *not* a declared field also raises a
bare ``AttributeError`` rather than the descriptive
:class:`~httk.store.UnsupportedQueryError` used for other unknown names: this
keeps probes shaped like a provider field but not one, such as IPython's
``_ipython_canary_method_should_not_exist_``, indistinguishable from "no such
attribute" instead of surfacing as a backend failure.
"""

import datetime
import json
import math
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from types import MappingProxyType
from typing import TYPE_CHECKING, cast
from urllib.parse import quote, urlencode, urljoin, urlsplit

from httk.core import load_entry_type_definition
from httk.core.optimade import (
    OptimadeDocument,
    OptimadeResource,
    optimade_document_root,
    redact_optimade_url,
)
from httk.store import CountUnavailableError as NeutralCountUnavailableError
from httk.store import (
    MultipleResultsError,
    NoResultError,
    PortableQueryCapabilities,
    ResultRow,
    SearchResult,
    UnsupportedQueryError,
    portable_query_capabilities,
    portable_query_fields,
)

from .client import ALL_ADVERTISED, OptimadeClientError, OptimadeStore, RemoteEntryType

if TYPE_CHECKING:
    from httk.store.query.slicer import Slicer

__all__ = [
    "CountUnavailableError",
    "OptimadePaginationError",
    "OptimadeResponseError",
    "RemoteResultColumn",
    "RemoteResultSet",
    "RemoteSearcher",
]

_CORE_ID = "https://schemas.optimade.org/defs/v1.2/properties/core/id"
_CORE_TYPE = "https://schemas.optimade.org/defs/v1.2/properties/core/type"


class OptimadeResponseError(OptimadeClientError):
    """A successful HTTP response was not a usable OPTIMADE entry document."""


class OptimadePaginationError(OptimadeResponseError):
    """A remote continuation was unsafe, malformed, or non-terminating."""


class CountUnavailableError(OptimadeResponseError, NeutralCountUnavailableError):
    """The service omitted a valid filtered ``meta.data_returned`` count."""


@dataclass(slots=True)
class _CountCache:
    value: int | None = None


def _unsupported(detail: str) -> UnsupportedQueryError:
    return UnsupportedQueryError(f"remote OPTIMADE query does not support {detail}")


def _scalar_value(value: object, generic: bool, field: "_RemoteField") -> object:
    """Extract one scalar output value from one materialized query result.

    A bound (non-generic) backend materializes a typed object with real
    attributes matching *field*'s local name, so a plain ``getattr`` is
    exact and unchanged here.

    A generic (unregistered) backend has no typed object at all: *value* is
    the raw :class:`~httk.core.optimade.OptimadeResource` itself, a
    source-exact lazy ``Mapping`` whose only real attributes are ``id``,
    ``type``, ``document``, ``data_index``, ``schema``, and ``unwrap()``.
    ``id``/``type`` are protocol envelope members and are read straight off
    the resource; every other field is read from ``unwrap()["attributes"]``
    by the field's *wire* name (``field._remote_name``) rather than its
    local name -- for a generic descriptor the two happen to be identical
    today, but the wire name is what the response envelope actually keys on,
    so a future bound-name divergence cannot silently misroute this lookup.

    An attribute absent from ``attributes`` yields ``None``, never a
    ``KeyError``: an OPTIMADE server may legitimately omit a property whose
    value is unknown (httk-serve itself does this), so "absent" and
    "present with an explicit null" are deliberately not distinguished here.

    :param value: Materialized query result object.
    :param generic: Whether the owning descriptor's backend is ``OptimadeResource``.
    :param field: Field being projected.
    :return: Extracted scalar value, or ``None`` for an absent generic attribute.
    """

    if not generic:
        return getattr(value, field._local_name)
    resource = cast(OptimadeResource, value)
    if field._local_name in ("id", "type"):
        return getattr(resource, field._local_name)
    attributes = resource.unwrap().get("attributes")
    if not isinstance(attributes, Mapping):
        return None
    return attributes.get(field._remote_name)


def _origin(url: str) -> tuple[str, str, int | None]:
    split = urlsplit(url)
    scheme = split.scheme.casefold()
    port = split.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, (split.hostname or "").casefold(), port


def _safe_source(url: str) -> str:
    return redact_optimade_url(url)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise _unsupported("non-finite numeric literals")
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    if rendered in ("", "-0"):
        return "0"
    return rendered


def _fraction_text(value: Fraction) -> str:
    denominator = value.denominator
    twos = 0
    fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise _unsupported(
            f"the non-terminating Fraction literal {value!s}; OPTIMADE numbers cannot represent it exactly"
        )
    scale = max(twos, fives)
    scaled = value.numerator * (2 ** (scale - twos)) * (5 ** (scale - fives))
    sign = "-" if scaled < 0 else ""
    digits = str(abs(scaled)).rjust(scale + 1, "0")
    if scale == 0:
        return sign + digits
    return sign + digits[:-scale] + "." + digits[-scale:]


def _literal(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, Fraction):
        return _fraction_text(value)
    if isinstance(value, datetime.datetime):
        if value.utcoffset() is None:
            raise _unsupported("naive timestamp literals")
        return json.dumps(value.isoformat(), ensure_ascii=False)
    if value is None:
        raise _unsupported("NULL outside equality or inequality comparisons")
    if isinstance(value, float):
        # A float literal renders as repr()'s shortest round-tripping decimal --
        # the text the caller typed -- so nothing binary leaks onto the wire.
        # Exactness beyond float precision is stated with Decimal instead.
        if not math.isfinite(value):
            raise _unsupported("non-finite numeric literals")
        return _decimal_text(Decimal(repr(value)))
    raise _unsupported(f"literal values of type {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class _RemoteExpression:
    searcher: "RemoteSearcher"
    text: str

    def _other(self, other: object) -> "_RemoteExpression":
        if not isinstance(other, _RemoteExpression) or other.searcher is not self.searcher:
            raise _unsupported("combining expressions from different searchers")
        return other

    def __and__(self, other: object) -> "_RemoteExpression":
        right = self._other(other)
        return _RemoteExpression(self.searcher, f"({self.text}) AND ({right.text})")

    def __or__(self, other: object) -> "_RemoteExpression":
        right = self._other(other)
        return _RemoteExpression(self.searcher, f"({self.text}) OR ({right.text})")

    def __invert__(self) -> "_RemoteExpression":
        return _RemoteExpression(self.searcher, f"NOT ({self.text})")


class _RemoteField:
    __slots__ = (
        "_capabilities",
        "_definition_id",
        "_item_kind",
        "_kind",
        "_local_name",
        "_remote_name",
        "_searcher",
    )

    def __init__(
        self,
        searcher: "RemoteSearcher",
        local_name: str,
        definition_id: str,
        remote_name: str,
        kind: str,
        item_kind: str | None,
        capabilities: PortableQueryCapabilities | None,
    ) -> None:
        self._searcher = searcher
        self._local_name = local_name
        self._definition_id = definition_id
        self._remote_name = remote_name
        self._kind = kind.casefold()
        self._item_kind = None if item_kind is None else item_kind.casefold()
        self._capabilities = capabilities

    def __getattr__(self, name: str) -> object:
        # Unlike _RemoteVariable, a bound field has no map of its own fields
        # to resolve against: nested/relationship traversal is wholly
        # unimplemented, so every name here is a miss regardless of an
        # underscore prefix. Single-leading-underscore names (provider-prefix
        # shaped or not, e.g. IPython's canary probe) therefore still get a
        # plain AttributeError rather than the descriptive error below -- the
        # same non-field-shaped-name safety net as _RemoteVariable, at zero
        # functional cost since there is nothing they could ever resolve to.
        if name.startswith("_"):
            raise AttributeError(name)
        raise _unsupported("field traversal or relationship queries")

    def _comparison(self, operator: str, value: object) -> _RemoteExpression:
        if isinstance(value, _RemoteField):
            raise _unsupported("field-to-field comparisons")
        if self._kind == "list":
            raise _unsupported(f"scalar comparisons on list field {self._local_name!r}")
        if self._kind == "boolean" and operator not in ("=", "!="):
            raise _unsupported(f"ordered comparisons on boolean field {self._local_name!r}")
        self._require("equality" if operator in ("=", "!=") else "ordering")
        if value is None:
            if operator == "=":
                return _RemoteExpression(self._searcher, f"{self._remote_name} IS UNKNOWN")
            if operator == "!=":
                return _RemoteExpression(self._searcher, f"{self._remote_name} IS KNOWN")
            raise _unsupported("ordered comparisons with NULL")
        self._validate(value, self._kind)
        return _RemoteExpression(self._searcher, f"{self._remote_name} {operator} {_literal(value)}")

    def _validate(self, value: object, kind: str | None) -> None:
        if kind in (None, "unknown"):
            return
        valid = (
            (kind == "string" and isinstance(value, str))
            or (kind == "timestamp" and isinstance(value, str | datetime.datetime))
            or (kind == "boolean" and isinstance(value, bool))
            or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (kind == "float" and isinstance(value, int | float | Decimal | Fraction) and not isinstance(value, bool))
        )
        if not valid:
            raise _unsupported(f"{kind} field {self._local_name!r} with {type(value).__name__} literal")

    def __eq__(self, value: object) -> _RemoteExpression:  # type: ignore[override]
        return self._comparison("=", value)

    def __ne__(self, value: object) -> _RemoteExpression:  # type: ignore[override]
        return self._comparison("!=", value)

    def __lt__(self, value: object) -> _RemoteExpression:
        return self._comparison("<", value)

    def __le__(self, value: object) -> _RemoteExpression:
        return self._comparison("<=", value)

    def __gt__(self, value: object) -> _RemoteExpression:
        return self._comparison(">", value)

    def __ge__(self, value: object) -> _RemoteExpression:
        return self._comparison(">=", value)

    def _string_match(self, operator: str, value: object) -> _RemoteExpression:
        if self._kind != "string":
            raise _unsupported(f"string matching on {self._kind} field {self._local_name!r}")
        self._require("stringmatching")
        if not isinstance(value, str):
            raise _unsupported(f"{operator.lower()} with a non-string literal")
        return _RemoteExpression(self._searcher, f"{self._remote_name} {operator} {_literal(value)}")

    def contains(self, text: str) -> _RemoteExpression:
        return self._string_match("CONTAINS", text)

    def startswith(self, prefix: str) -> _RemoteExpression:
        return self._string_match("STARTS WITH", prefix)

    def endswith(self, suffix: str) -> _RemoteExpression:
        return self._string_match("ENDS WITH", suffix)

    def has(self, value: object) -> _RemoteExpression:
        if self._kind != "list":
            raise _unsupported(f"HAS on non-list field {self._local_name!r}")
        self._require("set")
        self._validate(value, self._item_kind)
        return _RemoteExpression(self._searcher, f"{self._remote_name} HAS {_literal(value)}")

    def has_any(self, *values: object) -> _RemoteExpression:
        if self._kind != "list":
            raise _unsupported(f"HAS ANY on non-list field {self._local_name!r}")
        self._require("set")
        if not values:
            return self._searcher._constant(False)
        for value in values:
            self._validate(value, self._item_kind)
        rendered = ", ".join(_literal(value) for value in values)
        return _RemoteExpression(self._searcher, f"{self._remote_name} HAS ANY {rendered}")

    def has_only(self, *values: object) -> _RemoteExpression:
        if self._kind != "list":
            raise _unsupported(f"HAS ONLY on non-list field {self._local_name!r}")
        self._require("set")
        if not values:
            raise _unsupported("HAS ONLY without values")
        for value in values:
            self._validate(value, self._item_kind)
        rendered = ", ".join(_literal(value) for value in values)
        return _RemoteExpression(self._searcher, f"{self._remote_name} HAS ONLY {rendered}")

    def is_in(self, *values: object) -> _RemoteExpression:
        if self._kind == "list":
            raise _unsupported(f"scalar is_in on list field {self._local_name!r}")
        if not values:
            return self._searcher._constant(False)
        comparisons = [self._comparison("=", value) for value in values]
        expression = comparisons[0]
        for comparison in comparisons[1:]:
            expression = expression | comparison
        return expression

    def _require(self, operation: str) -> None:
        if self._capabilities is None or self._capabilities.supports(operation):
            return
        support = self._capabilities.query_support or "unspecified"
        raise _unsupported(
            f"{operation} on field {self._local_name!r}; its definition declares query-support {support!r}"
        )


class _RemoteVariable:
    __slots__ = ("_fields", "_searcher")

    def __init__(self, searcher: "RemoteSearcher", fields: Mapping[str, _RemoteField]) -> None:
        self._searcher = searcher
        self._fields = fields

    def always_true(self) -> _RemoteExpression:
        return self._searcher._constant(True)

    def always_false(self) -> _RemoteExpression:
        return self._searcher._constant(False)

    def __getattr__(self, name: str) -> _RemoteField:
        if name.startswith("__"):
            # Dunder probes (``__deepcopy__``, ``__iter__``, ...) never name a
            # field, so reject them before the field map is even consulted.
            raise AttributeError(name)
        try:
            return self._fields[name]
        except KeyError:
            if name.startswith("_"):
                # A single leading underscore is the OPTIMADE provider-prefix
                # shape (``_<prefix>_<property>``) as well as the shape used
                # by non-field introspection probes such as IPython's
                # ``_ipython_canary_method_should_not_exist_``. Neither is a
                # declared field here, so both get a plain AttributeError
                # rather than the descriptive error below.
                raise AttributeError(name) from None
            descriptor = self._searcher._descriptor
            endpoint = descriptor.name if descriptor is not None else "(unbound)"
            raise _unsupported(f"field {name!r} for endpoint {endpoint!r}") from None


@dataclass(frozen=True, slots=True)
class _Output:
    name: str
    variable: _RemoteVariable | None
    field: _RemoteField | None


class RemoteSearcher:
    """Build one portable single-root OPTIMADE query.

    :param store: Remote OPTIMADE store used for discovery and requests.
    :param response_fields: Optional field-selection policy for this search.
    """

    def __init__(self, store: OptimadeStore, *, response_fields: object = None) -> None:
        self._store = store
        self._response_fields_setting = response_fields
        self._response_transport_fields: tuple[str, ...] | None = None
        self._descriptor: RemoteEntryType | None = None
        self._variable: _RemoteVariable | None = None
        self._fields: Mapping[str, _RemoteField] = MappingProxyType({})
        self._expressions: list[_RemoteExpression] = []
        self._sorts: list[tuple[_RemoteField, bool]] = []
        self._outputs: list[_Output] = []
        self._limit: int | None = None
        self.offset = 0
        self._count_cache = _CountCache()

    def _clone(self) -> "RemoteSearcher":
        clone = type(self)(self._store, response_fields=self._response_fields_setting)
        if self._descriptor is not None:
            clone._bind_descriptor(self._descriptor)
            clone._response_transport_fields = self._response_transport_fields
            clone._expressions = [_RemoteExpression(clone, expression.text) for expression in self._expressions]
            clone._sorts = [(clone._fields[field._local_name], descending) for field, descending in self._sorts]
            clone._outputs = [
                _Output(
                    output.name,
                    clone._variable if output.variable is not None else None,
                    clone._fields[cast(_RemoteField, output.field)._local_name] if output.field is not None else None,
                )
                for output in self._outputs
            ]
        clone._limit = self._limit
        clone.offset = self.offset
        clone._count_cache = self._count_cache
        return clone

    def _invalidate_count(self) -> None:
        self._count_cache = _CountCache()

    def _resolve_descriptor(self, target: object) -> RemoteEntryType:
        if isinstance(target, RemoteEntryType):
            if not any(target is item for item in self._store.entry_types):
                raise _unsupported("a RemoteEntryType from another store or stale discovery snapshot")
            return target
        if isinstance(target, type):
            matches = tuple(item for item in self._store.entry_types if item.backend is target)
            if len(matches) == 1:
                return matches[0]
            if not matches:
                raise _unsupported(f"the unrecognized backend class {target.__name__}")
            raise _unsupported(
                f"backend class {target.__name__} because it matches multiple endpoints; pass a RemoteEntryType"
            )
        raise _unsupported("query targets other than a RemoteEntryType or registered backend class")

    @staticmethod
    def _typed_maps(
        descriptor: RemoteEntryType,
    ) -> tuple[
        dict[str, str],
        dict[str, str],
        dict[str, tuple[str, str | None]],
        dict[str, PortableQueryCapabilities],
    ]:
        binding = descriptor.binding
        if binding is None:
            generic_fields = {name: name for name in descriptor.advertised_properties}
            generic_all_fields = dict(generic_fields)
            generic_kinds: dict[str, tuple[str, str | None]] = {
                **descriptor.property_types,
                "id": ("string", None),
                "type": ("string", None),
            }
            for generic_local_name, generic_definition_id in (("id", _CORE_ID), ("type", _CORE_TYPE)):
                # id/type are protocol envelope members even for old/generic
                # schemas that predate property definition IRIs.
                generic_remote_name = descriptor.property_names.get(generic_definition_id, generic_local_name)
                generic_fields[generic_local_name] = generic_remote_name
                generic_all_fields[generic_local_name] = generic_remote_name
            return generic_fields, generic_all_fields, generic_kinds, {}

        schema = load_entry_type_definition(binding.definition_id)
        definitions_by_iri = {definition.definition_id: name for name, definition in schema.properties.items()}
        typed_all_fields = {
            name: descriptor.property_names[definition.definition_id]
            for name, definition in schema.properties.items()
            if definition.definition_id in descriptor.property_names
        }
        if binding.query_fields is None:
            local_names = portable_query_fields(schema)
            query_iris = tuple(schema.properties[name].definition_id for name in local_names)
        else:
            query_iris = binding.query_fields
        typed_fields: dict[str, str] = {}
        typed_kinds: dict[str, tuple[str, str | None]] = {}
        typed_capabilities: dict[str, PortableQueryCapabilities] = {}
        for definition_id in query_iris:
            typed_local_name = definitions_by_iri.get(definition_id)
            typed_remote_name = descriptor.property_names.get(definition_id)
            if typed_local_name is not None and typed_remote_name is not None:
                typed_fields[typed_local_name] = typed_remote_name
                definition = schema.properties[typed_local_name]
                payload = definition.as_optimade()
                items = payload.get("items")
                item_kind = (
                    cast(str, items.get("x-optimade-type"))
                    if isinstance(items, Mapping) and isinstance(items.get("x-optimade-type"), str)
                    else None
                )
                typed_kinds[typed_local_name] = (definition.optimade_type, item_kind)
                typed_capabilities[typed_local_name] = portable_query_capabilities(definition)
        return typed_fields, typed_all_fields, typed_kinds, typed_capabilities

    def _select_response_fields(
        self,
        descriptor: RemoteEntryType,
        all_fields: Mapping[str, str],
    ) -> tuple[str, ...] | None:
        setting = self._response_fields_setting
        if setting is None:
            return None
        if setting is ALL_ADVERTISED:
            return descriptor.advertised_properties
        if isinstance(setting, str):
            raise TypeError("response_fields must be an iterable of field names, ALL_ADVERTISED, or None")
        if not isinstance(setting, Iterable):
            raise TypeError("response_fields must be an iterable of field names, ALL_ADVERTISED, or None")
        selected: list[str] = []
        seen: set[str] = set()
        for name in setting:
            if not isinstance(name, str):
                raise TypeError("response_fields names must be strings")
            remote_name = all_fields.get(name)
            if remote_name is None and descriptor.binding is None and name in descriptor.advertised_properties:
                # Generic descriptors have no local semantic vocabulary beyond
                # id/type; explicit response selection therefore uses their
                # exact advertised transport names.
                remote_name = name
            if remote_name is None:
                raise _unsupported(f"response field {name!r} for endpoint {descriptor.name!r}")
            if remote_name not in seen:
                seen.add(remote_name)
                selected.append(remote_name)
        return tuple(selected)

    def _bind_descriptor(self, descriptor: RemoteEntryType) -> _RemoteVariable:
        query_fields, all_fields, kinds, capabilities = self._typed_maps(descriptor)
        fields: dict[str, _RemoteField] = {}
        if descriptor.binding is None:
            definition_ids = {
                name: descriptor.property_iris.get(name, name)
                for name in ("id", "type", *descriptor.advertised_properties)
            }
            definition_ids["id"] = _CORE_ID
            definition_ids["type"] = _CORE_TYPE
        else:
            schema = load_entry_type_definition(descriptor.binding.definition_id)
            definition_ids = {name: definition.definition_id for name, definition in schema.properties.items()}
        for local_name, remote_name in query_fields.items():
            fields[local_name] = _RemoteField(
                self,
                local_name,
                definition_ids[local_name],
                remote_name,
                *kinds[local_name],
                capabilities.get(local_name),
            )
        self._descriptor = descriptor
        self._fields = MappingProxyType(fields)
        self._response_transport_fields = self._select_response_fields(descriptor, all_fields)
        self._variable = _RemoteVariable(self, self._fields)
        self._invalidate_count()
        return self._variable

    def variable(self, target: object) -> _RemoteVariable:
        """Bind the query to one discovered remote entry type.

        :param target: Discovered entry descriptor or registered backend class.
        :return: Query variable exposing portable fields.
        :raises httk.store.query.protocols.UnsupportedQueryError: If the target is not recognized or a root variable is already bound.
        """
        if self._variable is not None:
            raise _unsupported("a second root variable")
        return self._bind_descriptor(self._resolve_descriptor(target))

    def _require_variable(self) -> tuple[RemoteEntryType, _RemoteVariable]:
        if self._descriptor is None or self._variable is None:
            raise ValueError("this searcher has no query variable; call variable() first")
        return self._descriptor, self._variable

    def _constant(self, value: bool) -> _RemoteExpression:
        _descriptor, variable = self._require_variable()
        try:
            id_field = variable.id
        except UnsupportedQueryError as exc:
            raise _unsupported("constant expressions when semantic id is not advertised") from exc
        state = "KNOWN" if value else "UNKNOWN"
        return _RemoteExpression(self, f"{id_field._remote_name} IS {state}")

    def add(self, expression: object) -> None:
        """Add a filter expression to the query.

        :param expression: Expression created by this searcher.
        :raises ValueError: If no query variable is bound.
        :raises httk.store.UnsupportedQueryError: If the expression belongs elsewhere.
        """
        self._require_variable()
        if not isinstance(expression, _RemoteExpression) or expression.searcher is not self:
            raise _unsupported("expressions from another backend or searcher")
        self._expressions.append(expression)
        self._invalidate_count()

    def output(self, variable: object, name: str) -> None:
        """Declare a whole-record or scalar output.

        :param variable: Root variable or field to project.
        :param name: Output name.
        :raises ValueError: If the name is empty or duplicated.
        :raises httk.store.UnsupportedQueryError: If the output belongs elsewhere.
        """
        _descriptor, root = self._require_variable()
        if not isinstance(name, str) or not name:
            raise ValueError("output name must be a nonempty string")
        if any(output.name == name for output in self._outputs):
            raise ValueError(f"duplicate output name: {name!r}")
        if variable is root:
            output = _Output(name, root, None)
        elif isinstance(variable, _RemoteField) and variable._searcher is self:
            output = _Output(name, None, variable)
        else:
            raise _unsupported("outputs other than the root record or a portable scalar field")
        self._outputs.append(output)

    def _effective_response_fields(self) -> tuple[str, ...] | None:
        """Return the request field selection implied by outputs and user policy.

        A scalar projection has to be requested when the service's default is
        not known to include it.  For a scalar-only result that is harmless.
        For a whole-resource result, retain every field the endpoint declares
        default-response and add the scalar.  If the endpoint omits that
        implementation metadata, the conservative fallback is every
        advertised field rather than a thin scalar-only resource.
        """

        descriptor, _variable = self._require_variable()
        scalar_fields = tuple(
            dict.fromkeys(
                cast(_RemoteField, output.field)._remote_name for output in self._outputs if output.field is not None
            )
        )
        if not scalar_fields:
            return self._response_transport_fields
        if self._response_transport_fields is None:
            has_record = any(output.variable is not None for output in self._outputs)
            if has_record:
                # id and type are JSON:API resource members, independent of
                # response field policy.  The remaining default fields must be
                # declared by the endpoint itself.
                all_fields = self._typed_maps(descriptor)[1]
                protocol_fields = tuple(
                    remote_name
                    for local_name in ("id", "type")
                    if (remote_name := all_fields.get(local_name)) is not None
                )
                default_fields = set(descriptor.default_response_properties)
                default_fields.update(protocol_fields)
                if all(field in default_fields for field in scalar_fields):
                    # No explicit parameter is needed when every projection
                    # is already guaranteed by the selected default policy.
                    return None
                if descriptor.default_response_properties:
                    selected = list(dict.fromkeys((*descriptor.default_response_properties, *protocol_fields)))
                else:
                    selected = list(descriptor.advertised_properties)
                for field in scalar_fields:
                    if field not in selected:
                        selected.append(field)
                return tuple(selected)
            # A scalar-only result has no raw-resource completeness promise,
            # so ask for exactly the fields it projects.
            return scalar_fields
        selected = list(self._response_transport_fields)
        for field in scalar_fields:
            if field not in selected:
                selected.append(field)
        return tuple(selected)

    def add_sort(self, field: object, descending: bool = False) -> None:
        """Append a sortable field to the remote query.

        :param field: Field exposed by this searcher's variable.
        :param descending: Sort in descending order when true.
        :raises httk.store.UnsupportedQueryError: If the field is not portable or sortable.
        """
        descriptor, _variable = self._require_variable()
        if not isinstance(field, _RemoteField) or field._searcher is not self:
            raise _unsupported("sort keys from another backend or searcher")
        if not isinstance(descending, bool):
            raise TypeError("descending must be a bool")
        if field._remote_name not in descriptor.sortable_properties:
            raise _unsupported(f"sorting by non-sortable field {field._local_name!r}")
        self._sorts.append((field, descending))

    def set_limit(self, limit: int) -> None:
        """Set the query result limit.

        :param limit: Nonnegative limit, or a negative value for no bound.
        """
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise TypeError("limit must be an integer")
        self._limit = None if limit < 0 else limit

    def add_offset(self, offset: int) -> None:
        """Advance the query offset.

        :param offset: Nonnegative number of matching rows to skip.
        """
        if not isinstance(offset, int) or isinstance(offset, bool):
            raise TypeError("offset must be an integer")
        if offset < 0:
            raise ValueError("offset must be nonnegative")
        self.offset += offset

    def _filter_text(self) -> str | None:
        if not self._expressions:
            return None
        return " AND ".join(f"({expression.text})" for expression in self._expressions)

    def _request_url(
        self,
        *,
        page_limit: int,
        include_sort: bool = True,
        response_fields: tuple[str, ...] | None | object = ...,
    ) -> str:
        descriptor, _variable = self._require_variable()
        parameters: list[tuple[str, str]] = []
        filter_text = self._filter_text()
        if filter_text is not None:
            parameters.append(("filter", filter_text))
        fields = self._effective_response_fields() if response_fields is ... else response_fields
        if fields:
            parameters.append(("response_fields", ",".join(cast(tuple[str, ...], fields))))
        if include_sort and self._sorts:
            parameters.append(
                (
                    "sort",
                    ",".join(("-" if descending else "") + field._remote_name for field, descending in self._sorts),
                )
            )
        parameters.append(("page_limit", str(page_limit)))
        return self._store._transport_base_url + "/" + quote(descriptor.name, safe="") + "?" + urlencode(parameters)

    @staticmethod
    def _raw_root(text: str, source_url: str) -> Mapping[str, object]:
        try:
            root = json.loads(text, parse_float=Decimal, parse_int=int)
        except json.JSONDecodeError as exc:
            raise OptimadeResponseError(
                f"OPTIMADE response from {_safe_source(source_url)!r} is not valid JSON: {exc.msg}"
            ) from exc
        if not isinstance(root, dict):
            raise OptimadeResponseError(f"OPTIMADE response from {_safe_source(source_url)!r} root must be an object")
        errors = root.get("errors")
        if isinstance(errors, list) and errors:
            raise OptimadeResponseError(
                f"OPTIMADE response from {_safe_source(source_url)!r} contains an errors document"
            )
        return root

    @staticmethod
    def _next_link(root: Mapping[str, object], source_url: str) -> str | None:
        links = root.get("links")
        if links is None:
            return None
        if not isinstance(links, dict):
            raise OptimadePaginationError(f"OPTIMADE response from {_safe_source(source_url)!r} has non-object links")
        next_link = links.get("next")
        if next_link is None:
            return None
        if isinstance(next_link, str) and next_link and next_link == next_link.strip():
            return next_link
        if isinstance(next_link, dict):
            href = next_link.get("href")
            if isinstance(href, str) and href and href == href.strip():
                return href
        raise OptimadePaginationError(f"OPTIMADE response from {_safe_source(source_url)!r} has malformed links.next")

    @staticmethod
    def _more_data(root: Mapping[str, object], source_url: str) -> bool:
        meta = root.get("meta")
        if meta is None:
            return False
        if not isinstance(meta, dict):
            raise OptimadeResponseError(f"OPTIMADE response from {_safe_source(source_url)!r} has non-object meta")
        value = meta.get("more_data_available", False)
        if not isinstance(value, bool):
            raise OptimadeResponseError(
                f"OPTIMADE response from {_safe_source(source_url)!r} has non-boolean meta.more_data_available"
            )
        return value

    @staticmethod
    def _validate_entry_page(
        root: Mapping[str, object],
        source_url: str,
        endpoint: str,
    ) -> list[Mapping[str, object]]:
        """Validate a complete successful entry envelope before yielding it.

        OPTIMADE list responses use an array ``data`` member and an object
        ``meta`` member.  When present, ``data_returned`` is the total number of
        data objects for the current filter query independent of pagination, so
        it is a nonnegative integer bearing no fixed relation to this page's
        length.  Resource-level checks
        intentionally stop at JSON:API envelope shapes: extension members and
        arbitrary link objects remain lossless raw data rather than being
        interpreted or rejected here.
        """

        raw_data = root.get("data")
        if not isinstance(raw_data, list):
            raise OptimadeResponseError(f"OPTIMADE response from {_safe_source(source_url)!r} data must be an array")
        meta = root.get("meta")
        if not isinstance(meta, dict):
            raise OptimadeResponseError(f"OPTIMADE response from {_safe_source(source_url)!r} has no object meta")
        data_returned = meta.get("data_returned")
        if "data_returned" in meta and (
            not isinstance(data_returned, int) or isinstance(data_returned, bool) or data_returned < 0
        ):
            raise OptimadeResponseError(
                f"OPTIMADE response from {_safe_source(source_url)!r} has invalid meta.data_returned"
            )
        more_data = meta.get("more_data_available", False)
        if not isinstance(more_data, bool):
            raise OptimadeResponseError(
                f"OPTIMADE response from {_safe_source(source_url)!r} has non-boolean meta.more_data_available"
            )

        resources: list[Mapping[str, object]] = []
        for data_index, item in enumerate(raw_data):
            if not isinstance(item, dict):
                raise OptimadeResponseError(f"OPTIMADE response data[{data_index}] must be an object")
            for envelope_member in ("id", "type"):
                envelope_value = item.get(envelope_member)
                if not isinstance(envelope_value, str) or not envelope_value:
                    raise OptimadeResponseError(
                        f"OPTIMADE response data[{data_index}].{envelope_member} must be a nonempty string"
                    )
            if item["type"] != endpoint:
                raise OptimadeResponseError(
                    f"OPTIMADE response data[{data_index}].type does not match queried endpoint {endpoint!r}"
                )
            for member in ("attributes", "relationships"):
                if member in item and not isinstance(item[member], dict):
                    raise OptimadeResponseError(
                        f"OPTIMADE response data[{data_index}].{member} must be an object when present"
                    )
            resources.append(item)
        return resources

    def _objects(
        self,
        *,
        maximum: int | None = None,
        page_limit_override: int | None = None,
    ) -> Iterator[tuple[object, OptimadeResource]]:
        descriptor, _variable = self._require_variable()
        effective_limit = self._limit
        if maximum is not None:
            effective_limit = maximum if effective_limit is None else min(effective_limit, maximum)
        if effective_limit == 0:
            return iter(())
        request_page_limit = self._store.page_limit
        if page_limit_override is not None:
            request_page_limit = min(request_page_limit, page_limit_override)
        if effective_limit is not None:
            request_page_limit = max(1, min(request_page_limit, effective_limit + self.offset))
        first_url = self._request_url(page_limit=request_page_limit)
        base_origin = _origin(self._store._transport_base_url)

        def resources() -> Iterator[tuple[object, OptimadeResource]]:
            next_url: str | None = first_url
            seen: set[str] = set()
            pages = 0
            skipped = 0
            emitted = 0
            while next_url is not None:
                split = urlsplit(next_url)
                if split.scheme not in ("http", "https") or not split.netloc:
                    raise OptimadePaginationError("OPTIMADE pagination continuation must be an absolute HTTP(S) URL")
                if next_url in seen:
                    raise OptimadePaginationError("OPTIMADE pagination cycle detected")
                if pages >= self._store.max_pages:
                    raise OptimadePaginationError(f"OPTIMADE pagination exceeded max_pages={self._store.max_pages}")
                if not self._store.allow_cross_origin_pagination and _origin(next_url) != base_origin:
                    raise OptimadePaginationError("OPTIMADE pagination attempted a cross-origin request")
                seen.add(next_url)
                pages += 1
                raw_text = self._store._get(next_url)
                # Continuations are extracted from the ephemeral raw response
                # so a credential-bearing cursor remains usable. Only the
                # redacted document below is retained by yielded resources.
                raw_root = self._raw_root(raw_text, next_url)
                raw_data = self._validate_entry_page(raw_root, next_url, descriptor.name)
                document = OptimadeDocument.from_response(raw_text, next_url)
                safe_root = optimade_document_root(document)
                safe_data = safe_root.get("data")
                if not isinstance(safe_data, tuple) or len(safe_data) != len(raw_data):
                    raise OptimadeResponseError("redacted OPTIMADE document changed the data envelope")
                for data_index, item in enumerate(raw_data):
                    if skipped < self.offset:
                        skipped += 1
                        continue
                    resource = OptimadeResource(document, data_index, descriptor.schema)
                    value = (
                        resource
                        if descriptor.backend is OptimadeResource
                        else cast(Callable[[OptimadeResource], object], descriptor.backend)(resource)
                    )
                    yield value, resource
                    emitted += 1
                    if effective_limit is not None and emitted >= effective_limit:
                        return
                continuation = self._next_link(raw_root, next_url)
                if continuation is None:
                    if self._more_data(raw_root, next_url):
                        raise OptimadePaginationError(
                            "OPTIMADE response reports more_data_available without a usable links.next"
                        )
                    return
                next_url = urljoin(next_url, continuation)

        return resources()

    def _search_results(self, *, maximum: int | None = None) -> Iterator[SearchResult]:
        if not self._outputs:
            raise ValueError("this search has no outputs; call output() before iterating")
        descriptor, _variable = self._require_variable()
        generic = descriptor.backend is OptimadeResource
        names = tuple(output.name for output in self._outputs)
        for value, _resource in self._objects(maximum=maximum, page_limit_override=maximum):
            values = tuple(
                value
                if output.variable is not None
                else _scalar_value(value, generic, cast(_RemoteField, output.field))
                for output in self._outputs
            )
            yield SearchResult(values, names)

    def __iter__(self) -> Iterator[SearchResult]:
        """Yield remote search results in output order."""

        return self._search_results()

    def count(self) -> int:
        """Return the filtered remote count.

        :return: ``meta.data_returned`` reported by the service.
        :raises CountUnavailableError: If the service omits a valid count.
        """
        self._require_variable()
        if self._count_cache.value is not None:
            return self._count_cache.value
        url = self._request_url(
            page_limit=1,
            include_sort=False,
            response_fields=(self._fields["id"]._remote_name,) if "id" in self._fields else None,
        )
        raw_text = self._store._get(url)
        root = self._raw_root(raw_text, url)
        meta = root.get("meta")
        if not isinstance(meta, dict):
            raise CountUnavailableError("OPTIMADE count response has no object meta")
        value = meta.get("data_returned")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise CountUnavailableError("OPTIMADE count response has no valid nonnegative meta.data_returned")
        self._count_cache.value = value
        return value

    def results(self, **outputs: object) -> "RemoteResultSet":
        """Freeze the query as a lazy, re-iterable result set.

        :param \\*\\*outputs: Optional output names mapped to this searcher's projections.
        :return: Frozen remote result plan.
        :raises ValueError: If no outputs are declared.
        """
        return RemoteResultSet(self, outputs or None)

    def slicer(self, target: RemoteEntryType) -> "Slicer":
        """A pandas-style ``[]`` indexing view over one discovered entry endpoint.

        Each terminal indexing operation runs against a fresh searcher minted
        with this searcher's ``response_fields`` policy, so slicer operations
        never share filter state. No sorting is offered here -- use
        :meth:`OptimadeStore.searcher` and :meth:`add_sort` directly for a
        sorted or relationship query.

        :param target: The discovered remote entry endpoint to index.
        :return: A slicer over ``target``.
        """
        from httk.store.query.slicer import Slicer

        def _make() -> "RemoteSearcher":
            return self._store.searcher(response_fields=self._response_fields_setting)

        return Slicer(_make, target)


class RemoteResultColumn:
    """Expose one named scalar projection from a lazy result set.

    :param result: Result set owning the projection.
    :param index: Zero-based projection index.
    """

    def __init__(self, result: "RemoteResultSet", index: int) -> None:
        self._result = result
        self._index = index
        self.name = result.names[index]

    def __len__(self) -> int:
        """Return the number of projected results.

        :return: Result count after query paging.
        """

        return len(self._result)

    def __iter__(self) -> Iterator[object]:
        """Yield projected scalar values."""

        return (row[self._index] for row in self._result)


class RemoteResultSet:
    """Represent a frozen, lazy, and re-iterable remote result plan.

    :param searcher: Search plan to clone.
    :param outputs: Optional output names mapped to the searcher's projections.
    """

    def __init__(self, searcher: RemoteSearcher, outputs: Mapping[str, object] | None = None) -> None:
        self._plan = searcher._clone()
        if outputs:
            self._plan._outputs = []
            for name, value in outputs.items():
                if value is searcher._variable:
                    self._plan.output(self._plan._variable, name)
                elif isinstance(value, _RemoteField) and value._searcher is searcher:
                    self._plan.output(self._plan._fields[value._local_name], name)
                else:
                    raise _unsupported("result projections from another backend or searcher")
        if not self._plan._outputs:
            raise ValueError("this search has no outputs; declare outputs or pass them to results()")
        self.names = tuple(output.name for output in self._plan._outputs)

    def __getitem__(self, item: slice) -> "RemoteResultSet":
        """Return a derived result plan for a unit-step slice.

        :param item: Nonnegative slice with no step or a unit step.
        :return: Derived lazy result plan.
        :raises TypeError: If integer indexing is requested.
        :raises ValueError: If slice bounds or step are unsupported.
        """
        if not isinstance(item, slice):
            raise TypeError("remote result sets support slicing, not integer indexing")
        if item.step not in (None, 1):
            raise ValueError("remote result slices require a unit step")
        start = 0 if item.start is None else item.start
        stop = item.stop
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or start < 0
            or (stop is not None and (not isinstance(stop, int) or isinstance(stop, bool) or stop < 0))
        ):
            raise ValueError("remote result slice bounds must be nonnegative integers")
        derived = object.__new__(type(self))
        derived._plan = self._plan._clone()
        derived.names = self.names
        available_limit = derived._plan._limit
        derived._plan.offset += start
        if available_limit is not None:
            available_limit = max(0, available_limit - start)
        if stop is not None:
            span = max(0, stop - start)
            available_limit = span if available_limit is None else min(available_limit, span)
        derived._plan._limit = available_limit
        return derived

    def __iter__(self) -> Iterator[ResultRow]:
        """Yield projected rows as result rows."""

        return (ResultRow(result.values, result.names) for result in self._plan._search_results())

    def __len__(self) -> int:
        """Return the bounded result count.

        :return: Number of rows available under this result plan.
        """

        available = max(0, self._plan.count() - self._plan.offset)
        if self._plan._limit is not None:
            available = min(available, self._plan._limit)
        return available

    def first(self) -> ResultRow | None:
        """Return the first result, if present.

        :return: First row or ``None``.
        """

        iterator = self._plan._search_results(maximum=1)
        try:
            result = next(iterator)
        except StopIteration:
            return None
        return ResultRow(result.values, result.names)

    def one(self) -> ResultRow:
        """Return the only result.

        :return: Sole result row.
        :raises httk.store.NoResultError: If no result exists.
        :raises httk.store.MultipleResultsError: If more than one result exists.
        """

        iterator = self._plan._search_results(maximum=2)
        try:
            first = next(iterator)
        except StopIteration:
            raise NoResultError("expected exactly one result, found none") from None
        try:
            next(iterator)
        except StopIteration:
            return ResultRow(first.values, first.names)
        raise MultipleResultsError("expected exactly one result, found more than one")

    def scalars(self, name: str | None = None) -> Iterator[object]:
        """Iterate one named scalar output from each result.

        :param name: Output name, or ``None`` when exactly one exists.
        :return: Iterator over scalar values.
        :raises KeyError: If the named output is unknown.
        :raises ValueError: If no name is given and multiple outputs exist.
        """
        if name is None:
            if len(self.names) != 1:
                raise ValueError(f"scalars() without a name requires exactly one output; declared: {self.names}")
            name = self.names[0]
        try:
            index = self.names.index(name)
        except ValueError:
            raise KeyError(f"unknown output {name!r}; declared: {self.names}") from None
        return (row[index] for row in self)

    def column(self, name: str) -> RemoteResultColumn:
        """Return a lazy column for a scalar output.

        :param name: Scalar output name.
        :return: Lazy result column.
        :raises KeyError: If the output is unknown.
        :raises TypeError: If the output is a whole-record projection.
        """
        try:
            index = self.names.index(name)
        except ValueError:
            raise KeyError(f"unknown output {name!r}; declared: {self.names}") from None
        if self._plan._outputs[index].field is None:
            scalar_names = tuple(output.name for output in self._plan._outputs if output.field is not None)
            raise TypeError(f"column {name!r} is an object output; scalar projections: {scalar_names}")
        return RemoteResultColumn(self, index)

    def cursor(self) -> Iterator[ResultRow]:
        """Reject unsupported cursor access.

        :return: Never; remote OPTIMADE cursors are unsupported.
        :raises NotImplementedError: Remote OPTIMADE cursors are unavailable.
        """

        raise NotImplementedError("remote OPTIMADE cursor rows are not supported")
