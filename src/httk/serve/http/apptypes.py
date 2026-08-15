"""Branded httk-serve app/callback types so API implementations avoid importing Starlette directly."""

from collections.abc import Awaitable, Callable

from starlette.applications import Starlette

type ServeApp = Starlette
"""The httk-serve serving application type (a Starlette app; branded so consumers need not import Starlette).

Annotation-only: this is a type alias, not the ``Starlette`` class, so use ``Starlette``
itself for ``isinstance``/subclass checks.
"""

type ResponseHook = Callable[[], Awaitable[None]]
"""A zero-argument coroutine callback run once, after the response has been sent."""

__all__ = ["ResponseHook", "ServeApp"]
