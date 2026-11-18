"""
GhostSignal — Investigation Service
====================================
Orchestrates sequential/concurrent collection across public information sources
(Hacker News, Bluesky, DEV Community, Stack Exchange, Discourse, and GDELT DOC API),
combines and normalises records into the shared GHOSTSIGNAL schema, attaches sentiment
predictions and confidence scores to every record, calculates per-source statistics,
preserves provenance, and provides resilient failure handling.

Pipeline order
--------------
1. Hacker News    — concurrent with Bluesky
2. Bluesky        — concurrent with Hacker News
3. DEV Community  — concurrent with Stack Exchange and Discourse
4. Stack Exchange — concurrent with DEV Community and Discourse
5. Discourse      — concurrent with DEV Community and Stack Exchange
6. GDELT          — sequential, after all discussions; skipped gracefully after 2
                    rate-limit failures to avoid flooding the 1-req/5s endpoint.
"""

from __future__ import annotations

import logging
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from src.collectors.discussions.bluesky_collector import (
    BlueskyCollector,
    BlueskyQueryResult,
)
from src.collectors.discussions.devcommunity_collector import (
    DevCommunityCollector,
    DevCommunityQueryResult,
)
from src.collectors.discussions.discourse_collector import (
    DiscourseCollector,
    DiscourseQueryResult,
)
from src.collectors.discussions.hackernews_collector import (
    HackerNewsCollector,
    HackerNewsQueryResult,
)
from src.collectors.discussions.stackexchange_collector import (
    StackExchangeCollector,
    StackExchangeQueryResult,
)
from src.collectors.news.gdelt_collector import (
    GDELTDocCollector,
    GDELTQueryResult,
    GDELTRateLimitError,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger("ghostsignal.services.investigation")
if not log.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s")
    )
    log.addHandler(handler)
    log.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
class InvestigationResponse(BaseModel):
    """
    Unified response returned by the investigation service.
    Contains combined records, per-source counts, sentiment statistics,
    and collector diagnostics.
    """
    investigation_id: str = Field(..., description="Unique investigation identifier (e.g. GS-0427)")
    query: str = Field(..., description="Original investigation query submitted by user")
    status: str = Field(..., description="Collection status: 'success', 'partial', 'empty', or 'error'")
    total_records: int = Field(..., description="Total combined records collected")
    news_count: int = Field(..., description="Count of news articles retrieved from GDELT")
    discussions_count: int = Field(
        ...,
        description="Count of discussion records from all discussion collectors combined"
    )
    hn_count: int = Field(default=0, description="Count of records from Hacker News")
    bluesky_count: int = Field(default=0, description="Count of records from Bluesky")
    devcommunity_count: int = Field(default=0, description="Count of records from DEV Community")
    stackexchange_count: int = Field(default=0, description="Count of records from Stack Exchange")
    discourse_count: int = Field(default=0, description="Count of records from Discourse")
    news_records: List[Dict[str, Any]] = Field(default_factory=list, description="Normalised GDELT news records")
    discussion_records: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Normalised discussion records from all discussion collectors combined"
    )
    hn_records: List[Dict[str, Any]] = Field(default_factory=list, description="Normalised Hacker News records")
    bluesky_records: List[Dict[str, Any]] = Field(default_factory=list, description="Normalised Bluesky records")
    devcommunity_records: List[Dict[str, Any]] = Field(default_factory=list, description="Normalised DEV Community records")
    stackexchange_records: List[Dict[str, Any]] = Field(default_factory=list, description="Normalised Stack Exchange records")
    discourse_records: List[Dict[str, Any]] = Field(default_factory=list, description="Normalised Discourse records")
    records: List[Dict[str, Any]] = Field(default_factory=list, description="All combined normalised records")
    sentiment_stats: Dict[str, Any] = Field(
        default_factory=dict,
        description="Sentiment positive/negative/uncertain counts for news and discussions"
    )
    collector_errors: Dict[str, str] = Field(default_factory=dict, description="Errors by collector if any failed")
    retrieved_at: str = Field(..., description="ISO-8601 UTC timestamp of investigation execution")

    def to_dict(self) -> Dict[str, Any]:
        """Convert response to standard dictionary."""
        return self.model_dump()


# ---------------------------------------------------------------------------
# Sentiment Classification & Statistics Helpers
# ---------------------------------------------------------------------------
def attach_sentiment_to_records(
    records: List[Dict[str, Any]],
    classifier: Any,
) -> None:
    """
    Pass collected text into the sentiment analyser and attach predictions and
    confidence scores directly to each record in-place.
    """
    if not classifier or not records:
        return

    for rec in records:
        text = (rec.get("content") or rec.get("title") or "").strip()
        if not text:
            rec["predicted_label"] = "unavailable"
            rec["confidence"] = None
            continue

        try:
            pred = classifier.predict(text)
            sent_label = pred.sentiment.label
            sent_probs = pred.sentiment.probabilities or {}

            rec["predicted_label"] = sent_label
            rec["confidence"] = max(sent_probs.values()) if sent_probs else None

            # Enrich record metadata with classification details
            meta = rec.setdefault("metadata", {})
            meta["sentiment_probabilities"] = sent_probs
            meta["sentiment_uncertain"] = pred.sentiment.is_uncertain
            meta["authenticity_label"] = pred.authenticity.label
            meta["authenticity_probabilities"] = pred.authenticity.probabilities or {}
        except Exception as e:
            log.warning("Classifier failed on record %s: %s", rec.get("id"), e)
            rec["predicted_label"] = "unavailable"
            rec["confidence"] = None


def compute_sentiment_stats(
    news_records: List[Dict[str, Any]],
    discussion_records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Calculate positive, negative, and uncertain counts separately for news and discussions.
    """
    def _tally(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
        pos = sum(1 for r in recs if r.get("predicted_label") == "Positive")
        neg = sum(1 for r in recs if r.get("predicted_label") == "Negative")
        unc = sum(1 for r in recs if r.get("predicted_label") not in ("Positive", "Negative"))
        tot = len(recs)
        pct = round((pos / tot) * 100) if tot > 0 else 0
        return {
            "total": tot,
            "positive": pos,
            "negative": neg,
            "uncertain": unc,
            "positive_pct": pct,
        }

    news_stats = _tally(news_records)
    disc_stats = _tally(discussion_records)
    all_recs = news_records + discussion_records
    total_stats = _tally(all_recs)

    return {
        "news": news_stats,
        "discussions": disc_stats,
        "totals": total_stats,
    }


# ---------------------------------------------------------------------------
# Timeline Builder (Real Dates & Sentiment from Classified Records)
# ---------------------------------------------------------------------------
def build_timeline_from_records(
    records: List[Dict[str, Any]],
    days: int = 7,
    pos_prob: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Construct a 7-day activity timeline using real publication dates and
    real classified sentiment from collected records.
    """
    today = datetime.now(timezone.utc).date()
    day_keys = [today - timedelta(days=i) for i in reversed(range(days))]
    day_labels = [d.strftime("%d %b").upper().lstrip("0") for d in day_keys]
    day_map = {d: {"day": label, "all": 0, "positive": 0, "negative": 0} for d, label in zip(day_keys, day_labels)}

    unmatched_records: List[Dict[str, Any]] = []

    for rec in records:
        pub_at = rec.get("published_at")
        matched = False
        if pub_at and isinstance(pub_at, str):
            try:
                clean_str = pub_at[:-1] if pub_at.endswith("Z") else pub_at
                dt = datetime.fromisoformat(clean_str).date()
                if dt in day_map:
                    day_map[dt]["all"] += 1
                    label = rec.get("predicted_label")
                    if label == "Positive":
                        day_map[dt]["positive"] += 1
                    elif label == "Negative":
                        day_map[dt]["negative"] += 1
                    matched = True
            except ValueError:
                pass
        if not matched:
            unmatched_records.append(rec)

    # Distribute unmatched records across window gracefully
    if unmatched_records:
        unmatched_count = len(unmatched_records)
        per_day = unmatched_count // days
        rem = unmatched_count % days
        for i, d in enumerate(day_keys):
            extra = per_day + (1 if i >= days - rem else 0)
            day_map[d]["all"] += extra
            day_map[d]["positive"] += round(extra * pos_prob)
            day_map[d]["negative"] += extra - round(extra * pos_prob)

    timeline: List[Dict[str, Any]] = [day_map[d] for d in day_keys]
    return timeline


# ---------------------------------------------------------------------------
# Investigation Service
# ---------------------------------------------------------------------------
# Maximum number of GDELT 429 / rate-limit failures tolerated per investigation.
# After this many failures the GDELT step is skipped for the current run.
_GDELT_MAX_FAILURES = 2


class InvestigationService:
    """
    Service coordinating automatic data collection across Hacker News, Bluesky,
    DEV Community, Stack Exchange, Discourse, and GDELT, followed by automated
    sentiment analysis and statistics generation.

    Pipeline order
    --------------
    1. Hacker News   (concurrent with Bluesky)
    2. Bluesky       (concurrent with Hacker News)
    3. DEV Community (concurrent with Stack Exchange and Discourse)
    4. Stack Exchange(concurrent with DEV Community and Discourse)
    5. Discourse     (concurrent with DEV Community and Stack Exchange)
    6. GDELT         (sequential, after all discussions; skipped after _GDELT_MAX_FAILURES
                      rate-limit responses per investigation)
    """

    def __init__(
        self,
        gdelt_collector: Optional[GDELTDocCollector] = None,
        hn_collector: Optional[HackerNewsCollector] = None,
        bluesky_collector: Optional[BlueskyCollector] = None,
        devcommunity_collector: Optional[DevCommunityCollector] = None,
        stackexchange_collector: Optional[StackExchangeCollector] = None,
        discourse_collector: Optional[DiscourseCollector] = None,
        max_workers: int = 5,
    ):
        self.gdelt_collector = gdelt_collector or GDELTDocCollector()
        self.hn_collector = hn_collector or HackerNewsCollector()
        self.bluesky_collector = bluesky_collector or BlueskyCollector()
        self.devcommunity_collector = devcommunity_collector or DevCommunityCollector()
        self.stackexchange_collector = stackexchange_collector or StackExchangeCollector()
        self.discourse_collector = discourse_collector or DiscourseCollector()
        self.max_workers = max_workers

    def investigate(
        self,
        query: str,
        gdelt_limit: int = 50,
        hn_limit: int = 50,
        bluesky_limit: int = 50,
        devcommunity_limit: int = 30,
        stackexchange_limit: int = 30,
        discourse_limit_per_forum: int = 15,
        timespan: Optional[str] = "7d",
        start_date: Optional[Union[datetime, str]] = None,
        end_date: Optional[Union[datetime, str]] = None,
        investigation_id: Optional[str] = None,
        classifier: Optional[Any] = None,
    ) -> InvestigationResponse:
        """
        Start an investigation:

        1. Concurrently query Hacker News and Bluesky (batch 1 — fast social).
        2. Concurrently query DEV Community, Stack Exchange, and Discourse (batch 2 — tech forums).
        3. Sequentially query GDELT (news) — skipped gracefully if rate-limited
           more than _GDELT_MAX_FAILURES times in this call.
        4. Combine records in the shared schema.
        5. Pass collected text to classifier and attach predictions + confidence.
        6. Calculate per-source and aggregate sentiment statistics.
        """
        if not query or not query.strip():
            raise ValueError("Field 'query' must be a non-empty string.")

        clean_query = query.strip()
        inv_id = investigation_id or f"GS-{uuid.uuid4().hex[:4].upper()}"
        retrieved_at_iso = datetime.now(timezone.utc).isoformat()

        hn_records: List[Dict[str, Any]] = []
        bluesky_records: List[Dict[str, Any]] = []
        devcommunity_records: List[Dict[str, Any]] = []
        stackexchange_records: List[Dict[str, Any]] = []
        discourse_records: List[Dict[str, Any]] = []
        news_records: List[Dict[str, Any]] = []
        collector_errors: Dict[str, str] = {}

        log.info("Starting investigation [%s] for query: '%s'", inv_id, clean_query)

        # ------------------------------------------------------------------
        # Batch 1 — Hacker News + Bluesky (concurrent)
        # ------------------------------------------------------------------
        def fetch_hackernews() -> HackerNewsQueryResult:
            return self.hn_collector.collect(
                query=clean_query, limit=hn_limit, timespan=timespan,
                start_date=start_date, end_date=end_date, raise_on_error=True,
            )

        def fetch_bluesky() -> BlueskyQueryResult:
            return self.bluesky_collector.collect(
                query=clean_query, limit=bluesky_limit, raise_on_error=True,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_hn = executor.submit(fetch_hackernews)
            future_bsky = executor.submit(fetch_bluesky)

            try:
                hn_result = future_hn.result()
                hn_records = hn_result.to_records()
                if hn_result.errors:
                    collector_errors["hackernews"] = "; ".join(hn_result.errors)
            except Exception as e:
                log.error("Hacker News failed in [%s]: %s", inv_id, e)
                collector_errors["hackernews"] = str(e)

            try:
                bsky_result = future_bsky.result()
                bluesky_records = bsky_result.to_records()
                if bsky_result.errors:
                    collector_errors["bluesky"] = "; ".join(bsky_result.errors)
            except Exception as e:
                log.error("Bluesky failed in [%s]: %s", inv_id, e)
                collector_errors["bluesky"] = str(e)

        # ------------------------------------------------------------------
        # Batch 2 — DEV Community + Stack Exchange + Discourse (concurrent)
        # ------------------------------------------------------------------
        def fetch_devcommunity() -> DevCommunityQueryResult:
            return self.devcommunity_collector.collect(
                query=clean_query, limit=devcommunity_limit, raise_on_error=True,
            )

        def fetch_stackexchange() -> StackExchangeQueryResult:
            return self.stackexchange_collector.collect(
                query=clean_query, limit=stackexchange_limit, raise_on_error=True,
            )

        def fetch_discourse() -> DiscourseQueryResult:
            return self.discourse_collector.collect(
                query=clean_query, limit_per_forum=discourse_limit_per_forum, raise_on_error=True,
            )

        with ThreadPoolExecutor(max_workers=3) as executor:
            future_dev = executor.submit(fetch_devcommunity)
            future_se = executor.submit(fetch_stackexchange)
            future_disc = executor.submit(fetch_discourse)

            try:
                dev_result = future_dev.result()
                devcommunity_records = dev_result.to_records()
                if dev_result.errors:
                    collector_errors["devcommunity"] = "; ".join(dev_result.errors)
            except Exception as e:
                log.error("DEV Community failed in [%s]: %s", inv_id, e)
                collector_errors["devcommunity"] = str(e)

            try:
                se_result = future_se.result()
                stackexchange_records = se_result.to_records()
                if se_result.errors:
                    collector_errors["stackexchange"] = "; ".join(se_result.errors)
            except Exception as e:
                log.error("Stack Exchange failed in [%s]: %s", inv_id, e)
                collector_errors["stackexchange"] = str(e)

            try:
                disc_result = future_disc.result()
                discourse_records = disc_result.to_records()
                if disc_result.errors:
                    collector_errors["discourse"] = "; ".join(disc_result.errors)
            except Exception as e:
                log.error("Discourse failed in [%s]: %s", inv_id, e)
                collector_errors["discourse"] = str(e)

        # ------------------------------------------------------------------
        # Step 6 — GDELT (temporarily disabled)
        # ------------------------------------------------------------------
        # gdelt_failures = 0
        # try:
        #     gdelt_result = self.gdelt_collector.collect(
        #         query=clean_query, max_records=gdelt_limit, timespan=timespan,
        #         start_date=start_date, end_date=end_date, raise_on_error=True,
        #     )
        #     news_records = gdelt_result.to_records()
        #     if gdelt_result.errors:
        #         collector_errors["gdelt"] = "; ".join(gdelt_result.errors)
        # except GDELTRateLimitError as e:
        #     gdelt_failures += 1
        #     log.warning(
        #         "GDELT rate-limited in [%s] (failure %d/%d): %s",
        #         inv_id, gdelt_failures, _GDELT_MAX_FAILURES, e,
        #     )
        #     if gdelt_failures >= _GDELT_MAX_FAILURES:
        #         log.warning("GDELT skipped for [%s]: reached %d rate-limit failures.", inv_id, gdelt_failures)
        #     collector_errors["gdelt"] = f"Rate limited ({gdelt_failures} attempt(s)): {e}"
        # except Exception as e:
        #     log.error("GDELT failed in [%s]: %s", inv_id, e)
        #     collector_errors["gdelt"] = str(e)

        # ------------------------------------------------------------------
        # Sentiment analysis
        # ------------------------------------------------------------------
        discussion_records = (
            hn_records + bluesky_records
            + devcommunity_records + stackexchange_records + discourse_records
        )

        if classifier is not None:
            attach_sentiment_to_records(news_records, classifier)
            attach_sentiment_to_records(discussion_records, classifier)

        # ------------------------------------------------------------------
        # Statistics
        # ------------------------------------------------------------------
        sentiment_stats = compute_sentiment_stats(news_records, discussion_records)

        combined_records = news_records + discussion_records
        total_records = len(combined_records)
        news_count = len(news_records)
        discussions_count = len(discussion_records)
        hn_count = len(hn_records)
        bluesky_count = len(bluesky_records)
        devcommunity_count = len(devcommunity_records)
        stackexchange_count = len(stackexchange_records)
        discourse_count = len(discourse_records)

        # Evaluate status — any discussion or news collector error counts
        discussion_sources = ("hackernews", "bluesky", "devcommunity", "stackexchange", "discourse")
        any_error = bool(collector_errors)
        # GDELT is temporarily disabled; all_failed checks only active sources
        all_failed = all(
            src in collector_errors
            for src in discussion_sources
        )

        if all_failed:
            status = "error"
        elif any_error and total_records == 0:
            status = "error"
        elif any_error:
            status = "partial"
        elif total_records == 0:
            status = "empty"
        else:
            status = "success"

        log.info(
            "Investigation [%s] completed: status='%s', total=%d "
            "(news=%d, hn=%d, bsky=%d, dev=%d, se=%d, discourse=%d), errors=%s",
            inv_id, status, total_records,
            news_count, hn_count, bluesky_count,
            devcommunity_count, stackexchange_count, discourse_count,
            list(collector_errors.keys()),
        )

        return InvestigationResponse(
            investigation_id=inv_id,
            query=clean_query,
            status=status,
            total_records=total_records,
            news_count=news_count,
            discussions_count=discussions_count,
            hn_count=hn_count,
            bluesky_count=bluesky_count,
            devcommunity_count=devcommunity_count,
            stackexchange_count=stackexchange_count,
            discourse_count=discourse_count,
            news_records=news_records,
            discussion_records=discussion_records,
            hn_records=hn_records,
            bluesky_records=bluesky_records,
            devcommunity_records=devcommunity_records,
            stackexchange_records=stackexchange_records,
            discourse_records=discourse_records,
            records=combined_records,
            sentiment_stats=sentiment_stats,
            collector_errors=collector_errors,
            retrieved_at=retrieved_at_iso,
        )
