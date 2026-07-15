"""Crossref REST API client for literature metadata mining."""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from .config import (
    CROSSREF_API_BASE,
    CROSSREF_SELECT_FIELDS,
    MAX_RETRIES,
    RATE_LIMIT_SEC,
    RETRY_BACKOFF_SEC,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": USER_AGENT}


def normalize_doi(doi: str) -> str:
    """Normalize DOI to lowercase bare form without URL prefix."""
    if not doi:
        return ""
    d = doi.strip()
    prefixes = (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    )
    for prefix in prefixes:
        if d.lower().startswith(prefix):
            d = d[len(prefix) :]
            break
    if d.lower().startswith("doi:"):
        d = d[4:]
    return d.strip().lower()


def strip_abstract_xml(raw: Optional[str]) -> str:
    """Strip JATS/HTML tags from Crossref abstract field."""
    if not raw:
        return ""
    text = raw.strip()
    if "<" in text and ">" in text:
        soup = BeautifulSoup(text, "lxml")
        text = soup.get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_year(item: Dict[str, Any]) -> Optional[int]:
    for key in ("published-print", "published-online", "created", "issued"):
        block = item.get(key) or {}
        parts = block.get("date-parts") or []
        if parts and isinstance(parts[0], list) and parts[0]:
            year = parts[0][0]
            if isinstance(year, int):
                return year
    return None


def _extract_authors(item: Dict[str, Any]) -> List[str]:
    authors: List[str] = []
    for author in item.get("author") or []:
        given = (author.get("given") or "").strip()
        family = (author.get("family") or "").strip()
        if given and family:
            authors.append(f"{given} {family}")
        elif family:
            authors.append(family)
        elif given:
            authors.append(given)
    return authors


def _pick_link(links: List[Dict[str, Any]]) -> str:
    if not links:
        return ""

    def score(link: Dict[str, Any]) -> int:
        s = 0
        app = (link.get("intended-application") or "").lower()
        ctype = (link.get("content-type") or "").lower()
        if app in ("text-mining", "tdm"):
            s += 3
        if app == "unspecified":
            s += 1
        if "pdf" in ctype:
            s += 2
        if "html" in ctype:
            s += 1
        return s

    best = sorted(links, key=score, reverse=True)[0]
    return (best.get("URL") or best.get("url") or "").strip()


def _request(url: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=_HEADERS, timeout=30)
            if resp.status_code == 429:
                wait = RETRY_BACKOFF_SEC * (attempt + 1)
                logger.warning("Rate limited (429); waiting %.1fs before retry", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF_SEC * (attempt + 1)
                logger.warning("Request failed (%s); retry in %.1fs", exc, wait)
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Crossref request failed after retries: {last_exc}")


def search_works(query: str, rows: int = 20) -> List[Dict[str, Any]]:
    """Search Crossref /works by keyword query."""
    params = {
        "query": query,
        "rows": str(rows),
        "select": CROSSREF_SELECT_FIELDS,
        "filter": "from-pub-date:2018",
    }
    data = _request(CROSSREF_API_BASE, params=params)
    items = data.get("message", {}).get("items") or []
    time.sleep(RATE_LIMIT_SEC)
    return items


def get_work(doi: str) -> Dict[str, Any]:
    """Fetch a single work by DOI (full metadata; no select filter)."""
    encoded = urllib.parse.quote(normalize_doi(doi), safe="")
    url = f"{CROSSREF_API_BASE}/{encoded}"
    data = _request(url)
    time.sleep(RATE_LIMIT_SEC)
    return data.get("message") or {}


def enrich_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch full metadata when search results lack abstract or other fields."""
    doi = item.get("DOI")
    if not doi:
        return item
    needs_fetch = not strip_abstract_xml(item.get("abstract"))
    if needs_fetch:
        full = get_work(doi)
        if full:
            merged = dict(item)
            for key, val in full.items():
                if key not in merged or not merged.get(key):
                    merged[key] = val
            return merged
    return item


def parse_item(item: Dict[str, Any], query_hit: str = "") -> Optional[Dict[str, Any]]:
    """Normalize a Crossref work item into our paper schema."""
    raw_doi = item.get("DOI")
    if not raw_doi:
        return None

    titles = item.get("title") or []
    containers = item.get("container-title") or []
    subjects = [str(s) for s in (item.get("subject") or [])]

    return {
        "doi": normalize_doi(raw_doi),
        "title": titles[0] if titles else "",
        "authors": _extract_authors(item),
        "year": _extract_year(item),
        "journal": containers[0] if containers else "",
        "abstract": strip_abstract_xml(item.get("abstract")),
        "link": _pick_link(item.get("link") or []),
        "keywords": subjects,
        "tags": [],
        "source": "crossref",
        "query_hit": query_hit,
    }
