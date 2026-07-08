"""The partial data endpoint, streaming large property values as JSON Lines.

See the OPTIMADE specification appendix "OPTIMADE JSON Lines partial data
format". A backend exposes a large or sliceable property value as a
:class:`~httk.optimade.backend.partial.PartialValue`; this endpoint fetches it
in chunks of ``config.partial_data_chunk_size`` outer items and emits the
JSON Lines document, with a next-marker when more data remains.
"""

import json
from typing import Any
from urllib.parse import quote

from ..backend.partial import PartialValue
from ..filter.parser import FilterAst
from ..model.config import OptimadeConfig
from ..model.errors import OptimadeError
from ..model.request import EndpointResponse, ValidatedRequest
from ..model.results import QueryFunction
from ..schema.served import ServedSchema


def generate_partial_data_reply(
    request: ValidatedRequest,
    config: OptimadeConfig,
    query_function: QueryFunction,
    schema: ServedSchema,
) -> EndpointResponse:
    """Produce the JSON Lines partial data reply for a single property value."""
    assert request.partial_data_parts is not None
    entry, entry_id, prop = request.partial_data_parts

    filter_ast: FilterAst = ('=', ('Identifier', 'id'), ('String', entry_id))
    results = query_function([entry], ['id', 'type', prop], [], 1, 0, filter_ast)

    row = None
    for result in results:
        row = result
        break
    if row is None:
        raise OptimadeError("Entry not found.", 404, "Not Found")

    value = row.values.get(prop)
    if not isinstance(value, PartialValue):
        raise OptimadeError("Property is not available as partial data.", 404, "Not Found")

    outer_length = value.dimensions[0].length
    if outer_length is None:
        raise OptimadeError("Partial data length is not available for this property.", 501, "Not implemented")

    offset = request.partial_data_offset
    chunk = config.partial_data_chunk_size
    remaining_slices = tuple(slice(None) for _ in value.dimensions[1:])
    items = value.fetch((slice(offset, offset + chunk),) + remaining_slices)

    header: dict[str, Any] = {
        "optimade-partial-data": {"format": "1.2"},
        "layout": "dense",
        "property_name": prop,
        "entry": {"id": entry_id, "type": entry},
        "has_references": False,
    }
    if items:
        # returned_ranges is RECOMMENDED and cannot be expressed for an empty
        # chunk (an offset at or beyond the data produces no items).
        header["returned_ranges"] = [{"start": offset, "stop": offset + len(items) - 1, "step": 1}]

    lines = [json.dumps(header)]
    for item in items:
        lines.append(json.dumps(item))

    next_offset = offset + len(items)
    if next_offset < outer_length:
        quoted = f"partial_data/{quote(entry, safe='')}/{quote(entry_id, safe='')}/{quote(prop, safe='')}"
        next_link = request.baseurl + quoted + f"?offset={next_offset}"
        lines.append(json.dumps(["PARTIAL-DATA-NEXT", [next_link]]))
    else:
        lines.append(json.dumps(["PARTIAL-DATA-END", [""]]))

    content = "\n".join(lines) + "\n"
    return EndpointResponse(content=content, content_type='application/jsonlines')
