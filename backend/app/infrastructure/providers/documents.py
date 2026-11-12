"""Reading what a traveller shares: a web page, a PDF or a picture behind a link, and an uploaded PDF.

A link the traveller pastes is fetched by this server, so it is treated as hostile:

- only ``http`` and ``https`` on their default ports, with no user or password in the address;
- the host must resolve only to public addresses (no loopback, private, link-local or reserved
  range), and the request is sent to the address that was checked, so a name that resolves
  differently a moment later cannot move it;
- redirects are followed by hand, at most ``MAX_REDIRECTS``, each target checked the same way;
- the body is read up to a byte limit and only a few content types are accepted.

What comes back is text (or a picture's bytes) and nothing else: no script is run and no page is
rendered. The caller screens the text for instructions like any other fetched text.
"""

from __future__ import annotations

import asyncio
import base64
import io
import ipaddress
import re
import socket
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import FailureKind
from app.infrastructure.http import current_transport

MAX_REDIRECTS = 3
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
_SKIPPED_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "template",
    "iframe",
    "nav",
    "footer",
    "form",
    "button",
}
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "tr",
    "table",
    "section",
    "article",
    "main",
    "header",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "pre",
    "dd",
    "dt",
}
_SPACES = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")

DocumentKind = Literal["page", "pdf", "image", "text"]


class FetchedDocument(BaseModel):
    """What a link held: readable text, or a picture's bytes (base64) for a writer that can see."""

    url: str
    kind: DocumentKind
    title: str = ""
    text: str = ""
    media_type: str = ""
    #: The bytes of a picture, base64; empty for anything else.
    data: str = ""


def _refuse(message: str) -> ProviderError:
    return ProviderError(
        message, kind=FailureKind.INVALID_REQUEST, source="web-fetch", retryable=False
    )


def _public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class _Text(HTMLParser):
    """The visible text of a page and its title; scripts, styles, forms and navigation are left out."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and not self.title:
            values = dict(attrs)
            if (
                values.get("property") or values.get("name") or ""
            ).lower() == "og:title":
                self.title = (values.get("content") or "").strip()
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS:
            self._skip = max(self._skip - 1, 0)
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title = f"{self.title}{data}"
        elif not self._skip:
            self.parts.append(data)


def html_text(html: str, limit: int) -> tuple[str, str]:
    """``(title, text)`` of a page: text only, whitespace collapsed, cut at ``limit`` characters."""
    parser = _Text()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass  # what was read before a malformed tag still counts
    text = _SPACES.sub(" ", "".join(parser.parts))
    text = _BLANK_LINES.sub(
        "\n", "\n".join(line.strip() for line in text.split("\n"))
    ).strip()
    return " ".join(parser.title.split())[:200], text[:limit]


def pdf_text(data: bytes, *, max_pages: int, limit: int) -> str:
    """The text layer of a PDF's first pages. Empty for a scan (no text layer). CPU work: call off the loop."""
    try:
        from pypdf import PdfReader
    except (
        ImportError
    ) as exc:  # the package is optional at import time, required to read a PDF
        raise ProviderError(
            "Reading a PDF needs the pypdf package.",
            kind=FailureKind.SERVER_ERROR,
            source="pdf",
            retryable=False,
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise _refuse("The PDF is password-protected.")
        parts: list[str] = []
        size = 0
        for index, page in enumerate(reader.pages):
            if index >= max_pages or size >= limit:
                break
            chunk = (page.extract_text() or "").strip()
            if chunk:
                parts.append(chunk)
                size += len(chunk)
    except ProviderError:
        raise
    except Exception as exc:
        raise _refuse(f"The PDF could not be read ({type(exc).__name__}).") from exc
    return "\n".join(parts)[:limit]


def image_type(data: bytes) -> str:
    """The media type the bytes themselves say they are, or "" when they are not a picture we accept."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


class DocumentReader:
    """The ``web.fetch`` tool and the PDF reader the intake step uses."""

    name = "web-fetch"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.available = bool(settings.enable_intake)

    @staticmethod
    def describe(data: bytes) -> tuple[str, str]:
        """``(kind, media_type)`` the bytes themselves say they are: "pdf", "image" or "" (neither)."""
        if data.startswith(b"%PDF-"):
            return "pdf", "application/pdf"
        media_type = image_type(data)
        return ("image", media_type) if media_type else ("", "")

    async def read_pdf(self, *, data: bytes) -> str:
        return await asyncio.to_thread(
            pdf_text,
            data,
            max_pages=self.settings.intake_max_pdf_pages,
            limit=self.settings.intake_max_chars,
        )

    async def _addresses(self, host: str) -> list[str]:
        """The public addresses ``host`` resolves to. Refused when any address it resolves to is not public."""
        try:
            ipaddress.ip_address(host)
            found = [host]
        except ValueError:
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(
                    host, None, type=socket.SOCK_STREAM
                )
            except OSError as exc:
                raise ProviderNoData(
                    "The link's host could not be found.", source=self.name
                ) from exc
            found = list(dict.fromkeys(str(info[4][0]) for info in infos))
        if not found or not all(_public(address) for address in found):
            raise _refuse("That link points to an address this server does not open.")
        return found

    async def _checked(
        self, url: str
    ) -> tuple[str, str, dict[str, str], dict[str, str]]:
        """``(request_url, host, headers, extensions)`` for a link that may be opened, pinned to a checked address."""
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise _refuse("Only http and https links can be opened.")
        if parts.username or parts.password:
            raise _refuse("A link with a user name or password in it is not opened.")
        try:
            port = parts.port
        except ValueError as exc:
            raise _refuse("The link's port is not valid.") from exc
        if port not in (None, 80 if parts.scheme == "http" else 443):
            raise _refuse("Only links on the default web ports are opened.")
        host = parts.hostname
        headers = {
            "User-Agent": self.settings.user_agent,
            "Accept": "text/html,application/pdf,image/*,text/plain",
        }
        if current_transport() is not None:
            # A test or replay transport: no network, so there is no address to check or pin.
            return url, host, headers, {}
        address = (await self._addresses(host))[0]
        literal = f"[{address}]" if ":" in address else address
        target = parts._replace(
            netloc=literal if port is None else f"{literal}:{port}"
        ).geturl()
        headers["Host"] = host
        return target, host, headers, {"sni_hostname": host}

    async def fetch(self, *, url: str) -> FetchedDocument:
        """Open one link and return its text or picture. Raises ``ProviderError`` when it may not or cannot be read."""
        if not self.available:
            raise _refuse("Reading links is turned off.")
        limit = self.settings.intake_max_fetch_bytes
        current = url.strip()
        async with httpx.AsyncClient(
            timeout=self.settings.intake_fetch_timeout_seconds,
            transport=current_transport(),
            follow_redirects=False,
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                target, _, headers, extensions = await self._checked(current)
                try:
                    async with client.stream(
                        "GET", target, headers=headers, extensions=extensions
                    ) as response:
                        if response.status_code in {
                            301,
                            302,
                            303,
                            307,
                            308,
                        } and response.headers.get("location"):
                            current = urljoin(current, response.headers["location"])
                            continue
                        if response.status_code >= 400:
                            raise ProviderError(
                                f"The page answered HTTP {response.status_code}.",
                                kind=FailureKind.NO_DATA
                                if response.status_code == 404
                                else FailureKind.SERVER_ERROR,
                                source=self.name,
                                retryable=False,
                            )
                        media_type = (
                            response.headers.get("content-type", "")
                            .split(";")[0]
                            .strip()
                            .lower()
                        )
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > limit:
                                raise _refuse("The page is too large to read.")
                        encoding = response.encoding or "utf-8"
                except httpx.TimeoutException as exc:
                    raise ProviderError(
                        "The page did not answer in time.",
                        kind=FailureKind.TIMEOUT,
                        source=self.name,
                    ) from exc
                except httpx.HTTPError as exc:
                    raise ProviderError(
                        "The page could not be reached.",
                        kind=FailureKind.NETWORK,
                        source=self.name,
                    ) from exc
                return await self._document(current, media_type, bytes(body), encoding)
        raise _refuse("The link redirects too many times.")

    async def _document(
        self, url: str, media_type: str, body: bytes, encoding: str
    ) -> FetchedDocument:
        chars = self.settings.intake_max_chars
        if media_type == "application/pdf" or body.startswith(b"%PDF-"):
            text = await self.read_pdf(data=body)
            if not text.strip():
                raise ProviderNoData(
                    "The PDF has no text to read (it may be a scan).", source=self.name
                )
            return FetchedDocument(
                url=url, kind="pdf", text=text, media_type="application/pdf"
            )
        if media_type.startswith("image/"):
            actual = image_type(body)
            if not actual:
                raise _refuse("That picture format is not supported.")
            return FetchedDocument(
                url=url,
                kind="image",
                media_type=actual,
                data=base64.b64encode(body).decode(),
            )
        if media_type in {"text/html", "application/xhtml+xml", ""}:
            try:
                html = body.decode(encoding, "replace")
            except LookupError:
                html = body.decode("utf-8", "replace")
            title, text = await asyncio.to_thread(html_text, html, chars)
            if len(text) < 80:
                raise ProviderNoData(
                    "The page has no readable text (it may need a browser or a sign-in).",
                    source=self.name,
                )
            return FetchedDocument(
                url=url, kind="page", title=title, text=text, media_type="text/html"
            )
        if media_type == "text/plain":
            text = body.decode(encoding, "replace").strip()[:chars]
            if not text:
                raise ProviderNoData("The link holds no text.", source=self.name)
            return FetchedDocument(
                url=url, kind="text", text=text, media_type=media_type
            )
        raise _refuse(
            "That kind of file cannot be read (pages, PDFs and pictures can)."
        )
