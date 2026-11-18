"""
GhostSignal — Flask API
========================
Minimal API layer exposing the ML classifier to the frontend.

Endpoints
---------
  GET  /api/health          → model readiness check
  POST /api/analyse         → classify a single text string
  POST /api/analyse/batch   → classify up to 20 texts at once

Run
---
  python app.py
  # or: flask --app app run --port 5000
"""

import logging
import sys
from dataclasses import asdict
from pathlib import Path

from flask import Flask, jsonify, request
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
)
log = logging.getLogger("ghostsignal.api")

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ---------------------------------------------------------------------------
# Classifier — lazy singleton
# ---------------------------------------------------------------------------
_classifier = None

def get_classifier():
    global _classifier
    if _classifier is None:
        from src.model.classifier import GhostSignalClassifier
        _classifier = GhostSignalClassifier()
        log.info("Classifier initialised: %s", _classifier)
    return _classifier


# ---------------------------------------------------------------------------
# Investigation Service — lazy singleton
# ---------------------------------------------------------------------------
_investigation_service = None

def get_investigation_service():
    global _investigation_service
    if _investigation_service is None:
        from src.services.investigation_service import InvestigationService
        _investigation_service = InvestigationService()
        log.info("InvestigationService initialised: %s", _investigation_service)
    return _investigation_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _prediction_to_dict(result) -> dict:
    """Convert a PredictionResult dataclass to a plain JSON-safe dict."""
    return {
        "text_preview": result.text_preview,
        "sentiment": {
            "label":           result.sentiment.label,
            "probabilities":   result.sentiment.probabilities,
            "is_uncertain":    result.sentiment.is_uncertain,
            "model_available": result.sentiment.model_available,
            "caveat":          result.sentiment.caveat,
            "error":           result.sentiment.error,
        },
        "authenticity": {
            "label":           result.authenticity.label,
            "probabilities":   result.authenticity.probabilities,
            "is_uncertain":    result.authenticity.is_uncertain,
            "model_available": result.authenticity.model_available,
            "caveat":          result.authenticity.caveat,
            "error":           result.authenticity.error,
        },
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    """Return model readiness status."""
    clf = get_classifier()
    ready = clf.is_ready()
    status = "ready" if all(ready.values()) else "degraded" if any(ready.values()) else "unavailable"
    return jsonify({
        "status": status,
        "models": ready,
    })


@app.post("/api/analyse")
def analyse():
    """
    Classify a single text string.

    Request body (JSON):
        { "text": "..." }

    Response:
        {
          "text_preview": "...",
          "sentiment":    { label, probabilities, is_uncertain, model_available, caveat, error },
          "authenticity": { label, probabilities, is_uncertain, model_available, caveat, error }
        }
    """
    body = request.get_json(silent=True) or {}
    text = body.get("text", "")

    if not isinstance(text, str):
        return jsonify({"error": "Field 'text' must be a string."}), 400
    if not text.strip():
        return jsonify({"error": "Field 'text' is empty."}), 400
    if len(text) > 20_000:
        return jsonify({"error": "Text exceeds 20,000 character limit."}), 400

    clf = get_classifier()
    result = clf.predict(text)
    return jsonify(_prediction_to_dict(result))


@app.post("/api/investigate")
def investigate():
    """
    Main investigation endpoint.

    Accepts an investigation query/topic, automatically dispatches concurrent
    collection via GDELT News and Hacker News discussions, combines records into
    the GHOSTSIGNAL schema, attaches sentiment and confidence to each record,
    calculates news and discussion sentiment breakdown, and computes timeline.

    Request body (JSON):
        { "topic": "...", "sources": ["GDELT News", "Hacker News"], "timespan": "7d" }
    """
    body   = request.get_json(silent=True) or {}
    topic  = body.get("topic") or body.get("query") or ""
    sources = body.get("sources", ["GDELT News", "Hacker News"])
    timespan = body.get("timespan", "7d")

    if not isinstance(topic, str) or not topic.strip():
        return jsonify({"error": "Field 'topic' must be a non-empty string."}), 400
    if len(topic) > 2000:
        return jsonify({"error": "Topic exceeds 2,000 character limit."}), 400

    topic = topic.strip()
    clf = get_classifier()

    # 1. Automatic multi-source collection with sentiment analysis attached to records
    service = get_investigation_service()
    inv_response = service.investigate(query=topic, timespan=timespan, classifier=clf)

    # If both collectors failed and no records were obtained
    if inv_response.status == "error" and inv_response.total_records == 0:
        err_msg = "; ".join(f"{k}: {v}" for k, v in inv_response.collector_errors.items())
        return jsonify({
            "investigation_id": inv_response.investigation_id,
            "topic": topic,
            "query": topic,
            "status": "error",
            "sources": sources,
            "error": f"All collection sources failed: {err_msg}",
            "collector_errors": inv_response.collector_errors,
            "metrics": {
                "records_total": 0,
                "sources_count": len(sources),
                "news_count": 0,
                "discussions_count": 0,
                "hn_count": 0,
                "bluesky_count": 0,
                "positive_pct": 0,
                "positive_count": 0,
                "negative_count": 0,
                "uncertain_count": 0,
                "news_positive": 0,
                "news_negative": 0,
                "discussions_positive": 0,
                "discussions_negative": 0,
            },
            "sentiment_stats": inv_response.sentiment_stats,
            "news_count": 0,
            "discussions_count": 0,
            "hn_count": 0,
            "bluesky_count": 0,
            "news_records": [],
            "discussion_records": [],
            "hn_records": [],
            "bluesky_records": [],
            "records": [],
            "timeline": [],
        }), 502

    # 2. Extract Sentiment & Authenticity statistics across actual records
    stats = inv_response.sentiment_stats or {}
    news_stats = stats.get("news", {"total": 0, "positive": 0, "negative": 0, "uncertain": 0, "positive_pct": 0})
    disc_stats = stats.get("discussions", {"total": 0, "positive": 0, "negative": 0, "uncertain": 0, "positive_pct": 0})
    total_stats = stats.get("totals", {"total": inv_response.total_records, "positive": 0, "negative": 0, "uncertain": 0, "positive_pct": 50})

    total_records = inv_response.total_records
    news_count = inv_response.news_count
    discussions_count = inv_response.discussions_count
    pos_pct = total_stats.get("positive_pct", 50)
    pos_count = total_stats.get("positive", 0)
    neg_count = total_stats.get("negative", 0)
    unc_count = total_stats.get("uncertain", 0)

    # Overall label and probability distribution
    if total_records > 0:
        classified_count = pos_count + neg_count  # exclude uncertain from ratio denominator
        if classified_count > 0:
            pos_ratio = round(pos_count / classified_count, 4)
            neg_ratio = round(neg_count / classified_count, 4)
        else:
            pos_ratio = 0.5
            neg_ratio = 0.5
        sent_label = "Positive" if pos_count > neg_count else "Negative" if neg_count > pos_count else "uncertain"
        sent_probs = {"Positive": pos_ratio, "Negative": neg_ratio}
        is_sent_uncertain = (pos_count == neg_count) or (max(pos_ratio, neg_ratio) < 0.60)
    else:
        topic_pred = clf.predict(topic)
        sent_label = topic_pred.sentiment.label
        sent_probs = topic_pred.sentiment.probabilities
        is_sent_uncertain = topic_pred.sentiment.is_uncertain

    # Aggregate authenticity across records
    all_recs = inv_response.records
    hon_count = sum(1 for r in all_recs if r.get("metadata", {}).get("authenticity_label") == "HONEST")
    fake_count = sum(1 for r in all_recs if r.get("metadata", {}).get("authenticity_label") == "FAKE")
    labelled_count = hon_count + fake_count
    if labelled_count > 0:
        hon_ratio = round(hon_count / labelled_count, 4)
        fake_ratio = round(fake_count / labelled_count, 4)
        auth_label = "HONEST" if hon_count >= fake_count else "FAKE"
        auth_probs = {"HONEST": hon_ratio, "FAKE": fake_ratio}
        is_auth_uncertain = (hon_count == fake_count)
    elif total_records > 0:
        # Records were collected but none carried an authenticity label
        # (e.g. all are discussions without product context); fall back to topic prediction.
        topic_pred = clf.predict(topic)
        auth_label = topic_pred.authenticity.label
        auth_probs = topic_pred.authenticity.probabilities
        is_auth_uncertain = topic_pred.authenticity.is_uncertain
    else:
        topic_pred = clf.predict(topic)
        auth_label = topic_pred.authenticity.label
        auth_probs = topic_pred.authenticity.probabilities
        is_auth_uncertain = topic_pred.authenticity.is_uncertain

    # 3. Build activity timeline from the actual collected and classified records
    from src.services.investigation_service import build_timeline_from_records
    timeline = build_timeline_from_records(inv_response.records, pos_prob=(pos_pct / 100.0))

    hn_count = inv_response.hn_count
    bluesky_count = inv_response.bluesky_count

    active_sources = []
    if news_count > 0 or "gdelt" not in inv_response.collector_errors:
        active_sources.append("GDELT News")
    if hn_count > 0 or "hackernews" not in inv_response.collector_errors:
        active_sources.append("Hacker News")
    if bluesky_count > 0 or "bluesky" not in inv_response.collector_errors:
        active_sources.append("Bluesky")
    if not active_sources:
        active_sources = sources

    return jsonify({
        "investigation_id": inv_response.investigation_id,
        "topic":            topic,
        "query":            topic,
        "status":           inv_response.status,
        "sources":          active_sources,
        "metrics": {
            "records_total":        total_records,
            "sources_count":        len(active_sources),
            "news_count":           news_count,
            "discussions_count":    discussions_count,
            "hn_count":             hn_count,
            "bluesky_count":        bluesky_count,
            "positive_pct":         pos_pct,
            "positive_count":       pos_count,
            "negative_count":       neg_count,
            "uncertain_count":      unc_count,
            "news_positive":        news_stats.get("positive", 0),
            "news_negative":        news_stats.get("negative", 0),
            "discussions_positive": disc_stats.get("positive", 0),
            "discussions_negative": disc_stats.get("negative", 0),
        },
        "sentiment_stats":      stats,
        "news_count":           news_count,
        "discussions_count":    discussions_count,
        "hn_count":             hn_count,
        "bluesky_count":        bluesky_count,
        "news_records":         inv_response.news_records,
        "discussion_records":   inv_response.discussion_records,
        "hn_records":           inv_response.hn_records,
        "bluesky_records":      inv_response.bluesky_records,
        "records":              inv_response.records,
        "collector_errors":     inv_response.collector_errors,
        "sentiment": {
            "label":           sent_label,
            "probabilities":   sent_probs,
            "is_uncertain":    is_sent_uncertain,
            "model_available": True,
            "caveat":          "Aggregated from TF-IDF + Logistic Regression predictions on collected records.",
        },
        "authenticity": {
            "label":           auth_label,
            "probabilities":   auth_probs,
            "is_uncertain":    is_auth_uncertain,
            "model_available": True,
            "caveat":          "Reviewer-profile pattern signals evaluated across collected text.",
        },
        "timeline": timeline,
    })


@app.post("/api/analyse/batch")
def analyse_batch():
    """
    Classify up to 20 texts at once.

    Request body (JSON):
        { "texts": ["...", "..."] }

    Response:
        { "results": [ <prediction>, ... ] }
    """
    body = request.get_json(silent=True) or {}
    texts = body.get("texts", [])

    if not isinstance(texts, list):
        return jsonify({"error": "Field 'texts' must be a list."}), 400
    if len(texts) == 0:
        return jsonify({"error": "Field 'texts' is empty."}), 400
    if len(texts) > 20:
        return jsonify({"error": "Batch limit is 20 texts per request."}), 400

    cleaned = []
    for i, t in enumerate(texts):
        if not isinstance(t, str):
            return jsonify({"error": f"Item {i} is not a string."}), 400
        if len(t) > 20_000:
            return jsonify({"error": f"Item {i} exceeds 20,000 character limit."}), 400
        cleaned.append(t)

    clf = get_classifier()
    results = clf.predict_batch(cleaned)
    return jsonify({"results": [_prediction_to_dict(r) for r in results]})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    log.info("Starting GhostSignal API on http://0.0.0.0:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
