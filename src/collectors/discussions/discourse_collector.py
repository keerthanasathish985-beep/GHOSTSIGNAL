"""
GhostSignal - Discourse Discussion Collector
============================================
Collects public topics and posts from Discourse-based communities
using the public Discourse JSON API.

Endpoints (per-forum):
    Latest:  https://<host>/latest.json?page=...
    Search:  https://<host>/search.json?q=...

Authentication:
    No API key required for public forums. Any forum that exposes
    public read access is supported.

Default forums:
    - meta.discourse.org  (Discourse Meta)
    - forum.arduino.cc    (Arduino)
    - discuss.elastic.co  (Elastic)
    - community.openai.com (OpenAI Community)
    - discuss.python.org  (Python)

Data Contract Compliance:
    Output adheres strictly to the shared GHOSTSIGNAL unified schema.
    Title, excerpt/body, author, timestamps, and engagement counts are
    extracted faithfully and never fabricated.
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import requests
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger("ghostsignal.collectors.discourse")
if not log.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s  %(levelname)-7s  %(name)s  %(message)s")
    )
    log.addHandler(handler)
    log.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class DiscourseCollectorError(Exception):
    """Base exception for Discourse collector errors."""


class DiscourseRateLimitError(DiscourseCollectorError):
    """Raised when a Discourse forum returns HTTP 429."""


class DiscourseTimeoutError(DiscourseCollectorError):
    """Raised when a request to a Discourse forum times out."""


class DiscourseAPIError(DiscourseCollectorError):
    """Raised when a Discourse forum returns an unexpected HTTP error."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


# ---------------------------------------------------------------------------
# Data Models (GHOSTSIGNAL Unified Schema)
# ---------------------------------------------------------------------------
class DiscourseRecord(BaseModel):
    """Normalized GHOSTSIGNAL record for a Discourse topic."""
    id: str = Field(..., description="Stable unique record identifier (e.g. discourse_meta_12345)")
    source: str = Field(default="discourse", description="Canonical source identifier")
    content: str = Field(..., description="Main text for analysis (title + excerpt)")
    title: Optional[str] = Field(default=None, description="Topic title")
    url: Optional[str] = Field(default=None, description="Permalink to the topic")
    published_at: Optional[str] = Field(default=None, description="ISO-8601 creation date in UTC")
    retrieved_at: str = Field(..., description="ISO-8601 UTC timestamp of retrieval")
    author: Optional[str] = Field(default=None, description="Username of the topic creator")
    product: Optional[str] = Field(default=None)
    rating: Optional[float] = Field(default=None)
    likes: Optional[int] = Field(default=None, description="Like count on the topic")
    comments: Optional[int] = Field(default=None, description="Reply count")
    shares: Optional[int] = Field(default=None)
    views: Optional[int] = Field(default=None, description="View count")
    label: Optional[str] = Field(default=None)
    predicted_label: Optional[str] = Field(default=None)
    confidence: Optional[float] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DiscourseQueryResult(BaseModel):
    """Container for Discourse collector execution output."""
    query: str
    forums_queried: List[str]
    total_records: int
    retrieved_at: str
    records: List[DiscourseRecord] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    def to_json(self, indent: Optional[int] = None) -> str:
        return self.model_dump_json(indent=indent)

    def to_records(self) -> List[Dict[str, Any]]:
        return [r.model_dump() for r in self.records]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def _strip_html(text: Optional[str]) -> str:
    """Remove HTML markup and unescape entities."""
    if not text or not isinstance(text, str):
        return ""
    s = re.sub(r"(?i)<\s*p\s*/?>", "\n\n", text)
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _parse_discourse_datetime(dt_str: Optional[str]) -> Optional[str]:
    """Parse a Discourse API timestamp to an ISO-8601 UTC string."""
    if not dt_str or not isinstance(dt_str, str):
        return None
    s = dt_str.strip()
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Discourse Collector
# ---------------------------------------------------------------------------

DEFAULT_FORUMS = [
    "meta.discourse.org",
    "discuss.python.org",
]


class DiscourseCollector:
    """
    Collector targeting one or more public Discourse forums.

    Features:
    - Searches topics via /search.json on each configured forum.
    - Falls back to /latest.json if search returns empty results.
    - Collects title, excerpt, author, timestamps, likes, reply counts, views.
    - Per-forum failure isolation: one unavailable forum does not block others.
    - Deduplication by topic ID per forum.
    - Full GHOSTSIGNAL schema compatibility.
    - Never fabricates content, timestamps, or engagement counts.
    """

    DEFAULT_USER_AGENT = "GhostSignal-DiscourseCollector/1.0 (+https://ghostsignal.local)"

    def __init__(
        self,
        forums: Optional[List[str]] = None,
        timeout: float = 15.0,
        max_retries: int = 2,
        retry_backoff: float = 2.0,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Optional[requests.Session] = None,
    ):
        self.forums = [f.rstrip("/") for f in (forums or DEFAULT_FORUMS)]
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.user_agent = user_agent
        self.session = session or requests.Session()

    def _request(self, url: str, params: Dict[str, Any]) -> Any:
        """Execute GET request with retry and status-code exception mapping."""
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }
        attempts = 0
        while attempts <= self.max_retries:
            attempts += 1
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.exceptions.Timeout as exc:
                if attempts > self.max_retries:
                    raise DiscourseTimeoutError(
                        f"Discourse forum timed out after {self.timeout}s: {url}"
                    ) from exc
                time.sleep(self.retry_backoff * attempts)
                continue
            except requests.exceptions.RequestException as exc:
                if attempts > self.max_retries:
                    raise DiscourseCollectorError(
                        f"Network error querying Discourse forum {url}: {exc}"
                    ) from exc
                time.sleep(self.retry_backoff * attempts)
                continue

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", self.retry_backoff * attempts * 2))
                if attempts > self.max_retries:
                    raise DiscourseRateLimitError(
                        f"Discourse forum rate limited (HTTP 429): {url}"
                    )
                time.sleep(retry_after)
                continue

            # Discourse forums that require login return 403 or redirect to login page
            if resp.status_code in (401, 403):
                raise DiscourseAPIError(
                    f"Discourse forum requires authentication (HTTP {resp.status_code}): {url}",
                    status_code=resp.status_code,
                    response_text=resp.text[:200],
                )

            if resp.status_code != 200:
                raise DiscourseAPIError(
                    f"Discourse forum returned HTTP {resp.status_code}: {url}",
                    status_code=resp.status_code,
                    response_text=resp.text[:500],
                )

            body = resp.text.strip()
            if not body:
                return {}

            try:
                data = resp.json()
                return data
            except (json.JSONDecodeError, ValueError) as exc:
                raise DiscourseAPIError(
                    f"Malformed JSON from Discourse forum {url}: {exc}",
                    status_code=resp.status_code,
                    response_text=body[:500],
                ) from exc
        raise DiscourseCollectorError(f"Exhausted retries querying Discourse forum: {url}")

    def _search_forum(self, host: str, query: str, limit: int) -> List[Dict[str, Any]]:
        """Search for topics on a single Discourse forum. Returns only results that
        contain at least one query word in the title or excerpt — never falls back
        to unrelated latest topics."""
        url = f"https://{host}/search.json"
        try:
            data = self._request(url, {"q": query})
            topics = data.get("topics") or []
            if isinstance(topics, list) and topics:
                # Filter: every returned topic must contain at least one query token
                q_tokens = [t.lower() for t in query.split() if len(t) >= 3]
                if q_tokens:
                    def _relevant(t: Dict[str, Any]) -> bool:
                        haystack = (
                            (t.get("title") or "") + " " +
                            (t.get("excerpt") or "") + " " +
                            (t.get("blurb") or "")
                        ).lower()
                        return any(tok in haystack for tok in q_tokens)
                    topics = [t for t in topics if _relevant(t)]
                return topics[:limit]
        except Exception as exc:
            log.debug("Search on %s failed (%s), no fallback to latest", host, exc)

        # No fallback to /latest.json — returning unrelated recent topics would
        # pollute the investigation with irrelevant records.
        return []

    def _normalize_topic(self, topic: Dict[str, Any], host: str, query: str, retrieved_at_iso: str) -> Optional[DiscourseRecord]:
        """Transform a raw Discourse topic dict into a normalized GHOSTSIGNAL record."""
        topic_id = topic.get("id")
        if not topic_id:
            return None

        # Build a slug-friendly forum label (strip dots and hyphens)
        forum_label = re.sub(r"[^a-z0-9]", "_", host.split(".")[0].lower())
        record_id = f"discourse_{forum_label}_{topic_id}"

        title = (topic.get("title") or "").strip() or None
        slug = topic.get("slug") or str(topic_id)
        url = f"https://{host}/t/{slug}/{topic_id}"

        # Excerpt comes from search results; fall back to title
        excerpt_raw = topic.get("excerpt") or topic.get("blurb") or ""
        excerpt = _strip_html(excerpt_raw).strip()
        content = excerpt if excerpt else (title or "")

        published_at = _parse_discourse_datetime(topic.get("created_at"))

        # Author: last_poster_username from latest.json, or poster from search results
        author = (
            topic.get("last_poster_username")
            or topic.get("posters", [{}])[0].get("user", {}).get("username")
            if topic.get("posters")
            else topic.get("last_poster_username")
        ) or None

        likes = topic.get("like_count")
        replies = topic.get("posts_count")
        views = topic.get("views")

        likes_val = int(likes) if isinstance(likes, (int, float)) else None
        replies_val = int(replies) if isinstance(replies, (int, float)) else None
        views_val = int(views) if isinstance(views, (int, float)) else None

        tags = topic.get("tags") or []

        metadata: Dict[str, Any] = {
            "item_type": "topic",
            "topic_id": topic_id,
            "forum_host": host,
            "slug": slug,
            "category_id": topic.get("category_id"),
            "tags": tags,
            "has_accepted_answer": topic.get("has_accepted_answer"),
            "reply_count": topic.get("reply_count"),
            "highest_post_number": topic.get("highest_post_number"),
            "last_poster_username": topic.get("last_poster_username"),
            "pinned": topic.get("pinned"),
            "provenance": {
                "collector": "DiscourseCollector",
                "api_endpoint": f"https://{host}/search.json",
                "forum_host": host,
                "query": query,
                "retrieved_at": retrieved_at_iso,
                "topic_id": topic_id,
            },
        }

        return DiscourseRecord(
            id=record_id,
            source="discourse",
            content=content,
            title=title,
            url=url,
            published_at=published_at,
            retrieved_at=retrieved_at_iso,
            author=author,
            product=None,
            rating=None,
            likes=likes_val,
            comments=replies_val,
            shares=None,
            views=views_val,
            label=None,
            predicted_label=None,
            confidence=None,
            metadata=metadata,
        )

    def collect(
        self,
        query: str,
        limit_per_forum: int = 15,
        deduplicate: bool = True,
        raise_on_error: bool = False,
    ) -> DiscourseQueryResult:
        """
        Execute an investigation search query across all configured Discourse forums.

        Only topics whose title or excerpt contain at least one token from the query
        are included. Forums that return no matching topics contribute zero records
        rather than unrelated content.
        """
        retrieved_at_iso = datetime.now(timezone.utc).isoformat()
        all_records: List[DiscourseRecord] = []
        all_errors: List[str] = []
        seen_ids: Set[str] = set()
        forums_queried: List[str] = []

        for host in self.forums:
            try:
                raw_topics = self._search_forum(host, query, limit_per_forum)
                forums_queried.append(host)
                added = 0
                for topic in raw_topics:
                    rec = self._normalize_topic(topic, host, query, retrieved_at_iso)
                    if rec is None:
                        continue
                    if deduplicate and rec.id in seen_ids:
                        continue
                    all_records.append(rec)
                    seen_ids.add(rec.id)
                    added += 1
                log.info(
                    "Discourse forum '%s': %d relevant topics for '%s'",
                    host, added, query,
                )
            except Exception as exc:
                msg = f"{host}: {exc}"
                log.warning("Discourse collection failed for forum '%s': %s", host, exc)
                all_errors.append(msg)
                if raise_on_error:
                    raise DiscourseCollectorError(msg) from exc

        log.info(
            "Discourse collection finished for '%s': %d total records across %d forum(s)",
            query, len(all_records), len(forums_queried),
        )

        return DiscourseQueryResult(
            query=query,
            forums_queried=forums_queried,
            total_records=len(all_records),
            retrieved_at=retrieved_at_iso,
            records=all_records,
            errors=all_errors,
        )
