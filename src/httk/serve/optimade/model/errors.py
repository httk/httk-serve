from httk.data.optimade_query import FilterTranslationError


class OptimadeError(Exception):
    def __init__(self, message: str, response_code: int, response_message: str, longmsg: str | None = None) -> None:
        super().__init__(message)
        self.response_code = response_code
        self.response_msg = response_message
        self.content = longmsg if longmsg is not None else message


class TranslatorError(OptimadeError):
    pass


# HTTP status and title for each neutral filter-translation failure category
# reported by httk.data.optimade_query.
_CATEGORY_STATUS: dict[str, tuple[int, str]] = {
    "unrecognized-property": (400, "Bad request"),
    "type-mismatch": (400, "Bad request"),
    "not-implemented": (501, "Not implemented"),
    "internal": (500, "Internal server error."),
}


def translator_error_from(error: FilterTranslationError) -> TranslatorError:
    """Wrap a neutral :class:`~httk.data.optimade_query.FilterTranslationError`
    into a :class:`TranslatorError` carrying the corresponding HTTP status."""
    response_code, response_message = _CATEGORY_STATUS.get(error.category, _CATEGORY_STATUS["internal"])
    return TranslatorError(str(error), response_code, response_message, longmsg=error.detail)
