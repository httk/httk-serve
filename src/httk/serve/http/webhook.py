"""Hardened outbound HTTPS JSON webhook delivery.

This module is a general-purpose hardened outbound webhook client: strict
HTTPS URL parsing (rejecting userinfo, query, fragment, control characters,
and non-ASCII after IDNA), DNS resolution filtered to globally-routable
addresses only, a connection pinned to a resolved numeric address with TLS SNI
carrying the original logical hostname (a DNS-rebinding defence), a bounded
HTTP/1.1 request and response cycle with header-size caps and Content-Length /
chunked / close-delimited body framing, per-address retry, a concurrency
semaphore, and layered connect/read/DNS/total timeouts.

It carries no protocol vocabulary. The Data Space Protocol consumes it through
:mod:`httk.serve.dsp.callbacks`, which retains the DSP-facing spelling of these
names.
"""

import asyncio
import functools
import ipaddress
import json
import socket
import ssl
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import urlsplit

from httk.serve.jsondata import JsonValue

type WebhookSender = Callable[[str, dict[str, JsonValue]], Awaitable[int]]
type _Resolver = Callable[["_WebhookTarget", float], Awaitable[tuple["_ResolvedAddress", ...]]]
type _Connector = Callable[
    ["_WebhookTarget", "_ResolvedAddress", float], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]
]

_HEADER_LIMIT = 65_536


class WebhookTransportError(RuntimeError):
    """Represent an outbound webhook transport or URL-policy failure.

    :param detail: Safe detail suitable for local process-delivery state.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True, slots=True)
class _WebhookTarget:
    """Describe a validated webhook URL before network resolution."""

    hostname: str
    port: int
    host_header: str
    request_target: str


@dataclass(frozen=True, slots=True)
class _ResolvedAddress:
    """Describe one public address which DNS returned for a webhook host."""

    family: int
    sockaddr: tuple[str, int] | tuple[str, int, int, int]


def join_url_path(base_url: str, path: str) -> str:
    """Append a fixed absolute path without depending on a trailing slash.

    :param base_url: Base URL from an inbound request.
    :param path: Absolute path beginning with ``/``.
    :return: Complete URL.
    :raises ValueError: If the base or fixed path is malformed.
    """
    if not isinstance(base_url, str) or not base_url:
        raise ValueError("base_url must be a non-empty URL")
    if not isinstance(path, str) or not path.startswith("/") or "//" in path:
        raise ValueError("callback path must be a canonical absolute path")
    return f"{base_url.rstrip('/')}{path}"


def _public_address(host: str, *, allow_private_addresses: bool = False) -> None:
    """Reject a literal address unsuitable for outbound peer webhooks."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not allow_private_addresses and not address.is_global:
        raise WebhookTransportError("callback address must not target a private or reserved IP address")


def _parse_callback_url(url: str, *, allow_private_addresses: bool = False) -> _WebhookTarget:
    """Validate a webhook URL and retain its logical HTTP host for TLS and Host."""
    if not isinstance(url, str) or any(
        character.isspace() or ord(character) < 32 or 127 <= ord(character) <= 159 or character in '<>"{}|\\^`'
        for character in url
    ):
        raise WebhookTransportError("callback URL is malformed")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise WebhookTransportError("callback URL is malformed") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise WebhookTransportError("callback URL must be an absolute HTTPS URL without userinfo, query, or fragment")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii")
        request_target = (parsed.path or "/").encode("ascii").decode("ascii")
    except UnicodeError as error:
        raise WebhookTransportError("callback URL host and path must be ASCII-compatible") from error
    if any(not (character.isalnum() or character in ".-:") for character in hostname):
        raise WebhookTransportError("callback URL host is malformed")
    _public_address(hostname, allow_private_addresses=allow_private_addresses)
    if any(character.isspace() or ord(character) < 32 or 127 <= ord(character) <= 159 for character in request_target):
        raise WebhookTransportError("callback URL path must not contain spaces or control characters")
    port = 443 if port is None else port
    host_header = f"[{hostname}]" if ":" in hostname else hostname
    if port != 443:
        host_header = f"{host_header}:{port}"
    return _WebhookTarget(hostname, port, host_header, request_target)


async def _resolve_public_addresses(
    target: _WebhookTarget,
    timeout: float,
    *,
    allow_private_addresses: bool = False,
) -> tuple[_ResolvedAddress, ...]:
    """Resolve and validate webhook addresses while retaining pinned socket tuples."""
    try:
        answers = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(target.hostname, target.port, type=socket.SOCK_STREAM),
            timeout,
        )
    except TimeoutError as error:
        raise WebhookTransportError("callback DNS resolution timed out") from error
    except socket.gaierror as error:
        raise WebhookTransportError("callback host could not be resolved") from error
    addresses: list[_ResolvedAddress] = []
    seen: set[tuple[int, tuple[str, int] | tuple[str, int, int, int]]] = set()
    for family, socket_type, _protocol, _canonical_name, sockaddr in answers:
        if family not in (socket.AF_INET, socket.AF_INET6) or socket_type != socket.SOCK_STREAM:
            continue
        if not isinstance(sockaddr, tuple) or len(sockaddr) not in (2, 4):
            continue
        if not isinstance(sockaddr[0], str) or not isinstance(sockaddr[1], int):
            continue
        if family == socket.AF_INET:
            pinned_sockaddr: tuple[str, int] | tuple[str, int, int, int] = (sockaddr[0], sockaddr[1])
        elif len(sockaddr) == 4 and isinstance(sockaddr[2], int) and isinstance(sockaddr[3], int):
            pinned_sockaddr = (sockaddr[0], sockaddr[1], sockaddr[2], sockaddr[3])
        else:
            continue
        address = sockaddr[0]
        _public_address(address, allow_private_addresses=allow_private_addresses)
        pinned = _ResolvedAddress(family, pinned_sockaddr)
        identity = (pinned.family, pinned.sockaddr)
        if identity not in seen:
            seen.add(identity)
            addresses.append(pinned)
    if not addresses:
        raise WebhookTransportError("callback host resolved to no usable public addresses")
    return tuple(addresses)


async def _open_pinned_connection(
    target: _WebhookTarget,
    address: _ResolvedAddress,
    timeout: float,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect one validated numeric address while retaining logical TLS and HTTP host names."""
    sock = socket.socket(address.family, socket.SOCK_STREAM)
    sock.setblocking(False)
    try:
        await asyncio.wait_for(asyncio.get_running_loop().sock_connect(sock, address.sockaddr), timeout)
        return await asyncio.wait_for(
            asyncio.open_connection(
                sock=sock,
                ssl=ssl.create_default_context(),
                server_hostname=target.hostname,
                ssl_handshake_timeout=timeout,
            ),
            timeout,
        )
    except TimeoutError as error:
        sock.close()
        raise WebhookTransportError("callback connection timed out") from error
    except (OSError, ssl.SSLError) as error:
        sock.close()
        raise WebhookTransportError(f"callback connection failed: {error.__class__.__name__}") from error


async def _validate_callback_url(url: str) -> None:
    """Require HTTPS and public literal and resolved webhook addresses.

    :param url: Complete webhook URL to validate without sending a request.
    :raises WebhookTransportError: If URL or DNS policy checks fail.
    """
    target = _parse_callback_url(url)
    await _resolve_public_addresses(target, 5.0)


class PinnedHttpsJsonPoster:
    """Send one HTTPS JSON POST through a validated, pinned socket.

    DNS resolution occurs inside the concurrency cap and produces the exact
    numeric socket address dialed for the request. TLS SNI and the HTTP
    ``Host`` header retain the original DNS hostname. The stdlib transport does
    not consult proxy or environment settings; redirects are not implemented.
    Both DNS and the complete operation have finite deadlines.

    :param connect_timeout: Maximum seconds for TCP connect and TLS handshake.
    :param read_timeout: Maximum seconds for reading a complete response body.
    :param dns_timeout: Maximum seconds for DNS resolution.
    :param total_timeout: Maximum seconds from the start of resolution through response completion.
    :param response_body_limit: Maximum response bytes consumed before rejection.
    :param max_concurrency: Maximum simultaneous resolution and send operations.
    :param allow_private_addresses: Skip global-address filtering when ``True``; the default rejects private targets.
    :param resolver: Optional test seam that resolves public pinned addresses.
    :param connector: Optional test seam that dials a supplied pinned address.
    """

    def __init__(
        self,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 10.0,
        dns_timeout: float = 5.0,
        total_timeout: float = 20.0,
        response_body_limit: int = 65_536,
        max_concurrency: int = 8,
        allow_private_addresses: bool = False,
        resolver: _Resolver | None = None,
        connector: _Connector | None = None,
    ) -> None:
        if min(connect_timeout, read_timeout, dns_timeout, total_timeout) <= 0:
            raise ValueError("callback timeouts must be positive")
        if response_body_limit < 0:
            raise ValueError("response_body_limit must be non-negative")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least one")
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._dns_timeout = dns_timeout
        self._total_timeout = total_timeout
        self._response_body_limit = response_body_limit
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._allow_private_addresses = allow_private_addresses
        if resolver is not None:
            self._resolver: _Resolver = resolver
        else:
            self._resolver = functools.partial(
                _resolve_public_addresses, allow_private_addresses=allow_private_addresses
            )
        self._connector = _open_pinned_connection if connector is None else connector

    async def __call__(self, url: str, document: dict[str, JsonValue]) -> int:
        """POST a JSON document and return its HTTP status code.

        :param url: Complete webhook URL.
        :param document: Plain JSON message document.
        :return: Peer HTTP response status code.
        :raises WebhookTransportError: If URL policy, transport, or response-size checks fail.
        """
        target = _parse_callback_url(url, allow_private_addresses=self._allow_private_addresses)
        payload = json.dumps(document, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        try:
            await asyncio.wait_for(self._semaphore.acquire(), self._total_timeout)
        except TimeoutError as error:
            raise WebhookTransportError("callback operation timed out while waiting for capacity") from error
        try:
            return await asyncio.wait_for(self._send(target, payload), self._total_timeout)
        except WebhookTransportError:
            raise
        except TimeoutError as error:
            raise WebhookTransportError("callback operation timed out") from error
        except (OSError, ssl.SSLError) as error:
            raise WebhookTransportError(f"callback transport failed: {error.__class__.__name__}") from error
        finally:
            self._semaphore.release()

    async def _send(self, target: _WebhookTarget, payload: bytes) -> int:
        """Resolve, dial a validated address, write one request, and read its bounded response."""
        try:
            addresses = await asyncio.wait_for(self._resolver(target, self._dns_timeout), self._dns_timeout)
        except TimeoutError as error:
            raise WebhookTransportError("callback DNS resolution timed out") from error
        last_error: WebhookTransportError | None = None
        for address in addresses:
            try:
                reader, writer = await self._connector(target, address, self._connect_timeout)
            except WebhookTransportError as error:
                last_error = error
                continue
            try:
                writer.write(self._request_bytes(target, payload))
                await writer.drain()
                return await self._read_response(reader)
            finally:
                writer.close()
                with suppress(Exception):
                    await asyncio.wait_for(writer.wait_closed(), self._read_timeout)
        if last_error is None:
            raise WebhookTransportError("callback connection failed")
        raise last_error

    @staticmethod
    def _request_bytes(target: _WebhookTarget, payload: bytes) -> bytes:
        """Build a simple HTTP/1.1 request using the original logical host."""
        try:
            headers = (
                f"POST {target.request_target} HTTP/1.1\r\n"
                f"Host: {target.host_header}\r\n"
                "Content-Type: application/json\r\n"
                "Accept: application/json\r\n"
                "Connection: close\r\n"
                f"Content-Length: {len(payload)}\r\n\r\n"
            ).encode("ascii")
        except UnicodeError as error:
            raise WebhookTransportError("callback request target cannot be encoded") from error
        return headers + payload

    async def _read_response(self, reader: asyncio.StreamReader) -> int:
        """Read one bounded HTTP/1.1 response without allowing a body to grow unbounded."""
        status_line = await self._readline(reader)
        try:
            _protocol, status_text, _reason = status_line.decode("ascii").rstrip("\r\n").split(" ", 2)
            status = int(status_text)
        except (UnicodeDecodeError, ValueError) as error:
            raise WebhookTransportError("callback returned an invalid HTTP status line") from error
        headers: dict[str, str] = {}
        header_bytes = len(status_line)
        while True:
            line = await self._readline(reader)
            header_bytes += len(line)
            if header_bytes > _HEADER_LIMIT:
                raise WebhookTransportError("callback response headers exceeded the configured limit")
            if line == b"\r\n":
                break
            try:
                name, value = line.decode("ascii").rstrip("\r\n").split(":", 1)
            except (UnicodeDecodeError, ValueError) as error:
                raise WebhookTransportError("callback returned malformed HTTP headers") from error
            headers[name.lower()] = value.strip()
        if headers.get("transfer-encoding", "").lower() == "chunked":
            await self._read_chunked_body(reader)
        elif "content-length" in headers:
            try:
                size = int(headers["content-length"])
            except ValueError as error:
                raise WebhookTransportError("callback returned an invalid Content-Length") from error
            if size < 0 or size > self._response_body_limit:
                raise WebhookTransportError("callback response body exceeded the configured limit")
            await self._readexactly(reader, size)
        else:
            await self._read_to_eof(reader)
        return status

    async def _read_chunked_body(self, reader: asyncio.StreamReader) -> None:
        """Read a chunked response while applying the configured decoded-body limit."""
        received = 0
        while True:
            line = await self._readline(reader)
            try:
                size = int(line.split(b";", 1)[0].strip(), 16)
            except ValueError as error:
                raise WebhookTransportError("callback returned an invalid chunk size") from error
            if size < 0:
                raise WebhookTransportError("callback returned an invalid chunk size")
            if size == 0:
                while await self._readline(reader) != b"\r\n":
                    pass
                return
            received += size
            if received > self._response_body_limit:
                raise WebhookTransportError("callback response body exceeded the configured limit")
            await self._readexactly(reader, size)
            if await self._readexactly(reader, 2) != b"\r\n":
                raise WebhookTransportError("callback returned malformed chunk framing")

    async def _read_to_eof(self, reader: asyncio.StreamReader) -> None:
        """Read an unknown-length, connection-close body within the configured limit."""
        received = 0
        while True:
            chunk = await self._read(reader, min(8_192, self._response_body_limit - received + 1))
            if not chunk:
                return
            received += len(chunk)
            if received > self._response_body_limit:
                raise WebhookTransportError("callback response body exceeded the configured limit")

    async def _readline(self, reader: asyncio.StreamReader) -> bytes:
        """Read one bounded CRLF line under the read deadline."""
        try:
            line = await asyncio.wait_for(reader.readline(), self._read_timeout)
        except TimeoutError as error:
            raise WebhookTransportError("callback response read timed out") from error
        except (ValueError, asyncio.LimitOverrunError) as error:
            raise WebhookTransportError("callback response line exceeded the configured limit") from error
        if not line or not line.endswith(b"\r\n"):
            raise WebhookTransportError("callback response ended unexpectedly")
        if len(line) > _HEADER_LIMIT:
            raise WebhookTransportError("callback response headers exceeded the configured limit")
        return line

    async def _readexactly(self, reader: asyncio.StreamReader, size: int) -> bytes:
        """Read an exact response segment under the read deadline."""
        try:
            return await asyncio.wait_for(reader.readexactly(size), self._read_timeout)
        except (TimeoutError, asyncio.IncompleteReadError) as error:
            raise WebhookTransportError("callback response ended unexpectedly") from error

    async def _read(self, reader: asyncio.StreamReader, size: int) -> bytes:
        """Read one response segment under the read deadline."""
        try:
            return await asyncio.wait_for(reader.read(size), self._read_timeout)
        except TimeoutError as error:
            raise WebhookTransportError("callback response read timed out") from error


async def deliver_with_retries(
    sender: WebhookSender,
    url: str,
    document: dict[str, JsonValue],
    *,
    attempts: int = 2,
) -> None:
    """Deliver a document through a sender at most ``attempts`` times, requiring a 2xx status.

    :param sender: Awaitable sender returning a peer HTTP status code.
    :param url: Complete webhook URL passed to the sender.
    :param document: Plain JSON message document passed to the sender.
    :param attempts: Maximum number of delivery attempts before failing.
    :raises WebhookTransportError: If no attempt yields a 2xx acknowledgement.
    """
    last_detail = "callback delivery was not attempted"
    for _attempt in range(attempts):
        try:
            status = await sender(url, document)
        except WebhookTransportError as error:
            last_detail = error.detail
            continue
        except Exception as error:
            last_detail = f"callback transport failed: {error.__class__.__name__}"
            continue
        if isinstance(status, bool) or not isinstance(status, int):
            last_detail = "callback sender did not return an HTTP status code"
            continue
        if 200 <= status < 300:
            return
        last_detail = f"callback returned HTTP {status}"
    raise WebhookTransportError(last_detail)


__all__ = [
    "PinnedHttpsJsonPoster",
    "WebhookSender",
    "WebhookTransportError",
    "deliver_with_retries",
    "join_url_path",
]
