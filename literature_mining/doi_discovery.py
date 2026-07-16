"""
DOI discovery utilities for LLM_RAG_V3.

OpenAlex-based discovery for retrosynthesis / CASP evaluation papers.
CLI entrypoint: scripts/doi_discovery.py → main_openalex().
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import requests

from config import USER_AGENT

try:
    from tqdm import tqdm  # type: ignore
except Exception:  # pragma: no cover
    tqdm = None


ROOT = Path(__file__).resolve().parents[1]
# Prefer results/ (existing project layout); fall back to data/.
RESULTS_JOURNAL_DIR = ROOT / "results" / "journals"
RESULTS_DOI_DIR = ROOT / "results" / "doi"
DATA_JOURNAL_DIR = ROOT / "data" / "journals"
DATA_DOI_DIR = ROOT / "data" / "doi"

OPENALEX_WORKS_URL = "https://api.openalex.org/works"

# ---------------------------------------------------------------------------
# Search queries (retrosynthesis / CASP evaluation)
# ---------------------------------------------------------------------------

SEARCH_QUERIES: List[str] = [
    "single-step retrosynthesis",
    "round-trip accuracy retrosynthesis",
    "retrosynthesis coverage diversity",
    "PaRoutes retrosynthesis",
    "retrosynthesis benchmarking",
    "retrosynthesis route metrics",
    "failure modes one-step retrosynthesis",
    "computer-aided synthesis planning evaluation",
    "multi-step retrosynthesis evaluation metrics",
    "synthetic accessibility score retrosynthesis",
]

# Strong topical signals used by the classifier.
RETROSYNTHESIS_POSITIVE_TERMS: List[str] = [
    "retrosynthesis",
    "retrosynthetic",
    "synthesis planning",
    "computer-aided synthesis",
    "computer aided synthesis",
    "casp",
    "paroutes",
    "round-trip accuracy",
    "round trip accuracy",
    "synthetic accessibility",
    "single-step retrosynthesis",
    "single step retrosynthesis",
    "one-step retrosynthesis",
    "one step retrosynthesis",
    "multi-step retrosynthesis",
    "multistep retrosynthesis",
    "reaction pathway",
    "route prediction",
    "template-based retrosynthesis",
    "template free retrosynthesis",
    "template-free retrosynthesis",
]

RETROSYNTHESIS_RELATED_TERMS: List[str] = [
    "reaction prediction",
    "forward synthesis",
    "backward synthesis",
    "molecular transformer",
    "synthesis route",
    "chemical synthesis planning",
    "ai-driven synthesis",
    "machine learning synthesis",
    "graph-based retrosynthesis",
    "sa score",
    "synthetic complexity",
]

# Clearly off-topic for this search (keep light — avoid over-filtering).
NOISE_FIELD_TERMS: List[str] = [
]


@dataclass
class OpenAlexWorkRecord:
    doi: str
    journal: str
    publisher: Optional[str]
    year: Optional[int]
    work_type: Optional[str]
    source_query: str
    category: str  # target_retrosynthesis / target_related


def _resolve_journal_dir() -> Path:
    if RESULTS_JOURNAL_DIR.exists():
        return RESULTS_JOURNAL_DIR
    return DATA_JOURNAL_DIR


def _resolve_doi_dir() -> Path:
    if RESULTS_DOI_DIR.exists() or not DATA_DOI_DIR.exists():
        return RESULTS_DOI_DIR
    return DATA_DOI_DIR


def _reconstruct_abstract(inverted_index: Optional[Dict]) -> str:
    if not inverted_index:
        return ""
    word_index = []
    for k, v in inverted_index.items():
        for index in v:
            word_index.append((k, index))
    word_index.sort(key=lambda x: x[1])
    return " ".join([w for w, _ in word_index])


def _classify_openalex_work(work: Dict, source_query: str) -> str:
    """
    Classify an OpenAlex work for retrosynthesis / CASP relevance.

    Returns one of:
        - "target_retrosynthesis" : clear retrosynthesis / CASP evaluation paper
        - "target_related"        : closely related synthesis-planning / metrics
        - "noise_field"           : clearly off-topic
        - "unknown_context"       : no usable topical signal
    """
    title = (work.get("title") or "").lower()
    abstract = _reconstruct_abstract(work.get("abstract_inverted_index")).lower()
    concepts = [c.get("display_name", "").lower() for c in work.get("concepts") or []]
    query_l = (source_query or "").lower()
    full_text = f"{title} {abstract} {' '.join(concepts)} {query_l}"

    # Off-topic domains without retrosynthesis language.
    if any(k in full_text for k in NOISE_FIELD_TERMS):
        if not any(t in full_text for t in RETROSYNTHESIS_POSITIVE_TERMS):
            return "noise_field"

    if any(t in full_text for t in RETROSYNTHESIS_POSITIVE_TERMS):
        return "target_retrosynthesis"

    # Query tokens often appear even when wording differs slightly.
    query_tokens = [tok for tok in query_l.replace('"', "").split() if len(tok) > 3]
    hit_tokens = sum(1 for tok in query_tokens if tok in full_text)
    if hit_tokens >= max(2, len(query_tokens) // 2):
        return "target_retrosynthesis"

    if any(t in full_text for t in RETROSYNTHESIS_RELATED_TERMS):
        return "target_related"

    return "unknown_context"


def _extract_publisher_journal(work: Dict, fallback_journal: str = "") -> tuple:
    primary_loc = work.get("primary_location") or {}
    source = primary_loc.get("source") or {}

    publisher = source.get("host_organization_name")
    if not publisher:
        publisher = source.get("publisher")
    if not publisher:
        host_venue = work.get("host_venue") or {}
        publisher = host_venue.get("publisher")

    if isinstance(publisher, list):
        publisher = "; ".join([str(p).strip() for p in publisher if p])
    publisher = publisher or ""

    journal_name = source.get("display_name") or fallback_journal
    return publisher, journal_name


def _build_openalex_filter(
    issn: Optional[str] = None,
    from_year: str = "2000-01-01",
) -> str:
    components = [
        "has_doi:true",
        "has_abstract:true",
        f"from_publication_date:{from_year}",
        "type:article|review",
    ]
    if issn:
        components.append(f"primary_location.source.issn:{issn}")
    return ",".join(components)


def _iterate_openalex(
    query: str,
    *,
    issn: Optional[str] = None,
    max_works: int = 500,
) -> Iterable[Dict]:
    """
    Iterate OpenAlex works for a query, optionally restricted to one ISSN.
    """
    per_page = 200
    cursor = "*"
    fetched = 0

    params_base = {
        "search": query,
        "filter": _build_openalex_filter(issn=issn),
        "per-page": str(per_page),
    }

    while True:
        params = dict(params_base)
        params["cursor"] = cursor
        resp = requests.get(
            OPENALEX_WORKS_URL,
            params=params,
            timeout=60,
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results") or []
        if not results:
            break

        for w in results:
            yield w
            fetched += 1
            if fetched >= max_works:
                return

        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            break


def _load_whitelist_for_openalex(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not path.exists():
        raise FileNotFoundError(f"Whitelist file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("selected", "").strip().lower() != "true":
                continue
            name = (row.get("journal_name") or "").strip()
            issn = (row.get("issn") or "").strip()
            if not name or not issn:
                continue
            rows.append({"journal_name": name, "issn": issn})
    return rows


def discover_dois_openalex_global(
    queries: Optional[List[str]] = None,
    max_works_per_query: int = 500,
) -> List[OpenAlexWorkRecord]:
    """
    Discover DOIs via OpenAlex search across all sources (no journal filter).
    Best for broad coverage of retrosynthesis / CASP literature.
    """
    query_list = queries or SEARCH_QUERIES
    records: List[OpenAlexWorkRecord] = []
    seen_dois: Set[str] = set()

    query_iter: Iterable[str]
    if tqdm is not None:
        query_iter = tqdm(query_list, desc="OpenAlex global queries", unit="query")
    else:
        query_iter = query_list

    for q in query_iter:
        print(f"[INFO] OpenAlex global search: '{q}'")
        try:
            for work in _iterate_openalex(q, issn=None, max_works=max_works_per_query):
                doi = work.get("doi")
                if not doi:
                    continue
                doi_key = doi.lower().strip()
                if doi_key in seen_dois:
                    continue

                category = _classify_openalex_work(work, q)
                if category not in ("target_retrosynthesis", "target_related"):
                    continue

                publisher, journal_name = _extract_publisher_journal(work)
                seen_dois.add(doi_key)
                records.append(
                    OpenAlexWorkRecord(
                        doi=doi,
                        journal=journal_name,
                        publisher=publisher,
                        year=work.get("publication_year"),
                        work_type=work.get("type"),
                        source_query=q,
                        category=category,
                    )
                )
        except Exception as exc:
            print(f"[WARN] OpenAlex global query failed for '{q}': {exc}")
            continue

        time.sleep(0.5)

    print(f"[INFO] Discovered {len(records)} unique DOIs via OpenAlex (global).")
    return records


def discover_dois_openalex_from_whitelist(
    whitelist_csv: Path,
    queries: Optional[List[str]] = None,
    max_works_per_query: int = 300,
) -> List[OpenAlexWorkRecord]:
    """
    Discover DOIs from journals in a whitelist CSV using OpenAlex.
    """
    journals = _load_whitelist_for_openalex(whitelist_csv)
    query_list = queries or SEARCH_QUERIES

    records: List[OpenAlexWorkRecord] = []
    seen_dois: Set[str] = set()

    journal_iter: Iterable[Dict[str, str]]
    if tqdm is not None:
        journal_iter = tqdm(
            journals,
            desc="OpenAlex DOI discovery (whitelist)",
            unit="journal",
        )
    else:
        journal_iter = journals

    for j in journal_iter:
        jname = j["journal_name"]
        issn = j["issn"]

        for q in query_list:
            try:
                for work in _iterate_openalex(
                    q, issn=issn, max_works=max_works_per_query
                ):
                    doi = work.get("doi")
                    if not doi:
                        continue
                    doi_key = doi.lower().strip()
                    if doi_key in seen_dois:
                        continue

                    category = _classify_openalex_work(work, q)
                    if category not in ("target_retrosynthesis", "target_related"):
                        continue

                    publisher, journal_name = _extract_publisher_journal(
                        work, fallback_journal=jname
                    )
                    seen_dois.add(doi_key)
                    records.append(
                        OpenAlexWorkRecord(
                            doi=doi,
                            journal=journal_name,
                            publisher=publisher,
                            year=work.get("publication_year"),
                            work_type=work.get("type"),
                            source_query=q,
                            category=category,
                        )
                    )
            except Exception as exc:
                print(f"[WARN] OpenAlex query failed for {jname} / '{q}': {exc}")
                continue

            time.sleep(0.5)

    print(
        f"[INFO] Discovered {len(records)} unique DOIs via OpenAlex (whitelist)."
    )
    return records


def save_openalex_records(records: List[OpenAlexWorkRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "doi",
                "publisher",
                "journal",
                "pub_year",
                "work_type",
                "source_query",
                "type",
                "source",
            ]
        )
        for r in records:
            writer.writerow(
                [
                    r.doi,
                    r.publisher or "",
                    r.journal,
                    r.year if r.year is not None else "",
                    r.work_type or "",
                    r.source_query,
                    r.category,
                    "openalex",
                ]
            )


def main_openalex(use_whitelist: bool = False) -> None:
    """
    CLI entry for OpenAlex-based discovery.

    Default: global OpenAlex search over SEARCH_QUERIES (broad coverage).
    Set use_whitelist=True to restrict to journal_whitelist_retrosynthesis.csv.
    """
    journal_dir = _resolve_journal_dir()
    doi_dir = _resolve_doi_dir()

    if use_whitelist:
        whitelist_path = journal_dir / "journal_whitelist_retrosynthesis.csv"
        if not whitelist_path.exists():
            # Fall back to legacy name if present.
            alt = journal_dir / "journal_whitelist_ooir_filtered.csv"
            whitelist_path = alt if alt.exists() else whitelist_path
        print(f"[INFO] Using journal whitelist: {whitelist_path}")
        records = discover_dois_openalex_from_whitelist(whitelist_path)
    else:
        print("[INFO] Using global OpenAlex search (no journal ISSN filter).")
        records = discover_dois_openalex_global()

    out_path = doi_dir / "doi_list_seed_retrosynthesis.csv"
    save_openalex_records(records, out_path)
    print(f"[INFO] Saved OpenAlex DOI seed list to {out_path}")


# ---------------------------------------------------------------------------
# Built-in TDM test DOIs (smoke tests for download pipeline)
# ---------------------------------------------------------------------------

TDM_TEST_DOIS: List[Dict[str, str]] = [
    {"doi": "10.1016/j.pecs.2019.03.002", "publisher": "Elsevier"},
    {"doi": "10.1016/j.pecs.2020.100846", "publisher": "Elsevier"},
    {"doi": "10.1007/s41918-019-00054-2", "publisher": "Springer"},
    {"doi": "10.1007/s41918-021-00101-x", "publisher": "Springer"},
    {"doi": "10.1002/adma.202401505", "publisher": "Wiley"},
    {"doi": "10.1002/adma.202403191", "publisher": "Wiley"},
    {
        "doi": "10.1021/acsenergylett.4c01999",
        "publisher": "American Chemical Society",
    },
    {
        "doi": "10.1021/acsenergylett.4c00790",
        "publisher": "American Chemical Society",
    },
    {"doi": "10.1039/a707503k", "publisher": "Royal Society of Chemistry"},
    {"doi": "10.1039/b612600f", "publisher": "Royal Society of Chemistry"},
    {"doi": "10.1039/d2cs00873d", "publisher": "Royal Society of Chemistry"},
    {
        "doi": "10.3390/antiox9040336",
        "publisher": "Multidisciplinary Digital Publishing Institute",
    },
    {
        "doi": "10.3390/antiox9070575",
        "publisher": "Multidisciplinary Digital Publishing Institute",
    },
    {"doi": "10.1155/2011/967307", "publisher": "Hindawi"},
    {"doi": "10.1155/2016/4283696", "publisher": "Hindawi"},
    {"doi": "10.1007/978-981-99-1350-3_4", "publisher": "Springer Nature"},
    {"doi": "10.1007/978-981-19-6282-0_15", "publisher": "Springer Nature"},
    {
        "doi": "10.1186/s40824-023-00454-y",
        "publisher": "American Association for the Advancement of Science",
    },
    {
        "doi": "10.34133/bmr.0251",
        "publisher": "American Association for the Advancement of Science",
    },
    {"doi": "10.1515/nanoph-2017-0044", "publisher": "De Gruyter"},
    {"doi": "10.1515/nanoph-2020-0013", "publisher": "De Gruyter"},
    {
        "doi": "10.1073/pnas.2020357118",
        "publisher": "National Academy of Sciences",
    },
    {
        "doi": "10.1073/pnas.1401033111",
        "publisher": "National Academy of Sciences",
    },
]
