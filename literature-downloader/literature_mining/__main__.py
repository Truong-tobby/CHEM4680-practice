"""CLI entry point for literature_mining."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import ROWS_PER_QUERY, SEED_DOIS
from .corpus_builder import build_corpus, ingest_doi, search_and_ingest


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Literature data mining for retrosynthesis metrics (Crossref)."
    )
    parser.add_argument(
        "--source",
        default="crossref",
        choices=["crossref"],
        help="Metadata source (currently only crossref).",
    )
    parser.add_argument(
        "--build-corpus",
        action="store_true",
        help="Run all SEARCH_QUERIES and seed DOIs into data/papers.jsonl.",
    )
    parser.add_argument("--query", type=str, help="Single Crossref search query.")
    parser.add_argument("--doi", type=str, help="Fetch and ingest one DOI.")
    parser.add_argument(
        "--rows",
        type=int,
        default=ROWS_PER_QUERY,
        help=f"Results per query (default: {ROWS_PER_QUERY}).",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Include default seed DOIs when using --build-corpus.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.build_corpus:
        seed_dois = SEED_DOIS if args.seed else []
        totals = build_corpus(rows=args.rows, seed_dois=seed_dois)
        print(
            f"Done. new={totals['new']} skip={totals['skip']} "
            f"error={totals['error']}"
        )
        return 0

    if args.doi:
        status, record = ingest_doi(args.doi)
        if record:
            print(f"{status}: {record['doi']} — {record.get('title', '')[:80]}")
        else:
            print(f"{status}: {args.doi}")
        return 0 if status != "error" else 1

    if args.query:
        stats = search_and_ingest(args.query, rows=args.rows)
        print(
            f"Done. new={stats['new']} skip={stats['skip']} error={stats['error']}"
        )
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
