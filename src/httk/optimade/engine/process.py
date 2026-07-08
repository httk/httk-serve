"""Endpoint dispatch: routes a validated OPTIMADE request to its reply generator."""

import logging
from pprint import pformat
from typing import Any, Callable

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
from ..filter.parser import FilterAst, ParserSyntaxError, parse_optimade_filter
from ..model.config import OptimadeConfig
from ..model.errors import OptimadeError, TranslatorError
from ..model.request import EndpointResponse, RawRequest
from ..model.results import QueryFunction
from ..schema.served import ServedSchema, default_served_schema
from .validate import validate_optimade_request

logger = logging.getLogger("httk.optimade")


def _make_related_resolver(
    query_function: QueryFunction, schema: ServedSchema, *, debug: bool = False
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

            filter_ast: FilterAst | None = None
            for rid in id_list:
                node: FilterAst = ('=', ('Identifier', 'id'), ('String', rid))
                filter_ast = node if filter_ast is None else ('OR', filter_ast, node)

            results = query_function(
                [etype],
                response_fields,
                [],
                len(id_list),
                0,
                filter_ast,
                debug=debug,
            )
            for row in results:
                obj = _resource_object(row)
                key = (obj['type'], obj['id'])
                if key in seen:
                    continue
                seen.add(key)
                included.append(obj)
        return included

    return resolve


def process(
    request: RawRequest,
    query_function: QueryFunction,
    version: str,
    config: OptimadeConfig,
    schema: ServedSchema | None = None,
    *,
    debug: bool = False,
) -> EndpointResponse:
    """Process an OPTIMADE query.

    ``request`` carries the incoming request; only ``baseurl`` and
    ``representation`` must be set, missing information is derived from
    ``representation``. ``query_function`` is the callback used to execute
    entry queries against the backend. ``schema`` describes the served entry
    types and properties; when omitted, the default httk schema is used.
    """

    if schema is None:
        schema = default_served_schema()

    if debug:
        logger.debug("==== OPTIMADE REQUEST FOR: %s", request.representation)

    validated_request = validate_optimade_request(request, version, schema)
    endpoint = validated_request.endpoint
    request_id = validated_request.request_id
    validated_parameters = validated_request.query

    if debug:
        logger.debug(
            "==== VALIDATED ENDPOINT: %s, REQUEST_ID: %s, PARAMETERS: %s",
            endpoint,
            request_id,
            validated_parameters,
        )

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

    elif endpoint in schema.all_entries:

        response_fields = validated_request.recognized_response_fields
        unknown_response_fields = validated_request.unrecognized_response_fields
        entries = [endpoint]

        if not response_fields:
            response_fields = list(schema.default_response_fields[endpoint])

        for response_field in schema.required_response_fields[endpoint]:
            if response_field not in response_fields:
                response_fields += [response_field]

        input_string = None
        filter_ast: FilterAst | None = None
        if request_id is not None:
            input_string = 'filter=id="' + request_id + '"'
            filter_ast = ('=', ('Identifier', 'id'), ('String', request_id))
        elif validated_parameters.filter is not None:
            input_string = validated_parameters.filter

        if input_string is not None:
            if filter_ast is None:
                try:
                    filter_ast = parse_optimade_filter(input_string)
                except ParserSyntaxError as e:
                    raise OptimadeError(str(e), 400, "Bad request")

            if debug:
                logger.debug("==== FILTER STRING PARSE RESULT: %s", pformat(filter_ast))

            try:
                results = query_function(
                    entries,
                    response_fields,
                    unknown_response_fields,
                    validated_parameters.page_limit,
                    validated_parameters.page_offset,
                    filter_ast,
                    sort=validated_request.sort_fields or None,
                    debug=debug,
                )
            except TranslatorError as e:
                raise OptimadeError(str(e), e.response_code, e.response_msg)

        else:
            results = query_function(
                entries,
                response_fields,
                unknown_response_fields,
                validated_parameters.page_limit,
                validated_parameters.page_offset,
                sort=validated_request.sort_fields or None,
                debug=debug,
            )

        related_resolver = _make_related_resolver(query_function, schema, debug=debug)

        if request_id is not None:
            response = generate_single_entry_endpoint_reply(validated_request, config, results, related_resolver)
        else:
            response = generate_entry_endpoint_reply(validated_request, config, results, related_resolver)

        if debug:
            logger.debug("==== END RESULT: %s", pformat(response))

    elif endpoint.startswith("info/"):
        info, _sep, base = endpoint.partition("/")
        assert info == "info"
        if base in schema.all_entries:
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


def process_init(
    config: OptimadeConfig, query_function: QueryFunction, schema: ServedSchema | None = None, *, debug: bool = False
) -> None:
    """Precompute the number of available entries per entry endpoint."""
    if schema is None:
        schema = default_served_schema()
    config.data_available = {}
    for endpoint in schema.all_entries:
        results = query_function([endpoint], [], [], 0, 0, debug=debug)
        config.data_available[endpoint] = results.count()
