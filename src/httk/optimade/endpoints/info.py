from typing import Any

from ..model.config import OptimadeConfig
from ..model.request import ValidatedRequest
from ..model.versions import optimade_supported_versions
from ..schema.httk_entries import httk_all_entries, httk_entry_info
from .meta import generate_meta


def generate_info_endpoint_reply(request: ValidatedRequest, config: OptimadeConfig) -> dict[str, Any]:
    available_api_versions = []
    for ver in optimade_supported_versions:
        available_api_versions += [{'version': optimade_supported_versions[ver], 'url': request.baseurl + ver}]

    response = {
        "data": {
            "id": "/",
            "type": "info",
            "attributes": {
                "api_version": request.version,
                "available_api_versions": available_api_versions,
                "formats": [
                    "json",
                ],
                "entry_types_by_format": {
                    "json": list(httk_all_entries),
                },
                "available_endpoints": ["info", "links"] + list(httk_all_entries),
                "is_index": False,
            },
        },
        "meta": generate_meta(representation=request.representation, api_version=request.version, config=config),
    }
    return response


def generate_entry_info_endpoint_reply(request: ValidatedRequest, config: OptimadeConfig, entry: str) -> dict[str, Any]:
    return {
        "data": {
            "description": httk_entry_info[entry]["description"],
            "properties": {
                x: {
                    "description": httk_entry_info[entry]["properties"][x].get("description", ""),
                }
                for x in httk_entry_info[entry]["properties"]
            },
            "formats": ["json"],
            "output_fields_by_format": {
                "json": [x for x in httk_entry_info[entry]["properties"]],
            },
        },
        "meta": generate_meta(representation=request.representation, api_version=request.version, config=config),
    }


def generate_base_endpoint_reply(request: ValidatedRequest, config: OptimadeConfig) -> str:
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
    return """version
1
"""


def generate_links_endpoint_reply(request: ValidatedRequest, config: OptimadeConfig) -> dict[str, Any]:
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
            data_count=len(links),
            more_data_available=False,
        ),
    }
