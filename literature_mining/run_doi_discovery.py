"""
CLI wrapper for DOI discovery via OpenAlex (retrosynthesis / CASP queries).

Run from the project root:
    python literature_mining/run_doi_discovery.py

Default: only Elsevier + Wiley DOIs.
Optional whitelist mode:
    python literature_mining/run_doi_discovery.py --whitelist
All publishers:
    python literature_mining/run_doi_discovery.py --all-publishers
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from doi_discovery import DEFAULT_PUBLISHER_FILTER, main_openalex  # type: ignore  # noqa: E402


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Discover retrosynthesis DOIs via OpenAlex"
    )
    parser.add_argument(
        "--whitelist",
        action="store_true",
        help="Restrict search to journal_whitelist_retrosynthesis.csv",
    )
    parser.add_argument(
        "--publishers",
        type=str,
        default=",".join(DEFAULT_PUBLISHER_FILTER),
        help=(
            "Comma-separated publisher allow-list "
            "(default: elsevier,wiley). Use 'all' for no filter."
        ),
    )
    parser.add_argument(
        "--all-publishers",
        action="store_true",
        help="Disable publisher filter (equivalent to --publishers all)",
    )
    args = parser.parse_args()

    if args.all_publishers:
        publishers = ["all"]
    else:
        publishers = [
            p.strip().lower() for p in args.publishers.split(",") if p.strip()
        ]
        if not publishers:
            publishers = ["all"]

    main_openalex(use_whitelist=args.whitelist, publishers=publishers)
