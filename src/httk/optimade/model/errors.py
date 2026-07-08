class OptimadeError(Exception):
    def __init__(self, message: str, response_code: int, response_message: str, longmsg: str | None = None) -> None:
        super().__init__(message)
        self.response_code = response_code
        self.response_msg = response_message
        self.content = longmsg if longmsg is not None else message


class TranslatorError(OptimadeError):
    pass
