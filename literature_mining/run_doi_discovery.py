"""
CLI wrapper for DOI discovery via OpenAlex (retrosynthesis / CASP queries).

Run from the LLM_RAG_V3 directory:
    python scripts/doi_discovery.py

Optional whitelist mode (restrict to cheminformatics journals):
    python scripts/doi_discovery.py --whitelist
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

# Add the "Literature mining" directory itself so relative imports in
# doi_discovery.py (e.g. from .config import ...) work correctly.
_PKG_DIR = Path(__file__).resolve().parent
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from doi_discovery import main_openalex  # type: ignore  # noqa: E402


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Discover retrosynthesis DOIs via OpenAlex")
    parser.add_argument(
        "--whitelist",
        action="store_true",
        help="Restrict search to journal_whitelist_retrosynthesis.csv",
    )
    args = parser.parse_args()
    main_openalex(use_whitelist=args.whitelist)
