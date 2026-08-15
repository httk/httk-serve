"""Tests for the generic hardened webhook transport and delivery helpers."""

import asyncio
import socket
from collections.abc import Awaitable, Callable
from typing import cast

import pytest

from httk.serve.http.webhook import (
    PinnedHttpsJsonPoster,
    WebhookTransportError,
    _ResolvedAddress,
    _WebhookTarget,
    deliver_with_retries,
    join_url_path,
)
from httk.serve.jsondata import JsonValue


class _Writer:
    """Provide the minimal stream-writer API used by the transport seam."""

    def write(self, _data: bytes) -> None:
        """Accept one request payload."""

    async def drain(self) -> None:
        """Simulate an immediately writable socket."""

    def close(self) -> None:
        """Simulate socket close."""

    async def wait_closed(self) -> None:
        """Simulate immediate socket shutdown."""


def _no_content_reader() -> asyncio.StreamReader:
    """Build a reader carrying one empty 204 response."""
    reader = asyncio.StreamReader()
    reader.feed_data(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
    reader.feed_eof()
    return reader


async def _resolver(_target: _WebhookTarget, _timeout: float) -> tuple[_ResolvedAddress, ...]:
    """Return one loopback address without applying the global-address filter."""
    return (_ResolvedAddress(socket.AF_INET, ("127.0.0.1", 443)),)


async def _connector(
    _target: _WebhookTarget,
    _address: _ResolvedAddress,
    _timeout: float,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Return a canned 204 response for any dialed address."""
    return _no_content_reader(), cast("asyncio.StreamWriter", _Writer())


def _patch_getaddrinfo(monkeypatch: pytest.MonkeyPatch, *hosts: str) -> None:
    """Replace the running loop's DNS resolution with a fixed IPv4 answer set.

    :param monkeypatch: Fixture used to shadow the loop's ``getaddrinfo`` method.
    :param \\*hosts: Literal IPv4 addresses the stubbed resolution should return.
    """
    answers: list[tuple[int, int, int, str, tuple[str, int]]] = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 443)) for host in hosts
    ]

    async def fake_getaddrinfo(
        _host: str,
        _port: int,
        **_kwargs: object,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        """Return the fixed answer set for any queried host and port."""
        return answers

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)


def _recording_connector(
    dialed: list[tuple[str, int]],
) -> Callable[[_WebhookTarget, _ResolvedAddress, float], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]:
    """Build a connector that records each dialed address and returns a canned 204 response.

    :param dialed: List that receives one ``(host, port)`` entry per dialed address.
    :return: Connector seam compatible with the poster.
    """

    async def connector(
        _target: _WebhookTarget,
        address: _ResolvedAddress,
        _timeout: float,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Record the dialed address and return a canned 204 response."""
        assert isinstance(address.sockaddr, tuple)
        dialed.append((address.sockaddr[0], address.sockaddr[1]))
        return _no_content_reader(), cast("asyncio.StreamWriter", _Writer())

    return connector


def test_default_poster_rejects_a_resolved_private_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real resolver rejects a hostname resolving to a private address before dialing."""

    async def exercise() -> None:
        dialed: list[tuple[str, int]] = []
        _patch_getaddrinfo(monkeypatch, "10.0.0.5")
        poster = PinnedHttpsJsonPoster(connector=_recording_connector(dialed))
        with pytest.raises(WebhookTransportError, match="private or reserved"):
            await poster("https://rebind.example/callback", {"type": "test"})
        assert dialed == []

    asyncio.run(exercise())


def test_default_poster_rejects_a_resolved_loopback_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real resolver rejects a hostname resolving to a loopback address before dialing."""

    async def exercise() -> None:
        dialed: list[tuple[str, int]] = []
        _patch_getaddrinfo(monkeypatch, "127.0.0.1")
        poster = PinnedHttpsJsonPoster(connector=_recording_connector(dialed))
        with pytest.raises(WebhookTransportError, match="private or reserved"):
            await poster("https://rebind.example/callback", {"type": "test"})
        assert dialed == []

    asyncio.run(exercise())


def test_allow_private_addresses_accepts_a_resolved_private_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enabling private addresses lets the real resolver return a private address that is then dialed."""

    async def exercise() -> None:
        dialed: list[tuple[str, int]] = []
        _patch_getaddrinfo(monkeypatch, "10.0.0.5")
        poster = PinnedHttpsJsonPoster(allow_private_addresses=True, connector=_recording_connector(dialed))
        assert await poster("https://rebind.example/callback", {"type": "test"}) == 204
        assert dialed == [("10.0.0.5", 443)]

    asyncio.run(exercise())


def test_default_poster_rejects_a_resolution_mixing_private_and_global_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resolution containing any private address is rejected wholesale, never partially dialed."""

    async def exercise() -> None:
        dialed: list[tuple[str, int]] = []
        _patch_getaddrinfo(monkeypatch, "93.184.216.34", "10.0.0.5")
        poster = PinnedHttpsJsonPoster(connector=_recording_connector(dialed))
        with pytest.raises(WebhookTransportError, match="private or reserved"):
            await poster("https://rebind.example/callback", {"type": "test"})
        assert dialed == []

    asyncio.run(exercise())


def test_join_url_path_joins_bases_with_and_without_trailing_slash() -> None:
    """A fixed absolute path is appended exactly once regardless of a trailing slash."""
    assert join_url_path("https://consumer.example/callback", "/a/b") == "https://consumer.example/callback/a/b"
    assert join_url_path("https://consumer.example/callback/", "/a/b") == "https://consumer.example/callback/a/b"


def test_join_url_path_rejects_empty_base_and_non_canonical_path() -> None:
    """An empty base or a non-canonical absolute path is rejected."""
    with pytest.raises(ValueError, match="base_url must be a non-empty URL"):
        join_url_path("", "/a")
    with pytest.raises(ValueError, match="callback path must be a canonical absolute path"):
        join_url_path("https://consumer.example", "a")
    with pytest.raises(ValueError, match="callback path must be a canonical absolute path"):
        join_url_path("https://consumer.example", "/a//b")


def test_default_poster_rejects_a_private_literal_address() -> None:
    """The default policy rejects a private or reserved literal target before dialing."""

    async def exercise() -> None:
        poster = PinnedHttpsJsonPoster(resolver=_resolver, connector=_connector)
        with pytest.raises(WebhookTransportError, match="private or reserved"):
            await poster("https://127.0.0.1/callback", {"type": "test"})

    asyncio.run(exercise())


def test_allow_private_addresses_accepts_a_private_literal_address() -> None:
    """Enabling private addresses accepts a target the default policy rejects."""

    async def exercise() -> None:
        poster = PinnedHttpsJsonPoster(allow_private_addresses=True, resolver=_resolver, connector=_connector)
        assert await poster("https://127.0.0.1/callback", {"type": "test"}) == 204

    asyncio.run(exercise())


def test_deliver_with_retries_returns_on_a_first_attempt_success() -> None:
    """A 2xx status on the first attempt completes delivery without a retry."""

    async def exercise() -> None:
        calls = 0

        async def sender(_url: str, _document: dict[str, JsonValue]) -> int:
            nonlocal calls
            calls += 1
            return 204

        await deliver_with_retries(sender, "https://consumer.example/callback", {})
        assert calls == 1

    asyncio.run(exercise())


def test_deliver_with_retries_recovers_on_a_later_attempt() -> None:
    """A non-2xx status is retried within the attempt budget until a 2xx is seen."""

    async def exercise() -> None:
        responses = iter([500, 204])

        async def sender(_url: str, _document: dict[str, JsonValue]) -> int:
            return next(responses)

        await deliver_with_retries(sender, "https://consumer.example/callback", {})

    asyncio.run(exercise())


def test_deliver_with_retries_fails_after_exhausting_the_attempt_budget() -> None:
    """Repeated non-2xx statuses fail with the last observed status detail."""

    async def exercise() -> None:
        calls = 0

        async def sender(_url: str, _document: dict[str, JsonValue]) -> int:
            nonlocal calls
            calls += 1
            return 500

        with pytest.raises(WebhookTransportError, match="callback returned HTTP 500"):
            await deliver_with_retries(sender, "https://consumer.example/callback", {})
        assert calls == 2

    asyncio.run(exercise())


def test_deliver_with_retries_reports_a_transport_error_detail() -> None:
    """A transport error inside the sender surfaces its detail after the budget."""

    async def exercise() -> None:
        async def sender(_url: str, _document: dict[str, JsonValue]) -> int:
            raise WebhookTransportError("offline")

        with pytest.raises(WebhookTransportError, match="offline"):
            await deliver_with_retries(sender, "https://consumer.example/callback", {}, attempts=1)

    asyncio.run(exercise())


def test_deliver_with_retries_rejects_a_boolean_status() -> None:
    """A boolean masquerading as an integer status is rejected by the guard."""

    async def exercise() -> None:
        async def sender(_url: str, _document: dict[str, JsonValue]) -> bool:
            return True

        with pytest.raises(WebhookTransportError, match="did not return an HTTP status code"):
            await deliver_with_retries(sender, "https://consumer.example/callback", {}, attempts=1)  # type: ignore[arg-type]

    asyncio.run(exercise())


def test_deliver_with_retries_rejects_a_non_2xx_final_status() -> None:
    """A single 4xx attempt fails without a retry when the budget is one."""

    async def exercise() -> None:
        async def sender(_url: str, _document: dict[str, JsonValue]) -> int:
            return 404

        with pytest.raises(WebhookTransportError, match="callback returned HTTP 404"):
            await deliver_with_retries(sender, "https://consumer.example/callback", {}, attempts=1)

    asyncio.run(exercise())
