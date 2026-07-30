class WebError(Exception):
    def __init__(self, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class NotFoundError(WebError):
    def __init__(self, message: str = "Not Found") -> None:
        super().__init__(message, status_code=404)


class FunctionInjectionError(WebError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=500)


class WidgetError(WebError):
    """Base error for a widget invocation with source-aware diagnostics."""

    phase = "widget"

    def __init__(
        self,
        message: str,
        *,
        source_path: object | None = None,
        line: int | None = None,
        column: int | None = None,
        snippet: str | None = None,
        widget_name: str | None = None,
        widget_id: str | None = None,
        correction: str | None = None,
    ) -> None:
        details: list[str] = [f"Widget {self.phase} error"]
        if source_path is not None:
            location = str(source_path)
            if line is not None:
                location += f":{line}"
                if column is not None:
                    location += f":{column}"
            details.append(location)
        if widget_name:
            details.append(f"widget={widget_name}")
        if widget_id:
            details.append(f"id={widget_id}")
        details.append(message)
        if snippet:
            details.append(f"source: {snippet}")
        if correction:
            details.append(f"Fix: {correction}")
        super().__init__("; ".join(details), status_code=500)


class WidgetParseError(WidgetError):
    phase = "parse"


class WidgetDiscoveryError(WidgetError):
    phase = "discovery"


class WidgetValidationError(WidgetError):
    phase = "validation"


class WidgetRenderingError(WidgetError):
    phase = "rendering"
