"""
CLI: download full text for DOIs from a discovery CSV, then parse to JSON.

API-first for Elsevier/Wiley (TDM), then OpenAlex OA / Crossref fallback.

Run from the project root:
    python literature_mining/run_download_parse.py
    python literature_mining/run_download_parse.py --limit 5
    python literature_mining/run_download_parse.py --api-only --limit 5
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from literature_mining.pipeline_core import (  # noqa: E402
    load_doi_list,
    run_download_parse,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Download and parse full text for DOIs "
            "(Elsevier/Wiley API first, then OA/Crossref fallback)"
        )
    )
    _default_seed = ROOT / "results" / "doi" / "doi_list_seed_elsevier_wiley.csv"
    if not _default_seed.exists():
        _default_seed = ROOT / "results" / "doi" / "doi_list_seed_retrosynthesis.csv"
    parser.add_argument(
        "--input",
        type=str,
        default=str(_default_seed),
        help="Path to DOI CSV (columns: doi, publisher, ...)",
    )
    parser.add_argument(
        "--parsed-dir",
        type=str,
        default=str(ROOT / "parsed"),
        help="Directory for parsed JSON documents",
    )
    parser.add_argument(
        "--report",
        type=str,
        default=str(ROOT / "results" / "doi" / "download_report.csv"),
        help="Path for download/parse status report CSV",
    )
    parser.add_argument(
        "--api-only",
        action="store_true",
        help=(
            "Only use publisher TDM APIs (Elsevier XML / Wiley TDM); "
            "do not fall back to OA pdf_url or Crossref"
        ),
    )
    parser.add_argument(
        "--oa-only",
        action="store_true",
        help="Only process rows with a non-empty pdf_url (CSV filter; still API-first)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N DOIs from the CSV (attempt cap)",
    )
    parser.add_argument(
        "--target-success",
        type=int,
        default=100,
        help=(
            "Stop after this many successful downloads/parses "
            "(ok or already-parsed). Default: 100. Use 0 for no success cap."
        ),
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds to wait between TDM/HTTP requests",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    items = load_doi_list(args.input)
    if args.oa_only:
        before = len(items)
        items = [row for row in items if (row.get("pdf_url") or "").strip()]
        print(f"[INFO] OA-only filter: {len(items)}/{before} rows have pdf_url")
    if args.limit is not None:
        items = items[: max(0, args.limit)]

    target_success = None if args.target_success == 0 else args.target_success

    print(f"[INFO] Loaded {len(items)} DOIs from {args.input}")
    print(
        "[INFO] Download order: publisher API -> OA pdf_url -> Crossref"
        if not args.api_only
        else "[INFO] Download order: publisher API only (--api-only)"
    )
    if target_success is not None:
        print(f"[INFO] Will stop after {target_success} successful papers")
    if not items:
        print("[WARN] No DOIs to process.")
        return

    results = run_download_parse(
        doi_items=items,
        parsed_dir=args.parsed_dir,
        report_path=args.report,
        request_interval=args.interval,
        target_success=target_success,
        api_only=args.api_only,
    )
    ok = sum(1 for r in results if r["status"] == "ok")
    skip_exists = sum(1 for r in results if r["status"] == "skipped_exists")
    failed = sum(1 for r in results if r["status"] == "failed")
    methods: dict[str, int] = {}
    for r in results:
        m = r.get("download_method") or "failed"
        methods[m] = methods.get(m, 0) + 1
    print(f"[INFO] Done: {ok} ok, {skip_exists} skipped_exists, {failed} failed")
    print(f"[INFO] Methods: {methods}")
    print(f"[INFO] Report: {args.report}")


if __name__ == "__main__":
    main()
