"""Tests for callback URL construction and retry delivery behavior."""

import asyncio
import os
import socket

import pytest
from test_dsp_config import config

from httk.serve.dsp import CallbackTransportError, DspProvider, callback_url
from httk.serve.dsp.callbacks import DefaultCallbackSender, _CallbackTarget, _ResolvedAddress, _validate_callback_url


def offer() -> dict[str, object]:
    """Build the configured target-bearing message offer."""
    return {
        "@id": "https://provider.example/offers/one",
        "@type": "Offer",
        "target": "https://provider.example/datasets/one",
        "permission": [{"action": "use"}],
    }


def request() -> dict[str, object]:
    """Build a valid initial negotiation request."""
    return {
        "@context": ["https://w3id.org/dspace/2025/1/context.jsonld"],
        "@type": "ContractRequestMessage",
        "consumerPid": "consumer",
        "callbackAddress": "https://consumer.example/callback/",
        "offer": offer(),
    }


def test_callback_url_joins_trailing_slash_bases_once() -> None:
    """Callback paths are stable for bases with and without a trailing slash."""
    assert callback_url("https://consumer.example/callback", "/negotiations/c/offers") == (
        "https://consumer.example/callback/negotiations/c/offers"
    )
    assert callback_url("https://consumer.example/callback/", "/negotiations/c/offers") == (
        "https://consumer.example/callback/negotiations/c/offers"
    )


def test_default_callback_policy_rejects_non_https_and_loopback_addresses() -> None:
    """Default network delivery refuses unencrypted and non-public callback targets."""

    async def exercise() -> None:
        with pytest.raises(CallbackTransportError, match="HTTPS"):
            await _validate_callback_url("http://consumer.example/callback")
        with pytest.raises(CallbackTransportError, match="private or reserved"):
            await _validate_callback_url("https://127.0.0.1/callback")

    asyncio.run(exercise())


def test_provider_rejects_callback_bases_with_query_components() -> None:
    """Inbound callback bases cannot carry a query that would absorb callback paths."""

    async def exercise() -> None:
        provider = DspProvider(config(automatic_progression=False))
        message = request()
        message["callbackAddress"] = "https://consumer.example/callback?token=secret"
        with pytest.raises(Exception, match="HTTPS URL"):
            await provider.request_negotiation(message)

    asyncio.run(exercise())


def test_default_sender_dials_the_validated_address_with_original_tls_and_host() -> None:
    """A resolver result is pinned for dialing while the logical hostname remains in TLS and Host."""

    class Writer:
        """Capture the request emitted by a pinned test connector."""

        def __init__(self) -> None:
            """Create an empty request capture."""
            self.data = bytearray()

        def write(self, data: bytes) -> None:
            """Capture written request bytes.

            :param data: Bytes produced by the callback sender.
            """
            self.data.extend(data)

        async def drain(self) -> None:
            """Simulate an immediately writable socket."""

        def close(self) -> None:
            """Simulate socket close."""

        async def wait_closed(self) -> None:
            """Simulate immediate socket shutdown."""

    async def exercise() -> None:
        captured: dict[str, object] = {}
        writer = Writer()

        async def resolver(target: _CallbackTarget, timeout: float) -> tuple[_ResolvedAddress, ...]:
            captured["resolved_host"] = target.hostname
            captured["dns_timeout"] = timeout
            return (_ResolvedAddress(socket.AF_INET, ("93.184.216.34", 443)),)

        async def connector(
            target: _CallbackTarget,
            address: _ResolvedAddress,
            timeout: float,
        ) -> tuple[asyncio.StreamReader, Writer]:
            captured["sni_host"] = target.hostname
            captured["dialed_address"] = address.sockaddr
            captured["connect_timeout"] = timeout
            reader = asyncio.StreamReader()
            reader.feed_data(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
            reader.feed_eof()
            return reader, writer

        sender = DefaultCallbackSender(resolver=resolver, connector=connector)
        assert await sender("https://rebind.example/callback", {"type": "test"}) == 204

        assert captured == {
            "resolved_host": "rebind.example",
            "dns_timeout": 5.0,
            "sni_host": "rebind.example",
            "dialed_address": ("93.184.216.34", 443),
            "connect_timeout": 5.0,
        }
        request_bytes = bytes(writer.data)
        assert b"Host: rebind.example\r\n" in request_bytes
        assert request_bytes.startswith(b"POST /callback HTTP/1.1\r\n")

    asyncio.run(exercise())


def test_default_sender_caps_resolution_and_ignores_proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Capacity includes DNS resolution and the stdlib pinned transport ignores proxy environment variables."""

    class Writer:
        """Provide a successful empty response writer for the callback transport seam."""

        def write(self, _data: bytes) -> None:
            """Accept one request payload.

            :param _data: Callback request bytes.
            """

        async def drain(self) -> None:
            """Simulate an immediately writable socket."""

        def close(self) -> None:
            """Simulate socket close."""

        async def wait_closed(self) -> None:
            """Simulate immediate socket shutdown."""

    async def exercise() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def resolver(_target: _CallbackTarget, _timeout: float) -> tuple[_ResolvedAddress, ...]:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return (_ResolvedAddress(socket.AF_INET, ("93.184.216.34", 443)),)

        async def connector(
            _target: _CallbackTarget,
            _address: _ResolvedAddress,
            _timeout: float,
        ) -> tuple[asyncio.StreamReader, Writer]:
            reader = asyncio.StreamReader()
            reader.feed_data(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
            reader.feed_eof()
            return reader, Writer()

        sender = DefaultCallbackSender(max_concurrency=1, resolver=resolver, connector=connector)
        first = asyncio.create_task(sender("https://first.example/callback", {}))
        await entered.wait()
        second = asyncio.create_task(sender("https://second.example/callback", {}))
        await asyncio.sleep(0)
        assert calls == 1
        release.set()
        assert await asyncio.gather(first, second) == [204, 204]
        assert calls == 2

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    original_proxy = os.environ["HTTPS_PROXY"]
    asyncio.run(exercise())
    assert os.environ["HTTPS_PROXY"] == original_proxy


def test_default_sender_bounds_dns_and_total_operation_time() -> None:
    """DNS timeout and total operation timeout fail with bounded transport errors."""

    async def exercise() -> None:
        async def stalled_resolver(_target: _CallbackTarget, _timeout: float) -> tuple[_ResolvedAddress, ...]:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        sender = DefaultCallbackSender(dns_timeout=0.01, total_timeout=0.1, resolver=stalled_resolver)
        with pytest.raises(CallbackTransportError, match="DNS resolution timed out"):
            await sender("https://bounded.example/callback", {})

    asyncio.run(exercise())


def test_default_sender_only_falls_back_before_a_request_is_sent() -> None:
    """A failed acknowledgement does not dial another resolved address and duplicate the request."""

    class Writer:
        """Offer the minimal stream-writer API used by the sender."""

        def write(self, _data: bytes) -> None:
            """Accept the one request."""

        async def drain(self) -> None:
            """Simulate an immediately flushed request."""

        def close(self) -> None:
            """Simulate connection closure."""

        async def wait_closed(self) -> None:
            """Simulate immediate connection closure."""

    async def exercise() -> None:
        addresses = (
            _ResolvedAddress(socket.AF_INET, ("93.184.216.34", 443)),
            _ResolvedAddress(socket.AF_INET, ("93.184.216.35", 443)),
        )
        dialed: list[tuple[str, int]] = []

        async def resolver(_target: _CallbackTarget, _timeout: float) -> tuple[_ResolvedAddress, ...]:
            return addresses

        async def connector(
            _target: _CallbackTarget,
            address: _ResolvedAddress,
            _timeout: float,
        ) -> tuple[asyncio.StreamReader, Writer]:
            assert isinstance(address.sockaddr, tuple)
            dialed.append((address.sockaddr[0], address.sockaddr[1]))
            reader = asyncio.StreamReader()
            reader.feed_eof()
            return reader, Writer()

        sender = DefaultCallbackSender(resolver=resolver, connector=connector)
        with pytest.raises(CallbackTransportError, match="ended unexpectedly"):
            await sender("https://consumer.example/callback", {})
        assert dialed == [("93.184.216.34", 443)]

    asyncio.run(exercise())


def test_default_sender_falls_back_after_connection_establishment_fails() -> None:
    """A numeric dial failure may use the next already validated resolution result."""

    class Writer:
        """Offer the minimal stream-writer API used by the sender."""

        def write(self, _data: bytes) -> None:
            """Accept the one request."""

        async def drain(self) -> None:
            """Simulate an immediately flushed request."""

        def close(self) -> None:
            """Simulate connection closure."""

        async def wait_closed(self) -> None:
            """Simulate immediate connection closure."""

    async def exercise() -> None:
        addresses = (
            _ResolvedAddress(socket.AF_INET, ("93.184.216.34", 443)),
            _ResolvedAddress(socket.AF_INET, ("93.184.216.35", 443)),
        )
        dialed: list[tuple[str, int]] = []

        async def resolver(_target: _CallbackTarget, _timeout: float) -> tuple[_ResolvedAddress, ...]:
            return addresses

        async def connector(
            _target: _CallbackTarget,
            address: _ResolvedAddress,
            _timeout: float,
        ) -> tuple[asyncio.StreamReader, Writer]:
            assert isinstance(address.sockaddr, tuple)
            dialed.append((address.sockaddr[0], address.sockaddr[1]))
            if len(dialed) == 1:
                raise CallbackTransportError("connection refused")
            reader = asyncio.StreamReader()
            reader.feed_data(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
            reader.feed_eof()
            return reader, Writer()

        sender = DefaultCallbackSender(resolver=resolver, connector=connector)
        assert await sender("https://consumer.example/callback", {}) == 204
        assert dialed == [("93.184.216.34", 443), ("93.184.216.35", 443)]

    asyncio.run(exercise())


def test_callback_failure_retries_then_acknowledges_termination_only() -> None:
    """Failed callbacks retry twice and transition to termination only on a 2xx termination callback."""

    async def exercise() -> None:
        sent: list[tuple[str, str]] = []
        responses = iter([500, 500, 204])

        async def sender(url: str, document: dict[str, object]) -> int:
            sent.append((url, str(document["@type"])))
            return next(responses)

        provider = DspProvider(
            config(automatic_progression=False), callback_sender=sender, uuid_factory=iter(["negotiation"]).__next__
        )
        created = await provider.request_negotiation(request())
        with pytest.raises(Exception, match="callback delivery failed"):
            await provider.send_offer(str(created["providerPid"]))
        state = await provider.get_negotiation(str(created["providerPid"]))

        assert state["state"] == "TERMINATED"
        assert [item[1] for item in sent] == [
            "ContractOfferMessage",
            "ContractOfferMessage",
            "ContractNegotiationTerminationMessage",
        ]
        assert sent[0][0].endswith("/negotiations/consumer/offers")
        assert sent[-1][0].endswith("/negotiations/consumer/termination")

    asyncio.run(exercise())


def test_injected_sender_can_raise_transport_error() -> None:
    """Injected transports participate in the provider retry policy."""

    async def exercise() -> None:
        calls = 0

        async def sender(_url: str, _document: dict[str, object]) -> int:
            nonlocal calls
            calls += 1
            raise CallbackTransportError("offline")

        provider = DspProvider(
            config(automatic_progression=False), callback_sender=sender, uuid_factory=iter(["negotiation"]).__next__
        )
        created = await provider.request_negotiation(request())
        with pytest.raises(Exception, match="callback delivery failed"):
            await provider.send_offer(str(created["providerPid"]))
        assert calls == 4  # Two failed offers and two failed best-effort terminations.

    asyncio.run(exercise())
