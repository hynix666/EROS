"""Connector framework — canonical §6.5.1.

The abstraction is the document's own::

    class Connector(ABC):
        def search(self, query, max_results) -> List[Source]
        def fetch(self, url) -> Artifact
        @property rate_limit -> RateLimit

API-first (Bing, OpenAlex, Crossref); no direct HTML scraping without an
API fallback. Politeness: 1 request/sec/host, robots.txt respected.
Bing activates only when BING_API_KEY is set. Wikipedia is included as a
key-free demo/general connector so the out-of-box loop has breadth
[judgment — additive, registered via connectors.yaml like any other].
"""
from __future__ import annotations

import logging
import os
import threading
import time
import urllib.robotparser
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

from eros.errors import ArtifactError

logger = logging.getLogger(__name__)

_UA = "EROS/3.2 (local-first research agent; polite; +https://localhost)"


@dataclass(frozen=True)
class RateLimit:
    requests_per_sec: float = 1.0  # canonical politeness: 1 rps/host


@dataclass(frozen=True)
class Source:
    title: str
    url: str
    snippet: str
    connector: str


@dataclass(frozen=True)
class FetchedArtifact:
    url: str
    content: bytes
    content_type: str
    source: str


class _Politeness:
    """Per-host token gate + robots.txt cache (shared by all connectors)."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def wait(self, url: str, rps: float) -> None:
        host = urlparse(url).netloc
        with self._lock:
            now = time.monotonic()
            gap = 1.0 / max(rps, 0.01)
            wait = self._last.get(host, 0.0) + gap - now
            self._last[host] = max(now, self._last.get(host, 0.0) + gap)
        if wait > 0:
            time.sleep(wait)

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                rp.set_url(base + "/robots.txt")
                rp.read()
                self._robots[base] = rp
            except Exception:
                self._robots[base] = None  # unreachable robots → permissive
        rp = self._robots[base]
        return True if rp is None else rp.can_fetch(_UA, url)


POLITENESS = _Politeness()


class Connector(ABC):
    name: str = "abstract"

    @abstractmethod
    def search(self, query: str, max_results: int) -> list[Source]: ...

    @abstractmethod
    def fetch(self, url: str) -> FetchedArtifact: ...

    @property
    @abstractmethod
    def rate_limit(self) -> RateLimit: ...

    # Shared polite fetch used by every concrete connector.
    # check_robots=False is for *invited* API endpoints (search APIs invite
    # programmatic use; robots.txt governs page crawling). Rate limiting
    # always applies regardless.
    def _get(self, url: str, *, check_robots: bool = True, **kw) -> httpx.Response:
        if check_robots and not POLITENESS.allowed(url):
            raise ArtifactError(f"robots.txt disallows {url}", url=url)
        POLITENESS.wait(url, self.rate_limit.requests_per_sec)
        r = httpx.get(url,
                      headers={"User-Agent": _UA,
                               "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8"},
                      timeout=30.0, follow_redirects=True, **kw)
        r.raise_for_status()
        return r

    def fetch_default(self, url: str) -> FetchedArtifact:
        r = self._get(url)
        return FetchedArtifact(url=str(r.url), content=r.content,
                               content_type=r.headers.get("content-type", "text/html"),
                               source=self.name)


class OpenAlexConnector(Connector):
    name = "openalex"

    @property
    def rate_limit(self) -> RateLimit:
        return RateLimit(1.0)

    def search(self, query: str, max_results: int) -> list[Source]:
        r = self._get(f"https://api.openalex.org/works?search={quote(query)}"
                      f"&per-page={min(max_results, 25)}&mailto=eros@localhost",
                      check_robots=False)
        out = []
        for w in r.json().get("results", []):
            loc = (w.get("best_oa_location") or w.get("primary_location") or {}) or {}
            url = loc.get("landing_page_url") or w.get("doi") or w.get("id")
            if not url:
                continue
            abstract = ""
            inv = w.get("abstract_inverted_index")
            if inv:
                pos = sorted((p, word) for word, ps in inv.items() for p in ps)
                abstract = " ".join(word for _, word in pos)[:400]
            out.append(Source(w.get("display_name") or "(untitled)", url, abstract, self.name))
        return out

    def fetch(self, url: str) -> FetchedArtifact:
        return self.fetch_default(url)


class CrossrefConnector(Connector):
    name = "crossref"

    @property
    def rate_limit(self) -> RateLimit:
        return RateLimit(1.0)

    def search(self, query: str, max_results: int) -> list[Source]:
        r = self._get(f"https://api.crossref.org/works?query={quote(query)}"
                      f"&rows={min(max_results, 20)}&mailto=eros@localhost",
                      check_robots=False)
        out = []
        for it in r.json().get("message", {}).get("items", []):
            url = it.get("URL")
            if not url:
                continue
            title = "; ".join(it.get("title") or ["(untitled)"])
            out.append(Source(title, url, (it.get("abstract") or "")[:400], self.name))
        return out

    def fetch(self, url: str) -> FetchedArtifact:
        return self.fetch_default(url)


class WikipediaConnector(Connector):
    name = "wikipedia"

    @property
    def rate_limit(self) -> RateLimit:
        return RateLimit(1.0)

    def search(self, query: str, max_results: int) -> list[Source]:
        r = self._get("https://en.wikipedia.org/w/api.php?action=query&list=search"
                      f"&srsearch={quote(query)}&srlimit={min(max_results, 10)}"
                      "&format=json&utf8=1", check_robots=False)
        out = []
        for hit in r.json().get("query", {}).get("search", []):
            title = hit["title"]
            url = "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_"))
            snippet = httpx_strip_tags(hit.get("snippet", ""))
            out.append(Source(title, url, snippet, self.name))
        return out

    def fetch(self, url: str) -> FetchedArtifact:
        # Prefer the REST html endpoint: clean article markup, no chrome.
        if "/wiki/" in url:
            title = url.rsplit("/wiki/", 1)[1]
            try:
                r = self._get(
                    f"https://en.wikipedia.org/api/rest_v1/page/html/{title}",
                    check_robots=False)
                return FetchedArtifact(url=url, content=r.content,
                                       content_type="text/html", source=self.name)
            except httpx.HTTPError:
                pass  # fall through to the ordinary page fetch
        return self.fetch_default(url)


class BingConnector(Connector):
    name = "bing"

    def __init__(self) -> None:
        self.key = os.environ.get("BING_API_KEY")

    @property
    def rate_limit(self) -> RateLimit:
        return RateLimit(1.0)

    def search(self, query: str, max_results: int) -> list[Source]:
        if not self.key:
            return []
        POLITENESS.wait("https://api.bing.microsoft.com/", self.rate_limit.requests_per_sec)
        r = httpx.get("https://api.bing.microsoft.com/v7.0/search",
                      params={"q": query, "count": min(max_results, 20)},
                      headers={"Ocp-Apim-Subscription-Key": self.key, "User-Agent": _UA},
                      timeout=30.0)
        r.raise_for_status()
        return [Source(v.get("name", ""), v.get("url", ""), v.get("snippet", ""), self.name)
                for v in r.json().get("webPages", {}).get("value", []) if v.get("url")]

    def fetch(self, url: str) -> FetchedArtifact:
        return self.fetch_default(url)


def httpx_strip_tags(s: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", s)


REGISTRY: dict[str, type[Connector]] = {
    "openalex": OpenAlexConnector,
    "crossref": CrossrefConnector,
    "wikipedia": WikipediaConnector,
    "bing": BingConnector,
}


def load_connectors(enabled: list[str] | None = None) -> list[Connector]:
    """Instantiate connectors (order = fan-out priority). Defaults to the
    key-free set plus Bing when a key is present."""
    if enabled is None:
        # Wikipedia first: key-free, fetchable, broad — the out-of-box loop
        # should land evidence before the paywalled-DOI long tail.
        enabled = ["wikipedia", "openalex", "crossref", "bing"]
    out: list[Connector] = []
    for name in enabled:
        cls = REGISTRY.get(name)
        if cls is None:
            logger.warning("unknown connector %r in configuration — skipped", name)
            continue
        c = cls()
        if name == "bing" and not getattr(c, "key", None):
            continue  # key-gated
        out.append(c)
    return out
