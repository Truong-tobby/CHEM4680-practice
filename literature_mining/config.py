from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
METADATA_JSON = DATA_DIR / "metadata.json"
FULLTEXT_DIR = DATA_DIR / "fulltext"
PAPERS_JSONL = DATA_DIR / "papers.jsonl"

CONTACT_EMAIL = "qtle@connect.ust.hk"
USER_AGENT = f"Literature-downloader (mailto:{CONTACT_EMAIL})"

CROSSREF_API_BASE = "https://api.crossref.org/works"

# Search /works supports select; single-DOI /works/{doi} does not.
CROSSREF_SELECT_FIELDS = (
    "DOI,title,author,container-title,abstract,link,subject,created,issued"
)

SEARCH_QUERIES = [
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

SEED_DOIS = [
    "10.1039/C9SC05704H",
]

ROWS_PER_QUERY = 20
RATE_LIMIT_SEC = 1.0
MAX_RETRIES = 3
RETRY_BACKOFF_SEC = 5.0
