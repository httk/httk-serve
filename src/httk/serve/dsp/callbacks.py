"""Hardened outbound callback delivery for the Data Space Protocol."""

import asyncio
import ipaddress
import json
import socket
import ssl
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import urlsplit

from .models import JsonValue

type CallbackSender = Callable[[str, dict[str, JsonValue]], Awaitable[int]]
type _Resolver = Callable[["_CallbackTarget", float], Awaitable[tuple["_ResolvedAddress", ...]]]
type _Connector = Callable[
    ["_CallbackTarget", "_ResolvedAddress", float], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]
]

_HEADER_LIMIT = 65_536


class CallbackTransportError(RuntimeError):
    """Represent an outbound callback transport or URL-policy failure.

    :param detail: Safe detail suitable for local process-delivery state.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True, slots=True)
class _CallbackTarget:
    """Describe a validated callback URL before network resolution."""

    hostname: str
    port: int
    host_header: str
    request_target: str


@dataclass(frozen=True, slots=True)
class _ResolvedAddress:
    """Describe one public address which DNS returned for a callback host."""

    family: int
    sockaddr: tuple[str, int] | tuple[str, int, int, int]


def callback_url(callback_address: str, path: str) -> str:
    """Append a fixed DSP callback path without depending on a trailing slash.

    :param callback_address: Consumer callback base URL from an inbound request.
    :param path: Absolute DSP callback path beginning with ``/``.
    :return: Complete callback URL.
    :raises ValueError: If the base or fixed path is malformed.
    """
    if not isinstance(callback_address, str) or not callback_address:
        raise ValueError("callback_address must be a non-empty URL")
    if not isinstance(path, str) or not path.startswith("/") or "//" in path:
        raise ValueError("callback path must be a canonical absolute path")
    return f"{callback_address.rstrip('/')}{path}"


def _public_address(host: str) -> None:
    """Reject a literal address unsuitable for outbound peer callbacks."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise CallbackTransportError("callback address must not target a private or reserved IP address")


def _parse_callback_url(url: str) -> _CallbackTarget:
    """Validate a callback URL and retain its logical HTTP host for TLS and Host."""
    if not isinstance(url, str) or any(
        character.isspace() or ord(character) < 32 or 127 <= ord(character) <= 159 or character in '<>"{}|\\^`'
        for character in url
    ):
        raise CallbackTransportError("callback URL is malformed")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise CallbackTransportError("callback URL is malformed") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise CallbackTransportError("callback URL must be an absolute HTTPS URL without userinfo, query, or fragment")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii")
        request_target = (parsed.path or "/").encode("ascii").decode("ascii")
    except UnicodeError as error:
        raise CallbackTransportError("callback URL host and path must be ASCII-compatible") from error
    if any(not (character.isalnum() or character in ".-:") for character in hostname):
        raise CallbackTransportError("callback URL host is malformed")
    _public_address(hostname)
    if any(character.isspace() or ord(character) < 32 or 127 <= ord(character) <= 159 for character in request_target):
        raise CallbackTransportError("callback URL path must not contain spaces or control characters")
    port = 443 if port is None else port
    host_header = f"[{hostname}]" if ":" in hostname else hostname
    if port != 443:
        host_header = f"{host_header}:{port}"
    return _CallbackTarget(hostname, port, host_header, request_target)


async def _resolve_public_addresses(target: _CallbackTarget, timeout: float) -> tuple[_ResolvedAddress, ...]:
    """Resolve and validate callback addresses while retaining pinned socket tuples."""
    try:
        answers = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(target.hostname, target.port, type=socket.SOCK_STREAM),
            timeout,
        )
    except TimeoutError as error:
        raise CallbackTransportError("callback DNS resolution timed out") from error
    except socket.gaierror as error:
        raise CallbackTransportError("callback host could not be resolved") from error
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
        _public_address(address)
        pinned = _ResolvedAddress(family, pinned_sockaddr)
        identity = (pinned.family, pinned.sockaddr)
        if identity not in seen:
            seen.add(identity)
            addresses.append(pinned)
    if not addresses:
        raise CallbackTransportError("callback host resolved to no usable public addresses")
    return tuple(addresses)


async def _open_pinned_connection(
    target: _CallbackTarget,
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
        raise CallbackTransportError("callback connection timed out") from error
    except (OSError, ssl.SSLError) as error:
        sock.close()
        raise CallbackTransportError(f"callback connection failed: {error.__class__.__name__}") from error


async def _validate_callback_url(url: str) -> None:
    """Require HTTPS and public literal and resolved callback addresses.

    :param url: Complete callback URL to validate without sending a request.
    :raises CallbackTransportError: If URL or DNS policy checks fail.
    """
    target = _parse_callback_url(url)
    await _resolve_public_addresses(target, 5.0)


class DefaultCallbackSender:
    """Send one HTTPS DSP callback through a validated, pinned socket.

    DNS resolution occurs inside the concurrency cap and produces the exact
    numeric socket address dialed for the callback. TLS SNI and the HTTP
    ``Host`` header retain the original DNS hostname. The stdlib transport does
    not consult proxy or environment settings; redirects are not implemented.
    Both DNS and the complete callback operation have finite deadlines.

    :param connect_timeout: Maximum seconds for TCP connect and TLS handshake.
    :param read_timeout: Maximum seconds for reading a complete response body.
    :param dns_timeout: Maximum seconds for DNS resolution.
    :param total_timeout: Maximum seconds from the start of resolution through response completion.
    :param response_body_limit: Maximum response bytes consumed before rejection.
    :param max_concurrency: Maximum simultaneous resolution and send operations.
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
        self._resolver = _resolve_public_addresses if resolver is None else resolver
        self._connector = _open_pinned_connection if connector is None else connector

    async def __call__(self, url: str, document: dict[str, JsonValue]) -> int:
        """POST a JSON callback and return its HTTP status code.

        :param url: Complete callback URL.
        :param document: Plain JSON DSP message document.
        :return: Peer HTTP response status code.
        :raises CallbackTransportError: If URL policy, transport, or response-size checks fail.
        """
        target = _parse_callback_url(url)
        payload = json.dumps(document, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        try:
            await asyncio.wait_for(self._semaphore.acquire(), self._total_timeout)
        except TimeoutError as error:
            raise CallbackTransportError("callback operation timed out while waiting for capacity") from error
        try:
            return await asyncio.wait_for(self._send(target, payload), self._total_timeout)
        except CallbackTransportError:
            raise
        except TimeoutError as error:
            raise CallbackTransportError("callback operation timed out") from error
        except (OSError, ssl.SSLError) as error:
            raise CallbackTransportError(f"callback transport failed: {error.__class__.__name__}") from error
        finally:
            self._semaphore.release()

    async def _send(self, target: _CallbackTarget, payload: bytes) -> int:
        """Resolve, dial a validated address, write one request, and read its bounded response."""
        try:
            addresses = await asyncio.wait_for(self._resolver(target, self._dns_timeout), self._dns_timeout)
        except TimeoutError as error:
            raise CallbackTransportError("callback DNS resolution timed out") from error
        last_error: CallbackTransportError | None = None
        for address in addresses:
            try:
                reader, writer = await self._connector(target, address, self._connect_timeout)
            except CallbackTransportError as error:
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
            raise CallbackTransportError("callback connection failed")
        raise last_error

    @staticmethod
    def _request_bytes(target: _CallbackTarget, payload: bytes) -> bytes:
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
            raise CallbackTransportError("callback request target cannot be encoded") from error
        return headers + payload

    async def _read_response(self, reader: asyncio.StreamReader) -> int:
        """Read one bounded HTTP/1.1 response without allowing a body to grow unbounded."""
        status_line = await self._readline(reader)
        try:
            _protocol, status_text, _reason = status_line.decode("ascii").rstrip("\r\n").split(" ", 2)
            status = int(status_text)
        except (UnicodeDecodeError, ValueError) as error:
            raise CallbackTransportError("callback returned an invalid HTTP status line") from error
        headers: dict[str, str] = {}
        header_bytes = len(status_line)
        while True:
            line = await self._readline(reader)
            header_bytes += len(line)
            if header_bytes > _HEADER_LIMIT:
                raise CallbackTransportError("callback response headers exceeded the configured limit")
            if line == b"\r\n":
                break
            try:
                name, value = line.decode("ascii").rstrip("\r\n").split(":", 1)
            except (UnicodeDecodeError, ValueError) as error:
                raise CallbackTransportError("callback returned malformed HTTP headers") from error
            headers[name.lower()] = value.strip()
        if headers.get("transfer-encoding", "").lower() == "chunked":
            await self._read_chunked_body(reader)
        elif "content-length" in headers:
            try:
                size = int(headers["content-length"])
            except ValueError as error:
                raise CallbackTransportError("callback returned an invalid Content-Length") from error
            if size < 0 or size > self._response_body_limit:
                raise CallbackTransportError("callback response body exceeded the configured limit")
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
                raise CallbackTransportError("callback returned an invalid chunk size") from error
            if size < 0:
                raise CallbackTransportError("callback returned an invalid chunk size")
            if size == 0:
                while await self._readline(reader) != b"\r\n":
                    pass
                return
            received += size
            if received > self._response_body_limit:
                raise CallbackTransportError("callback response body exceeded the configured limit")
            await self._readexactly(reader, size)
            if await self._readexactly(reader, 2) != b"\r\n":
                raise CallbackTransportError("callback returned malformed chunk framing")

    async def _read_to_eof(self, reader: asyncio.StreamReader) -> None:
        """Read an unknown-length, connection-close body within the configured limit."""
        received = 0
        while True:
            chunk = await self._read(reader, min(8_192, self._response_body_limit - received + 1))
            if not chunk:
                return
            received += len(chunk)
            if received > self._response_body_limit:
                raise CallbackTransportError("callback response body exceeded the configured limit")

    async def _readline(self, reader: asyncio.StreamReader) -> bytes:
        """Read one bounded CRLF line under the read deadline."""
        try:
            line = await asyncio.wait_for(reader.readline(), self._read_timeout)
        except TimeoutError as error:
            raise CallbackTransportError("callback response read timed out") from error
        except (ValueError, asyncio.LimitOverrunError) as error:
            raise CallbackTransportError("callback response line exceeded the configured limit") from error
        if not line or not line.endswith(b"\r\n"):
            raise CallbackTransportError("callback response ended unexpectedly")
        if len(line) > _HEADER_LIMIT:
            raise CallbackTransportError("callback response headers exceeded the configured limit")
        return line

    async def _readexactly(self, reader: asyncio.StreamReader, size: int) -> bytes:
        """Read an exact response segment under the read deadline."""
        try:
            return await asyncio.wait_for(reader.readexactly(size), self._read_timeout)
        except (TimeoutError, asyncio.IncompleteReadError) as error:
            raise CallbackTransportError("callback response ended unexpectedly") from error

    async def _read(self, reader: asyncio.StreamReader, size: int) -> bytes:
        """Read one response segment under the read deadline."""
        try:
            return await asyncio.wait_for(reader.read(size), self._read_timeout)
        except TimeoutError as error:
            raise CallbackTransportError("callback response read timed out") from error


__all__ = ["CallbackSender", "CallbackTransportError", "DefaultCallbackSender", "callback_url"]
