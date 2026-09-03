"""Endpoint dispatch: routes a validated OPTIMADE request to its reply generator."""

import logging
import time
from collections.abc import Callable
from pprint import pformat
from typing import Any

from httk.core.optimade import FilterAst, ParserSyntaxError, parse_optimade_filter

from ..endpoints.entries import (
    _resource_object,
    generate_entry_endpoint_reply,
    generate_single_entry_endpoint_reply,
)
from ..endpoints.info import (
    generate_base_endpoint_reply,
    generate_entry_info_endpoint_reply,
    generate_info_endpoint_reply,
    generate_links_endpoint_reply,
    generate_versions_endpoint_reply,
)
from ..endpoints.partial_data import generate_partial_data_reply
from ..model.config import OptimadeConfig, OptimadeIndexConfig
from ..model.errors import OptimadeError, TranslatorError
from ..model.request import EndpointResponse, RawRequest
from ..model.results import QueryFunction
from ..schema.served import _RELATIONSHIPS_ROOT, ServedSchema
from .validate import validate_optimade_request

_LOG = logging.getLogger("httk.serve.optimade")
type SnapshotCutoff = Callable[[str, int], int | None]


def _make_related_resolver(
    query_function: QueryFunction,
    schema: ServedSchema,
    baseurl: str,
    *,
    as_of: int | None = None,
    debug: bool = False,
) -> "Callable[[dict[str, set[str]]], list[dict[str, Any]]]":
    """Build a resolver that fetches related resources for the ``included`` field.

    Given a mapping of related entry type to the set of related ids, it queries
    each entry type (depth-1 only, never recursing further) with its default
    response fields, formats each result as a full resource object (including
    its own relationships block), and returns the deduplicated list.
    """

    def resolve(collected: dict[str, set[str]]) -> list[dict[str, Any]]:
        included: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for etype, ids in collected.items():
            if not ids or etype not in schema.all_entries:
                continue
            id_list = sorted(ids)

            response_fields = list(schema.default_response_fields.get(etype, ()))
            for required in schema.required_response_fields.get(etype, ()):
                if required not in response_fields:
                    response_fields.append(required)

            # Build a balanced OR tree: the filter translator recurses per node,
            # so a linear chain would overflow the stack for many related ids.
            nodes: list[FilterAst] = [('=', ('Identifier', 'id'), ('String', rid)) for rid in id_list]
            while len(nodes) > 1:
                nodes = [
                    ('OR', nodes[i], nodes[i + 1]) if i + 1 < len(nodes) else nodes[i] for i in range(0, len(nodes), 2)
                ]
            filter_ast: FilterAst | None = nodes[0] if nodes else None

            results = query_function(
                [etype],
                response_fields,
                [],
                len(id_list),
                0,
                filter_ast,
                as_of=as_of,
                debug=debug,
            )
            for row in results:
                obj = _resource_object(row, baseurl)
                key = (obj['type'], obj['id'])
                if key in seen:
                    continue
                seen.add(key)
                included.append(obj)
        return included

    return resolve


def _reject_hidden_property(name: str, entry: str, schema: ServedSchema) -> None:
    """Reject one filter property name against the served schema for ``entry``.

    :param name: Referenced property name.
    :param entry: Served entry type whose ``entry_info`` governs ``name``.
    :param schema: Served schema whose per-property ``queryable`` flags apply.
    :raises httk.serve.optimade.model.errors.OptimadeError: If ``name`` is non-queryable or a hidden prefixed property.
    """
    properties = schema.entry_info[entry]['properties']
    if name in properties:
        if not properties[name].get('queryable', True):
            raise OptimadeError("Filtering is not supported for property: " + name, 400, "Bad request")
    elif name.startswith(schema.recognized_prefixes):
        # Absent from the served schema yet carrying a recognized definition
        # prefix: an adapter-hidden internal projection, not filterable at the
        # protocol boundary. Unprefixed unknown names are left to the translator.
        raise OptimadeError("Filter invokes unrecognized property name: " + name, 400, "Bad request")


def _reject_hidden_filter_properties(node: FilterAst, endpoint: str, schema: ServedSchema) -> None:
    """Reject a client filter referencing schema-hidden or non-queryable properties.

    Queryability enforcement is a protocol-boundary policy applied to the parsed
    client filter *before* any backend or adapter rewriting, so the neutral store
    layer stays able to query every stored property for trusted internal callers.
    The tree is walked without mutation; every ``('Identifier', ...)`` node is
    validated against the served schema: a plain identifier against ``endpoint``,
    and a depth-1 relationship identifier ``<type>.<property>`` against that
    served type (deeper or non-served-type dotted paths are left to the
    translator, which is the only thing that can handle or reject them).

    :param node: Parsed filter node to inspect.
    :param endpoint: Served entry type the filter targets.
    :param schema: Served schema whose per-property ``queryable`` flags apply.
    :raises httk.serve.optimade.model.errors.OptimadeError: If the filter names a hidden or non-queryable property.
    """
    if not isinstance(node, tuple) or not node:
        return
    if node[0] == 'Identifier':
        # A dotted identifier is a depth-1 relationship filter only when its head
        # names a served entry type; validate the trailing property against that
        # type. Every other identifier is a property of the current endpoint, and
        # the trailing segments the translator silently ignores must not smuggle a
        # hidden head property past validation (e.g. `_httk_custom_public_id.x`).
        if len(node) == 2:
            _reject_hidden_property(node[1], endpoint, schema)
        elif len(node) > 2:
            if node[1] == _RELATIONSHIPS_ROOT:
                # A `_httk_relationships.<key>.id` filter-grammar extension
                # identifier: not a served property, resolved entirely by the
                # translator (which filters a known key and 400s an unknown one).
                return
            if node[1] in schema.all_entries:
                _reject_hidden_property(node[-1], node[1], schema)
            else:
                _reject_hidden_property(node[1], endpoint, schema)
        return
    for child in node:
        _reject_hidden_filter_properties(child, endpoint, schema)


def process(
    request: RawRequest,
    query_function: QueryFunction,
    version: str,
    config: OptimadeConfig,
    schema: ServedSchema,
    *,
    snapshot_cutoff_ns: SnapshotCutoff | None = None,
    debug: bool = False,
) -> EndpointResponse:
    """Process one OPTIMADE query.

    ``request`` carries the incoming request; only ``baseurl`` and
    ``representation`` must be set, missing information is derived from
    ``representation``. ``query_function`` is the callback used to execute
    entry queries against the backend. ``schema`` describes the served entry
    types and properties.

    :param request: Raw request to validate and dispatch.
    :param query_function: Backend callback used for entry queries.
    :param version: API version selected for the request.
    :param config: Service response configuration.
    :param schema: Explicit served schema for endpoint validation.
    :param snapshot_cutoff_ns: Optional stored-backend snapshot capability.
    :param debug: Enable backend diagnostics.
    :return: Endpoint response before web serialization.
    :raises httk.serve.optimade.model.errors.OptimadeError: If request validation or endpoint processing fails.
    """

    if _LOG.isEnabledFor(logging.DEBUG):
        _LOG.debug("==== OPTIMADE REQUEST FOR: %s", request.representation, extra={"context": "optimade"})

    validated_request = validate_optimade_request(request, version, schema, config.page_limit_max)
    endpoint = validated_request.endpoint
    request_id = validated_request.request_id
    validated_parameters = validated_request.query

    if endpoint in schema.all_entries + schema.revision_endpoints + schema.alt_endpoints:
        snapshot_entry = schema.revision_base.get(endpoint) or schema.alt_base.get(endpoint) or endpoint
        if snapshot_cutoff_ns is None:
            validated_parameters.as_of = None
        else:
            cutoff = snapshot_cutoff_ns(snapshot_entry, time.time_ns())
            if cutoff is None:
                validated_parameters.as_of = None
            elif validated_parameters.as_of is None:
                validated_parameters.as_of = cutoff

    if _LOG.isEnabledFor(logging.DEBUG):
        _LOG.debug(
            "==== VALIDATED ENDPOINT: %s, REQUEST_ID: %s, PARAMETERS: %s",
            endpoint,
            request_id,
            validated_parameters,
            extra={"context": "optimade"},
        )

    if endpoint == '' and isinstance(config, OptimadeIndexConfig):
        raise OptimadeError("Request for non-existing endpoint.", 404, "Not Found")

    if endpoint == '':
        content = generate_base_endpoint_reply(validated_request, config)
        return EndpointResponse(content=content, content_type='text/html', response_code=200, response_msg='OK')

    elif endpoint == 'versions':
        content = generate_versions_endpoint_reply(validated_request, config)
        return EndpointResponse(
            content=content, content_type='text/csv; header=present', response_code=200, response_msg='OK'
        )

    elif endpoint == 'links':
        response = generate_links_endpoint_reply(validated_request, config)

    elif endpoint == 'info':
        response = generate_info_endpoint_reply(validated_request, config, schema)

    elif endpoint == 'partial_data':
        return generate_partial_data_reply(validated_request, config, query_function, schema)

    elif endpoint in schema.all_entries + schema.revision_endpoints + schema.alt_endpoints:
        response_fields = validated_request.recognized_response_fields
        unknown_response_fields = validated_request.unrecognized_response_fields
        entries = [schema.revision_base.get(endpoint) or schema.alt_base.get(endpoint) or endpoint]

        if not response_fields:
            response_fields = list(schema.default_response_fields[endpoint])

        for response_field in schema.required_response_fields[endpoint]:
            if response_field not in response_fields:
                response_fields += [response_field]

        filter_ast: FilterAst | None = None
        route_filter_ast: FilterAst | None = None
        if validated_request.request_immutable_id is not None and request_id is not None:
            # StoredBackendAdapter uses this synthesized lineage id together
            # with request_immutable_id to call fetch_revision().
            filter_ast = ('=', ('Identifier', 'id'), ('String', request_id))
        elif request_id is not None:
            if validated_request.revisions or validated_request.alternatives:
                route_filter_ast = ('=', ('Identifier', '_httk_id'), ('String', request_id))
                filter_ast = route_filter_ast
            else:
                filter_ast = ('=', ('Identifier', 'id'), ('String', request_id))

        if validated_parameters.filter is not None and validated_request.request_immutable_id is None:
            try:
                client_filter = parse_optimade_filter(validated_parameters.filter)
            except ParserSyntaxError as e:
                raise OptimadeError(str(e), 400, "Bad request")
            _reject_hidden_filter_properties(client_filter, endpoint, schema)
            filter_ast = client_filter if filter_ast is None else ('AND', filter_ast, client_filter)

        if filter_ast is not None:
            if _LOG.isEnabledFor(logging.DEBUG):
                _LOG.debug("==== FILTER STRING PARSE RESULT: %s", pformat(filter_ast), extra={"context": "optimade"})

            query_kwargs: dict[str, Any] = {
                "as_of": validated_parameters.as_of,
                "sort": validated_request.sort_fields or None,
                "debug": debug,
            }
            if validated_request.revisions:
                query_kwargs["revisions"] = True
                query_kwargs["immutable_id"] = validated_request.request_immutable_id
            if validated_request.alternatives:
                query_kwargs["alternatives"] = True
                query_kwargs["immutable_id"] = validated_request.request_immutable_id
            try:
                results = query_function(
                    entries,
                    response_fields,
                    unknown_response_fields,
                    validated_parameters.page_limit,
                    validated_parameters.page_offset,
                    filter_ast,
                    **query_kwargs,
                )
            except TranslatorError as e:
                raise OptimadeError(str(e), e.response_code, e.response_msg)

        else:
            query_kwargs = {
                "as_of": validated_parameters.as_of,
                "sort": validated_request.sort_fields or None,
                "debug": debug,
            }
            if validated_request.revisions:
                query_kwargs["revisions"] = True
                query_kwargs["immutable_id"] = validated_request.request_immutable_id
            if validated_request.alternatives:
                query_kwargs["alternatives"] = True
                query_kwargs["immutable_id"] = validated_request.request_immutable_id
            results = query_function(
                entries,
                response_fields,
                unknown_response_fields,
                validated_parameters.page_limit,
                validated_parameters.page_offset,
                **query_kwargs,
            )

        if validated_request.request_immutable_id is not None and results.count() == 0:
            raise OptimadeError("Request for non-existing revision.", 404, "Not Found")

        related_resolver = _make_related_resolver(
            query_function,
            schema,
            validated_request.baseurl,
            as_of=validated_parameters.as_of,
            debug=debug,
        )

        # meta.data_available is the unfiltered endpoint total, computed per request
        # with the same as_of as the filtered page. With an explicit _httk_as_of
        # snapshot the envelope is arithmetically consistent (data_available >=
        # data_returned); without one (as_of None) the two reads are back-to-back
        # and a delete interleaved between them can still skew the pair for that
        # one response. ponytail: an extra count query per entry request; memoize
        # per (endpoint, as_of) if it ever shows up in a profile.
        count_kwargs: dict[str, Any] = {"as_of": validated_parameters.as_of, "debug": debug}
        if validated_request.revisions:
            count_kwargs["revisions"] = True
        if validated_request.alternatives:
            count_kwargs["alternatives"] = True
        data_available = query_function(entries, [], [], 0, 0, route_filter_ast, **count_kwargs).count()

        if (
            request_id is not None and not validated_request.revisions and not validated_request.alternatives
        ) or validated_request.request_immutable_id is not None:
            response = generate_single_entry_endpoint_reply(
                validated_request, config, results, data_available, related_resolver
            )
        else:
            response = generate_entry_endpoint_reply(
                validated_request, config, results, data_available, related_resolver
            )

        if _LOG.isEnabledFor(logging.DEBUG):
            _LOG.debug("==== END RESULT: %s", pformat(response), extra={"context": "optimade"})

    elif endpoint.startswith("info/"):
        info, _sep, base = endpoint.partition("/")
        assert info == "info"
        if base in schema.all_entries + schema.revision_endpoints + schema.alt_endpoints:
            response = generate_entry_info_endpoint_reply(validated_request, config, base, schema)
        else:
            raise OptimadeError("Internal error: unexpected endpoint.", 500, "Internal server error")

    else:
        raise OptimadeError("Internal error: unexpected endpoint.", 500, "Internal server error")

    return EndpointResponse(
        json_response=response,
        content_type='application/vnd.api+json',
        response_code=200,
        response_msg='OK',
    )
