from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RawRequest:
    """An incoming OPTIMADE request, as handed over by the web layer.

    Only ``baseurl`` and ``representation`` are mandatory; missing information
    is derived from ``representation`` during validation.
    """

    baseurl: str
    representation: str
    relurl: str | None = None
    querystr: str | None = None
    query: dict[str, str] | None = None
    endpoint: str | None = None
    request_id: str | None = None
    version: str | None = None


@dataclass(slots=True)
class RequestedSlice:
    """A slice requested via the ``dimension_slices`` query parameter.

    The values are stored exactly as given (a ``None`` component means the
    client omitted it and the default applies). Per the OPTIMADE specification,
    ``stop`` is *inclusive*.
    """

    start: int | None = None
    stop: int | None = None
    step: int | None = None


@dataclass(slots=True)
class ValidatedParameters:
    """Validated URL query parameters of an OPTIMADE request."""

    response_format: str = 'json'
    page_limit: int = 50
    page_offset: int = 0
    response_fields: str | None = None
    filter: str | None = None
    sort: str | None = None
    include: str | None = None
    dimension_slices: dict[str, RequestedSlice] = field(default_factory=dict)

    def as_query_dict(self) -> dict[str, str]:
        """Return the parameters as a URL query dict, omitting unset values."""
        query: dict[str, str] = {
            'response_format': self.response_format,
            'page_limit': str(self.page_limit),
            'page_offset': str(self.page_offset),
        }
        if self.response_fields is not None:
            query['response_fields'] = self.response_fields
        if self.filter is not None:
            query['filter'] = self.filter
        if self.sort is not None:
            query['sort'] = self.sort
        if self.include is not None:
            query['include'] = self.include
        if self.dimension_slices:
            parts = []
            for name, requested in self.dimension_slices.items():
                start = "" if requested.start is None else str(requested.start)
                stop = "" if requested.stop is None else str(requested.stop)
                step = "" if requested.step is None else str(requested.step)
                parts.append(f"{name}[{start}:{stop}:{step}]")
            query['dimension_slices'] = ",".join(parts)
        return query


@dataclass(slots=True)
class ValidatedRequest:
    """The result of validating a :class:`RawRequest`."""

    baseurl: str
    representation: str
    endpoint: str
    version: str
    query: ValidatedParameters
    url_version: str | None = None
    request_id: str | None = None
    recognized_response_fields: list[str] = field(default_factory=list)
    unrecognized_response_fields: list[str] = field(default_factory=list)
    sort_fields: list[tuple[str, bool]] = field(default_factory=list)
    include_paths: list[str] = field(default_factory=list)
    property_metadata_requested: bool = False
    partial_data_parts: tuple[str, str, str] | None = None
    partial_data_offset: int = 0


@dataclass(slots=True)
class EndpointResponse:
    """A response produced by an endpoint, to be serialized by the web layer.

    Either ``json_response`` (a JSON:API document) or ``content`` (a raw body)
    is set.
    """

    response_code: int = 200
    response_msg: str = 'OK'
    content_type: str = 'application/vnd.api+json'
    encoding: str = 'utf-8'
    content: str | None = None
    json_response: dict[str, Any] | None = None
