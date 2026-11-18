# GHOSTSIGNAL — Technical Overview

> Models · Algorithms · Training pipeline · Product architecture · Formulas

---

## 1. What GHOSTSIGNAL is

GHOSTSIGNAL is an on-demand investigation tool. Given any topic — a brand, institution, product, event or claim — it queries six public data sources concurrently, classifies every collected record using two trained ML models, builds a 7-day activity timeline, and produces a structured investigation report with source attribution, timestamps, and sentiment signals.

**The report is the product's primary output.** The system does not remove content, contact accounts, or make moderation decisions.

---

## 2. Full system pipeline

```
User query
    │
    ▼
POST /api/investigate (Flask)
    │
    ├── Batch 1 (ThreadPoolExecutor, 2 workers, concurrent)
    │       ├── Hacker News collector
    │       └── Bluesky collector
    │
    ├── Batch 2 (ThreadPoolExecutor, 3 workers, concurrent)
    │       ├── DEV Community collector
    │       ├── Stack Exchange collector
    │       └── Discourse collector
    │
    ├── GDELT collector (sequential — rate-limited, max 2 attempts)
    │
    ├── Normalise all records → unified 17-field schema
    │
    ├── ML inference: sentiment classifier on every record
    ├── ML inference: authenticity classifier on every record
    │
    ├── Compute per-source sentiment statistics
    ├── Build 7-day activity timeline from published_at timestamps
    │
    └── Return InvestigationResponse → React frontend renders results + report
```

Each collector runs inside its own `try/except`. A failure writes to `collector_errors[source]` and execution continues. Status is `"partial"` if any source failed but records were collected; `"error"` only if every source failed.

---

## 3. Data sources

| Source | API endpoint | Records collected |
|---|---|---|
| Hacker News | `hn.algolia.com/api/v1/search_by_date` | Stories + comments: title, score, comment count, URL, author, timestamp |
| Bluesky | `api.bsky.app/xrpc/app.bsky.feed.searchPosts` | Posts: text, author handle, likes, replies, reposts, external links |
| DEV Community | `dev.to/api/articles/search` | Articles: title, description, author, reactions, comments, tags |
| Stack Exchange | `api.stackexchange.com/2.3/search/advanced` | Questions: title, tags, score, answer count, view count |
| Discourse | `<forum>/search.json` | Topics: title, excerpt, author, likes, reply count, views |
| GDELT News | `api.gdeltproject.org/api/v2/doc/doc` | News articles: title, URL, domain, publication date |

No API keys, OAuth, or authentication required for any source.

### Relevance post-filter

The Algolia (HN), DEV, and SE search APIs tokenise queries — a search for "GSSS institute of engineering and technology" returns posts containing any individual word including "engineering" or "technology". To fix this, every hit is post-filtered using whole-word regex matching on the query's distinctive tokens after stripping stop-words and common technical vocabulary.

```python
# Token extraction
_distinctive_tokens("amazon")                            → ['amazon']
_distinctive_tokens("GSSS institute of engineering")     → ['gsss']
_distinctive_tokens("artificial intelligence")           → ['artificial', 'intelligence']

# Whole-word match — prevents substring false positives
re.search(r"\bamazon\b", "amazynk repository")  → None   # rejected
re.search(r"\bamazon\b", "amazon aws pricing")  → match  # accepted
```

---

## 4. Unified record schema

Every source record is normalised to 17 canonical fields before ML inference:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable identifier, e.g. `hn_123456`, `bsky_abc`, `gdelt_sha16` |
| `source` | string | `"hackernews"`, `"bluesky"`, `"gdelt"`, etc. |
| `content` | string | Main text for classification — never fabricated |
| `title` | string \| null | Article/story title where available |
| `url` | string \| null | Canonical URL to the original item |
| `published_at` | ISO-8601 \| null | UTC timestamp from the source API |
| `retrieved_at` | ISO-8601 | UTC timestamp of collection |
| `author` | string \| null | Username or handle |
| `likes` | int \| null | Upvotes / reactions / likes |
| `comments` | int \| null | Reply or comment count |
| `views` | int \| null | View count where available |
| `predicted_label` | string \| null | Set after ML inference |
| `confidence` | float \| null | `max(predict_proba)` — set after ML inference |
| `metadata.provenance` | object | Collector name, API endpoint, query, timestamp |
| `metadata.sentiment_probabilities` | object | `{Positive: 0.87, Negative: 0.13}` |
| `metadata.authenticity_label` | string \| null | `"HONEST"` or `"FAKE"` |
| `metadata.authenticity_probabilities` | object | `{HONEST: 0.93, FAKE: 0.07}` |

Missing fields are always `null`. Nothing is fabricated.

---

## 5. Machine learning models

### 5.1 Two independent classifiers

**Sentiment classifier**
- Task: classify text as `Positive` or `Negative`
- Labels: rating-derived — 1–2 ★ → Negative, 4–5 ★ → Positive, 3-star excluded
- Training data: Amazon reviews + Google reviews (combined JSONL)

**Authenticity classifier**
- Task: classify reviewer profiles as `HONEST` or `FAKE`
- Labels: reviewer-associated — `reviewer_labeled_fake=1` → FAKE, `reviewer_labeled_honest=1` → HONEST
- Records where both fields are 0 or null are excluded entirely
- Training data: Amazon reviews (labelled subset only)

### 5.2 Algorithm: TF-IDF + Logistic Regression

Both classifiers use the same sklearn `Pipeline`:

```
raw text  →  TfidfVectorizer  →  sparse vector ∈ ℝ⁵⁰⁰⁰⁰  →  LogisticRegression  →  class probabilities
```

---

## 6. TF-IDF — how it works

TF-IDF (Term Frequency – Inverse Document Frequency) converts a text into a numerical vector. Words that appear frequently in one document but rarely across all documents get high weight; common words like "the" get near-zero weight.

### Term Frequency

```
TF(t, d)  =  count(t in d)  /  total_terms(d)

Example: "great product great price" (4 words)
  TF("great", d)   =  2 / 4  =  0.50
  TF("product", d) =  1 / 4  =  0.25
```

### Sublinear TF scaling (used in GHOSTSIGNAL)

```
TF_sublinear(t, d) = 1 + log( count(t in d) )    if count > 0
                   = 0                             otherwise

Reason: dampens the effect of a word appearing 100× vs 10×
        in a single document (e.g. a reviewer who writes "great"
        20 times shouldn't get 20× the weight of one who writes it once)
```

### Inverse Document Frequency

```
IDF(t, D)  =  log( (1 + N) / (1 + df(t)) )  +  1

where:
  N     = total number of documents in the corpus
  df(t) = number of documents containing term t
  +1    = smoothing to avoid zero division for unseen terms

Intuition: "great" appears in 80% of reviews → low IDF
           "counterfeit" appears in 2% of reviews → high IDF
```

### Final TF-IDF weight

```
TFIDF(t, d, D)  =  TF_sublinear(t, d)  ×  IDF(t, D)

Each document becomes a sparse vector in ℝ⁵⁰⁰⁰⁰ (max_features = 50,000)
L2-normalised: ||x||₂ = 1  →  dot products equal cosine similarity
```

### GHOSTSIGNAL TF-IDF parameters

| Parameter | Value | Reason |
|---|---|---|
| `max_features` | 50,000 | Covers review vocabulary; limits memory |
| `ngram_range` | (1, 2) | Captures "broken" and "stopped working" |
| `sublinear_tf` | True | Log-scale TF dampens term repetition |
| `min_df` | 2 | Ignores terms in only 1 document (noise) |
| `strip_accents` | "unicode" | Normalises accented characters |
| `analyzer` | "word" | Word-level tokenisation |

---

## 7. Logistic Regression — how it works

Given the TF-IDF vector **x** ∈ ℝ⁵⁰⁰⁰⁰, Logistic Regression learns weight vector **w** and applies the sigmoid function to produce class probabilities.

### Forward pass (binary)

```
z  =  w · x  +  b          (linear combination of TF-IDF features)

P(Positive | x)  =  σ(z)  =  1 / (1 + e^(−z))
P(Negative | x)  =  1 − P(Positive | x)

Decision:
  ŷ = Positive   if P(Positive | x) > 0.5
  ŷ = Negative   otherwise
```

### Training objective — log-loss with L2 regularisation

```
L(w)  =  −(1/N) Σᵢ [ yᵢ · log(ŷᵢ)  +  (1 − yᵢ) · log(1 − ŷᵢ) ]  +  λ · ‖w‖²
         ─────────────────────────────────────────────────────────    ──────────
                        cross-entropy loss                            L2 penalty

λ = 1/C  →  C = 1.0 (GHOSTSIGNAL default)
L2 term penalises large weights → prevents overfitting on rare n-grams
Minimised with solver = "lbfgs" (Limited-memory BFGS, handles sparse matrices)
```

### Class imbalance — balanced weighting

```
class_weight = "balanced"

wᵢ  =  N / (K × nᵢ)

where:
  N  = total training samples
  K  = number of classes (2)
  nᵢ = samples in class i

Effect: minority class gets proportionally higher loss contribution,
        forcing the model to learn it rather than predict the majority always
```

### Uncertainty threshold

```
confidence  =  max( P(Positive | x),  P(Negative | x) )

label = predicted_class    if confidence ≥ 0.60
label = "uncertain"        if confidence  < 0.60

UNCERTAINTY_THRESHOLD = 0.60 (defined in classifier.py)
```

Predictions below 0.60 are shown with a "Low confidence" flag in the UI rather than a potentially misleading label.

---

## 8. Training pipeline

```
Step 1  Load JSONL
        Amazon reviews + Google reviews (sentiment)
        Amazon reviews labelled subset (authenticity)

Step 2  Label assignment
        Sentiment:    rating 1–2 → "Negative", 4–5 → "Positive", 3 → excluded
        Authenticity: reviewer_labeled_fake=1 → "FAKE"
                      reviewer_labeled_honest=1 → "HONEST"
                      both 0 or null → excluded

Step 3  Group-aware split (GroupShuffleSplit, 80/20, seed=42)
        Guarantees every reviewer's records are in EITHER train OR test
        → prevents data leakage from reviewer writing-style recognition

Step 4  Pipeline fit (TF-IDF → LogisticRegression on training set only)
        Vocabulary is learned only from train; test set is never seen during fit

Step 5  Evaluate on held-out test set
        Accuracy, precision, recall, F1 (per-class + macro + weighted)
        Confusion matrix
        Reviewer-level majority-vote accuracy (authenticity only)

Step 6  Save artifact
        joblib.dump(pipeline, "models/sentiment_pipeline.joblib")
        joblib.dump(pipeline, "models/authenticity_pipeline.joblib")
```

### Why GroupShuffleSplit matters

A naive `train_test_split` would allow the same reviewer's reviews to appear in both training and test sets. Because a reviewer's writing style is consistent, the model would learn to recognise specific authors rather than genuine sentiment patterns, inflating test accuracy without real generalisation.

```
GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)

→ reviewer A's 47 reviews all go to train OR all go to test, never split
→ verified: train_groups ∩ test_groups = ∅  (raises RuntimeError if violated)
→ no silent fallback to record-level split — data leakage is not tolerated
```

---

## 9. Inference — how a record gets classified

After collection, every record's `content` field (falling back to `title`) is passed through both classifiers:

```
raw text
    │
    ▼
TfidfVectorizer.transform()  →  sparse vector ∈ ℝ⁵⁰⁰⁰⁰
    │
    ▼
LogisticRegression.predict_proba()
    │
    ├── Sentiment:    {"Positive": 0.87, "Negative": 0.13}
    └── Authenticity: {"HONEST": 0.93,  "FAKE": 0.07}
    │
    ▼
Threshold check: max_prob ≥ 0.60 → label, else "uncertain"
    │
    ▼
Written back to record:
{
  "predicted_label": "Positive",
  "confidence": 0.87,
  "metadata": {
    "sentiment_probabilities":    {"Positive": 0.87, "Negative": 0.13},
    "sentiment_uncertain":        false,
    "authenticity_label":         "HONEST",
    "authenticity_probabilities": {"HONEST": 0.93, "FAKE": 0.07}
  }
}
```

### Aggregate statistics

```python
# Sentiment ratio uses only classified records (not uncertain)
classified_count = positive_count + negative_count
pos_ratio        = positive_count / classified_count
neg_ratio        = negative_count / classified_count

# Authenticity ratio uses only records that have an authenticity_label
# (discussion records without product context don't dilute the signal)
labelled_count = hon_count + fake_count
hon_ratio      = hon_count / labelled_count
```

---

## 10. Technology stack

### Backend

| Component | Technology |
|---|---|
| API framework | Flask + flask-cors |
| ML | scikit-learn — Pipeline, TfidfVectorizer, LogisticRegression |
| Model serialisation | joblib (.joblib files) |
| Data models | Pydantic v2 (all collector + service DTOs) |
| HTTP client | requests with per-collector Session |
| Concurrency | concurrent.futures.ThreadPoolExecutor |
| Numerical | numpy |

### Frontend

| Component | Technology |
|---|---|
| Framework | React 18 + TypeScript |
| Build tool | Vite 6 |
| Animations | Framer Motion |
| Icons | lucide-react |
| Charts | Custom SVG ActivityChart component |
| State | useState + useCallback (no external state library) |

### API endpoints

| Endpoint | Purpose |
|---|---|
| `GET  /api/health` | Model readiness check |
| `POST /api/analyse` | Classify one text string |
| `POST /api/analyse/batch` | Classify up to 20 texts |
| `POST /api/investigate` | Full investigation pipeline |

---

## 11. Key engineering decisions

| Decision | Reason |
|---|---|
| GroupShuffleSplit for train/test | Prevents reviewer-identity leakage across the split boundary |
| Whole-word `\b` post-filter on HN/DEV/SE | Stops substring false positives — "amazynk" ≠ "amazon" |
| Failure isolation per collector | One source error never blocks others; partial results are still useful |
| Authenticity denominator = labelled records only | Discussion records without product context don't dilute the signal to near zero |
| GDELT max_retries hard-capped at 1 | Limits total HTTP attempts to 2; prevents 429 flood on a 1 req/5 s endpoint |
| Pydantic v2 DTOs at every collector boundary | Schema enforced at collection time — no silent field dropping |
| `sublinear_tf=True` in TF-IDF | Dampens repetition; a reviewer writing "great" 20× doesn't dominate |
| `class_weight="balanced"` in LR | Handles class imbalance without manual oversampling |
| Uncertainty threshold at 0.60 | Avoids displaying misleading labels for genuinely ambiguous text |

---

## 12. Explicit limitations

- **Sentiment labels are rating-derived**, not human-annotated sentiment. A 1-star review saying "returned immediately" is Negative regardless of linguistic sentiment.
- **Authenticity labels are reviewer-associated**, not per-review fraud verdicts. FAKE means the reviewer account was labelled fake in the source dataset; it does not prove the specific text is fraudulent.
- **Similarity is a signal**, not proof of coordination or shared authorship.
- **The earliest collected record is not the narrative's origin** — only the earliest record observed in the collected dataset.
- **Probabilities are model scores**, not calibrated confidence values (Platt scaling is not applied).
- GHOSTSIGNAL does not remove, suppress, modify, or act on any content.
