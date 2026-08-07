"""Define the request data passed into site rendering."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HttpRequestContext:
    """Carry the request values exposed to a rendered site page.

    :param method: HTTP method.
    :param query: Query-string values.
    :param postvars: Parsed POST values.
    :param headers: Lower-case request headers.
    """

    method: str = "GET"
    query: dict[str, str] = field(default_factory=dict)
    postvars: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
