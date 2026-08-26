"""Validate OPTIMADE paths, versions, and query parameters."""

import re
from urllib.parse import parse_qsl, urlparse

from ..model.errors import OptimadeError
from ..model.request import (
    RawRequest,
    RequestedSlice,
    ValidatedParameters,
    ValidatedRequest,
)
from ..model.versions import optimade_default_version, optimade_supported_versions
from ..schema.served import ServedSchema

# The filter lexer is quadratic in input length (16 KB ~ 6 s of CPU) and runs
# synchronously on the ASGI event loop, so one oversized filter would block all
# clients. Raising this bound therefore REQUIRES a lexer performance fix first.
FILTER_LENGTH_MAX = 4096

# A single dimension_slices specification: a dimension name, then '[', the
# start/stop/step components (optional non-negative integers) separated by two
# mandatory colons, then ']'.
_DIMENSION_SLICE_RE = re.compile(r'^([^\[\]:]+)\[(\d*):(\d*):(\d*)\]$')
_CANONICAL_NONNEGATIVE_INTEGER_RE = re.compile(r'[0-9]+')
_CANONICAL_POSITIVE_INTEGER_RE = re.compile(r'[1-9][0-9]*')


def _is_printable_ascii(value: str) -> bool:
    """Return whether ``value`` is a non-empty printable ASCII identifier."""
    return len(value) > 0 and all(ord(char) >= 32 and ord(char) <= 126 for char in value)


def _versioned_baseurl(baseurl: str, url_version: str | None) -> str:
    """Return the URL base from which links for this request are generated."""
    if url_version is None:
        return baseurl
    return baseurl.rstrip("/") + "/" + url_version + "/"


def _parse_dimension_slices(value: str) -> dict[str, RequestedSlice]:
    """Parse the ``dimension_slices`` query parameter into requested slices."""
    result: dict[str, RequestedSlice] = {}
    for part in value.split(","):
        part = part.strip()
        if part == "":
            continue
        match = _DIMENSION_SLICE_RE.match(part)
        if match is None:
            raise OptimadeError("Malformed dimension_slices specification: " + part, 400, "Bad request")
        name, start_str, stop_str, step_str = match.groups()
        start = int(start_str) if start_str != "" else None
        stop = int(stop_str) if stop_str != "" else None
        step = int(step_str) if step_str != "" else None
        if step == 0:
            raise OptimadeError("dimension_slices step must be a non-zero positive integer.", 400, "Bad request")
        result[name] = RequestedSlice(start=start, stop=stop, step=step)
    return result


def _validate_query(
    endpoint: str, query: dict[str, str], schema: ServedSchema, page_limit_max: int
) -> ValidatedParameters:
    validated_parameters = ValidatedParameters()

    if ('response_format' in query and query['response_format'] is not None) and query['response_format'] != 'json':
        raise OptimadeError("Requested response_format not supported.", 400, "Bad request")

    if 'page_limit' in query and query['page_limit'] is not None:
        try:
            validated_parameters.page_limit = int(query['page_limit'])
        except ValueError:
            raise OptimadeError("Cannot interprete page_limit.", 400, "Bad request")
        if validated_parameters.page_limit < 0:
            # A negative limit must not reach the backend: the execution layer
            # interprets negative limits as "no bound", which would bypass the cap.
            raise OptimadeError("page_limit must be a non-negative integer.", 400, "Bad request")
        if validated_parameters.page_limit > page_limit_max:
            # OPTIMADE: the service MAY cap page_limit and MUST answer a larger
            # request with 403 Forbidden rather than silently clamping.
            raise OptimadeError(f"page_limit larger than the maximum supported ({page_limit_max}).", 403, "Forbidden")

    if 'page_offset' in query and query['page_offset'] is not None:
        try:
            validated_parameters.page_offset = int(query['page_offset'])
        except ValueError:
            raise OptimadeError("Cannot interprete page_offset.", 400, "Bad request")
        validated_parameters.page_offset = max(validated_parameters.page_offset, 0)

    if 'response_fields' in query and query['response_fields'] is not None:
        validated_response_fields = []
        response_fields = [x.strip() for x in query['response_fields'].split(",")]
        if endpoint in schema.properties_by_entry:
            for response_field in response_fields:
                if response_field == 'property_metadata':
                    # A reserved token requesting per-property metadata rather
                    # than an attribute; handled in validate_optimade_request.
                    continue
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
        if len(query['filter']) > FILTER_LENGTH_MAX:
            raise OptimadeError(
                "The filter exceeds the maximum supported length (4096 characters).", 400, "Bad request"
            )
        validated_parameters.filter = query['filter']

    # The sort parameter is only meaningful for entry endpoints; on other
    # endpoints it is ignored. The raw string is stored here; parsing into
    # (property, descending) pairs happens in validate_optimade_request.
    if 'sort' in query and query['sort'] is not None and endpoint in schema.all_entries + schema.revision_endpoints:
        validated_parameters.sort = query['sort']

    # The include parameter is only meaningful for entry endpoints. The raw
    # string is stored here only when the client explicitly provides it (so
    # next-links reproduce the user's intent); parsing into the list of served
    # entry types happens in validate_optimade_request.
    if 'include' in query and query['include'] is not None and endpoint in schema.all_entries:
        validated_parameters.include = query['include']

    if '_httk_as_of' in query and query['_httk_as_of'] is not None:
        value = query['_httk_as_of']
        if _CANONICAL_NONNEGATIVE_INTEGER_RE.fullmatch(value) is None or (len(value) > 1 and value.startswith('0')):
            raise OptimadeError("_httk_as_of must be a non-negative integer.", 400, "Bad request")
        validated_parameters.as_of = int(value)

    return validated_parameters


def validate_optimade_request(
    request: RawRequest, version: str, schema: ServedSchema, page_limit_max: int = 50
) -> ValidatedRequest:
    """Validate a raw request against an explicit served schema.

    :param request: Raw request supplied by the web layer.
    :param version: API version selected for the request.
    :param schema: Served entry and property schema.
    :param page_limit_max: Largest accepted ``page_limit``; larger requests raise 403.
    :return: Canonical request and validated query parameters.
    :raises httk.serve.optimade.model.errors.OptimadeError: If the path, version, or query is invalid.
    """
    endpoint = request.endpoint
    request_id = request.request_id
    revisions = False
    request_immutable_id: str | None = None
    endpoint_path: str | None = None
    validated_version = request.version if request.version is not None else optimade_default_version
    url_version: str | None = None
    partial_data_parts: tuple[str, str, str] | None = None

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
                + (", ".join(["/" + str(x) for x in optimade_supported_versions])),
                553,
                "Bad request",
            )

    if endpoint is None:
        parts = endpoint_str.split('/')
        first_level_endpoint = parts[0]

        # First check fixed endpoints
        if endpoint_str in schema.valid_endpoints:
            # Defensive programming; don't trust '=='/in to be byte-for-byte equivalent,
            # so don't use the insecure string from the user
            endpoint = schema.valid_endpoints[schema.valid_endpoints.index(endpoint_str)]

        # Then check "entries" endpoint with a request_id
        elif first_level_endpoint in schema.revision_endpoints:
            if len(parts) == 2 and _is_printable_ascii(parts[1]):
                endpoint = schema.revision_endpoints[schema.revision_endpoints.index(first_level_endpoint)]
                revisions = True
                request_immutable_id = parts[1]
            else:
                raise OptimadeError("Request for non-existing endpoint.", 404, "Not Found")

        # Then check base entry and stored-revision paths.
        elif first_level_endpoint in schema.all_entries:
            # Defensive programming; don't trust '=='/in to be byte-for-byte equivalent,
            # so don't use the insecure string from the user
            endpoint = schema.valid_endpoints[schema.valid_endpoints.index(first_level_endpoint)]
            if len(parts) == 1:
                request_id = None
            elif len(parts) == 2 and _is_printable_ascii(parts[1]):
                request_id = parts[1]
            elif (
                endpoint in schema.revision_base.values()
                and len(parts) in (3, 4)
                and parts[2] == "_httk_revs"
                and _is_printable_ascii(parts[1])
                and (len(parts) == 3 or parts[3] == "" or _is_printable_ascii(parts[3]))
            ):
                revision_endpoint = f"_httk_{endpoint}~revs"
                endpoint = revision_endpoint
                revisions = True
                request_id = parts[1]
                endpoint_path = "/".join(parts[:3])
                if len(parts) == 4 and parts[3] != "":
                    if _CANONICAL_POSITIVE_INTEGER_RE.fullmatch(parts[3]) is None:
                        raise OptimadeError("Request for non-existing endpoint.", 404, "Not Found")
                    request_immutable_id = request_id + "~" + parts[3]
            elif len(parts) > 1 and not _is_printable_ascii(parts[1]):
                raise OptimadeError("Unexpected characters in entry id.", 400, "Bad request")
            else:
                raise OptimadeError("Request for non-existing endpoint.", 404, "Not Found")

        # Then check the partial data endpoint: partial_data/<entry>/<id>/<property>
        elif first_level_endpoint == 'partial_data':
            parts = endpoint_str.split('/')
            if len(parts) != 4:
                raise OptimadeError("Request for non-existing endpoint.", 404, "Not Found")
            _partial, pd_entry, pd_id, pd_property = parts
            if pd_entry not in schema.all_entries + schema.revision_endpoints:
                raise OptimadeError("Request for non-existing endpoint.", 404, "Not Found")
            # Defensive programming; don't trust '=='/in to be byte-for-byte
            # equivalent, so don't use the insecure string from the user.
            valid_entries = schema.all_entries + schema.revision_endpoints
            pd_entry = valid_entries[valid_entries.index(pd_entry)]
            if len(pd_id) == 0 or not all(ord(c) >= 32 and ord(c) <= 126 for c in pd_id):
                raise OptimadeError("Unexpected characters in entry id.", 400, "Bad request")
            if pd_property not in schema.properties_by_entry[pd_entry]:
                raise OptimadeError("Request for non-existing endpoint.", 404, "Not Found")
            valid_properties = schema.properties_by_entry[pd_entry]
            pd_property = valid_properties[valid_properties.index(pd_property)]
            endpoint = 'partial_data'
            request_id = None
            partial_data_parts = (pd_entry, pd_id, pd_property)

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

    if endpoint in schema.revision_endpoints:
        revisions = True

    if request.query is not None:
        query = request.query
    else:
        if request.querystr is not None:
            querystr = request.querystr
        else:
            querystr = urlparse(request.representation).query
        query = dict(parse_qsl(querystr, keep_blank_values=True))

    validated_request = ValidatedRequest(
        baseurl=_versioned_baseurl(request.baseurl, url_version),
        representation=request.representation,
        endpoint=endpoint,
        version=validated_version,
        url_version=url_version,
        request_id=request_id,
        revisions=revisions,
        request_immutable_id=request_immutable_id,
        endpoint_path=endpoint_path,
        query=_validate_query(endpoint, query, schema, page_limit_max),
    )

    if 'response_fields' in query and query['response_fields'] is not None:
        response_fields = [x.strip() for x in query['response_fields'].split(",")]
        if endpoint in schema.properties_by_entry:
            for response_field in response_fields:
                if response_field == 'property_metadata':
                    # A reserved token requesting per-property metadata; it must
                    # not be treated as an attribute, nor rejected as unknown.
                    validated_request.property_metadata_requested = True
                    continue
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
                    validated_request.warnings.append(
                        {
                            "type": "warning",
                            "title": "Unrecognized response field",
                            "detail": (
                                "Unknown provider-specific response field: "
                                + response_field
                                + ". It is served as null."
                            ),
                        }
                    )
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

    # The partial data endpoint carries the target entry/id/property in the URL
    # path and an optional non-negative ``offset`` query parameter.
    if endpoint == 'partial_data':
        validated_request.partial_data_parts = partial_data_parts
        if 'offset' in query and query['offset'] is not None and query['offset'] != "":
            try:
                offset = int(query['offset'])
            except ValueError:
                raise OptimadeError("Cannot interprete offset.", 400, "Bad request")
            if offset < 0:
                raise OptimadeError("offset must be a non-negative integer.", 400, "Bad request")
            validated_request.partial_data_offset = offset

    # The dimension_slices parameter is only supported on single-entry endpoints.
    if (
        'dimension_slices' in query
        and query['dimension_slices'] is not None
        and query['dimension_slices'].strip() != ""
        and endpoint in schema.all_entries + schema.revision_endpoints
    ):
        if validated_request.request_id is None:
            raise OptimadeError("dimension_slices is only supported on single-entry endpoints.", 400, "Bad request")
        validated_request.query.dimension_slices = _parse_dimension_slices(query['dimension_slices'])

    if validated_request.version != version:
        raise OptimadeError("validate_optimade_request: unexpected version", 500, "Internal server error")

    return validated_request


def determine_optimade_version(request: RawRequest) -> str:
    """Determine the OPTIMADE version named by a request path.

    :param request: Raw request whose relative path is inspected.
    :return: Supported API version, using the default when unversioned.
    :raises httk.serve.optimade.model.errors.OptimadeError: If the path names an unsupported version.
    """
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
                + (", ".join(["/" + str(x) for x in optimade_supported_versions])),
                553,
                "Version Not Supported",
            )
    else:
        return optimade_default_version
