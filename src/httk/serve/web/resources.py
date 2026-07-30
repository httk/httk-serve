"""Lifecycle management for resources owned by a site engine."""

from collections.abc import Callable
from threading import RLock
from types import TracebackType
from typing import Literal, Self

SITE_RESOURCES_KEY = "httk_serve_resources"
"""Stable ``global_data`` key through which site startup code gets resources."""


type CleanupCallback = Callable[[], None]


class SiteResources:
    """Callbacks owned by one :class:`httk.serve.web.engine.site_engine.SiteEngine`.

    Site ``functions/init.py`` code can obtain this object from
    ``global_data[SITE_RESOURCES_KEY]`` and call :meth:`register` for every
    persistent resource it opens.  Callbacks run in reverse registration order
    when the :class:`httk.serve.web.engine.site_engine.SiteEngine` closes.  Closing is
    idempotent, so ASGI lifespan shutdown and an enclosing helper may both
    safely close the same engine.
    """

    def __init__(self) -> None:
        self._callbacks: list[CleanupCallback] = []
        self._closed = False
        self._lock = RLock()

    @property
    def closed(self) -> bool:
        """Whether cleanup has started for this registry."""

        with self._lock:
            return self._closed

    def register(self, callback: CleanupCallback) -> None:
        """Register one synchronous callback to run during :meth:`close`.

        Registration after close is a programming error: the callback could no
        longer be guaranteed to run.
        """

        if not callable(callback):
            raise TypeError("Site resource cleanup callback must be callable")
        with self._lock:
            if self._closed:
                raise RuntimeError("Cannot register a site resource after the engine has closed")
            self._callbacks.append(callback)

    def close(self) -> None:
        """Run pending callbacks in LIFO order exactly once.

        Every callback gets a chance to run.  If cleanup fails, the first
        exception is re-raised after the remaining callbacks have executed;
        additional failures are attached as notes for diagnosis.
        """

        with self._lock:
            if self._closed:
                return
            self._closed = True
            callbacks = tuple(reversed(self._callbacks))
            self._callbacks.clear()

        errors: list[BaseException] = []
        for callback in callbacks:
            try:
                callback()
            except BaseException as exc:
                errors.append(exc)

        if errors:
            primary = errors[0]
            for error in errors[1:]:
                primary.add_note(f"Additional site resource cleanup failure: {error!r}")
            raise primary

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            self.close()
        except BaseException as cleanup_error:
            if exc_value is None:
                raise
            exc_value.add_note(f"Additional site resource cleanup failure: {cleanup_error!r}")
        return False
