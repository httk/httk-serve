from urllib.parse import parse_qsl, urlparse

from ..model.errors import OptimadeError
from ..model.request import RawRequest, ValidatedParameters, ValidatedRequest
from ..model.versions import optimade_default_version, optimade_supported_versions
from ..schema.served import ServedSchema, default_served_schema

PAGE_LIMIT_MAX = 50


def _validate_query(endpoint: str, query: dict[str, str], schema: ServedSchema) -> ValidatedParameters:
    validated_parameters = ValidatedParameters()

    if ('response_format' in query and query['response_format'] is not None) and query['response_format'] != 'json':
        raise OptimadeError("Requested response_format not supported.", 400, "Bad request")

    if 'page_limit' in query and query['page_limit'] is not None:
        try:
            validated_parameters.page_limit = int(query['page_limit'])
        except ValueError:
            raise OptimadeError("Cannot interprete page_limit.", 400, "Bad request")
        if validated_parameters.page_limit > PAGE_LIMIT_MAX:
            validated_parameters.page_limit = PAGE_LIMIT_MAX

    if 'page_offset' in query and query['page_offset'] is not None:
        try:
            validated_parameters.page_offset = int(query['page_offset'])
        except ValueError:
            raise OptimadeError("Cannot interprete page_offset.", 400, "Bad request")
        if validated_parameters.page_offset < 0:
            validated_parameters.page_offset = 0

    if 'response_fields' in query and query['response_fields'] is not None:
        validated_response_fields = []
        response_fields = [x.strip() for x in query['response_fields'].split(",")]
        if endpoint in schema.properties_by_entry:
            for response_field in response_fields:
                if response_field in schema.properties_by_entry[endpoint]:
                    # Defensive programming; don't trust '=='/in to be byte-for-byte equivalent,
                    # so don't use the insecure string from the user
                    valid_fields = schema.properties_by_entry[endpoint]
                    validated_response_fields += [valid_fields[valid_fields.index(response_field)]]
                elif response_field in schema.unknown_response_fields[endpoint]:
                    validated_response_fields += [response_field]
                elif response_field.startswith(schema.recognized_prefixes) or (
                    len(response_field) > 0 and response_field[0] != '_'
                ):
                    raise OptimadeError(
                        "Response_fields contains unrecognized property name: " + response_field, 400, "Bad request"
                    )
                else:
                    validated_response_fields += [response_field]
            validated_parameters.response_fields = ",".join(validated_response_fields)
        else:
            validated_parameters.response_fields = ""

    # Validating the filter string is deferred to its parser
    if 'filter' in query and query['filter'] is not None:
        validated_parameters.filter = query['filter']

    # The sort parameter is only meaningful for entry endpoints; on other
    # endpoints it is ignored. The raw string is stored here; parsing into
    # (property, descending) pairs happens in validate_optimade_request.
    if 'sort' in query and query['sort'] is not None and endpoint in schema.all_entries:
        validated_parameters.sort = query['sort']

    # The include parameter is only meaningful for entry endpoints. The raw
    # string is stored here only when the client explicitly provides it (so
    # next-links reproduce the user's intent); parsing into the list of served
    # entry types happens in validate_optimade_request.
    if 'include' in query and query['include'] is not None and endpoint in schema.all_entries:
        validated_parameters.include = query['include']

    return validated_parameters


def validate_optimade_request(
    request: RawRequest, version: str, schema: ServedSchema | None = None
) -> ValidatedRequest:
    if schema is None:
        schema = default_served_schema()
    endpoint = request.endpoint
    request_id = request.request_id
    validated_version = request.version if request.version is not None else optimade_default_version
    url_version: str | None = None

    if endpoint is None:
        if request.relurl is not None:
            relurl = request.relurl
        else:
            relurl = request.representation.partition('?')[0]

        endpoint_str = relurl.strip("/")

        potential_optimade_version, _sep, rest = endpoint_str.partition('/')

        if (
            len(potential_optimade_version) >= 2
            and potential_optimade_version[0] == 'v'
            and potential_optimade_version[1] in "0123456789"
        ):
            if potential_optimade_version in optimade_supported_versions:
                validated_version = optimade_supported_versions[potential_optimade_version]
                url_version = potential_optimade_version
                endpoint_str = rest
            else:
                raise OptimadeError(
                    "Unsupported version requested. Supported versioned base URLs are: "
                    + (", ".join(["/" + str(x) for x in optimade_supported_versions.keys()])),
                    553,
                    "Bad request",
                )

        first_level_endpoint, _sep, path_request_id = endpoint_str.partition('/')

        # First check fixed endpoints
        if endpoint_str in schema.valid_endpoints:
            # Defensive programming; don't trust '=='/in to be byte-for-byte equivalent,
            # so don't use the insecure string from the user
            endpoint = schema.valid_endpoints[schema.valid_endpoints.index(endpoint_str)]

        # Then check "entries" endpoint with a request_id
        elif first_level_endpoint in schema.all_entries:
            # Defensive programming; don't trust '=='/in to be byte-for-byte equivalent,
            # so don't use the insecure string from the user
            endpoint = schema.valid_endpoints[schema.valid_endpoints.index(first_level_endpoint)]
            # Only allow printable ascii characters in id; this is not in the standard, but your
            # database really should adhere to it or you are doing weird things.
            if len(path_request_id) > 0:
                if all(ord(c) >= 32 and ord(c) <= 126 for c in path_request_id):
                    request_id = path_request_id
                else:
                    raise OptimadeError("Unexpected characters in entry id.", 400, "Bad request")
            else:
                request_id = None

        # Finally check the special versions endpoint
        elif endpoint_str == 'versions':
            if url_version is not None:
                raise OptimadeError(
                    "Request for non-existing endpoint. "
                    "The 'versions' endpoint is only available on the unversioned URL.",
                    404,
                    "Not Found",
                )
            endpoint = 'versions'
            request_id = None

        if endpoint is None:
            raise OptimadeError("Request for non-existing endpoint.", 404, "Bad request")

    if request.query is not None:
        query = request.query
    else:
        if request.querystr is not None:
            querystr = request.querystr
        else:
            querystr = urlparse(request.representation).query
        query = dict(parse_qsl(querystr, keep_blank_values=True))

    validated_request = ValidatedRequest(
        baseurl=request.baseurl,
        representation=request.representation,
        endpoint=endpoint,
        version=validated_version,
        url_version=url_version,
        request_id=request_id,
        query=_validate_query(endpoint, query, schema),
    )

    if 'response_fields' in query and query['response_fields'] is not None:
        response_fields = [x.strip() for x in query['response_fields'].split(",")]
        if endpoint in schema.properties_by_entry:
            for response_field in response_fields:
                if response_field in schema.properties_by_entry[endpoint]:
                    # Defensive programming; don't trust '=='/in to be byte-for-byte equivalent,
                    # so don't use the insecure string from the user
                    valid_fields = schema.properties_by_entry[endpoint]
                    validated_request.recognized_response_fields += [valid_fields[valid_fields.index(response_field)]]
                elif response_field in schema.unknown_response_fields[endpoint]:
                    validated_request.unrecognized_response_fields += [response_field]
                elif response_field.startswith(schema.recognized_prefixes) or (
                    len(response_field) > 0 and response_field[0] != '_'
                ):
                    raise OptimadeError(
                        "Response_fields contains unrecognized property name: " + response_field, 400, "Bad request"
                    )
                else:
                    validated_request.unrecognized_response_fields += [response_field]
    else:
        if endpoint in schema.default_response_fields:
            validated_request.recognized_response_fields = list(schema.default_response_fields[endpoint])

    if endpoint in schema.required_response_fields:
        for response_field in schema.required_response_fields[endpoint]:
            if response_field not in validated_request.recognized_response_fields:
                validated_request.recognized_response_fields += [response_field]

    # Parse the sort parameter (JSON:API 1.1: comma-separated property names,
    # a leading '-' meaning descending) into (property, descending) pairs.
    if validated_request.query.sort is not None:
        sortable = schema.sortable_response_fields[endpoint]
        for token in validated_request.query.sort.split(","):
            token = token.strip()
            if token == "":
                continue
            descending = token.startswith("-")
            name = token[1:] if descending else token
            if name not in sortable:
                raise OptimadeError("Sorting not supported for field: " + name, 400, "Bad request")
            # Defensive programming; don't trust '=='/in to be byte-for-byte
            # equivalent, so don't use the insecure string from the user.
            canonical = sortable[sortable.index(name)]
            validated_request.sort_fields.append((canonical, descending))

    # Parse the include parameter (a comma-separated list of relationship
    # paths) into the list of served entry types whose related resources are to
    # be returned under the top-level "included" field. When the parameter is
    # absent the default is 'references' (filtered to the served entry types);
    # an explicit empty string means include nothing; each listed path must be
    # a served entry type (only depth-1 paths are supported) or the request is
    # rejected with 400.
    if endpoint in schema.all_entries:
        if validated_request.query.include is None:
            validated_request.include_paths = [e for e in ('references',) if e in schema.all_entries]
        elif validated_request.query.include.strip() == "":
            validated_request.include_paths = []
        else:
            for token in validated_request.query.include.split(","):
                token = token.strip()
                if token == "":
                    continue
                if token not in schema.all_entries:
                    raise OptimadeError("Unrecognized relationship path in include: " + token, 400, "Bad request")
                # Defensive programming; don't trust '=='/in to be byte-for-byte
                # equivalent, so don't use the insecure string from the user.
                canonical = schema.all_entries[schema.all_entries.index(token)]
                validated_request.include_paths.append(canonical)

    if validated_request.version != version:
        raise OptimadeError("validate_optimade_request: unexpected version", 500, "Internal server error")

    return validated_request


def determine_optimade_version(request: RawRequest) -> str:
    if request.relurl is not None:
        relurl = request.relurl
    else:
        relurl = request.representation.partition('?')[0]

    endpoint = relurl.strip("/")

    potential_optimade_version, _sep, _rest = endpoint.partition('/')

    if (
        len(potential_optimade_version) >= 2
        and potential_optimade_version[0] == 'v'
        and potential_optimade_version[1] in "0123456789"
    ):
        if potential_optimade_version in optimade_supported_versions:
            return optimade_supported_versions[potential_optimade_version]
        else:
            raise OptimadeError(
                "Unsupported version requested. Supported versioned base URLs are: "
                + (", ".join(["/" + str(x) for x in optimade_supported_versions.keys()])),
                553,
                "Version Not Supported",
            )
    else:
        return optimade_default_version
