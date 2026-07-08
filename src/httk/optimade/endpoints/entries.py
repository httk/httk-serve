from typing import Any, Callable
from urllib.parse import urlencode

from ..model.config import OptimadeConfig
from ..model.errors import OptimadeError
from ..model.request import ValidatedRequest
from ..model.results import QueryResults, ResultRow
from .meta import generate_meta

RelatedResolver = Callable[[dict[str, set[str]]], list[dict[str, Any]]]


def _relationships_block(relationships: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Build the JSON:API ``relationships`` object for a resource.

    Relationships are grouped by related entry type; each resource identifier
    object carries a ``meta`` dictionary with the ``description`` and ``role``
    keys when present.
    """
    block: dict[str, Any] = {}
    for etype, rels in relationships.items():
        data = []
        for rel in rels:
            identifier: dict[str, Any] = {"type": etype, "id": rel["id"]}
            meta = {k: rel[k] for k in ("description", "role") if rel.get(k) is not None}
            if meta:
                identifier["meta"] = meta
            data.append(identifier)
        block[etype] = {"data": data}
    return block


def _resource_object(row: ResultRow) -> dict[str, Any]:
    """Build a JSON:API resource object (attributes/id/type/relationships) from a row."""
    attributes = dict(row.values)
    row_id = attributes.pop('id')
    row_type = attributes.pop('type')
    obj: dict[str, Any] = {
        'attributes': attributes,
        'id': row_id,
        'type': row_type,
    }
    if row.relationships:
        obj['relationships'] = _relationships_block(row.relationships)
    meta: dict[str, Any] = {}
    if row.property_metadata:
        meta['property_metadata'] = dict(row.property_metadata)
    if meta:
        obj['meta'] = meta
    return obj


def generate_entry_endpoint_reply(
    request: ValidatedRequest,
    config: OptimadeConfig,
    data: QueryResults,
    related_resolver: RelatedResolver | None = None,
) -> dict[str, Any]:
    ndata_returned = data.count()
    data_part = []
    collected: dict[str, set[str]] = {}
    for row in data:
        data_part += [_resource_object(row)]
        for etype, rels in row.relationships.items():
            if etype in request.include_paths:
                collected.setdefault(etype, set()).update(rel["id"] for rel in rels)

    links: dict[str, str | None]
    if data.more_data_available:
        query = request.query.as_query_dict()
        query['page_offset'] = str(request.query.page_offset + len(data_part))
        links = {"next": request.baseurl + request.endpoint + "?" + urlencode(query)}
    else:
        links = {"next": None}

    response: dict[str, Any] = {
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

    if related_resolver is not None and collected:
        response["included"] = related_resolver(collected)

    return response


def generate_single_entry_endpoint_reply(
    request: ValidatedRequest,
    config: OptimadeConfig,
    data: QueryResults,
    related_resolver: RelatedResolver | None = None,
) -> dict[str, Any]:
    data_part = []
    collected: dict[str, set[str]] = {}
    for row in data:
        data_part += [_resource_object(row)]
        for etype, rels in row.relationships.items():
            if etype in request.include_paths:
                collected.setdefault(etype, set()).update(rel["id"] for rel in rels)

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

    response: dict[str, Any] = {
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

    if related_resolver is not None and collected:
        response["included"] = related_resolver(collected)

    return response
