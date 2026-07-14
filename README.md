# Metrics + RAG to evaluate single-step retrosynthesis

> **Topic:** We take the output of a single-step retrosynthesis model (multiple reaction proposals), then compute SA score and SC score. We then use RAG to retrieve information from papers to determine which proposals are supported by prior literature. The output is a JSON file containing scores for each reaction proposal, literature evidence, etc.

---

# Part 1 — Motivation (Why?)

## 1.1 Current problem

A **single-step model** proposes many bond disconnections (precursors) for a target.

Issues:

- The model proposes **many** candidates but **does not explain** why one step is better than another.
- **Chemistry metrics** (SA, SC) usually live only in papers / search code and are **not turned into a narrative** for chemists.
- **Chemical plausibility** analysis is still mostly **manual**.

## 1.2 Core idea

Build an **assistant**:

> **Input** = target from a published model ([higherlev_retro repository](https://github.com/jihye-roh/higherlev_retro)) + single-step proposals  
> **Output** = ranked table with **metric scores** + **justification with citations**

Using the ranking, metric scores, as well as yield and reaction conditions in the aggregated output file, users can choose a synthesis route that fits their lab’s actual experimental constraints.

---

# Part 2 — Project goals

## 2.1 Main goals

1. Call the single-step model → obtain top-K precursors.
2. Compute metrics for each reaction proposal.
3. Use RAG to:
   - find similar USPTO reactions,
   - find relevant literature passages,
   - generate explanations **only from retrieved evidence**.
4. Export a ranked table + verdict + sources.

---

# Part 3 — High-level architecture (3 blocks)

```text
[Target SMILES]
       │
       ▼
┌──────────────────────┐
│ Block 1: Single-step │  → top-K candidates
└──────────────────────┘
       │
       ▼
┌──────────────────────┐
│ Block 2: Metrics     │  → score table (model_p, SA, SC)
└──────────────────────┘
       │
       ▼
┌──────────────────────┐
│ Block 3: RAG         │  → similar reactions + literature
│  Retriever + LLM     │  → justification + citations
└──────────────────────┘
       │
       ▼
[Ranked table + grounded explanation]
```

---

# Part 4 — Block details

## 4.1 Block 1 — Single-step proposals

**Input:** target SMILES  
**Output:** list of candidates  
**Model use:** Download and extract the one-step model

Download the [`uspto_original_consol.mar` file](https://hkustconnect-my.sharepoint.com/:u:/g/personal/ztanaj_connect_ust_hk/IQD3QJWNq_prQppG2VtJLTDRAYRK4WIuLVGXSx9WK9a3Bz4?e=1z5MeS) on OneDrive — the USPTO full consolidated one-step model from the [higherlev_retro repository](https://github.com/jihye-roh/higherlev_retro). The `.mar` file is a zip archive; extract it with:

```bash
unzip uspto_original_consol.mar -d tree_search/uspto_original_consol_Roh
```

This unpacks the model files into `tree_search/uspto_original_consol_Roh/`. After extraction it should contain at least:

- `model_latest.pt` — the trained model weights
- `templates.jsonl` — template library
- `models.py` — model architecture definitions
- `utils.py` — model utilities

> **Note:** The loader passes `weights_only=False` to `torch.load` automatically, which this checkpoint requires on PyTorch ≥ 2.6.

Example (illustrative):

```text
Target T:
  CC(C)c1ccc(-n2nc(O)c3c(=O)c4ccc(Cl)cc4[nH]c3c2=O)cc1

Candidate 1:  A.B  >> T     (model_p = 0.91)
Candidate 2:  C.D  >> T     (model_p = 0.84)
Candidate 3:  E.F  >> T     (model_p = 0.72)
...
Candidate K:  ...           (K = 10 in the MVP)
```

---

## 4.2 Block 2 — Metrics

For each reaction proposal, compute the following metrics:

| Metric | Short meaning | Used for | Link |
|--------|---------------|----------|------|
| `model_p` | Confidence of the one-step model | ML prior | — |
| `SA_score` | Synthetic accessibility | Prefer easier-to-synthesize precursors (~1–10; lower = easier) | [Ertl et al.](https://link.springer.com/article/10.1186/1758-2946-1-8) |
| `SC_score` | Synthetic complexity | Complexity learned from a reaction corpus (~1–5; lower = less complex) | [Coley scscore](https://github.com/connorcoley/scscore) |

### 4.2.1 Ranking rule (MVP)

Each candidate `i` has three signals:

- `model_p`: one-step model confidence (higher = better)
- `ΔSA`: SA reduction from target → precursor (higher = better accessibility)
- `ΔSC`: SC reduction likewise (higher = better complexity reduction)

**Step 1 — Normalize within the same target**  
For each metric, rank the top-K candidates of the same target onto a `[0, 1]` scale (rank 1 = best for that metric).

**Step 2 — Chemistry score**

\[
G_i = 0.5 \cdot r(\Delta SA)_i + 0.5 \cdot r(\Delta SC)_i
\]

**Step 3 — Final score (hybrid)**

\[
S_i = (1 - \alpha)\, r(model\_p)_i + \alpha\, G_i
\]

The MVP uses `α = 0.3` (favor the model; metrics only adjust lightly).  
The output still **keeps raw metrics** in JSON for chemists to inspect; `S_i` is used only for the default sort order.

Post-MVP ablation: `α ∈ {0, 0.3, 0.5}` and compare against ranking by `model_p` alone.

> **Pitch:** The model prior is primary; SA/SC only lightly re-rank within top-K and do not replace the model. Ranking ≠ final decision — RAG still provides explanation / citation.

---

## 4.3 Block 3 — RAG (core of the project)

RAG uses **two separate retrievers**:

### A) Reaction retriever (chemistry)

- Corpus: USPTO subset (e.g., 50k–200k reactions)
- Query: reaction SMILES / fingerprint of the candidate
- Output: top-k similar reactions + IDs

### B) Literature retriever (text)

- Corpus: 50–100 papers/reviews (expand gradually to ~550 following the professor’s suggestion)
- Query: reaction class + keywords (“amide coupling”, “Suzuki”, “protecting group”…)
- Output: top-k chunks with DOI + quote

### C) LLM grounded generation

The prompt may use only:

1. candidate + metrics  
2. similar USPTO reactions  
3. literature quotes  

If evidence is missing → return `uncertain` / `insufficient evidence` — **do not hallucinate**.

---

## Appendix — Output JSON schema

```json
{
  "target": "SMILES",
  "candidates": [
    {
      "id": 1,
      "reaction": "A.B>>T",
      "metrics": {
        "model_p": 0.88,
        "sa_score": 1.2,
        "sc_score": 0.72
      },
      "rag": {
        "verdict": "accept",
        "condition": "...",
        "yield": ["..."],
        "sources": [
          {"type": "uspto", "id": "xx123", "similarity": 0.94},
          {"type": "doi", "id": "10.xxxx/yyy", "quote": "..."}
        ]
      }
    }
  ]
}
```
