"""Generate OPTIMADE discovery and provider-link responses."""

from typing import Any

from ..model.config import OptimadeConfig, OptimadeIndexConfig
from ..model.request import ValidatedRequest
from ..model.versions import optimade_supported_versions
from ..schema.served import ServedSchema
from .meta import generate_meta


def _unversioned_baseurl(request: ValidatedRequest) -> str:
    """Return the API base used by the ``available_api_versions`` links."""
    if request.url_version is None:
        return request.baseurl
    return request.baseurl[: -len(request.url_version) - 1]


def generate_info_endpoint_reply(
    request: ValidatedRequest, config: OptimadeConfig, schema: ServedSchema
) -> dict[str, Any]:
    """Build the service ``/info`` response.

    :param request: Validated request supplying the API version and base URL.
    :param config: Service metadata and license configuration.
    :param schema: Served entry types and properties.
    :return: JSON:API service-info document.
    """
    baseurl = _unversioned_baseurl(request)
    available_api_versions = []
    for ver in optimade_supported_versions:
        available_api_versions += [{'version': optimade_supported_versions[ver], 'url': baseurl + ver}]

    index_config = config if isinstance(config, OptimadeIndexConfig) else None
    is_index = index_config is not None
    attributes: dict[str, Any] = {
        "api_version": request.version,
        "available_api_versions": available_api_versions,
        "formats": [
            "json",
        ],
        "entry_types_by_format": {
            "json": list(schema.all_entries),
        },
        "available_endpoints": ["info", "links"] + list(schema.all_entries) + list(schema.revision_endpoints),
        "is_index": is_index,
    }
    if config.license is not None:
        attributes["license"] = config.license
    if config.available_licenses is not None:
        attributes["available_licenses"] = config.available_licenses
    if config.available_licenses_for_entries is not None:
        attributes["available_licenses_for_entries"] = config.available_licenses_for_entries

    data: dict[str, Any] = {
        "id": "/",
        "type": "info",
        "attributes": attributes,
    }
    if is_index:
        default_data: dict[str, str] | None = None
        if index_config is not None and index_config.default_link_id is not None:
            default_data = {"type": "links", "id": index_config.default_link_id}
        data["relationships"] = {"default": {"data": default_data}}

    response = {
        "data": {
            **data,
        },
        "meta": generate_meta(
            representation=request.representation,
            api_version=request.version,
            config=config,
            warnings=request.warnings or None,
        ),
    }
    return response


def generate_entry_info_endpoint_reply(
    request: ValidatedRequest, config: OptimadeConfig, entry: str, schema: ServedSchema
) -> dict[str, Any]:
    """Build the ``/info/{entry}`` response for one served entry type.

    :param request: Validated request supplying response metadata context.
    :param config: Service metadata configuration.
    :param entry: Served entry endpoint name.
    :param schema: Served entry definitions.
    :return: JSON:API entry-info document.
    """
    response: dict[str, Any] = {
        "data": {
            "id": entry,
            "type": "info",
            "description": schema.entry_info[entry]["description"],
            "properties": schema.property_definitions[entry],
            "formats": ["json"],
            "output_fields_by_format": {
                "json": list(schema.properties_by_entry[entry]),
            },
        },
        "meta": generate_meta(
            representation=request.representation,
            api_version=request.version,
            config=config,
            warnings=request.warnings or None,
        ),
    }
    definition_id = schema.entry_definition_ids.get(entry)
    if definition_id is not None:
        response["links"] = {"describedby": definition_id}
    return response


def generate_base_endpoint_reply(request: ValidatedRequest, config: OptimadeConfig) -> str:
    """Build the HTML response for the unversioned API base endpoint.

    :param request: Validated request supplying the displayed API version.
    :param config: Service configuration.
    :return: HTML response body.
    """
    return (
        """<!DOCTYPE html>
<html lang="en">
    <head>
        <title>Optimate Endpoint</title>
        <meta charset="UTF-8">
    </head>
    <body>
        <p>This is an <a href="https://www.optimade.org">OPTIMADE</a> base URL which can be queried with an OPTIMADE client.</p>
        <p>OPTIMADE version:"""
        + request.version
        + """</p>
    </body>
</html>
"""
    )


def generate_versions_endpoint_reply(request: ValidatedRequest, config: OptimadeConfig) -> str:
    """Build the preference-ordered CSV of supported API major versions.

    :param request: Validated request context.
    :param config: Service configuration.
    :return: Restricted ``/versions`` CSV body.
    """
    return """version
1
"""


def generate_links_endpoint_reply(request: ValidatedRequest, config: OptimadeConfig) -> dict[str, Any]:
    """Build the provider-links response.

    :param request: Validated request supplying response metadata context.
    :param config: Service links and metadata configuration.
    :return: JSON:API links document.
    """
    links = config.links
    return {
        "data": [
            {
                "type": "links",
                "id": x["id"],
                "attributes": {y: x[y] for y in x if y != "id"},
            }
            for x in links
        ]
        + [
            {
                "type": "links",
                "id": "optimade",
                "attributes": {
                    "name": "Materials Consortia",
                    "description": "List of OPTIMADE providers maintained by the Materials Consortia organisation",
                    "base_url": "https://providers.optimade.org",
                    "homepage": "https://optimade.org",
                    "link_type": "providers",
                },
            }
        ],
        "meta": generate_meta(
            representation=request.representation,
            api_version=request.version,
            config=config,
            data_returned=len(links) + 1,
            more_data_available=False,
            warnings=request.warnings or None,
        ),
    }
