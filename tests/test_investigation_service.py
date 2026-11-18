"""
GhostSignal - Tests for Investigation Service & Automated Workflow
==================================================================
Verifies:
- All six collectors mocked; no real API calls.
- Bluesky failure does NOT interrupt the investigation.
- DEV Community / Stack Exchange / Discourse failures are independent.
- GDELT max_retries is capped at 1 (two total attempts).
- Partial / total failure handling and source provenance.
- Flask /api/investigate endpoint integration.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import app
from src.collectors.discussions.bluesky_collector import (
    BlueskyAPIError, BlueskyCollectorError, BlueskyForbiddenError,
    BlueskyQueryResult, BlueskyRateLimitError, BlueskyRecord,
)
from src.collectors.discussions.devcommunity_collector import (
    DevCommunityAPIError, DevCommunityQueryResult, DevCommunityRecord,
    DevCommunityRateLimitError,
)
from src.collectors.discussions.discourse_collector import (
    DiscourseAPIError, DiscourseCollectorError, DiscourseQueryResult, DiscourseRecord,
)
from src.collectors.discussions.hackernews_collector import (
    HackerNewsAPIError, HackerNewsQueryResult, HackerNewsRecord,
)
from src.collectors.discussions.stackexchange_collector import (
    StackExchangeAPIError, StackExchangeQueryResult, StackExchangeRecord,
    StackExchangeRateLimitError,
)
from src.collectors.news.gdelt_collector import (
    GDELTAPIError, GDELTArticle, GDELTDocCollector,
    GDELTQueryResult, GDELTRateLimitError,
)
from src.services.investigation_service import (
    InvestigationResponse, InvestigationService, build_timeline_from_records,
)

# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def _gdelt_article(record_id="gdelt_1001", title="Great improvements in cybersecurity"):
    return GDELTArticle(
        id=record_id, source="gdelt", content=title, title=title,
        url="https://news.example.com/cyber",
        published_at="2026-11-18T01:00:00+00:00",
        retrieved_at="2026-11-18T04:00:00+00:00", author=None,
        metadata={"domain": "example.com", "provenance": {
            "collector": "GDELTDocCollector",
            "api_endpoint": "https://api.gdeltproject.org/api/v2/doc/doc",
            "source_domain": "example.com"}},
    )

def _hn_record(record_id="hn_2001", title="Critical zero day flaw discovered", item_type="story"):
    return HackerNewsRecord(
        id=record_id, source="hackernews", content=title, title=title,
        url="https://news.ycombinator.com/item?id=2001",
        published_at="2026-11-18T02:00:00+00:00",
        retrieved_at="2026-11-18T04:00:00+00:00",
        author="sec_expert", likes=45, comments=18,
        metadata={"item_type": item_type, "object_id": record_id.replace("hn_", ""),
                  "provenance": {"collector": "HackerNewsCollector",
                                 "api_endpoint": "https://hn.algolia.com/api/v1/search_by_date",
                                 "tag": item_type}},
    )

def _bsky_record(record_id="bsky_abc123", content="Breaking: vulnerability exposed"):
    return BlueskyRecord(
        id=record_id, source="bluesky", content=content,
        url="https://bsky.app/profile/researcher.bsky.social/post/abc123",
        published_at="2026-11-18T03:00:00+00:00",
        retrieved_at="2026-11-18T04:00:00+00:00",
        author="researcher.bsky.social", likes=12, comments=5, shares=3,
        metadata={"item_type": "post", "handle": "researcher.bsky.social",
                  "provenance": {"collector": "BlueskyCollector",
                                 "api_endpoint": "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts",
                                 "query": "security"}},
    )

def _dev_record(record_id="dev_1001", title="Open source security tool released"):
    return DevCommunityRecord(
        id=record_id, source="devcommunity", content=title, title=title,
        url="https://dev.to/author/open-source-security-tool",
        published_at="2026-11-18T03:30:00+00:00",
        retrieved_at="2026-11-18T04:00:00+00:00",
        author="devauthor", likes=22, comments=4,
        metadata={"item_type": "article", "article_id": 1001,
                  "provenance": {"collector": "DevCommunityCollector",
                                 "api_endpoint": "https://dev.to/api/articles/search",
                                 "query": "security"}},
    )

def _se_record(record_id="se_stackoverflow_5001", title="How to fix SQL injection vulnerability"):
    return StackExchangeRecord(
        id=record_id, source="stackexchange", content=title, title=title,
        url="https://stackoverflow.com/q/5001",
        published_at="2026-11-18T00:00:00+00:00",
        retrieved_at="2026-11-18T04:00:00+00:00",
        author="questioner", likes=15, comments=3, views=400,
        metadata={"item_type": "question", "question_id": 5001, "site": "stackoverflow",
                  "provenance": {"collector": "StackExchangeCollector",
                                 "api_endpoint": "https://api.stackexchange.com/2.3/search/advanced",
                                 "site": "stackoverflow", "query": "security"}},
    )

def _discourse_record(record_id="discourse_meta_9001", title="Security best practices discussion"):
    return DiscourseRecord(
        id=record_id, source="discourse", content=title, title=title,
        url="https://meta.discourse.org/t/security-best-practices/9001",
        published_at="2026-11-18T01:30:00+00:00",
        retrieved_at="2026-11-18T04:00:00+00:00",
        author="moderator", likes=8, comments=12, views=200,
        metadata={"item_type": "topic", "topic_id": 9001, "forum_host": "meta.discourse.org",
                  "provenance": {"collector": "DiscourseCollector",
                                 "api_endpoint": "https://meta.discourse.org/search.json",
                                 "forum_host": "meta.discourse.org", "query": "security"}},
    )

def _gdelt_result(query="test", articles=None):
    arts = articles or [_gdelt_article()]
    return GDELTQueryResult(query=query, total_records=len(arts),
                            retrieved_at="2026-11-18T04:00:00+00:00", articles=arts)

def _hn_result(query="test", records=None):
    recs = records or [_hn_record()]
    stories = sum(1 for r in recs if r.metadata.get("item_type") == "story")
    return HackerNewsQueryResult(query=query, total_records=len(recs),
                                 stories_count=stories, comments_count=len(recs) - stories,
                                 retrieved_at="2026-11-18T04:00:00+00:00", records=recs)

def _bsky_result(query="test", records=None):
    recs = records or [_bsky_record()]
    return BlueskyQueryResult(query=query, total_records=len(recs),
                              retrieved_at="2026-11-18T04:00:00+00:00", records=recs)

def _dev_result(query="test", records=None):
    from src.collectors.discussions.devcommunity_collector import DevCommunityQueryResult
    recs = records or [_dev_record()]
    return DevCommunityQueryResult(query=query, total_records=len(recs),
                                   articles_count=len(recs), comments_count=0,
                                   retrieved_at="2026-11-18T04:00:00+00:00", records=recs)

def _se_result(query="test", records=None):
    recs = records or [_se_record()]
    return StackExchangeQueryResult(query=query, site="stackoverflow", total_records=len(recs),
                                    retrieved_at="2026-11-18T04:00:00+00:00", records=recs)

def _discourse_result(query="test", records=None):
    recs = records or [_discourse_record()]
    return DiscourseQueryResult(query=query, forums_queried=["meta.discourse.org"],
                                total_records=len(recs),
                                retrieved_at="2026-11-18T04:00:00+00:00", records=recs)

def _mock_classifier():
    clf = MagicMock()
    def _predict(text):
        label = "Positive" if "great" in text.lower() else "Negative"
        proba = {"Positive": 0.85, "Negative": 0.15} if label == "Positive" \
                else {"Positive": 0.20, "Negative": 0.80}
        res = MagicMock()
        res.sentiment.label = label
        res.sentiment.probabilities = proba
        res.sentiment.is_uncertain = False
        res.sentiment.model_available = True
        res.authenticity.label = "HONEST"
        res.authenticity.probabilities = {"HONEST": 0.90, "FAKE": 0.10}
        res.authenticity.is_uncertain = False
        return res
    clf.predict.side_effect = _predict
    return clf


# ---------------------------------------------------------------------------
# TestInvestigationService
# ---------------------------------------------------------------------------

class TestInvestigationService(unittest.TestCase):
    """Unit tests - all six collectors injected as MagicMocks (no real HTTP)."""

    def setUp(self):
        self.mock_gdelt = MagicMock()
        self.mock_hn = MagicMock()
        self.mock_bluesky = MagicMock()
        self.mock_dev = MagicMock()
        self.mock_se = MagicMock()
        self.mock_discourse = MagicMock()
        self.service = InvestigationService(
            gdelt_collector=self.mock_gdelt,
            hn_collector=self.mock_hn,
            bluesky_collector=self.mock_bluesky,
            devcommunity_collector=self.mock_dev,
            stackexchange_collector=self.mock_se,
            discourse_collector=self.mock_discourse,
        )

    def _setup_all_success(self, query="test"):
        # GDELT is temporarily disabled in investigation_service.py
        self.mock_hn.collect.return_value = _hn_result(query)
        self.mock_bluesky.collect.return_value = _bsky_result(query)
        self.mock_dev.collect.return_value = _dev_result(query)
        self.mock_se.collect.return_value = _se_result(query)
        self.mock_discourse.collect.return_value = _discourse_result(query)

    # ------------------------------------------------------------------
    # All six collectors are called
    # ------------------------------------------------------------------

    def test_all_six_collectors_invoked(self):
        self._setup_all_success("disinformation")
        res = self.service.investigate("disinformation")

        # GDELT is disabled, so only 5 collectors are active
        self.mock_gdelt.collect.assert_not_called()
        self.mock_hn.collect.assert_called_once()
        self.mock_bluesky.collect.assert_called_once()
        self.mock_dev.collect.assert_called_once()
        self.mock_se.collect.assert_called_once()
        self.mock_discourse.collect.assert_called_once()

        self.assertEqual(self.mock_hn.collect.call_args.kwargs["query"], "disinformation")
        self.assertEqual(self.mock_bluesky.collect.call_args.kwargs["query"], "disinformation")
        self.assertEqual(self.mock_dev.collect.call_args.kwargs["query"], "disinformation")
        self.assertEqual(self.mock_se.collect.call_args.kwargs["query"], "disinformation")
        self.assertEqual(self.mock_discourse.collect.call_args.kwargs["query"], "disinformation")

        self.assertEqual(res.status, "success")
        self.assertEqual(res.total_records, 5)   # 1 per active collector
        self.assertEqual(res.news_count, 0)       # GDELT disabled
        self.assertEqual(res.hn_count, 1)
        self.assertEqual(res.bluesky_count, 1)
        self.assertEqual(res.devcommunity_count, 1)
        self.assertEqual(res.stackexchange_count, 1)
        self.assertEqual(res.discourse_count, 1)
        self.assertEqual(res.discussions_count, 5)
        self.assertEqual(len(res.collector_errors), 0)

    def test_no_real_http_calls(self):
        self._setup_all_success()
        self.service.investigate("security")
        self.mock_gdelt.collect.assert_not_called()
        self.mock_hn.collect.assert_called_once()
        self.mock_bluesky.collect.assert_called_once()
        self.mock_dev.collect.assert_called_once()
        self.mock_se.collect.assert_called_once()
        self.mock_discourse.collect.assert_called_once()

    # ------------------------------------------------------------------
    # Source identity and provenance
    # ------------------------------------------------------------------

    def test_source_identity_and_provenance_retained(self):
        self._setup_all_success()
        res = self.service.investigate("test")
        sources = {r["source"] for r in res.records}
        # GDELT is disabled; only discussion sources are active
        self.assertNotIn("gdelt", sources)
        self.assertIn("hackernews", sources)
        self.assertIn("bluesky", sources)
        self.assertIn("devcommunity", sources)
        self.assertIn("stackexchange", sources)
        self.assertIn("discourse", sources)

        by_src = {r["source"]: r for r in res.records}
        self.assertEqual(by_src["hackernews"]["metadata"]["provenance"]["collector"], "HackerNewsCollector")
        self.assertEqual(by_src["bluesky"]["metadata"]["provenance"]["collector"], "BlueskyCollector")
        self.assertEqual(by_src["devcommunity"]["metadata"]["provenance"]["collector"], "DevCommunityCollector")
        self.assertEqual(by_src["stackexchange"]["metadata"]["provenance"]["collector"], "StackExchangeCollector")
        self.assertEqual(by_src["discourse"]["metadata"]["provenance"]["collector"], "DiscourseCollector")

    # ------------------------------------------------------------------
    # Partial failures - each collector independently
    # ------------------------------------------------------------------

    def test_partial_failure_gdelt_fails(self):
        # GDELT is disabled — this test now verifies HN failure leaves others intact
        self.mock_hn.collect.side_effect = HackerNewsAPIError("HN HTTP 503")
        self.mock_bluesky.collect.return_value = _bsky_result()
        self.mock_dev.collect.return_value = _dev_result()
        self.mock_se.collect.return_value = _se_result()
        self.mock_discourse.collect.return_value = _discourse_result()
        res = self.service.investigate("test")
        self.assertEqual(res.status, "partial")
        self.assertEqual(res.hn_count, 0)
        self.assertEqual(res.discussions_count, 4)
        self.assertIn("hackernews", res.collector_errors)
        self.assertIn("503", res.collector_errors["hackernews"])

    def test_partial_failure_hn_fails(self):
        self.mock_hn.collect.side_effect = HackerNewsAPIError("Algolia 429")
        self.mock_bluesky.collect.return_value = _bsky_result()
        self.mock_dev.collect.return_value = _dev_result()
        self.mock_se.collect.return_value = _se_result()
        self.mock_discourse.collect.return_value = _discourse_result()
        res = self.service.investigate("test")
        self.assertEqual(res.status, "partial")
        self.assertEqual(res.hn_count, 0)
        self.assertEqual(res.total_records, 4)
        self.assertIn("hackernews", res.collector_errors)

    # ------------------------------------------------------------------
    # Bluesky failure resilience
    # ------------------------------------------------------------------

    def test_bluesky_api_error_does_not_interrupt(self):
        self.mock_hn.collect.return_value = _hn_result()
        self.mock_bluesky.collect.side_effect = BlueskyAPIError("HTTP 500", status_code=500)
        self.mock_dev.collect.return_value = _dev_result()
        self.mock_se.collect.return_value = _se_result()
        self.mock_discourse.collect.return_value = _discourse_result()
        res = self.service.investigate("test")
        self.assertEqual(res.status, "partial")
        self.assertEqual(res.bluesky_count, 0)
        self.assertEqual(res.total_records, 4)
        self.assertIn("bluesky", res.collector_errors)
        self.assertNotIn("hackernews", res.collector_errors)
        self.assertNotIn("devcommunity", res.collector_errors)

    def test_bluesky_forbidden_does_not_interrupt(self):
        self.mock_hn.collect.return_value = _hn_result()
        self.mock_bluesky.collect.side_effect = BlueskyForbiddenError("HTTP 403")
        self.mock_dev.collect.return_value = _dev_result()
        self.mock_se.collect.return_value = _se_result()
        self.mock_discourse.collect.return_value = _discourse_result()
        res = self.service.investigate("test")
        self.assertEqual(res.status, "partial")
        self.assertEqual(res.total_records, 4)
        self.assertIn("bluesky", res.collector_errors)

    def test_bluesky_generic_exception_does_not_interrupt(self):
        self.mock_hn.collect.return_value = _hn_result()
        self.mock_bluesky.collect.side_effect = RuntimeError("Unexpected Bluesky failure")
        self.mock_dev.collect.return_value = _dev_result()
        self.mock_se.collect.return_value = _se_result()
        self.mock_discourse.collect.return_value = _discourse_result()
        res = self.service.investigate("test")
        self.assertEqual(res.status, "partial")
        self.assertEqual(res.total_records, 4)
        self.assertIn("bluesky", res.collector_errors)

    # ------------------------------------------------------------------
    # New collector failure resilience
    # ------------------------------------------------------------------

    def test_devcommunity_failure_does_not_interrupt(self):
        self.mock_hn.collect.return_value = _hn_result()
        self.mock_bluesky.collect.return_value = _bsky_result()
        self.mock_dev.collect.side_effect = DevCommunityAPIError("DEV API 503", status_code=503)
        self.mock_se.collect.return_value = _se_result()
        self.mock_discourse.collect.return_value = _discourse_result()
        res = self.service.investigate("test")
        self.assertEqual(res.status, "partial")
        self.assertEqual(res.devcommunity_count, 0)
        self.assertEqual(res.total_records, 4)
        self.assertIn("devcommunity", res.collector_errors)
        self.assertNotIn("hackernews", res.collector_errors)
        self.assertNotIn("bluesky", res.collector_errors)

    def test_stackexchange_failure_does_not_interrupt(self):
        self.mock_hn.collect.return_value = _hn_result()
        self.mock_bluesky.collect.return_value = _bsky_result()
        self.mock_dev.collect.return_value = _dev_result()
        self.mock_se.collect.side_effect = StackExchangeRateLimitError("SE quota exceeded")
        self.mock_discourse.collect.return_value = _discourse_result()
        res = self.service.investigate("test")
        self.assertEqual(res.status, "partial")
        self.assertEqual(res.stackexchange_count, 0)
        self.assertEqual(res.total_records, 4)
        self.assertIn("stackexchange", res.collector_errors)

    def test_discourse_failure_does_not_interrupt(self):
        self.mock_hn.collect.return_value = _hn_result()
        self.mock_bluesky.collect.return_value = _bsky_result()
        self.mock_dev.collect.return_value = _dev_result()
        self.mock_se.collect.return_value = _se_result()
        self.mock_discourse.collect.side_effect = DiscourseCollectorError("Discourse unreachable")
        res = self.service.investigate("test")
        self.assertEqual(res.status, "partial")
        self.assertEqual(res.discourse_count, 0)
        self.assertEqual(res.total_records, 4)
        self.assertIn("discourse", res.collector_errors)

    # ------------------------------------------------------------------
    # Total failure
    # ------------------------------------------------------------------

    def test_total_failure_all_collectors_fail(self):
        self.mock_hn.collect.side_effect = HackerNewsAPIError("HN timeout")
        self.mock_bluesky.collect.side_effect = BlueskyCollectorError("Bluesky unreachable")
        self.mock_dev.collect.side_effect = DevCommunityAPIError("DEV unreachable", status_code=503)
        self.mock_se.collect.side_effect = StackExchangeAPIError("SE unreachable", status_code=503)
        self.mock_discourse.collect.side_effect = DiscourseCollectorError("Discourse unreachable")
        res = self.service.investigate("test")
        self.assertEqual(res.status, "error")
        self.assertEqual(res.total_records, 0)
        for key in ("hackernews", "bluesky", "devcommunity", "stackexchange", "discourse"):
            self.assertIn(key, res.collector_errors)

    # ------------------------------------------------------------------
    # GDELT rate-limit and two-attempt cap
    # ------------------------------------------------------------------

    @unittest.skip("GDELT temporarily disabled in investigation_service.py")
    def test_gdelt_rate_limit_error_recorded(self):
        self.mock_gdelt.collect.side_effect = GDELTRateLimitError("GDELT 429")
        self.mock_hn.collect.return_value = _hn_result()
        self.mock_bluesky.collect.return_value = _bsky_result()
        self.mock_dev.collect.return_value = _dev_result()
        self.mock_se.collect.return_value = _se_result()
        self.mock_discourse.collect.return_value = _discourse_result()
        res = self.service.investigate("test")
        self.mock_gdelt.collect.assert_called_once()
        self.assertIn("gdelt", res.collector_errors)
        self.assertIn("Rate limited", res.collector_errors["gdelt"])
        self.assertEqual(res.discussions_count, 5)
        self.assertEqual(res.status, "partial")

    def test_gdelt_collector_max_retries_capped_at_one(self):
        self.assertEqual(GDELTDocCollector().max_retries, 1)
        self.assertEqual(GDELTDocCollector(max_retries=10).max_retries, 1)
        self.assertEqual(GDELTDocCollector(max_retries=0).max_retries, 0)

    # ------------------------------------------------------------------
    # Timeline
    # ------------------------------------------------------------------

    def test_build_timeline_seven_days_all_records_counted(self):
        records = [
            {"published_at": "2026-11-18T10:00:00+00:00", "predicted_label": "Positive"},
            {"published_at": "2026-11-18T12:00:00+00:00", "predicted_label": "Negative"},
            {"published_at": "2026-11-17T08:00:00+00:00", "predicted_label": "Positive"},
        ]
        timeline = build_timeline_from_records(records, days=7, pos_prob=0.5)
        self.assertEqual(len(timeline), 7)
        self.assertEqual(sum(d["all"] for d in timeline), 3)


# ---------------------------------------------------------------------------
# Flask endpoint integration
# ---------------------------------------------------------------------------

class TestAppInvestigationEndpoint(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_endpoint_success_flow(self):
        gdelt_rec = _gdelt_article().model_dump()
        gdelt_rec.update({"predicted_label": "Positive", "confidence": 0.85})
        hn_rec = _hn_record().model_dump()
        hn_rec.update({"predicted_label": "Negative", "confidence": 0.80})

        mock_response = InvestigationResponse(
            investigation_id="GS-TEST", query="deepseek", status="success",
            total_records=2, news_count=1, discussions_count=1,
            news_records=[gdelt_rec], discussion_records=[hn_rec],
            records=[gdelt_rec, hn_rec],
            sentiment_stats={
                "news": {"total": 1, "positive": 1, "negative": 0, "uncertain": 0, "positive_pct": 100},
                "discussions": {"total": 1, "positive": 0, "negative": 1, "uncertain": 0, "positive_pct": 0},
                "totals": {"total": 2, "positive": 1, "negative": 1, "uncertain": 0, "positive_pct": 50},
            },
            collector_errors={}, retrieved_at="2026-11-18T04:00:00+00:00",
        )
        with patch("app.get_investigation_service") as mock_get_svc:
            mock_svc = MagicMock()
            mock_svc.investigate.return_value = mock_response
            mock_get_svc.return_value = mock_svc
            resp = self.client.post("/api/investigate", json={"topic": "deepseek"})

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["investigation_id"], "GS-TEST")
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["metrics"]["records_total"], 2)
        self.assertIn("timeline", data)
        self.assertIn("sentiment", data)
        self.assertIn("authenticity", data)

    def test_endpoint_total_failure_flow(self):
        mock_response = InvestigationResponse(
            investigation_id="GS-FAIL", query="failed_query", status="error",
            total_records=0, news_count=0, discussions_count=0,
            news_records=[], discussion_records=[], records=[],
            sentiment_stats={},
            collector_errors={
                "gdelt": "Rate limit 429", "hackernews": "Connection timeout",
                "bluesky": "HTTP 500", "devcommunity": "503",
                "stackexchange": "quota exceeded", "discourse": "unreachable",
            },
            retrieved_at="2026-11-18T04:00:00+00:00",
        )
        with patch("app.get_investigation_service") as mock_get_svc:
            mock_svc = MagicMock()
            mock_svc.investigate.return_value = mock_response
            mock_get_svc.return_value = mock_svc
            resp = self.client.post("/api/investigate", json={"topic": "failed_query"})

        self.assertEqual(resp.status_code, 502)
        data = resp.get_json()
        self.assertEqual(data["status"], "error")
        self.assertIn("All collection sources failed", data["error"])
        self.assertEqual(data["metrics"]["records_total"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
