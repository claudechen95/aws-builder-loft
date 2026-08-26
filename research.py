"""Live-web tools and source tracking for the research agent."""
from __future__ import annotations

import io
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

SEARCH_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (compatible; AcmeResearchAgent/1.0; "
    "+https://example.com/research-agent)"
)
TIMEOUT_S = 20
MAX_DOWNLOAD_BYTES = 12 * 1024 * 1024
MAX_TEXT_CHARS = 8_000
MAX_REDIRECTS = 5


@dataclass
class Source:
    source_id: str
    title: str
    url: str
    snippet: str = ""
    fetched: bool = False


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _public_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public http(s) URLs can be read.")
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("Local URLs cannot be read.")
    try:
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve {host}.") from exc
    for address in {item[4][0] for item in addresses}:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Private, local, and reserved network addresses are blocked.")
    return parsed.geturl()


def _unwrap_ddg_url(url: str) -> str:
    absolute = urljoin("https://duckduckgo.com", url)
    parsed = urlparse(absolute)
    target = parse_qs(parsed.query).get("uddg")
    return unquote(target[0]) if target else absolute


class ResearchSession:
    """A pristine source notebook for one command-adapter invocation."""

    def __init__(self) -> None:
        self.sources: dict[str, Source] = {}
        self._ids_by_url: dict[str, str] = {}
        self.search_count = 0
        self.read_count = 0

    def _register(self, url: str, title: str = "", snippet: str = "") -> Source:
        normalized = url.strip()
        existing = self._ids_by_url.get(normalized)
        if existing:
            source = self.sources[existing]
            if title and not source.title:
                source.title = _clean(title)
            if snippet and not source.snippet:
                source.snippet = _clean(snippet)
            return source
        source_id = f"S{len(self.sources) + 1}"
        source = Source(source_id, _clean(title), normalized, _clean(snippet))
        self.sources[source_id] = source
        self._ids_by_url[normalized] = source_id
        return source

    def web_search(self, query: str, max_results: int = 6) -> dict:
        query = _clean(query)
        if not query:
            return {"error": "A search query is required."}
        limit = max(1, min(int(max_results), 8))
        response = httpx.post(
            SEARCH_URL,
            data={"q": query},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT_S,
            follow_redirects=True,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for result in soup.select(".result"):
            link = result.select_one("a.result__a")
            if link is None or not link.get("href"):
                continue
            url = _unwrap_ddg_url(str(link["href"]))
            try:
                url = _public_url(url)
            except ValueError:
                continue
            snippet_node = result.select_one(".result__snippet")
            source = self._register(
                url,
                link.get_text(" ", strip=True),
                snippet_node.get_text(" ", strip=True) if snippet_node else "",
            )
            results.append({
                "source_id": source.source_id,
                "title": source.title,
                "url": source.url,
                "snippet": source.snippet,
            })
            if len(results) >= limit:
                break
        self.search_count += 1
        return {"query": query, "results": results}

    def _download(self, url: str) -> tuple[httpx.Response, str]:
        current = _public_url(url)
        with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S) as client:
            for _ in range(MAX_REDIRECTS + 1):
                response = client.get(current, follow_redirects=False)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        response.raise_for_status()
                    current = _public_url(urljoin(current, location))
                    continue
                response.raise_for_status()
                length = int(response.headers.get("content-length") or 0)
                if length > MAX_DOWNLOAD_BYTES or len(response.content) > MAX_DOWNLOAD_BYTES:
                    raise ValueError("Document is too large to read safely.")
                return response, current
        raise ValueError("Too many redirects.")

    def read_url(self, url: str) -> dict:
        response, final_url = self._download(url)
        content_type = response.headers.get("content-type", "").lower()
        source = self._register(final_url)
        if "pdf" in content_type or final_url.lower().endswith(".pdf"):
            reader = PdfReader(io.BytesIO(response.content))
            pages = []
            for page in reader.pages[:40]:
                pages.append(page.extract_text() or "")
                if sum(len(item) for item in pages) >= MAX_TEXT_CHARS:
                    break
            text = _clean("\n".join(pages))[:MAX_TEXT_CHARS]
            title = source.title or final_url.rsplit("/", 1)[-1] or "PDF document"
        elif "html" in content_type or not content_type:
            soup = BeautifulSoup(response.text, "html.parser")
            for node in soup(["script", "style", "noscript", "svg", "form", "nav", "footer"]):
                node.decompose()
            title = _clean(soup.title.get_text(" ")) if soup.title else source.title
            body = soup.find("main") or soup.find("article") or soup.body or soup
            text = _clean(body.get_text(" ", strip=True))[:MAX_TEXT_CHARS]
        elif content_type.startswith("text/"):
            title = source.title or final_url.rsplit("/", 1)[-1] or "Text document"
            text = _clean(response.text)[:MAX_TEXT_CHARS]
        else:
            return {"error": f"Unsupported content type: {content_type or 'unknown'}"}
        if not text:
            return {"error": "The page returned no readable text."}
        source.title = title or source.title or final_url
        source.fetched = True
        self.read_count += 1
        return {
            "source_id": source.source_id,
            "title": source.title,
            "url": source.url,
            "content": text,
            "truncated": len(text) == MAX_TEXT_CHARS,
        }

    def manifest(self) -> list[dict]:
        return [
            {"source_id": source.source_id, "title": source.title, "url": source.url}
            for source in self.sources.values()
            if source.fetched
        ]

    def source_block(self) -> str:
        sources = self.manifest()
        if not sources:
            return "--- VERIFIED SOURCES ---\nNo sources were successfully read."
        lines = ["--- VERIFIED SOURCES ---"]
        lines.extend(
            f"[{item['source_id']}] {item['title']} — {item['url']}"
            for item in sources
        )
        return "\n".join(lines)
