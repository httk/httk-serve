from dataclasses import dataclass, field


@dataclass(frozen=True)
class HttpRequestContext:
    method: str = "GET"
    query: dict[str, str] = field(default_factory=dict)
    postvars: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
