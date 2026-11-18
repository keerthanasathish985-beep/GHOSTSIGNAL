"""
GhostSignal - DEV Community Discussion Collector
=================================================
Collects public articles and comments from the DEV Community (dev.to)
using the public Forem API v1.

Endpoints:
    Articles:  https://dev.to/api/articles?tag=...&per_page=...
    Search:    https://dev.to/api/articles/search?q=...
    Comments:  https://dev.to/api/comments?a_id=<article_id>

Authentication:
    No API key required for public read endpoints.

Data Contract Compliance:
    Output adheres strictly to the shared GHOSTSIGNAL unified schema
    (Section 6 of Project Brief). Title, description, body_markdown,
    author, timestamps, reactions, and comments are extracted faithfully
    from API responses and never fabricated.
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
log = logging.getLogger("ghostsignal.collectors.devcommunity")
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
class DevCommunityCollectorError(Exception):
    """Base exception for DEV Community collector errors."""


class DevCommunityRateLimitError(DevCommunityCollectorError):
    """Raised when DEV API returns HTTP 429."""


class DevCommunityTimeoutError(DevCommunityCollectorError):
    """Raised when a request to the DEV API times out."""


class DevCommunityAPIError(DevCommunityCollectorError):
    """Raised when DEV API returns an unexpected HTTP error or malformed payload."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


# ---------------------------------------------------------------------------
# Data Models (GHOSTSIGNAL Unified Schema)
# ---------------------------------------------------------------------------
class DevCommunityRecord(BaseModel):
    """Normalized GHOSTSIGNAL record for a DEV Community article or comment."""
    id: str = Field(..., description="Stable unique record identifier (e.g. dev_123456)")
    source: str = Field(default="devcommunity", description="Canonical source identifier")
    content: str = Field(..., description="Main text for analysis (description or body excerpt)")
    title: Optional[str] = Field(default=None, description="Article title")
    url: Optional[str] = Field(default=None, description="Canonical URL to the article on dev.to")
    published_at: Optional[str] = Field(default=None, description="ISO-8601 publication date in UTC")
    retrieved_at: str = Field(..., description="ISO-8601 UTC timestamp of retrieval")
    author: Optional[str] = Field(default=None, description="Author username on dev.to")
    product: Optional[str] = Field(default=None)
    rating: Optional[float] = Field(default=None)
    likes: Optional[int] = Field(default=None, description="Public reactions count")
    comments: Optional[int] = Field(default=None, description="Comments count")
    shares: Optional[int] = Field(default=None)
    views: Optional[int] = Field(default=None, description="Page views if available")
    label: Optional[str] = Field(default=None)
    predicted_label: Optional[str] = Field(default=None)
    confidence: Optional[float] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class DevCommunityQueryResult(BaseModel):
    """Container for DEV Community collector execution output."""
    query: str
    total_records: int
    articles_count: int
    comments_count: int
    retrieved_at: str
    records: List[DevCommunityRecord] = Field(default_factory=list)
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
    """Remove HTML tags and unescape entities from text."""
    if not text or not isinstance(text, str):
        return ""
    s = re.sub(r"(?i)<\s*p\s*/?>", "\n\n", text)
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _parse_dev_datetime(dt_str: Optional[str]) -> Optional[str]:
    """Parse a DEV API timestamp into an ISO-8601 UTC string."""
    if not dt_str or not isinstance(dt_str, str):
        return None
    s = dt_str.strip()
    if not s:
        return None
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
# DEV Community Collector
# ---------------------------------------------------------------------------
class DevCommunityCollector:
    """
    Collector targeting the public DEV Community (dev.to) Forem API v1.

    Features:
    - Searches articles by query term and optional tag filter.
    - Optionally fetches comments for retrieved articles.
    - Full GHOSTSIGNAL schema compatibility.
    - Deduplication by article/comment ID.
    - Resilient failure handling: HTTP 429, timeouts, network errors.
    - Never fabricates content, timestamps, or engagement counts.
    """

    SEARCH_ENDPOINT = "https://dev.to/api/articles/search"
    ARTICLES_ENDPOINT = "https://dev.to/api/articles"
    COMMENTS_ENDPOINT = "https://dev.to/api/comments"
    DEFAULT_USER_AGENT = "GhostSignal-DevCommunityCollector/1.0 (+https://ghostsignal.local)"

    def __init__(
        self,
        timeout: float = 15.0,
        max_retries: int = 2,
        retry_backoff: float = 2.0,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Optional[requests.Session] = None,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.user_agent = user_agent
        self.session = session or requests.Session()

    def _request(self, url: str, params: Dict[str, Any]) -> Any:
        """Execute GET request with retry logic and status-code exception mapping."""
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        attempts = 0
        while attempts <= self.max_retries:
            attempts += 1
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except requests.exceptions.Timeout as exc:
                if attempts > self.max_retries:
                    raise DevCommunityTimeoutError(
                        f"DEV API timed out after {self.timeout}s"
                    ) from exc
                time.sleep(self.retry_backoff * attempts)
                continue
            except requests.exceptions.RequestException as exc:
                if attempts > self.max_retries:
                    raise DevCommunityCollectorError(
                        f"Network error querying DEV API: {exc}"
                    ) from exc
                time.sleep(self.retry_backoff * attempts)
                continue

            if resp.status_code == 429:
                if attempts > self.max_retries:
                    raise DevCommunityRateLimitError(
                        f"DEV API rate limited (HTTP 429): {resp.text[:200]}"
                    )
                time.sleep(self.retry_backoff * attempts * 2)
                continue

            if resp.status_code != 200:
                raise DevCommunityAPIError(
                    f"DEV API returned HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    response_text=resp.text[:500],
                )

            body = resp.text.strip()
            if not body:
                return []
            try:
                data = resp.json()
                return data
            except (json.JSONDecodeError, ValueError) as exc:
                raise DevCommunityAPIError(
                    f"Malformed JSON from DEV API: {exc}",
                    status_code=resp.status_code,
                    response_text=body[:500],
                ) from exc
        raise DevCommunityCollectorError("Exhausted retries querying DEV API.")

    def _normalize_article(self, article: Dict[str, Any], query: str, retrieved_at_iso: str) -> Optional[DevCommunityRecord]:
        """Transform a raw Forem article dict into a normalized GHOSTSIGNAL record."""
        raw_id = article.get("id")
        if not raw_id:
            return None

        record_id = f"dev_{raw_id}"

        title = (article.get("title") or "").strip() or None

        # Prefer description, then body_markdown excerpt (first 500 chars)
        description = article.get("description") or ""
        body_md = article.get("body_markdown") or ""
        content_raw = description.strip() if description.strip() else body_md.strip()[:500]
        content = _strip_html(content_raw) if content_raw else (title or "")

        url = article.get("url") or article.get("canonical_url") or None
        published_at = _parse_dev_datetime(article.get("published_at"))

        user = article.get("user") or {}
        author = user.get("username") or user.get("name") or None

        reactions = article.get("public_reactions_count")
        comments_count = article.get("comments_count")
        page_views = article.get("page_views_count")

        likes_val = int(reactions) if isinstance(reactions, (int, float)) else None
        comments_val = int(comments_count) if isinstance(comments_count, (int, float)) else None
        views_val = int(page_views) if isinstance(page_views, (int, float)) else None

        tags = article.get("tag_list") or article.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        metadata: Dict[str, Any] = {
            "item_type": "article",
            "article_id": raw_id,
            "slug": article.get("slug"),
            "tags": tags,
            "reading_time_minutes": article.get("reading_time_minutes"),
            "cover_image": article.get("cover_image"),
            "canonical_url": article.get("canonical_url"),
            "provenance": {
                "collector": "DevCommunityCollector",
                "api_endpoint": self.SEARCH_ENDPOINT,
                "query": query,
                "retrieved_at": retrieved_at_iso,
                "article_id": raw_id,
            },
        }

        return DevCommunityRecord(
            id=record_id,
            source="devcommunity",
            content=content,
            title=title,
            url=url,
            published_at=published_at,
            retrieved_at=retrieved_at_iso,
            author=author,
            product=None,
            rating=None,
            likes=likes_val,
            comments=comments_val,
            shares=None,
            views=views_val,
            label=None,
            predicted_label=None,
            confidence=None,
            metadata=metadata,
        )

    def _normalize_comment(self, comment: Dict[str, Any], article_id: Any, article_url: Optional[str], query: str, retrieved_at_iso: str) -> Optional[DevCommunityRecord]:
        """Transform a raw Forem comment dict into a normalized GHOSTSIGNAL record."""
        raw_id = comment.get("id_code") or comment.get("id")
        if not raw_id:
            return None

        record_id = f"dev_c{raw_id}"
        body_html = comment.get("body_html") or ""
        content = _strip_html(body_html).strip()
        if not content:
            return None

        user = comment.get("user") or {}
        author = user.get("username") or user.get("name") or None
        published_at = _parse_dev_datetime(comment.get("created_at"))

        comment_url = f"https://dev.to/comments/{raw_id}" if raw_id else article_url

        metadata: Dict[str, Any] = {
            "item_type": "comment",
            "comment_id": raw_id,
            "article_id": article_id,
            "article_url": article_url,
            "children_count": len(comment.get("children") or []),
            "provenance": {
                "collector": "DevCommunityCollector",
                "api_endpoint": self.COMMENTS_ENDPOINT,
                "query": query,
                "retrieved_at": retrieved_at_iso,
                "article_id": article_id,
                "comment_id": raw_id,
            },
        }

        return DevCommunityRecord(
            id=record_id,
            source="devcommunity",
            content=content,
            title=None,
            url=comment_url,
            published_at=published_at,
            retrieved_at=retrieved_at_iso,
            author=author,
            product=None,
            rating=None,
            likes=None,
            comments=None,
            shares=None,
            views=None,
            label=None,
            predicted_label=None,
            confidence=None,
            metadata=metadata,
        )

    def _fetch_articles(self, query: str, limit: int, retrieved_at_iso: str) -> List[Dict[str, Any]]:
        """Fetch articles matching query via the Forem search endpoint, then
        post-filter so every returned article contains at least one query token
        in its title or description."""
        per_page = min(limit * 2, 30)   # fetch extra so post-filter leaves enough
        params: Dict[str, Any] = {"q": query.strip(), "per_page": per_page}
        raw = self._request(self.SEARCH_ENDPOINT, params)
        if not isinstance(raw, list):
            raw = (raw or {}).get("articles") or []

        # Relevance post-filter: at least one meaningful query token must appear
        q_tokens = [t.lower() for t in query.split() if len(t) >= 3]
        if q_tokens:
            def _relevant(a: Dict[str, Any]) -> bool:
                # tag_list from DEV API is a list; join it to a string safely
                tag_list = a.get("tag_list") or []
                tags_str = " ".join(tag_list) if isinstance(tag_list, list) else str(tag_list or "")
                haystack = (
                    (a.get("title") or "") + " " +
                    (a.get("description") or "") + " " +
                    tags_str
                ).lower()
                return any(tok in haystack for tok in q_tokens)
            raw = [a for a in raw if _relevant(a)]

        return raw[:limit]

    def _fetch_comments_for_article(self, article_id: Any) -> List[Dict[str, Any]]:
        """Fetch top-level comments for a given article ID."""
        try:
            raw = self._request(self.COMMENTS_ENDPOINT, {"a_id": article_id})
            if isinstance(raw, list):
                return raw
        except Exception as exc:
            log.debug("Could not fetch comments for article %s: %s", article_id, exc)
        return []

    def collect(
        self,
        query: str,
        limit: int = 30,
        fetch_comments: bool = False,
        comments_per_article: int = 3,
        deduplicate: bool = True,
        raise_on_error: bool = False,
    ) -> DevCommunityQueryResult:
        """
        Execute an investigation search query against the DEV Community.

        Comments are disabled by default because the DEV comments API returns
        all comments on a matched article regardless of topic relevance, which
        introduces unrelated content. Set fetch_comments=True only when you
        need comment-level signals and are prepared to accept lower precision.
        """
        retrieved_at_iso = datetime.now(timezone.utc).isoformat()
        errors: List[str] = []
        records: List[DevCommunityRecord] = []
        seen_ids: Set[str] = set()
        articles_count = 0
        comments_count = 0

        try:
            raw_articles = self._fetch_articles(query, limit, retrieved_at_iso)
        except Exception as exc:
            log.warning("DEV Community article fetch failed for '%s': %s", query, exc)
            if raise_on_error:
                raise
            errors.append(str(exc))
            return DevCommunityQueryResult(
                query=query, total_records=0, articles_count=0, comments_count=0,
                retrieved_at=retrieved_at_iso, records=[], errors=errors,
            )

        for article in raw_articles:
            rec = self._normalize_article(article, query, retrieved_at_iso)
            if rec is None:
                continue
            if deduplicate and rec.id in seen_ids:
                continue
            records.append(rec)
            seen_ids.add(rec.id)
            articles_count += 1

            if fetch_comments and comments_per_article > 0:
                raw_comments = self._fetch_comments_for_article(article.get("id"))
                for comment in raw_comments[:comments_per_article]:
                    crec = self._normalize_comment(
                        comment, article.get("id"), article.get("url"), query, retrieved_at_iso
                    )
                    if crec is None:
                        continue
                    if deduplicate and crec.id in seen_ids:
                        continue
                    records.append(crec)
                    seen_ids.add(crec.id)
                    comments_count += 1

        log.info(
            "DEV Community collection finished for '%s': %d articles, %d comments",
            query, articles_count, comments_count,
        )

        return DevCommunityQueryResult(
            query=query,
            total_records=len(records),
            articles_count=articles_count,
            comments_count=comments_count,
            retrieved_at=retrieved_at_iso,
            records=records,
            errors=errors,
        )
