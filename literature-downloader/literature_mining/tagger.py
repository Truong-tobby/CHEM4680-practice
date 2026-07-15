"""Rule-based keyword tagging for retrosynthesis metrics papers."""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Set

TAG_RULES: Dict[str, List[str]] = {
    "single-step metrics": [
        "single-step",
        "single step",
        "one-step",
        "one step",
        "top-k",
        "top k",
        "top-1",
        "top-10",
        "round-trip",
        "round trip",
    ],
    "route metrics": [
        "route",
        "multi-step",
        "multi step",
        "pathway",
        "paroutes",
        "route-level",
        "route level",
        "synthesis planning",
    ],
    "failure modes": [
        "failure mode",
        "failure modes",
        "incorrect",
        "error analysis",
        "quantifying the failure",
    ],
    "benchmarking": [
        "benchmark",
        "benchmarking",
        "evaluation",
        "comparison",
        "uspto",
    ],
    "coverage diversity": [
        "coverage",
        "diversity",
        "class diversity",
    ],
    "accessibility": [
        "synthetic accessibility",
        "sa score",
        "sc score",
        "complexity",
    ],
}


def _tokenize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def assign_tags(
    title: str = "",
    abstract: str = "",
    keywords: Iterable[str] | None = None,
    query_hit: str = "",
) -> List[str]:
    """Assign tags based on keyword rules over title, abstract, keywords, query."""
    keywords = keywords or []
    blob = _tokenize(" ".join([title, abstract, " ".join(keywords), query_hit]))
    tags: Set[str] = set()
    for tag, patterns in TAG_RULES.items():
        for pattern in patterns:
            if pattern.lower() in blob:
                tags.add(tag)
                break
    return sorted(tags)


def enrich_keywords(
    existing: Iterable[str],
    query_hit: str = "",
    tags: Iterable[str] | None = None,
) -> List[str]:
    """Merge Crossref subjects with query-derived keywords."""
    seen: Set[str] = set()
    result: List[str] = []
    for kw in list(existing) + ([query_hit] if query_hit else []) + list(tags or []):
        key = kw.strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(kw.strip())
    return result
