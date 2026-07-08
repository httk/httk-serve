"""Endpoint dispatch: routes a validated OPTIMADE request to its reply generator."""

import logging
from pprint import pformat

from ..endpoints.entries import (
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
from ..filter.parser import FilterAst, ParserSyntaxError, parse_optimade_filter
from ..model.config import OptimadeConfig
from ..model.errors import OptimadeError, TranslatorError
from ..model.request import EndpointResponse, RawRequest
from ..model.results import QueryFunction
from ..schema.httk_entries import (
    default_response_fields,
    httk_all_entries,
    required_response_fields,
)
from .validate import validate_optimade_request

logger = logging.getLogger("httk.optimade")


def process(
    request: RawRequest,
    query_function: QueryFunction,
    version: str,
    config: OptimadeConfig,
    *,
    debug: bool = False,
) -> EndpointResponse:
    """Process an OPTIMADE query.

    ``request`` carries the incoming request; only ``baseurl`` and
    ``representation`` must be set, missing information is derived from
    ``representation``. ``query_function`` is the callback used to execute
    entry queries against the backend.
    """

    if debug:
        logger.debug("==== OPTIMADE REQUEST FOR: %s", request.representation)

    validated_request = validate_optimade_request(request, version)
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
        response = generate_info_endpoint_reply(validated_request, config)

    elif endpoint in httk_all_entries:

        response_fields = validated_request.recognized_response_fields
        unknown_response_fields = validated_request.unrecognized_response_fields
        entries = [endpoint]

        if not response_fields:
            response_fields = list(default_response_fields[endpoint])

        for response_field in required_response_fields[endpoint]:
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
                debug=debug,
            )

        if request_id is not None:
            response = generate_single_entry_endpoint_reply(validated_request, config, results)
        else:
            response = generate_entry_endpoint_reply(validated_request, config, results)

        if debug:
            logger.debug("==== END RESULT: %s", pformat(response))

    elif endpoint.startswith("info/"):
        info, _sep, base = endpoint.partition("/")
        assert info == "info"
        if base in httk_all_entries:
            response = generate_entry_info_endpoint_reply(validated_request, config, base)
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


def process_init(config: OptimadeConfig, query_function: QueryFunction, *, debug: bool = False) -> None:
    """Precompute the number of available entries per entry endpoint."""
    config.data_available = {}
    for endpoint in httk_all_entries:
        results = query_function([endpoint], [], [], 0, 0, debug=debug)
        config.data_available[endpoint] = results.count()
