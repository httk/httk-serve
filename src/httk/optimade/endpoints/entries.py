from typing import Any
from urllib.parse import urlencode

from ..model.config import OptimadeConfig
from ..model.errors import OptimadeError
from ..model.request import ValidatedRequest
from ..model.results import QueryResults
from .meta import generate_meta


def generate_entry_endpoint_reply(
    request: ValidatedRequest, config: OptimadeConfig, data: QueryResults
) -> dict[str, Any]:
    ndata_returned = data.count()
    data_part = []
    for d in data:
        attributes = dict(d)
        del attributes['id']
        del attributes['type']
        data_part += [
            {
                'attributes': attributes,
                'id': d['id'],
                'type': d['type'],
            }
        ]

    links: dict[str, str | None]
    if data.more_data_available:
        query = request.query.as_query_dict()
        query['page_offset'] = str(request.query.page_offset + len(data_part))
        links = {"next": request.baseurl + request.endpoint + "?" + urlencode(query)}
    else:
        links = {"next": None}

    response = {
        "links": links,
        "data": data_part,
        "meta": generate_meta(
            representation=request.representation,
            api_version=request.version,
            config=config,
            data_count=ndata_returned,
            more_data_available=data.more_data_available,
            data_available=config.data_available[request.endpoint],
        ),
    }

    return response


def generate_single_entry_endpoint_reply(
    request: ValidatedRequest, config: OptimadeConfig, data: QueryResults
) -> dict[str, Any]:
    data_part = []
    for d in data:
        attributes = dict(d)
        del attributes['id']
        del attributes['type']
        data_part += [
            {
                'attributes': attributes,
                'id': d['id'],
                'type': d['type'],
            }
        ]

    single_data_part: dict[str, Any] | None
    if len(data_part) > 1:
        raise OptimadeError(
            "Unexpectedly received a data object with length > 1 for a single entry endpoint response",
            500,
            "Internal server error",
        )
    elif len(data_part) == 0:
        single_data_part = None
        ndata = 0
    else:
        single_data_part = data_part[0]
        ndata = 1

    if data.more_data_available:
        raise OptimadeError(
            "Unexpectedly received a data object with more data available for a single entry endpoint response",
            500,
            "Internal server error",
        )

    response = {
        "links": {"next": None},
        "data": single_data_part,
        "meta": generate_meta(
            representation=request.representation,
            api_version=request.version,
            config=config,
            data_count=ndata,
            more_data_available=False,
            data_available=config.data_available[request.endpoint],
        ),
    }

    return response
