"""
GhostSignal — Hacker News Discussion Collector
================================================
Collects public stories and discussions from Hacker News via the free Algolia Search API.

Endpoints:
    Stories:  https://hn.algolia.com/api/v1/search_by_date?tags=story
    Comments: https://hn.algolia.com/api/v1/search_by_date?tags=comment

Documentation:
    https://hn.algolia.com/api

Data Contract Compliance:
    Output adheres strictly to the shared GHOSTSIGNAL record schema defined in
    GHOSTSIGNAL_PROJECT_BRIEF.md (Section 6) and matches the schema conventions of
    the GDELT collector (src/collectors/news/gdelt_collector.py).
    Missing or unavailable fields (like product, rating, shares, views) remain null.
    No content, author names, timestamps, or engagement metrics are ever fabricated.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Union

import requests
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger("ghostsignal.collectors.hackernews")
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
class HackerNewsCollectorError(Exception):
    """Base exception for Hacker News collector errors."""
    pass


class HackerNewsRateLimitError(HackerNewsCollectorError):
    """Raised when Algolia HN API returns a rate limit response (HTTP 429)."""
    pass


class HackerNewsTimeoutError(HackerNewsCollectorError):
    """Raised when an HTTP request to the Algolia HN API times out."""
    pass


class HackerNewsAPIError(HackerNewsCollectorError):
    """Raised when Algolia HN API returns an unhandled HTTP error or malformed payload."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


# ---------------------------------------------------------------------------
# Data Models (GHOSTSIGNAL Section 6 Contract & GDELT Schema Compatible)
# ---------------------------------------------------------------------------
class HackerNewsRecord(BaseModel):
    """
    Normalized GHOSTSIGNAL record for an item retrieved from Hacker News.
    Strictly compatible with GDELTArticle and GHOSTSIGNAL Data Contract.
    """
    id: str = Field(..., description="Stable unique record identifier (e.g. hn_123456)")
    source: str = Field(default="hackernews", description="Canonical source identifier ('hackernews')")
    content: str = Field(
        ...,
        description="Main text for analysis (story text/title, or cleaned comment body; never fabricated)"
    )
    title: Optional[str] = Field(default=None, description="Story title or parent story title for comments")
    url: Optional[str] = Field(default=None, description="Article/external URL or HN permalink")
    published_at: Optional[str] = Field(
        default=None,
        description="ISO-8601 publication date in UTC, if available"
    )
    retrieved_at: str = Field(..., description="ISO-8601 UTC timestamp of retrieval")
    author: Optional[str] = Field(default=None, description="HN username if available")
    product: Optional[str] = Field(default=None, description="Product identifier (None for discussions)")
    rating: Optional[float] = Field(default=None, description="Rating (None for discussions)")
    likes: Optional[int] = Field(default=None, description="Score/points if available")
    comments: Optional[int] = Field(default=None, description="Comment count for stories if available")
    shares: Optional[int] = Field(default=None, description="Shares count (None for HN)")
    views: Optional[int] = Field(default=None, description="Views count (None for HN)")
    label: Optional[str] = Field(default=None, description="Ground truth training label")
    predicted_label: Optional[str] = Field(default=None, description="Model prediction")
    confidence: Optional[float] = Field(default=None, description="Model prediction confidence")
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Source-specific metadata, story provenance, and raw fields"
    )


class HackerNewsQueryResult(BaseModel):
    """Container for collector execution output and diagnostics."""
    query: str
    total_records: int
    stories_count: int
    comments_count: int
    retrieved_at: str
    records: List[HackerNewsRecord] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to standard dictionary."""
        return self.model_dump()

    def to_json(self, indent: Optional[int] = None) -> str:
        """Serialize result to structured JSON string."""
        return self.model_dump_json(indent=indent)

    def to_records(self) -> List[Dict[str, Any]]:
        """Return items as a list of dictionaries matching GHOSTSIGNAL schema."""
        return [rec.model_dump() for rec in self.records]


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------
def clean_html_text(text: Optional[str]) -> str:
    """
    Remove HTML tags and unescape HTML entities from Hacker News comment text.

    Preserves paragraph line breaks and readable structure while stripping tags.
    """
    if not text or not isinstance(text, str):
        return ""

    s = text.strip()
    if not s:
        return ""

    # Replace <p> and <br> with newlines
    s = re.sub(r"(?i)<\s*p\s*/?>", "\n\n", s)
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)

    # Strip remaining HTML tags
    s = re.sub(r"<[^>]+>", "", s)

    # Unescape HTML entities (&amp;, &#x27;, &gt;, &lt;, &quot;, etc.)
    s = html.unescape(s)

    # Normalize excessive blank lines
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def parse_hn_datetime(created_at: Optional[str], created_at_i: Optional[int]) -> Optional[str]:
    """
    Produce a standard ISO-8601 UTC string from Algolia HN created_at or created_at_i.
    """
    if created_at and isinstance(created_at, str):
        cleaned = created_at.strip()
        if cleaned:
            try:
                # If already ending with Z or offset
                if cleaned.endswith("Z"):
                    dt = datetime.fromisoformat(cleaned[:-1]).replace(tzinfo=timezone.utc)
                else:
                    dt = datetime.fromisoformat(cleaned)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except ValueError:
                pass

    if created_at_i is not None and isinstance(created_at_i, (int, float)):
        try:
            dt = datetime.fromtimestamp(created_at_i, tz=timezone.utc)
            return dt.isoformat()
        except (ValueError, OSError, OverflowError):
            pass

    return None


def to_epoch_seconds(val: Union[datetime, date, str, int, float]) -> int:
    """Convert various date representations to an integer Unix epoch timestamp."""
    if isinstance(val, (int, float)):
        return int(val)

    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return int(val.timestamp())

    if isinstance(val, date):
        dt = datetime(val.year, val.month, val.day, tzinfo=timezone.utc)
        return int(dt.timestamp())

    if isinstance(val, str):
        s = val.strip()
        if s.isdigit():
            return int(s)
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
            except ValueError:
                pass
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            pass

    raise ValueError(f"Cannot parse date/timestamp value: {val}")




# ---------------------------------------------------------------------------
# Relevance filter helper
# ---------------------------------------------------------------------------

# Words that are extremely common on HN and in tech contexts, so they should
# NOT be used as the sole relevance signal for multi-word queries.
_HN_GENERIC: Set[str] = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "do", "for", "from", "has", "have", "he", "her", "him", "his",
    "how", "if", "in", "is", "it", "its", "me", "my", "not", "of",
    "on", "or", "our", "out", "she", "so", "than", "that", "the",
    "their", "them", "then", "there", "they", "this", "to", "up",
    "us", "was", "we", "were", "what", "when", "where", "who",
    "will", "with", "you", "your",
    "about", "after", "also", "another", "based", "because", "before",
    "being", "between", "build", "built", "code", "coding", "come",
    "community", "company", "could", "data", "design", "does", "each",
    "engineering", "every", "first", "good", "great", "here", "high",
    "implementation", "india", "information", "institute", "into",
    "just", "know", "large", "learn", "library", "like", "line",
    "link", "list", "little", "look", "made", "make", "many", "might",
    "more", "most", "much", "need", "news", "next", "note", "number",
    "open", "other", "over", "people", "point", "post", "problem",
    "product", "public", "question", "quite", "really", "right",
    "same", "science", "should", "since", "site", "some", "still",
    "such", "system", "systems", "technology", "thing", "think",
    "time", "tool", "tools", "type", "under", "university", "until",
    "used", "user", "users", "using", "very", "want", "well", "which",
    "while", "work", "would", "year",
}


def _distinctive_tokens(query: str) -> List[str]:
    """
    Return the tokens from *query* that are most distinctive for a relevance
    post-filter.  Stop-words and ultra-common tech/academic words are stripped
    so that institutional queries like 'GSSS institute of engineering and
    technology' do not match every HN post that contains 'engineering'.

    Short tokens (2-4 chars) such as acronyms ('gsss', 'mit', 'iit') are
    always kept if they are not plain stop-words, because they are usually the
    most specific part of an institutional query.

    If every word in the query is a stop-word the full word list is returned
    as a fallback (over-filtering is worse than no filter for simple queries).
    """
    words = [w.lower() for w in re.split(r"[^a-zA-Z0-9]+", query) if len(w) >= 2]
    distinctive = [w for w in words if w not in _HN_GENERIC]
    return distinctive if distinctive else (words if words else [query.lower().strip()])


def parse_timespan_to_epoch(timespan: str) -> int:
    """
    Parse a relative timespan string (e.g. '24h', '7d', '30d', '1y') to a cutoff epoch timestamp.
    """
    pattern = r"^(\d+)\s*([smhdwMy])$"
    match = re.match(pattern, timespan.strip())
    if not match:
        raise ValueError(
            f"Invalid timespan format '{timespan}'. Expected e.g. '24h', '7d', '1w', '30d', '1y'."
        )

    amount = int(match.group(1))
    unit = match.group(2)
    now = datetime.now(timezone.utc)

    if unit == "s":
        delta = timedelta(seconds=amount)
    elif unit == "m":
        delta = timedelta(minutes=amount)
    elif unit == "h":
        delta = timedelta(hours=amount)
    elif unit in ("d", "D"):
        delta = timedelta(days=amount)
    elif unit in ("w", "W"):
        delta = timedelta(weeks=amount)
    elif unit == "M":
        delta = timedelta(days=amount * 30)
    elif unit == "y":
        delta = timedelta(days=amount * 365)
    else:
        delta = timedelta(days=7)

    cutoff = now - delta
    return int(cutoff.timestamp())


# ---------------------------------------------------------------------------
# Hacker News Collector
# ---------------------------------------------------------------------------
class HackerNewsCollector:
    """
    Collector targeting the public Hacker News Algolia Search API.

    Features:
    - Queries stories (`tags=story`) and comments (`tags=comment`) separately.
    - Full schema compatibility with GHOSTSIGNAL data contract and GDELT collector.
    - Deduplication using Algolia object IDs.
    - Configurable date ranges (`start_date`, `end_date`), relative `timespan`, and result limits.
    - Multi-page pagination support.
    - Robust handling of missing fields, deleted items, empty responses, timeouts, and errors.
    - Never fabricates content, authors, timestamps, or engagement values.
    """

    SEARCH_BY_DATE_ENDPOINT = "https://hn.algolia.com/api/v1/search_by_date"
    DEFAULT_USER_AGENT = "GhostSignal-DiscussionCollector/1.0 (+https://ghostsignal.local)"

    def __init__(
        self,
        endpoint: str = SEARCH_BY_DATE_ENDPOINT,
        timeout: float = 15.0,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
        hits_per_page: int = 50,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Optional[requests.Session] = None,
    ):
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.hits_per_page = max(1, min(hits_per_page, 1000))
        self.user_agent = user_agent
        self.session = session or requests.Session()

    def build_numeric_filters(
        self,
        start_date: Optional[Union[datetime, date, str, int]] = None,
        end_date: Optional[Union[datetime, date, str, int]] = None,
        timespan: Optional[str] = None,
        min_points: Optional[int] = None,
        min_comments: Optional[int] = None,
    ) -> Optional[str]:
        """
        Build Algolia numericFilters query string (e.g. 'created_at_i>=X,created_at_i<=Y').
        """
        filters: List[str] = []

        if timespan and not start_date:
            cutoff = parse_timespan_to_epoch(timespan)
            filters.append(f"created_at_i>={cutoff}")
        elif start_date:
            start_epoch = to_epoch_seconds(start_date)
            filters.append(f"created_at_i>={start_epoch}")

        if end_date:
            end_epoch = to_epoch_seconds(end_date)
            filters.append(f"created_at_i<={end_epoch}")

        if min_points is not None:
            filters.append(f"points>={int(min_points)}")

        if min_comments is not None:
            filters.append(f"num_comments>={int(min_comments)}")

        return ",".join(filters) if filters else None

    def build_params(
        self,
        query: str,
        tag: str,
        page: int = 0,
        hits_per_page: Optional[int] = None,
        start_date: Optional[Union[datetime, date, str, int]] = None,
        end_date: Optional[Union[datetime, date, str, int]] = None,
        timespan: Optional[str] = None,
        min_points: Optional[int] = None,
        min_comments: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Construct query parameters for Algolia HN search API.
        """
        if not query or not query.strip():
            raise ValueError("Query string cannot be empty.")

        if tag not in ("story", "comment"):
            raise ValueError(f"Invalid tag '{tag}'. Expected 'story' or 'comment'.")

        params: Dict[str, Any] = {
            "query": query.strip(),
            "tags": tag,
            "page": max(0, int(page)),
            "hitsPerPage": max(1, min(hits_per_page or self.hits_per_page, 1000)),
        }

        numeric_filters = self.build_numeric_filters(
            start_date=start_date,
            end_date=end_date,
            timespan=timespan,
            min_points=min_points,
            min_comments=min_comments,
        )
        if numeric_filters:
            params["numericFilters"] = numeric_filters

        return params

    def _execute_request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute HTTP request to Algolia API with retry logic and comprehensive error handling.
        """
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
        }

        attempts = 0
        while attempts <= self.max_retries:
            attempts += 1
            log.debug("Querying Algolia HN API (attempt %d/%d): %s", attempts, self.max_retries + 1, params)

            try:
                response = self.session.get(
                    self.endpoint,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                )
            except requests.exceptions.Timeout as e:
                log.warning("Hacker News API timed out: %s", e)
                if attempts > self.max_retries:
                    raise HackerNewsTimeoutError(f"Hacker News API timed out after {self.timeout}s: {e}") from e
                time.sleep(self.retry_backoff * attempts)
                continue
            except requests.exceptions.RequestException as e:
                log.error("Network error querying Hacker News API: %s", e)
                if attempts > self.max_retries:
                    raise HackerNewsCollectorError(f"Network error querying Hacker News API: {e}") from e
                time.sleep(self.retry_backoff * attempts)
                continue

            # Handle Rate Limiting (429)
            if response.status_code == 429:
                wait_time = self.retry_backoff * (2 ** (attempts - 1))
                log.warning(
                    "Algolia HN Rate Limit hit (HTTP 429). Retrying in %.1fs (attempt %d/%d)",
                    wait_time,
                    attempts,
                    self.max_retries + 1,
                )
                if attempts > self.max_retries:
                    raise HackerNewsRateLimitError("Hacker News rate limit exceeded.")
                time.sleep(wait_time)
                continue

            # Handle other HTTP error statuses
            if response.status_code != 200:
                raise HackerNewsAPIError(
                    f"Algolia HN API returned HTTP {response.status_code}",
                    status_code=response.status_code,
                    response_text=response.text[:500],
                )

            body = response.text.strip()
            if not body:
                return {"hits": [], "nbHits": 0, "page": 0, "nbPages": 0}

            try:
                data = response.json()
                if not isinstance(data, dict):
                    log.warning("Algolia returned unexpected JSON type: %s", type(data))
                    return {"hits": [], "nbHits": 0, "page": 0, "nbPages": 0}
                return data
            except (requests.exceptions.JSONDecodeError, json.JSONDecodeError, ValueError) as e:
                raise HackerNewsAPIError(
                    f"Malformed JSON from Algolia HN API: {e}",
                    status_code=response.status_code,
                    response_text=body[:500],
                ) from e

        raise HackerNewsCollectorError("Exhausted retries while querying Hacker News API.")

    def _normalize_story_hit(
        self,
        hit: Dict[str, Any],
        query: str,
        retrieved_at_iso: str,
    ) -> Optional[HackerNewsRecord]:
        """Transform an Algolia story hit into a normalized GHOSTSIGNAL record."""
        raw_id = hit.get("objectID")
        if not raw_id:
            return None

        object_id_str = str(raw_id).strip()
        record_id = f"hn_{object_id_str}"
        title = hit.get("title")

        # Main content: story_text if present, else title. Never fabricated.
        story_text = hit.get("story_text")
        if story_text and isinstance(story_text, str) and story_text.strip():
            content_text = clean_html_text(story_text)
        else:
            content_text = (title or "").strip()

        # URL: external article URL if available, fallback to HN discussion thread
        hn_permalink = f"https://news.ycombinator.com/item?id={object_id_str}"
        external_url = hit.get("url")
        url = external_url if (external_url and isinstance(external_url, str) and external_url.strip()) else hn_permalink

        published_at = parse_hn_datetime(hit.get("created_at"), hit.get("created_at_i"))
        author = hit.get("author") or None

        # Points and comments
        points = hit.get("points")
        num_comments = hit.get("num_comments")

        likes_val = int(points) if isinstance(points, (int, float)) else None
        comments_val = int(num_comments) if isinstance(num_comments, (int, float)) else None

        metadata: Dict[str, Any] = {
            "item_type": "story",
            "object_id": object_id_str,
            "hn_url": hn_permalink,
            "external_url": external_url or None,
            "points": likes_val,
            "num_comments": comments_val,
            "created_at_i": hit.get("created_at_i"),
            "provenance": {
                "collector": "HackerNewsCollector",
                "api_endpoint": self.endpoint,
                "query": query,
                "tag": "story",
                "retrieved_at": retrieved_at_iso,
                "object_id": object_id_str,
            },
        }

        return HackerNewsRecord(
            id=record_id,
            source="hackernews",
            content=content_text,
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
            views=None,
            label=None,
            predicted_label=None,
            confidence=None,
            metadata=metadata,
        )

    def _normalize_comment_hit(
        self,
        hit: Dict[str, Any],
        query: str,
        retrieved_at_iso: str,
    ) -> Optional[HackerNewsRecord]:
        """Transform an Algolia comment hit into a normalized GHOSTSIGNAL record."""
        raw_id = hit.get("objectID")
        if not raw_id:
            return None

        object_id_str = str(raw_id).strip()
        record_id = f"hn_{object_id_str}"

        raw_comment_text = hit.get("comment_text")
        content_text = clean_html_text(raw_comment_text)

        story_title = hit.get("story_title")
        story_url = hit.get("story_url")
        hn_permalink = f"https://news.ycombinator.com/item?id={object_id_str}"

        published_at = parse_hn_datetime(hit.get("created_at"), hit.get("created_at_i"))
        author = hit.get("author") or None

        points = hit.get("points")
        likes_val = int(points) if isinstance(points, (int, float)) else None

        metadata: Dict[str, Any] = {
            "item_type": "comment",
            "object_id": object_id_str,
            "story_id": hit.get("story_id"),
            "parent_id": hit.get("parent_id"),
            "story_title": story_title,
            "story_url": story_url,
            "hn_url": hn_permalink,
            "points": likes_val,
            "raw_comment_text": raw_comment_text,
            "created_at_i": hit.get("created_at_i"),
            "provenance": {
                "collector": "HackerNewsCollector",
                "api_endpoint": self.endpoint,
                "query": query,
                "tag": "comment",
                "retrieved_at": retrieved_at_iso,
                "object_id": object_id_str,
                "parent_id": hit.get("parent_id"),
                "story_id": hit.get("story_id"),
            },
        }

        return HackerNewsRecord(
            id=record_id,
            source="hackernews",
            content=content_text,
            title=story_title,
            url=hn_permalink,
            published_at=published_at,
            retrieved_at=retrieved_at_iso,
            author=author,
            product=None,
            rating=None,
            likes=likes_val,
            comments=None,
            shares=None,
            views=None,
            label=None,
            predicted_label=None,
            confidence=None,
            metadata=metadata,
        )

    def _fetch_tag_with_pagination(
        self,
        query: str,
        tag: str,
        target_limit: int,
        seen_object_ids: Set[str],
        start_date: Optional[Union[datetime, date, str, int]] = None,
        end_date: Optional[Union[datetime, date, str, int]] = None,
        timespan: Optional[str] = None,
        min_points: Optional[int] = None,
        min_comments: Optional[int] = None,
        deduplicate: bool = True,
        retrieved_at_iso: Optional[str] = None,
    ) -> List[HackerNewsRecord]:
        """
        Fetch items for a specific tag ('story' or 'comment') across pages until
        target_limit is reached. A relevance post-filter rejects hits that contain
        none of the query's distinctive tokens, preventing Algolia's broad
        tokenisation from returning unrelated results for institutional queries.
        """
        records: List[HackerNewsRecord] = []
        page = 0
        timestamp_str = retrieved_at_iso or datetime.now(timezone.utc).isoformat()

        # Build the per-query relevance filter once.
        filter_tokens = _distinctive_tokens(query)

        def _is_relevant(hit: Dict[str, Any]) -> bool:
            haystack = " ".join([
                str(hit.get("title") or ""),
                str(hit.get("story_title") or ""),
                str(hit.get("comment_text") or ""),
                str(hit.get("story_text") or ""),
                str(hit.get("url") or ""),
            ]).lower()
            # Whole-word match: "amazon" must not match "amazynk" or "amazonian".
            return any(
                bool(re.search(r"\b" + re.escape(tok) + r"\b", haystack))
                for tok in filter_tokens
            )

        while len(records) < target_limit:
            remaining = target_limit - len(records)
            # Fetch a larger page so post-filtering doesn't under-fill results.
            per_page = min(self.hits_per_page, max(remaining * 3, 20), 1000)

            params = self.build_params(
                query=query,
                tag=tag,
                page=page,
                hits_per_page=per_page,
                start_date=start_date,
                end_date=end_date,
                timespan=timespan,
                min_points=min_points,
                min_comments=min_comments,
            )

            response_data = self._execute_request(params)
            hits = response_data.get("hits") or []
            if not hits:
                break

            for hit in hits:
                obj_id = str(hit.get("objectID") or "").strip()
                if not obj_id:
                    continue

                if deduplicate and obj_id in seen_object_ids:
                    log.debug("Skipping duplicate Algolia objectID: %s", obj_id)
                    continue

                if not _is_relevant(hit):
                    log.debug("Skipping irrelevant HN hit %s for query '%s'", obj_id, query)
                    continue

                if tag == "story":
                    rec = self._normalize_story_hit(hit, query, timestamp_str)
                else:
                    rec = self._normalize_comment_hit(hit, query, timestamp_str)

                if rec is not None:
                    records.append(rec)
                    seen_object_ids.add(obj_id)

                if len(records) >= target_limit:
                    break

            nb_pages = response_data.get("nbPages", 0)
            page += 1
            if page >= nb_pages:
                break

        return records

    def collect_stories(
        self,
        query: str,
        limit: int = 25,
        start_date: Optional[Union[datetime, date, str, int]] = None,
        end_date: Optional[Union[datetime, date, str, int]] = None,
        timespan: Optional[str] = None,
        min_points: Optional[int] = None,
        min_comments: Optional[int] = None,
        deduplicate: bool = True,
    ) -> List[HackerNewsRecord]:
        """Fetch only matching stories."""
        seen: Set[str] = set()
        return self._fetch_tag_with_pagination(
            query=query,
            tag="story",
            target_limit=max(1, limit),
            seen_object_ids=seen,
            start_date=start_date,
            end_date=end_date,
            timespan=timespan,
            min_points=min_points,
            min_comments=min_comments,
            deduplicate=deduplicate,
        )

    def collect_comments(
        self,
        query: str,
        limit: int = 25,
        start_date: Optional[Union[datetime, date, str, int]] = None,
        end_date: Optional[Union[datetime, date, str, int]] = None,
        timespan: Optional[str] = None,
        deduplicate: bool = True,
    ) -> List[HackerNewsRecord]:
        """Fetch only matching comments."""
        seen: Set[str] = set()
        return self._fetch_tag_with_pagination(
            query=query,
            tag="comment",
            target_limit=max(1, limit),
            seen_object_ids=seen,
            start_date=start_date,
            end_date=end_date,
            timespan=timespan,
            deduplicate=deduplicate,
        )

    def collect(
        self,
        query: str,
        limit: int = 50,
        limit_stories: Optional[int] = None,
        limit_comments: Optional[int] = None,
        collect_stories: bool = True,
        collect_comments: bool = True,
        start_date: Optional[Union[datetime, date, str, int]] = None,
        end_date: Optional[Union[datetime, date, str, int]] = None,
        timespan: Optional[str] = None,
        min_points: Optional[int] = None,
        min_comments: Optional[int] = None,
        deduplicate: bool = True,
        raise_on_error: bool = False,
    ) -> HackerNewsQueryResult:
        """
        Execute an investigation query across Hacker News stories and comments.

        Parameters
        ----------
        query : str
            Investigation search query.
        limit : int, optional
            Default total limit target when specific limits are omitted (default 50).
        limit_stories : int, optional
            Maximum stories to retrieve (defaults to limit // 2 if both types requested).
        limit_comments : int, optional
            Maximum comments to retrieve (defaults to limit // 2 if both types requested).
        collect_stories : bool, optional
            Whether to retrieve stories (tags=story, default True).
        collect_comments : bool, optional
            Whether to retrieve comments (tags=comment, default True).
        start_date : datetime | date | str | int, optional
            Start date boundary.
        end_date : datetime | date | str | int, optional
            End date boundary.
        timespan : str, optional
            Relative timespan (e.g. '24h', '7d', '30d', '1y').
        min_points : int, optional
            Minimum story points filter.
        min_comments : int, optional
            Minimum comment count filter.
        deduplicate : bool, optional
            Whether to deduplicate records by Algolia objectID (default True).
        raise_on_error : bool, optional
            If False (default), logs errors and returns result container with errors list.
            If True, re-raises collector exceptions.

        Returns
        -------
        HackerNewsQueryResult
            Structured result containing validated records adhering to GHOSTSIGNAL schema.
        """
        retrieved_at_iso = datetime.now(timezone.utc).isoformat()
        errors: List[str] = []
        all_records: List[HackerNewsRecord] = []
        seen_object_ids: Set[str] = set()

        # Determine target limits
        if collect_stories and collect_comments:
            story_target = limit_stories if limit_stories is not None else max(1, limit // 2)
            comment_target = limit_comments if limit_comments is not None else max(1, limit - story_target)
        elif collect_stories:
            story_target = limit_stories if limit_stories is not None else limit
            comment_target = 0
        elif collect_comments:
            story_target = 0
            comment_target = limit_comments if limit_comments is not None else limit
        else:
            return HackerNewsQueryResult(
                query=query,
                total_records=0,
                stories_count=0,
                comments_count=0,
                retrieved_at=retrieved_at_iso,
                records=[],
                errors=["Neither collect_stories nor collect_comments was enabled."],
            )

        stories_found = 0
        comments_found = 0

        # Retrieve Stories
        if collect_stories and story_target > 0:
            try:
                story_records = self._fetch_tag_with_pagination(
                    query=query,
                    tag="story",
                    target_limit=story_target,
                    seen_object_ids=seen_object_ids,
                    start_date=start_date,
                    end_date=end_date,
                    timespan=timespan,
                    min_points=min_points,
                    min_comments=min_comments,
                    deduplicate=deduplicate,
                    retrieved_at_iso=retrieved_at_iso,
                )
                all_records.extend(story_records)
                stories_found = len(story_records)
            except Exception as e:
                log.error("Failed to collect Hacker News stories for '%s': %s", query, e)
                if raise_on_error:
                    raise
                errors.append(f"Stories collection error: {e}")

        # Retrieve Comments
        if collect_comments and comment_target > 0:
            try:
                comment_records = self._fetch_tag_with_pagination(
                    query=query,
                    tag="comment",
                    target_limit=comment_target,
                    seen_object_ids=seen_object_ids,
                    start_date=start_date,
                    end_date=end_date,
                    timespan=timespan,
                    deduplicate=deduplicate,
                    retrieved_at_iso=retrieved_at_iso,
                )
                all_records.extend(comment_records)
                comments_found = len(comment_records)
            except Exception as e:
                log.error("Failed to collect Hacker News comments for '%s': %s", query, e)
                if raise_on_error:
                    raise
                errors.append(f"Comments collection error: {e}")

        log.info(
            "Hacker News collection finished for '%s': %d stories, %d comments (total: %d)",
            query,
            stories_found,
            comments_found,
            len(all_records),
        )

        return HackerNewsQueryResult(
            query=query,
            total_records=len(all_records),
            stories_count=stories_found,
            comments_count=comments_found,
            retrieved_at=retrieved_at_iso,
            records=all_records,
            errors=errors,
        )

    def collect_records(self, *args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
        """Convenience method returning items as standard dictionaries."""
        result = self.collect(*args, **kwargs)
        return result.to_records()

    def collect_json(self, *args: Any, indent: Optional[int] = 2, **kwargs: Any) -> str:
        """Convenience method returning serialized JSON string."""
        result = self.collect(*args, **kwargs)
        return result.to_json(indent=indent)


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------
def _main() -> None:
    parser = argparse.ArgumentParser(
        description="GhostSignal Hacker News Discussion Collector CLI"
    )
    parser.add_argument("query", nargs="?", default="artificial intelligence", help="Investigation query")
    parser.add_argument("--limit", type=int, default=10, help="Total record limit")
    parser.add_argument("--stories-only", action="store_true", help="Collect stories only")
    parser.add_argument("--comments-only", action="store_true", help="Collect comments only")
    parser.add_argument("--timespan", type=str, default=None, help="Relative timespan (e.g. 24h, 7d, 30d)")
    parser.add_argument("--output", type=str, default=None, help="Path to write JSON output")

    args = parser.parse_args()

    collector = HackerNewsCollector()
    collect_stories = not args.comments_only
    collect_comments = not args.stories_only

    result = collector.collect(
        query=args.query,
        limit=args.limit,
        collect_stories=collect_stories,
        collect_comments=collect_comments,
        timespan=args.timespan,
    )

    json_str = result.to_json(indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_str)
        print(f"Results written to {args.output}")
    else:
        print(json_str)


if __name__ == "__main__":
    _main()
