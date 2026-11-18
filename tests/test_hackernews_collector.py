"""
GhostSignal — Tests for Hacker News Discussion Collector
=========================================================
Offline unit tests using mocked API responses covering:
- Stories collection & normalization
- Comments collection & normalization
- Missing fields & no fabrication
- Deduplication by Algolia objectID
- Multi-page pagination
- Timeouts, rate limits, and error handling
- Schema compatibility with GDELT collector

Optional live API integration test (skipped during standard test suite).

Run offline tests:
    python -m pytest tests/test_hackernews_collector.py -v

Run optional live integration test:
    $env:RUN_HN_LIVE_TEST="1"
    python -m pytest tests/test_hackernews_collector.py -v -k "test_live_hackernews_api"
    # or standalone:
    python tests/test_hackernews_collector.py --live
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collectors.discussions.hackernews_collector import (
    HackerNewsAPIError,
    HackerNewsCollector,
    HackerNewsCollectorError,
    HackerNewsQueryResult,
    HackerNewsRateLimitError,
    HackerNewsRecord,
    HackerNewsTimeoutError,
    clean_html_text,
    parse_hn_datetime,
    parse_timespan_to_epoch,
    to_epoch_seconds,
)
from src.collectors.news.gdelt_collector import GDELTArticle


# ===========================================================================
# 1. Utility & Normalization Unit Tests
# ===========================================================================
class TestHNUtilityFunctions(unittest.TestCase):
    """Test HTML cleaning, date parsing, and epoch conversion."""

    def test_clean_html_text(self):
        html_input = (
            "&gt; Quoted text from user.<p>Here is an <i>italic</i> word and "
            "<a href='https://example.com'>link</a>.</p><p>Second paragraph&#x27;s text.</p>"
        )
        cleaned = clean_html_text(html_input)
        self.assertNotIn("<p>", cleaned)
        self.assertNotIn("<i>", cleaned)
        self.assertNotIn("</a>", cleaned)
        self.assertIn("> Quoted text", cleaned)
        self.assertIn("Second paragraph's text.", cleaned)
        self.assertIn("\n\n", cleaned)

    def test_clean_html_empty_and_none(self):
        self.assertEqual(clean_html_text(""), "")
        self.assertEqual(clean_html_text(None), "")

    def test_parse_hn_datetime_iso(self):
        iso_str = "2026-11-18T00:00:47Z"
        parsed = parse_hn_datetime(iso_str, None)
        self.assertEqual(parsed, "2026-11-18T00:00:47+00:00")

    def test_parse_hn_datetime_from_epoch(self):
        epoch = 1794960047
        parsed = parse_hn_datetime(None, epoch)
        self.assertTrue(parsed.startswith("2026-11-18"))
        self.assertTrue("+00:00" in parsed or parsed.endswith("Z"))

    def test_parse_hn_datetime_invalid(self):
        self.assertIsNone(parse_hn_datetime("invalid-date", None))
        self.assertIsNone(parse_hn_datetime(None, None))

    def test_to_epoch_seconds(self):
        dt = datetime(2026, 11, 18, 0, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(to_epoch_seconds(dt), int(dt.timestamp()))
        self.assertEqual(to_epoch_seconds("2026-11-18"), int(dt.timestamp()))
        self.assertEqual(to_epoch_seconds(1700000000), 1700000000)

    def test_parse_timespan_to_epoch(self):
        epoch_24h = parse_timespan_to_epoch("24h")
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        self.assertTrue(now_epoch - 86400 - 5 <= epoch_24h <= now_epoch - 86400 + 5)


# ===========================================================================
# 2. Collector Parameter Building & Hit Normalization Unit Tests
# ===========================================================================
class TestHNCollectorNormalization(unittest.TestCase):
    """Test parameter building and conversion of Algolia hits into GHOSTSIGNAL records."""

    def setUp(self):
        self.collector = HackerNewsCollector(timeout=5.0, retry_backoff=0.01)

    def test_build_params_story(self):
        params = self.collector.build_params("deepseek", tag="story", hits_per_page=20)
        self.assertEqual(params["query"], "deepseek")
        self.assertEqual(params["tags"], "story")
        self.assertEqual(params["hitsPerPage"], 20)
        self.assertEqual(params["page"], 0)

    def test_build_params_comment_with_date_range(self):
        params = self.collector.build_params(
            "kernel",
            tag="comment",
            start_date="2026-11-01",
            end_date="2026-11-15",
            min_points=10,
        )
        self.assertEqual(params["tags"], "comment")
        self.assertIn("created_at_i>=", params["numericFilters"])
        self.assertIn("created_at_i<=", params["numericFilters"])
        self.assertIn("points>=10", params["numericFilters"])

    def test_normalize_story_hit(self):
        sample_hit = {
            "objectID": "400100",
            "title": "Quantum Computing Breakthrough",
            "url": "https://example.com/quantum",
            "author": "alice_researcher",
            "points": 142,
            "num_comments": 58,
            "created_at": "2026-11-17T14:30:00Z",
            "created_at_i": 1794925800,
            "_tags": ["story"],
        }
        rec = self.collector._normalize_story_hit(sample_hit, "quantum", "2026-11-18T00:00:00Z")

        self.assertIsNotNone(rec)
        self.assertEqual(rec.id, "hn_400100")
        self.assertEqual(rec.source, "hackernews")
        self.assertEqual(rec.title, "Quantum Computing Breakthrough")
        self.assertEqual(rec.content, "Quantum Computing Breakthrough")
        self.assertEqual(rec.url, "https://example.com/quantum")
        self.assertEqual(rec.author, "alice_researcher")
        self.assertEqual(rec.likes, 142)
        self.assertEqual(rec.comments, 58)
        self.assertEqual(rec.metadata["item_type"], "story")
        self.assertEqual(rec.metadata["hn_url"], "https://news.ycombinator.com/item?id=400100")
        self.assertEqual(rec.metadata["provenance"]["collector"], "HackerNewsCollector")
        self.assertEqual(rec.metadata["provenance"]["tag"], "story")

    def test_normalize_story_without_external_url(self):
        # Ask HN or self-post where url is null
        sample_hit = {
            "objectID": "400200",
            "title": "Ask HN: Best practices for agent evaluation?",
            "url": None,
            "story_text": "<p>I am building an automated evaluation suite.</p>",
            "author": "bob_builder",
            "points": 25,
            "num_comments": 12,
            "created_at": "2026-11-17T18:00:00Z",
        }
        rec = self.collector._normalize_story_hit(sample_hit, "evaluation", "2026-11-18T00:00:00Z")

        self.assertIsNotNone(rec)
        self.assertEqual(rec.url, "https://news.ycombinator.com/item?id=400200")
        self.assertEqual(rec.content, "I am building an automated evaluation suite.")

    def test_normalize_comment_hit(self):
        sample_hit = {
            "objectID": "400300",
            "comment_text": "<p>This is a <i>fascinating</i> claim, but lacks evidence.</p>",
            "author": "charlie_critic",
            "story_id": 400100,
            "parent_id": 400100,
            "story_title": "Quantum Computing Breakthrough",
            "story_url": "https://example.com/quantum",
            "created_at": "2026-11-17T15:00:00Z",
            "points": None,
        }
        rec = self.collector._normalize_comment_hit(sample_hit, "quantum", "2026-11-18T00:00:00Z")

        self.assertIsNotNone(rec)
        self.assertEqual(rec.id, "hn_400300")
        self.assertEqual(rec.source, "hackernews")
        self.assertEqual(rec.title, "Quantum Computing Breakthrough")
        self.assertEqual(rec.content, "This is a fascinating claim, but lacks evidence.")
        self.assertEqual(rec.url, "https://news.ycombinator.com/item?id=400300")
        self.assertEqual(rec.author, "charlie_critic")
        self.assertIsNone(rec.likes)
        self.assertIsNone(rec.comments)
        self.assertEqual(rec.metadata["item_type"], "comment")
        self.assertEqual(rec.metadata["story_title"], "Quantum Computing Breakthrough")
        self.assertEqual(rec.metadata["story_url"], "https://example.com/quantum")
        self.assertEqual(rec.metadata["parent_id"], 400100)


# ===========================================================================
# 3. Collector Behavior: Missing Fields, Deduplication, Pagination, Errors
# ===========================================================================
class TestHNCollectorPipeline(unittest.TestCase):
    """Test full collection logic: missing fields, deduplication, pagination, and resilience."""

    def setUp(self):
        self.collector = HackerNewsCollector(timeout=5.0, retry_backoff=0.01)

    def test_missing_fields_no_fabrication(self):
        """Verify unavailable fields remain null/None without fabrication."""
        sparse_story = {
            "objectID": "500001",
            "title": None,
            "url": None,
            "author": None,
            "points": None,
            "num_comments": None,
            "created_at": None,
            "created_at_i": None,
        }
        rec = self.collector._normalize_story_hit(sparse_story, "test", "2026-11-18T00:00:00Z")

        self.assertEqual(rec.content, "")
        self.assertIsNone(rec.title)
        self.assertEqual(rec.url, "https://news.ycombinator.com/item?id=500001")
        self.assertIsNone(rec.author)
        self.assertIsNone(rec.likes)
        self.assertIsNone(rec.comments)
        self.assertIsNone(rec.published_at)
        self.assertIsNone(rec.product)
        self.assertIsNone(rec.rating)
        self.assertIsNone(rec.shares)
        self.assertIsNone(rec.views)

    def test_deduplication_by_object_id(self):
        """Verify identical Algolia objectIDs are deduplicated across calls."""
        duplicate_hits = {
            "hits": [
                {"objectID": "999", "title": "First test instance", "created_at": "2026-11-17T00:00:00Z"},
                {"objectID": "999", "title": "Duplicate test instance", "created_at": "2026-11-17T00:00:00Z"},
                {"objectID": "1000", "title": "Distinct test story", "created_at": "2026-11-17T01:00:00Z"},
            ],
            "nbPages": 1,
        }

        with patch.object(self.collector, "_execute_request", return_value=duplicate_hits):
            result = self.collector.collect("test", limit=10, collect_stories=True, collect_comments=False)

        self.assertEqual(result.total_records, 2)
        ids = [r.id for r in result.records]
        self.assertEqual(ids, ["hn_999", "hn_1000"])

    def test_multi_page_pagination(self):
        """Verify that requests span across multiple pages until limit is reached."""
        page_0 = {
            "hits": [
                {"objectID": "101", "title": "Test story one"},
                {"objectID": "102", "title": "Test story two"},
            ],
            "page": 0,
            "nbPages": 2,
        }
        page_1 = {
            "hits": [
                {"objectID": "103", "title": "Test story three"},
                {"objectID": "104", "title": "Test story four"},
            ],
            "page": 1,
            "nbPages": 2,
        }

        with patch.object(self.collector, "_execute_request", side_effect=[page_0, page_1]):
            # Target limit 3 stories
            stories = self.collector.collect_stories("test", limit=3)

        self.assertEqual(len(stories), 3)
        self.assertEqual([s.id for s in stories], ["hn_101", "hn_102", "hn_103"])

    def test_empty_results_handling(self):
        """Verify zero results returns an empty container gracefully."""
        empty_resp = {"hits": [], "nbHits": 0, "page": 0, "nbPages": 0}
        with patch.object(self.collector, "_execute_request", return_value=empty_resp):
            result = self.collector.collect("nonexistent_term_987654321")

        self.assertEqual(result.total_records, 0)
        self.assertEqual(result.stories_count, 0)
        self.assertEqual(result.comments_count, 0)
        self.assertEqual(len(result.errors), 0)

    def test_timeout_and_network_error_graceful(self):
        """Verify errors are captured in result.errors when raise_on_error=False."""
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.exceptions.Timeout("Connection timed out")
        collector = HackerNewsCollector(session=mock_session, max_retries=1, retry_backoff=0.01)

        result = collector.collect("fail_query", raise_on_error=False)
        self.assertEqual(result.total_records, 0)
        self.assertTrue(len(result.errors) > 0)
        self.assertIn("timed out", result.errors[0])

    def test_timeout_error_raised(self):
        """Verify HackerNewsTimeoutError is raised when raise_on_error=True."""
        mock_session = MagicMock()
        mock_session.get.side_effect = requests.exceptions.Timeout("Connection timed out")
        collector = HackerNewsCollector(session=mock_session, max_retries=1, retry_backoff=0.01)

        with self.assertRaises(HackerNewsTimeoutError):
            collector.collect("fail_query", raise_on_error=True)

    def test_http_429_rate_limit(self):
        """Verify HTTP 429 raises HackerNewsRateLimitError on retry exhaustion."""
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "Rate limit exceeded"

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        collector = HackerNewsCollector(session=mock_session, max_retries=1, retry_backoff=0.01)

        with self.assertRaises(HackerNewsRateLimitError):
            collector._execute_request({"query": "test"})

    def test_schema_compatibility_with_gdelt(self):
        """Verify HackerNewsRecord has identical schema fields to GDELTArticle."""
        hn_fields = set(HackerNewsRecord.model_fields.keys())
        gdelt_fields = set(GDELTArticle.model_fields.keys())
        self.assertEqual(
            hn_fields,
            gdelt_fields,
            f"Schema mismatch between HN and GDELT! Difference: {hn_fields ^ gdelt_fields}"
        )


# ===========================================================================
# 4. Optional Live API Integration Test
# ===========================================================================
_RUN_LIVE = os.environ.get("RUN_HN_LIVE_TEST") == "1"


@pytest.mark.integration
@pytest.mark.skipif(not _RUN_LIVE, reason="Live API tests skipped by default. Set RUN_HN_LIVE_TEST=1 to run.")
def test_live_hackernews_api():
    """
    Live integration test querying Algolia Hacker News Search API.
    Only executed when RUN_HN_LIVE_TEST=1 is explicitly set.
    """
    collector = HackerNewsCollector(timeout=15.0)
    result = collector.collect(
        query="cybersecurity",
        limit=4,
        limit_stories=2,
        limit_comments=2,
        timespan="7d",
        raise_on_error=False,
    )

    print(f"\n[HN Live Test] Query: 'cybersecurity'")
    print(f"[HN Live Test] Total Records: {result.total_records}")
    print(f"[HN Live Test] Stories: {result.stories_count}, Comments: {result.comments_count}")

    assert result.total_records > 0
    first = result.records[0]
    assert first.source == "hackernews"
    assert first.id.startswith("hn_")
    assert first.content is not None
    assert "provenance" in first.metadata
    assert first.metadata["provenance"]["collector"] == "HackerNewsCollector"


if __name__ == "__main__":
    if "--live" in sys.argv:
        os.environ["RUN_HN_LIVE_TEST"] = "1"
        print("Running live Hacker News API integration test...")
        test_live_hackernews_api()
        print("Live test completed successfully.")
    else:
        print("Running offline unit tests for Hacker News collector...")
        suite = unittest.TestLoader().loadTestsFromTestCase(TestHNUtilityFunctions)
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestHNCollectorNormalization))
        suite.addTests(unittest.TestLoader().loadTestsFromTestCase(TestHNCollectorPipeline))
        runner = unittest.TextTestRunner(verbosity=2)
        res = runner.run(suite)
        if not res.wasSuccessful():
            sys.exit(1)
