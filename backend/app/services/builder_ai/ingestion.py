from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import re
from urllib.parse import quote, urldefrag, urlsplit

import httpx

from app.core import egress
from app.core.config import settings
from app.core.errors import ValidationError
from app.services.builder_ai.parsing import html_text_and_links

ALLOWED_SUFFIXES = {
    ".txt", ".md", ".html", ".htm", ".json", ".yaml", ".yml",
    ".pdf", ".png", ".jpg", ".jpeg", ".webp",
}
ALLOWED_MIME_PREFIXES = ("text/", "image/")
ALLOWED_MIMES = {
    "application/json", "application/yaml", "application/x-yaml", "application/pdf",
}


def validate_upload(name: str, mime_type: str | None, content: bytes) -> None:
    suffix = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    allowed_mime = (mime_type or "") in ALLOWED_MIMES or any(
        (mime_type or "").startswith(prefix) for prefix in ALLOWED_MIME_PREFIXES
    )
    if suffix not in ALLOWED_SUFFIXES and not allowed_mime:
        raise ValidationError(
            "Định dạng tài liệu chưa được hỗ trợ.", code="AI_SOURCE_TYPE_UNSUPPORTED",
            details={"allowed": sorted(ALLOWED_SUFFIXES)},
        )
    if not content:
        raise ValidationError("Tệp rỗng.", code="AI_SOURCE_EMPTY")
    if len(content) > settings.builder_ai_source_max_bytes:
        raise ValidationError(
            "Tệp vượt quá giới hạn của AI Builder.", code="AI_SOURCE_TOO_LARGE",
            details={"max_bytes": settings.builder_ai_source_max_bytes},
        )


@dataclass(slots=True)
class CrawledSource:
    text: str
    size_bytes: int
    pages: list[dict[str, str | int]]


async def _bounded_body(response: httpx.Response, remaining: int) -> bytes:
    chunks: list[bytes] = []
    used = 0
    async for chunk in response.aiter_bytes():
        used += len(chunk)
        if used > remaining:
            raise ValidationError(
                "Tài liệu URL vượt quá giới hạn tải.", code="AI_SOURCE_TOO_LARGE",
                details={"max_bytes": settings.builder_ai_crawl_max_bytes},
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _fetch(client: httpx.AsyncClient, url: str, remaining: int) -> tuple[str, bytes, str]:
    current = url
    for _ in range(6):
        egress.check_url(current, field="source_url")
        async with client.stream("GET", current) as response:
            network_stream = response.extensions.get("network_stream")
            peer = network_stream.get_extra_info("server_addr") if network_stream else None
            if not isinstance(peer, (tuple, list)) or not peer:
                raise ValidationError(
                    "Không xác định được máy chủ tài liệu đã kết nối.",
                    code="AI_SOURCE_PEER_UNKNOWN",
                )
            egress.check_connected_address(current, str(peer[0]), field="source_url")
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise ValidationError("URL chuyển hướng thiếu Location.", code="AI_SOURCE_REDIRECT_INVALID")
                current = str(httpx.URL(current).join(location))
                continue
            response.raise_for_status()
            body = await _bounded_body(response, remaining)
            return current, body, response.headers.get("content-type", "").split(";", 1)[0]
    raise ValidationError("URL chuyển hướng quá nhiều lần.", code="AI_SOURCE_REDIRECT_LIMIT")


def _postman_collection_url(url: str) -> str | None:
    """Resolve a public Postman Documenter page to its collection JSON API."""
    parsed = urlsplit(url)
    if (parsed.hostname or "").casefold() != "documenter.getpostman.com":
        return None
    match = re.fullmatch(r"/view/([^/]+)/([^/]+)/?", parsed.path)
    if not match:
        return None
    owner, public_id = (quote(part, safe="") for part in match.groups())
    return (
        f"https://documenter.gw.postman.com/api/collections/{owner}/{public_id}"
        "?segregateAuth=true&versionTag=latest"
    )


async def _crawl_postman_collection(
    client: httpx.AsyncClient, source_url: str, collection_url: str,
) -> CrawledSource:
    final_url, body, mime = await _fetch(
        client, collection_url, settings.builder_ai_crawl_max_bytes,
    )
    expected = urlsplit(collection_url)
    final = urlsplit(final_url)
    if (final.scheme.lower(), final.hostname or "", final.port) != (
        expected.scheme.lower(), expected.hostname or "", expected.port,
    ):
        raise ValidationError(
            "Postman collection redirected to a different host.",
            code="AI_SOURCE_CROSS_ORIGIN_REDIRECT",
        )
    decoded = body.decode("utf-8", errors="replace")
    try:
        document = json.loads(decoded)
    except ValueError as exc:
        raise ValidationError(
            "Postman did not return valid collection JSON.",
            code="AI_SOURCE_POSTMAN_INVALID",
        ) from exc
    if (
        mime != "application/json"
        or not isinstance(document, dict)
        or not isinstance(document.get("info"), dict)
        or not isinstance(document.get("item"), list)
    ):
        raise ValidationError(
            "This Postman URL does not contain a supported collection.",
            code="AI_SOURCE_POSTMAN_INVALID",
        )
    return CrawledSource(
        text=decoded,
        size_bytes=len(body),
        pages=[{"url": source_url, "bytes": len(body), "depth": 0}],
    )


async def crawl_url(url: str) -> CrawledSource:
    egress.check_url_syntax(url, field="source_url")
    root = urlsplit(url)
    origin = (root.scheme.lower(), root.hostname or "", root.port)
    pending = deque([(url, 0)])
    seen: set[str] = set()
    pages: list[dict[str, str | int]] = []
    text_parts: list[str] = []
    total = 0

    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False,
        headers={"User-Agent": "AppBI-Connector-Builder/1.0", "Accept": "text/html,text/plain,application/json,application/yaml"},
    ) as client:
        postman_collection_url = _postman_collection_url(url)
        if postman_collection_url:
            return await _crawl_postman_collection(client, url, postman_collection_url)
        while pending and len(pages) < settings.builder_ai_crawl_max_pages:
            candidate, depth = pending.popleft()
            candidate = urldefrag(candidate)[0]
            if candidate in seen:
                continue
            seen.add(candidate)
            final_url, body, mime = await _fetch(
                client, candidate, settings.builder_ai_crawl_max_bytes - total,
            )
            final = urlsplit(final_url)
            if (final.scheme.lower(), final.hostname or "", final.port) != origin:
                raise ValidationError(
                    "URL chuyển sang một tên miền khác.", code="AI_SOURCE_CROSS_ORIGIN_REDIRECT",
                )
            total += len(body)
            decoded = body.decode("utf-8", errors="replace")
            links: list[str] = []
            if mime == "text/html" or "<html" in decoded[:500].lower():
                extracted, links = html_text_and_links(decoded, final_url)
            else:
                extracted = decoded
            pages.append({"url": final_url, "bytes": len(body), "depth": depth})
            text_parts.append(f"\n--- {final_url} ---\n{extracted}")
            if depth >= settings.builder_ai_crawl_max_depth:
                continue
            for link in links:
                parsed = urlsplit(link)
                if (parsed.scheme.lower(), parsed.hostname or "", parsed.port) == origin:
                    pending.append((link, depth + 1))
    if not pages:
        raise ValidationError("Không đọc được nội dung từ URL.", code="AI_SOURCE_EMPTY")
    return CrawledSource(text="\n".join(text_parts), size_bytes=total, pages=pages)
