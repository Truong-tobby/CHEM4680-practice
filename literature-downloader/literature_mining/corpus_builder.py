"""Build and merge the papers.jsonl corpus with DOI deduplication."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .config import METADATA_DIR, PAPERS_JSONL, SEARCH_QUERIES
from .crossref_client import enrich_item, get_work, normalize_doi, parse_item, search_works
from .tagger import assign_tags, enrich_keywords

logger = logging.getLogger(__name__)


def load_existing_dois(path: Path = PAPERS_JSONL) -> Set[str]:
    """Load DOIs already present in papers.jsonl."""
    dois: Set[str] = set()
    if not path.exists():
        return dois
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            doi = normalize_doi(rec.get("doi", ""))
            if doi:
                dois.add(doi)
    return dois


def _apply_tagger(record: Dict) -> Dict:
    tags = assign_tags(
        title=record.get("title", ""),
        abstract=record.get("abstract", ""),
        keywords=record.get("keywords", []),
        query_hit=record.get("query_hit", ""),
    )
    record["tags"] = tags
    record["keywords"] = enrich_keywords(
        record.get("keywords", []),
        query_hit=record.get("query_hit", ""),
        tags=tags,
    )
    return record


def _save_metadata(record: Dict) -> None:
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    safe_id = record["doi"].replace("/", "_")
    out_path = METADATA_DIR / f"{safe_id}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)


def append_record(record: Dict, path: Path = PAPERS_JSONL) -> None:
    """Append one paper record to papers.jsonl and save metadata copy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    _save_metadata(record)


def ingest_item(
    item: Dict,
    query_hit: str,
    existing: Set[str],
) -> Tuple[str, Optional[Dict]]:
    """
    Parse and optionally append one Crossref item.

    Returns (status, record) where status is 'new', 'skip', or 'error'.
    """
    doi_raw = item.get("DOI")
    if not doi_raw:
        return "error", None
    doi = normalize_doi(doi_raw)
    if doi in existing:
        return "skip", None
    item = enrich_item(item)
    record = parse_item(item, query_hit=query_hit)
    if record is None:
        return "error", None
    record = _apply_tagger(record)
    append_record(record)
    existing.add(doi)
    return "new", record


def ingest_doi(doi: str, existing: Optional[Set[str]] = None) -> Tuple[str, Optional[Dict]]:
    """Fetch and ingest a single DOI."""
    existing = existing if existing is not None else load_existing_dois()
    norm = normalize_doi(doi)
    if norm in existing:
        logger.info("Skip existing DOI: %s", norm)
        return "skip", None
    try:
        item = get_work(doi)
        return ingest_item(item, query_hit=f"doi:{norm}", existing=existing)
    except Exception as exc:
        logger.error("Failed to fetch DOI %s: %s", doi, exc)
        return "error", None


def search_and_ingest(
    query: str,
    rows: int = 20,
    existing: Optional[Set[str]] = None,
) -> Dict[str, int]:
    """Search Crossref and ingest results into papers.jsonl."""
    existing = existing if existing is not None else load_existing_dois()
    stats = {"new": 0, "skip": 0, "error": 0}
    logger.info("Searching Crossref: query=%r rows=%d", query, rows)
    try:
        items = search_works(query, rows=rows)
    except Exception as exc:
        logger.error("Search failed for query=%r: %s", query, exc)
        stats["error"] += 1
        return stats
    for item in items:
        status, _ = ingest_item(item, query_hit=query, existing=existing)
        stats[status] = stats.get(status, 0) + 1
    return stats


def build_corpus(
    queries: Optional[List[str]] = None,
    rows: int = 20,
    seed_dois: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Run all search queries and optional seed DOIs; return aggregate stats."""
    queries = queries or SEARCH_QUERIES
    seed_dois = seed_dois or []
    existing = load_existing_dois()
    totals = {"new": 0, "skip": 0, "error": 0}

    for query in queries:
        stats = search_and_ingest(query, rows=rows, existing=existing)
        for key, val in stats.items():
            totals[key] += val
        logger.info(
            "Query done: %r -> new=%d skip=%d error=%d",
            query,
            stats["new"],
            stats["skip"],
            stats["error"],
        )

    for doi in seed_dois:
        status, _ = ingest_doi(doi, existing=existing)
        totals[status] = totals.get(status, 0) + 1

    logger.info(
        "Corpus build complete: new=%d skip=%d error=%d total_in_corpus=%d",
        totals["new"],
        totals["skip"],
        totals["error"],
        len(existing),
    )
    return totals
