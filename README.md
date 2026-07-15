# Retrosynthesis Metrics Literature RAG Explorer

> **Goal:** Build a small system to (1) automatically search and download paper metadata on *retrosynthesis metrics* via Crossref, (2) store them as a structured mini-corpus, and (3) use RAG for Q&A on metric definitions and comparisons.

---

## High-level architecture

```text
┌──────────────────────────────────────────────────────────────┐
│  Part 1 — Literature Data Mining (Crossref)                  │
│  query keywords → /works → metadata + TDM links             │
│  → data/papers.jsonl  (+ optional fulltext chunks)           │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Part 2 — RAG Q&A                                            │
│  index (title+abstract+keywords) → retrieve top-k → LLM      │
│  → answer + DOI evidence list                                │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────┐
│  Part 3 — Metric Explorer (optional)                         │
│  metric name → RAG definition + formula + context            │
│  (optional) try top-k / coverage on a toy subset             │
└──────────────────────────────────────────────────────────────┘
```

**User flow:**

1. User enters a question (Vietnamese or English).
2. Retriever finds 3–5 relevant papers from `papers.jsonl`.
3. LLM reads retrieved abstract/intro → generates a grounded answer.
4. UI/CLI also shows DOI evidence for further reading.

---

## Part 1 — Literature data mining / downloader

### 1.1 Data source: Crossref REST API

Use the `/works` endpoint to find papers and fetch metadata (no API key required; send a polite `User-Agent` with a contact email).

**Search by keyword:**

```http
GET https://api.crossref.org/works?query=retrosynthesis%20metrics&rows=20
```

**Metadata for a specific DOI:**

```http
GET https://api.crossref.org/works/10.1039/C9SC05704H
```

**Reduce payload** with `select` (fetch only needed fields):

```http
GET https://api.crossref.org/works?query=round-trip%20accuracy%20retrosynthesis&rows=20&select=DOI,title,author,published,container-title,abstract,link,subject
```

### 1.2 Suggested query set (metrics-focused)

Replace or supplement old reaction-class queries with a set focused on **retrosynthesis evaluation**:

```python
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
```

You can combine `query.bibliographic`, `filter=from-pub-date:2018` to filter recent years, or start from seed DOIs and expand via citations (later phase).

### 1.3 Metadata fields to store

| Field | Crossref source (suggested) | Notes |
| ----- | --------------------------- | ----- |
| `doi` | `DOI` | Normalize to lowercase |
| `title` | `title[0]` | |
| `authors` | `author[].given` + `family` | List of strings |
| `year` | `published-print` / `published-online` / `created` | Use the first available year |
| `journal` | `container-title[0]` | |
| `abstract` | `abstract` | Often JATS XML → strip tags |
| `link` | `link[]` | Prefer URL with `intended-application` = TDM / `unspecified` |
| `keywords` | assign from query + `subject` | |
| `tags` | rule-based or lightweight LLM | e.g. `single-step metrics`, `route metrics`, `failure modes` |

### 1.4 JSON schema per paper

```json
{
  "doi": "10.1039/c9sc05704h",
  "title": "Predicting retrosynthetic pathways using transformer-based models and a hyper-graph exploration strategy",
  "authors": ["Philippe Schwaller", "..."],
  "year": 2020,
  "journal": "Chemical Science",
  "abstract": "...",
  "link": "https://...",
  "keywords": ["round-trip", "retrosynthesis", "transformer"],
  "tags": ["single-step metrics", "route metrics"],
  "source": "crossref",
  "query_hit": "round-trip accuracy retrosynthesis"
}
```

**Primary output:** `data/papers.jsonl` — one record per line (easy to stream when building an index).

Optionally keep in parallel:

- `data/metadata/<id>.json` — full per-paper copy (debug / audit)
- `data/fulltext/<id>.txt` — cleaned abstract, or intro if TDM download succeeds

### 1.5 Full-text (optional, later phase)

1. From Crossref `link[]`, choose a URL intended for **text and data mining** (TDM).
2. `requests.get` PDF/HTML with headers appropriate for license/TDM.
3. Extract text with `pdfminer.six` or Grobid.
4. **For learning MVP:** **title + abstract (+ keywords)** is enough — reduces copyright risk and PDF complexity.

### 1.6 Module 1 implementation pipeline

```text
config.py          → queries, paths, User-Agent, rate limit
crossref_client.py → search_works(query), get_work(doi), parse_item()
tagger.py          → assign tags/keywords by rule (keyword hit → tag)
corpus_builder.py  → merge dedupe by DOI → papers.jsonl
downloader.py      → (optional) TDM fetch + extract text
__main__.py        → CLI: search | build-corpus | fetch-doi
```

**Target CLI:**

```bash
# Search + save metadata
python -m literature_mining --source crossref --query "PaRoutes" --rows 20

# Run all SEARCH_QUERIES → data/papers.jsonl
python -m literature_mining --source crossref --build-corpus

# Fetch one specific DOI (seed paper)
python -m literature_mining --doi 10.1039/C9SC05704H
```

**Technical requirements:**

- Rate-limit ~1 request/second; retry on HTTP 429.
- Deduplicate by DOI.
- Idempotent: re-runs skip DOIs already in `papers.jsonl`.
- Log counts of new / skip / error papers.


## Part 2 — RAG Q&A on retrosynthesis metrics

### 2.1 Build index from corpus

For each record in `papers.jsonl`:

```text
document_text = title + "\n" + abstract + "\n" + " ".join(keywords + tags)
```

| Retriever | When to use | Dependency |
| --------- | ----------- | ---------- |
| **BM25** (`rank-bm25`) | MVP, no GPU, small corpus | Already in `requirements.txt` |
| **Embedding** (E5 / MiniLM + FAISS or numpy cosine) | When better semantic match is needed | Add later if needed |

A few dozen to a few hundred docs → runs on Colab or a laptop.

### 2.2 Supported question types

| Category | Example |
| -------- | ------- |
| Metric definition | “How is round-trip accuracy defined across different papers?” |
| Paper comparison | “How does round-trip differ between Schwaller 2019/2020 and later papers?” |
| Benchmark framework | “Which route-level metrics does PaRoutes use?” |
| Failure modes | “How are failure modes of single-step models categorized in *Quantifying the Failure Modes…*?” |
| Metric scope | “Are coverage / class diversity used for single-step or multi-step evaluation?” |

### 2.3 Detailed RAG flow

```text
Question
   │
   ▼
[Retriever]  top_k = 3..5 papers  (score + doi + snippet)
   │
   ▼
[Prompt builder]
   system: use only provided evidence; state clearly if information is missing
   user: question + retrieved abstract/title/keyword snippets + DOI
   │
   ▼
[LLM]  → answer (summarize definitions, compare A/B if available)
       → evidence: [{doi, title, year, relevance}]
```

**Generation principles:**

- Summarize in your own words; avoid long verbatim copies from abstracts.
- Attach a DOI to each claim when possible.
- If the corpus is insufficient → answer “insufficient evidence in corpus” instead of hallucinating.

### 2.4 Module 2 implementation pipeline

```text
rag/
  corpus.py       → load papers.jsonl, build docs
  retriever.py    → PaperRetriever (BM25; optional embedding)
  prompt.py       → Q&A + metric-card templates
  generator.py    → call LLM (OpenAI / local / Colab)
  pipeline.py     → ask(question) → {answer, evidence}
```

**Target CLI:**

```bash
python -m rag.pipeline --question "What is round-trip accuracy in retrosynthesis?"
python -m rag.pipeline --question "PaRoutes route-level metrics?" --top-k 5
```

**Suggested output schema:**

```json
{
  "question": "How is round-trip accuracy defined?",
  "answer": "...",
  "evidence": [
    {
      "doi": "10.1039/c9sc05704h",
      "title": "...",
      "year": 2020,
      "score": 12.4,
      "snippet": "first 300 chars of abstract..."
    }
  ]
}
```

If `rag/retriever.py` already has `ReactionRetriever` for USPTO — **add** a `PaperRetriever` class in parallel; merging both corpora is not required for the literature MVP.

---

## Part 3 — Retrosynthesis metrics

Goal: the assistant not only retrieves papers but also **explains a metric by name** in a structured way.

### 3.1 Metric card (RAG + LLM)

User enters a metric name, e.g.: `round-trip`, `coverage`, `class diversity`, `route solvability`, `LISAS`, `inverse efficiency score`.

Pipeline:

1. Query retriever with metric name (+ synonyms).
2. LLM generates a **metric card**:

| Section | Content |
| ------- | ------- |
| Definition | Short paraphrase, no long copying |
| Formula | Normalized form (if stated in papers) |
| Context | single-step vs multi-step / route-level |
| Source papers | DOI list |
| Notes | limitations, related failure modes (if any) |

```bash
python -m rag.pipeline --metric "round-trip accuracy"
```
