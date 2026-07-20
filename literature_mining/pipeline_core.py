"""
Core pipeline utilities shared by command-line scripts.

Download/parse path is self-contained. LLM extraction (run_pipeline) imports
optional modules lazily so discovery + full-text tooling still works without them.
"""

from __future__ import annotations

import csv
import logging
import os
from typing import Any, Dict, Iterable, List, Optional

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - optional dependency
    tqdm = None

from .config import load_api_keys
from .parsers import (
    load_parsed_document,
    parse_elsevier_xml,
    parse_wiley_pdf,
    save_parsed_document,
)
from .publisher_tdm import PublisherTDMDownloader


logger = logging.getLogger(__name__)


TEST_DOIS: List[Dict[str, str]] = [
    {"doi": "10.1016/j.ensm.2023.102948", "publisher": "Elsevier"},
    {"doi": "10.1002/aenm.202300001", "publisher": "Wiley"},
    {"doi": "10.1038/s41560-023-01234-x", "publisher": "Springer"},
]


def _normalize_doi(doi: str) -> str:
    """
    Normalize DOI strings to a canonical form, stripping common URL/prefix
    wrappers such as 'https://doi.org/' or 'doi:'.
    """
    if not doi:
        return ""
    d = doi.strip()
    prefixes = [
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    ]
    for p in prefixes:
        if d.lower().startswith(p):
            d = d[len(p) :]
            break
    if d.lower().startswith("doi:"):
        d = d[4:]
    return d.strip()


def load_doi_list(path: str) -> List[Dict[str, str]]:
    """
    Load DOIs and publisher information from a CSV file.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"DOI list file not found: {path} (expect columns doi,publisher)"
        )
    items: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_doi = (row.get("doi") or "").strip()
            doi_norm = _normalize_doi(raw_doi)
            if not doi_norm:
                continue
            row["doi"] = doi_norm
            items.append(row)
    return items


def process_xml_images(
    parsed_doc: Dict[str, Any],
    downloader: PublisherTDMDownloader,
    base_dir: str,
) -> None:
    """
    For XML-based documents (Elsevier/Springer), iterate over figures,
    download missing images via the appropriate API/URL, and attach a
    local image_path so downstream components can perform multimodal
    analysis.
    """
    doi = parsed_doc.get("doi")
    if not doi:
        return

    publisher = (parsed_doc.get("publisher") or "").lower()
    figures = parsed_doc.get("figures") or []
    if not figures:
        return

    safe_doi = doi.replace("/", "_").replace(":", "_")
    image_dir = os.path.join(base_dir, "images", safe_doi)
    os.makedirs(image_dir, exist_ok=True)

    updated_count = 0
    for fig in figures:
        existing_path = fig.get("image_path")
        if isinstance(existing_path, str) and os.path.exists(existing_path):
            continue

        fig_id = fig.get("id", "fig")
        local_filename = f"{fig_id}.jpg"
        local_path = os.path.join(image_dir, local_filename)
        success = False

        if "elsevier" in publisher and fig.get("image_ref"):
            success = downloader.download_elsevier_image(
                doi=doi,
                image_ref=fig["image_ref"],
                out_path=local_path,
            )
        elif fig.get("image_url"):
            success = downloader.download_image_generic(
                url=fig["image_url"],
                out_path=local_path,
            )

        if success:
            fig["image_path"] = local_path
            fig.setdefault("context_type", "figure")
            updated_count += 1

    if updated_count > 0:
        logger.info("Retrieved %d images for XML DOI %s", updated_count, doi)


def get_parsed_path(base_dir: str, publisher: str, doi: str) -> str:
    """
    Build the path to the parsed JSON document for a given publisher and DOI.
    """
    pub_norm = (publisher or "unknown").lower().strip() or "unknown"
    # Avoid path separators in publisher names.
    for ch in ("/", "\\", ":"):
        pub_norm = pub_norm.replace(ch, "_")
    safe_doi = doi.replace("/", "_").replace(":", "_")
    return os.path.join(base_dir, pub_norm, f"{safe_doi}.json")


def _save_pdf_parsed(
    raw_path: str,
    parsed_path: str,
    doi: str,
    publisher: str,
    source: str,
) -> None:
    parsed = parse_wiley_pdf(raw_path)
    parsed["doi"] = doi
    parsed["publisher"] = publisher
    parsed["source"] = source
    save_parsed_document(parsed, parsed_path)


def _try_oa_pdf(
    downloader: PublisherTDMDownloader,
    item: Dict[str, str],
    doi: str,
    publisher: str,
    parsed_path: str,
) -> Optional[str]:
    """Download OpenAlex pdf_url if present; return raw path on success."""
    pdf_url = (item.get("pdf_url") or "").strip()
    if not pdf_url:
        return None
    logger.info("Fallback OA pdf_url for %s", doi)
    raw_path = downloader.download_pdf_from_url(doi, pdf_url, out_dir="raw/oa/pdf")
    if raw_path:
        try:
            _save_pdf_parsed(raw_path, parsed_path, doi, publisher, "oa_pdf")
            return raw_path
        except Exception as exc:
            logger.warning("OA PDF parse failed for %s: %s", doi, exc)
    return None


def download_and_parse_one(
    downloader: PublisherTDMDownloader,
    item: Dict[str, str],
    parsed_dir: str,
    api_only: bool = False,
) -> Dict[str, Any]:
    """
    Download and parse a single DOI.

    Order for Elsevier/Wiley (learning TDM APIs):
      1) publisher API (Elsevier XML / Wiley TDM)
      2) OpenAlex OA pdf_url (unless api_only)
      3) Crossref PDF (unless api_only)

    Returns keys: doi, publisher, status, raw_path, parsed_path, error, download_method
    """
    doi = item["doi"]
    publisher = (item.get("publisher") or "").strip()
    pub_lower = publisher.lower()

    result: Dict[str, Any] = {
        "doi": doi,
        "publisher": publisher,
        "status": "failed",
        "raw_path": "",
        "parsed_path": "",
        "error": "",
        "download_method": "failed",
    }

    parsed_path = get_parsed_path(parsed_dir, publisher, doi)
    if os.path.exists(parsed_path):
        logger.info("Parsed JSON already exists for %s (%s)", doi, publisher)
        result["status"] = "skipped_exists"
        result["parsed_path"] = parsed_path
        result["download_method"] = "skipped_exists"
        return result

    raw_path: Optional[str] = None
    method = "failed"
    api_fail_reason = ""

    try:
        is_elsevier = "elsevier" in pub_lower or doi.lower().startswith("10.1016/")
        is_wiley = "wiley" in pub_lower or doi.lower().startswith(
            ("10.1002/", "10.1111/")
        )

        # --- 1) Publisher TDM API first ---
        if is_elsevier:
            logger.info("Trying Elsevier API for %s", doi)
            raw_path = downloader.download_elsevier_xml(doi)
            if raw_path:
                parsed = parse_elsevier_xml(raw_path)
                parsed["doi"] = doi
                parsed["publisher"] = publisher
                parsed["source"] = "elsevier_api"
                save_parsed_document(parsed, parsed_path)
                method = "elsevier_api"
            else:
                api_fail_reason = downloader.last_error or "http_error"
                logger.warning(
                    "Elsevier API failed for %s (%s)%s",
                    doi,
                    api_fail_reason,
                    "" if api_only else "; will try OA/Crossref fallback",
                )
        elif is_wiley:
            logger.info("Trying Wiley TDM API for %s", doi)
            raw_path = downloader.download_wiley_pdf(doi)
            if raw_path:
                _save_pdf_parsed(raw_path, parsed_path, doi, publisher, "wiley_tdm")
                method = "wiley_tdm"
            else:
                api_fail_reason = downloader.last_error or "http_error"
                logger.warning(
                    "Wiley TDM failed for %s (%s)%s",
                    doi,
                    api_fail_reason,
                    "" if api_only else "; will try OA/Crossref fallback",
                )
        elif not api_only:
            # Other publishers: keep previous generic path (not API-first focus).
            raw_path = downloader.download_fulltext(doi, publisher)
            if raw_path and raw_path.lower().endswith(".pdf"):
                _save_pdf_parsed(
                    raw_path, parsed_path, doi, publisher, pub_lower or "unknown"
                )
                method = "crossref_pdf"
            elif raw_path and raw_path.lower().endswith(".xml"):
                parsed = parse_elsevier_xml(raw_path)
                parsed["doi"] = doi
                parsed["publisher"] = publisher
                parsed["source"] = pub_lower or "unknown"
                save_parsed_document(parsed, parsed_path)
                method = "crossref_pdf"

        # --- 2) OpenAlex OA pdf_url fallback ---
        if not os.path.exists(parsed_path) and not api_only and (is_elsevier or is_wiley):
            oa_raw = _try_oa_pdf(downloader, item, doi, publisher, parsed_path)
            if oa_raw:
                raw_path = oa_raw
                method = "oa_pdf"

        # --- 3) Crossref PDF fallback ---
        if not os.path.exists(parsed_path) and not api_only and (is_elsevier or is_wiley):
            logger.info("Fallback Crossref PDF for %s", doi)
            if is_elsevier:
                raw_path = downloader.download_elsevier_pdf_via_crossref(doi)
            else:
                raw_path = downloader.download_via_crossref_tdm(
                    doi,
                    out_dir="raw/wiley/pdf",
                    preferred_hosts=["wiley.com", "onlinelibrary.wiley.com"],
                    publisher_key=None,
                )
            if raw_path and raw_path.lower().endswith(".pdf"):
                try:
                    _save_pdf_parsed(
                        raw_path, parsed_path, doi, publisher, "crossref_pdf"
                    )
                    method = "crossref_pdf"
                except Exception as exc:
                    logger.warning("Crossref PDF parse failed for %s: %s", doi, exc)

        result["raw_path"] = raw_path or ""

        if os.path.exists(parsed_path):
            result["status"] = "ok"
            result["parsed_path"] = parsed_path
            result["download_method"] = method
            if method == "elsevier_api":
                parsed_doc = load_parsed_document(parsed_path)
                process_xml_images(parsed_doc, downloader, parsed_dir)
                save_parsed_document(parsed_doc, parsed_path)
            if api_fail_reason and method in ("oa_pdf", "crossref_pdf"):
                result["error"] = f"api_failed:{api_fail_reason}; recovered_via:{method}"
        else:
            result["status"] = "failed"
            result["download_method"] = "failed"
            if api_fail_reason:
                result["error"] = (
                    f"API failed ({api_fail_reason})"
                    + ("; no OA/Crossref fallback" if api_only else "; fallbacks also failed")
                )
            else:
                result["error"] = (
                    f"Download or parse failed for {doi} ({publisher or 'unknown'})"
                )
    except Exception as exc:
        result["status"] = "failed"
        result["raw_path"] = raw_path or ""
        result["download_method"] = "failed"
        result["error"] = str(exc)
        logger.error("Failed processing %s (%s): %s", doi, publisher, exc)

    return result


def run_download_parse(
    doi_items: List[Dict[str, str]],
    parsed_dir: str,
    report_path: str,
    request_interval: float = 2.0,
    target_success: Optional[int] = None,
    api_only: bool = False,
) -> List[Dict[str, Any]]:
    """
    Download and parse a list of DOIs; write a CSV report of outcomes.

    If target_success is set, stop once that many papers succeed
    (status ok or skipped_exists). Failures do not count toward the target.
    If api_only, skip OA/Crossref fallbacks (publisher TDM API only).
    """
    api_keys = load_api_keys()
    if api_only:
        logger.info("api_only=True: OA/Crossref fallbacks disabled")
    if not api_keys.get("elsevier"):
        logger.warning("No elsevier key in API.txt — Elsevier API steps will fail")
    if not api_keys.get("wiley"):
        logger.warning("No wiley TDM token in API.txt — Wiley API steps will fail")

    downloader = PublisherTDMDownloader(
        api_keys=api_keys, request_interval=request_interval
    )

    os.makedirs(parsed_dir, exist_ok=True)
    report_parent = os.path.dirname(report_path)
    if report_parent:
        os.makedirs(report_parent, exist_ok=True)

    results: List[Dict[str, Any]] = []
    success_count = 0
    iterator: Iterable[Dict[str, str]]
    if tqdm is not None:
        total = len(doi_items)
        iterator = tqdm(doi_items, desc="Download+parse", unit="paper", total=total)
    else:
        iterator = doi_items

    for item in iterator:
        result = download_and_parse_one(
            downloader, item, parsed_dir, api_only=api_only
        )
        results.append(result)
        status = result["status"]
        doi = result["doi"]
        method = result.get("download_method", "")
        if status == "ok":
            success_count += 1
            logger.info(
                "OK %s [%s] -> %s (%d%s)",
                doi,
                method,
                result["parsed_path"],
                success_count,
                f"/{target_success}" if target_success else "",
            )
        elif status == "skipped_exists":
            logger.info(
                "SKIP %s (already parsed) (%d%s)",
                doi,
                success_count,
                f"/{target_success}" if target_success else "",
            )
        else:
            logger.warning("FAIL %s: %s", doi, result.get("error") or "unknown")

        if tqdm is not None and hasattr(iterator, "set_postfix"):
            iterator.set_postfix(ok=success_count, refresh=False)

        if target_success is not None and success_count >= target_success:
            logger.info(
                "Reached target of %d successful papers; stopping early.",
                target_success,
            )
            break

    fieldnames = [
        "doi",
        "publisher",
        "status",
        "download_method",
        "raw_path",
        "parsed_path",
        "error",
    ]
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in results:
            writer.writerow(row)

    ok = sum(1 for r in results if r["status"] in ("ok", "skipped_exists"))
    failed = sum(1 for r in results if r["status"] == "failed")
    methods = {}
    for r in results:
        m = r.get("download_method") or "failed"
        methods[m] = methods.get(m, 0) + 1
    logger.info(
        "Download+parse done: %d ok/skipped, %d failed. Methods=%s Report: %s",
        ok,
        failed,
        methods,
        report_path,
    )
    return results



