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
