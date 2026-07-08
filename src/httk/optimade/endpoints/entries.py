from typing import Any, Callable
from urllib.parse import urlencode

from ..backend.partial import PartialDimension, PartialValue
from ..model.config import OptimadeConfig
from ..model.errors import OptimadeError
from ..model.request import RequestedSlice, ValidatedRequest
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


def _list_axes(
    dimensions: tuple[PartialDimension, ...],
    sliced: dict[str, RequestedSlice] | None = None,
) -> list[dict[str, Any]]:
    """Build the ``list_axes`` metadata for a partial (sliceable) property.

    One entry per declared list axis, echoing the raw requested slice for any
    axis named in ``sliced``.
    """
    axes: list[dict[str, Any]] = []
    for dimension in dimensions:
        axis: dict[str, Any] = {"dimension_name": dimension.name}
        if dimension.length is not None:
            axis["length"] = dimension.length
        axis["sliceable"] = dimension.sliceable
        if sliced is not None and dimension.name in sliced:
            requested = sliced[dimension.name]
            requested_slice: dict[str, int] = {}
            if requested.start is not None:
                requested_slice["start"] = requested.start
            if requested.stop is not None:
                requested_slice["stop"] = requested.stop
            if requested.step is not None:
                requested_slice["step"] = requested.step
            axis["requested_slice"] = requested_slice
        axes.append(axis)
    return axes


def _resolve_partial_value(
    prop: str,
    value: PartialValue,
    baseurl: str,
    row_type: str,
    row_id: str,
    dimension_slices: dict[str, RequestedSlice] | None,
) -> tuple[Any, list[dict[str, Any]] | None, dict[str, Any]]:
    """Turn a :class:`PartialValue` attribute into (value, links, metadata).

    Without a matching slice request the value is omitted (``null``) and a
    partial-data link is returned. When the request slices one or more of the
    property's axes the value is fetched inline (honouring the *inclusive* stop
    convention) and no link is returned; a 501 is raised if a requested axis is
    not sliceable.
    """
    dimensions = value.dimensions
    sliced: dict[str, RequestedSlice] = {}
    if dimension_slices:
        for dimension in dimensions:
            if dimension.name in dimension_slices:
                sliced[dimension.name] = dimension_slices[dimension.name]

    if sliced:
        slices: list[slice] = []
        for dimension in dimensions:
            if dimension.name in sliced:
                if not dimension.sliceable:
                    raise OptimadeError(
                        "Slicing is not supported for dimension: " + dimension.name, 501, "Not implemented"
                    )
                requested = sliced[dimension.name]
                start = requested.start if requested.start is not None else 0
                # The requested stop is inclusive; Python slice stop is exclusive.
                stop = (requested.stop + 1) if requested.stop is not None else dimension.length
                step = requested.step if requested.step is not None else 1
                slices.append(slice(start, stop, step))
            else:
                slices.append(slice(None))
        data = value.fetch(tuple(slices))
        return data, None, {"list_axes": _list_axes(dimensions, sliced)}

    links = [{"format": "jsonlines", "link": baseurl + f"partial_data/{row_type}/{row_id}/{prop}"}]
    return None, links, {"list_axes": _list_axes(dimensions)}


def _resource_object(
    row: ResultRow,
    baseurl: str = "",
    dimension_slices: dict[str, RequestedSlice] | None = None,
) -> dict[str, Any]:
    """Build a JSON:API resource object (attributes/id/type/relationships) from a row."""
    row_id = row.values['id']
    row_type = row.values['type']
    attributes: dict[str, Any] = {}
    property_metadata: dict[str, Any] = dict(row.property_metadata)
    partial_data_links: dict[str, Any] = {}
    for key, value in row.values.items():
        if key in ('id', 'type'):
            continue
        if isinstance(value, PartialValue):
            attributes[key], links, axes_meta = _resolve_partial_value(
                key, value, baseurl, row_type, row_id, dimension_slices
            )
            if links is not None:
                partial_data_links[key] = links
            existing = property_metadata.get(key)
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(axes_meta)
            property_metadata[key] = merged
        else:
            attributes[key] = value

    obj: dict[str, Any] = {
        'attributes': attributes,
        'id': row_id,
        'type': row_type,
    }
    if row.relationships:
        obj['relationships'] = _relationships_block(row.relationships)
    meta: dict[str, Any] = {}
    if property_metadata:
        meta['property_metadata'] = property_metadata
    if partial_data_links:
        meta['partial_data_links'] = partial_data_links
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
        data_part += [_resource_object(row, request.baseurl)]
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
    if config.schema_url is not None:
        links["describedby"] = config.schema_url

    last_id = data_part[-1]['id'] if data_part else None

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
            warnings=request.warnings or None,
            last_id=last_id,
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
        data_part += [_resource_object(row, request.baseurl, request.query.dimension_slices)]
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

    links: dict[str, str | None] = {"next": None}
    if config.schema_url is not None:
        links["describedby"] = config.schema_url

    response: dict[str, Any] = {
        "links": links,
        "data": single_data_part,
        "meta": generate_meta(
            representation=request.representation,
            api_version=request.version,
            config=config,
            data_count=ndata,
            more_data_available=False,
            data_available=config.data_available[request.endpoint],
            warnings=request.warnings or None,
        ),
    }

    if related_resolver is not None and collected:
        response["included"] = related_resolver(collected)

    return response
