"""
GhostSignal - Stack Exchange Discussion Collector
=================================================
Collects public questions (and optionally answers) from the Stack Exchange
network using the public Stack Exchange API v2.3.

Endpoint:
    https://api.stackexchange.com/2.3/search/advanced

Authentication:
    No API key required. Unauthenticated requests allow 300 req/day per IP.
    The collector respects backoff directives returned in the API response.

Data Contract Compliance:
    Output adheres strictly to the shared GHOSTSIGNAL unified schema.
    Title, body, tags, author, timestamps, scores, answer counts, and view
    counts are extracted faithfully from API responses and never fabricated.
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
from typing import Any, Dict, List, Optional, Set, Union

import requests
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log = logging.getLogger("ghostsignal.collectors.stackexchange")
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
class StackExchangeCollectorError(Exception):
    """Base exception for Stack Exchange collector errors."""


class StackExchangeRateLimitError(StackExchangeCollectorError):
    """Raised when Stack Exchange API signals quota exhaustion or HTTP 429."""


class StackExchangeTimeoutError(StackExchangeCollectorError):
    """Raised when a request to the Stack Exchange API times out."""


class StackExchangeAPIError(StackExchangeCollectorError):
    """Raised when Stack Exchange API returns an unexpected HTTP error."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_text: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


# ---------------------------------------------------------------------------
# Data Models (GHOSTSIGNAL Unified Schema)
# ---------------------------------------------------------------------------
class StackExchangeRecord(BaseModel):
    """Normalized GHOSTSIGNAL record for a Stack Exchange question or answer."""
    id: str = Field(..., description="Stable unique record identifier (e.g. se_stackoverflow_12345)")
    source: str = Field(default="stackexchange", description="Canonical source identifier")
    content: str = Field(..., description="Main text for analysis (title + body excerpt)")
    title: Optional[str] = Field(default=None, description="Question title")
    url: Optional[str] = Field(default=None, description="Permalink to the question on the site")
    published_at: Optional[str] = Field(default=None, description="ISO-8601 creation date in UTC")
    retrieved_at: str = Field(..., description="ISO-8601 UTC timestamp of retrieval")
    author: Optional[str] = Field(default=None, description="Display name of the question owner")
    product: Optional[str] = Field(default=None)
    rating: Optional[float] = Field(default=None)
    likes: Optional[int] = Field(default=None, description="Question score (upvotes minus downvotes)")
    comments: Optional[int] = Field(default=None, description="Answer count")
    shares: Optional[int] = Field(default=None)
    views: Optional[int] = Field(default=None, description="View count")
    label: Optional[str] = Field(default=None)
    predicted_label: Optional[str] = Field(default=None)
    confidence: Optional[float] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StackExchangeQueryResult(BaseModel):
    """Container for Stack Exchange collector execution output."""
    query: str
    site: str
    total_records: int
    retrieved_at: str
    records: List[StackExchangeRecord] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    quota_remaining: Optional[int] = Field(default=None)

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


def _epoch_to_iso(epoch: Optional[Union[int, float]]) -> Optional[str]:
    """Convert a Unix epoch integer to an ISO-8601 UTC string."""
    if epoch is None or not isinstance(epoch, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# Stack Exchange Collector
# ---------------------------------------------------------------------------
class StackExchangeCollector:
    """
    Collector targeting the public Stack Exchange API v2.3.

    Features:
    - Searches questions on a configurable site (default: stackoverflow).
    - Collects title, body excerpt, tags, scores, answer counts, view counts.
    - Pagination support across multiple result pages.
    - Respects API-issued backoff directives.
    - Deduplication by question ID.
    - Full GHOSTSIGNAL schema compatibility.
    - Never fabricates content, timestamps, or engagement counts.
    """

    SEARCH_ENDPOINT = "https://api.stackexchange.com/2.3/search/advanced"
    DEFAULT_USER_AGENT = "GhostSignal-StackExchangeCollector/1.0 (+https://ghostsignal.local)"
    _FILTER = "!nNPvU0osKB"   # filter that includes body in compressed responses

    def __init__(
        self,
        site: str = "stackoverflow",
        timeout: float = 15.0,
        max_retries: int = 2,
        retry_backoff: float = 3.0,
        page_size: int = 25,
        user_agent: str = DEFAULT_USER_AGENT,
        session: Optional[requests.Session] = None,
    ):
        self.site = site
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.page_size = max(1, min(page_size, 100))
        self.user_agent = user_agent
        self.session = session or requests.Session()
        self._last_backoff: float = 0.0

    def _request(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a GET request with retry logic and API-directive backoff handling."""
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip"}
        attempts = 0
        while attempts <= self.max_retries:
            attempts += 1

            # Honor any backoff the API previously requested
            if self._last_backoff > 0:
                log.debug("SE backoff: sleeping %.1f s", self._last_backoff)
                time.sleep(self._last_backoff)
                self._last_backoff = 0.0

            try:
                resp = self.session.get(
                    self.SEARCH_ENDPOINT, params=params, headers=headers, timeout=self.timeout
                )
            except requests.exceptions.Timeout as exc:
                if attempts > self.max_retries:
                    raise StackExchangeTimeoutError(
                        f"Stack Exchange API timed out after {self.timeout}s"
                    ) from exc
                time.sleep(self.retry_backoff * attempts)
                continue
            except requests.exceptions.RequestException as exc:
                if attempts > self.max_retries:
                    raise StackExchangeCollectorError(
                        f"Network error querying Stack Exchange API: {exc}"
                    ) from exc
                time.sleep(self.retry_backoff * attempts)
                continue

            if resp.status_code == 429:
                if attempts > self.max_retries:
                    raise StackExchangeRateLimitError(
                        f"Stack Exchange API rate limited (HTTP 429)"
                    )
                time.sleep(self.retry_backoff * attempts * 2)
                continue

            if resp.status_code != 200:
                raise StackExchangeAPIError(
                    f"Stack Exchange API returned HTTP {resp.status_code}",
                    status_code=resp.status_code,
                    response_text=resp.text[:500],
                )

            body = resp.text.strip()
            if not body:
                return {"items": [], "has_more": False}

            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise StackExchangeAPIError(
                    f"Malformed JSON from Stack Exchange API: {exc}",
                    status_code=resp.status_code,
                    response_text=body[:500],
                ) from exc

            # API-level error codes (quota_max exceeded, invalid key, etc.)
            if "error_id" in data:
                err_msg = data.get("error_message", "unknown error")
                err_id = data.get("error_id")
                if err_id in (502, 503):
                    raise StackExchangeRateLimitError(
                        f"Stack Exchange quota exceeded: {err_msg}"
                    )
                raise StackExchangeAPIError(
                    f"Stack Exchange API error {err_id}: {err_msg}"
                )

            # Respect backoff for next call
            backoff = data.get("backoff")
            if backoff and isinstance(backoff, (int, float)) and backoff > 0:
                self._last_backoff = float(backoff)

            return data

        raise StackExchangeCollectorError("Exhausted retries querying Stack Exchange API.")

    def _normalize_question(self, item: Dict[str, Any], query: str, retrieved_at_iso: str) -> Optional[StackExchangeRecord]:
        """Transform a raw Stack Exchange API question item into a GHOSTSIGNAL record."""
        q_id = item.get("question_id")
        if not q_id:
            return None

        record_id = f"se_{self.site}_{q_id}"
        title = (item.get("title") or "").strip() or None
        body_html = item.get("body") or ""
        body_text = _strip_html(body_html)[:600]

        # Content: prefer body excerpt if available, else title
        content = body_text if body_text else (title or "")

        link = item.get("link") or f"https://{self.site}.com/q/{q_id}"
        creation_epoch = item.get("creation_date")
        published_at = _epoch_to_iso(creation_epoch)

        owner = item.get("owner") or {}
        author = owner.get("display_name") or None

        score = item.get("score")
        answer_count = item.get("answer_count")
        view_count = item.get("view_count")
        tags = item.get("tags") or []

        likes_val = int(score) if isinstance(score, (int, float)) else None
        comments_val = int(answer_count) if isinstance(answer_count, (int, float)) else None
        views_val = int(view_count) if isinstance(view_count, (int, float)) else None

        metadata: Dict[str, Any] = {
            "item_type": "question",
            "question_id": q_id,
            "site": self.site,
            "tags": tags,
            "is_answered": item.get("is_answered"),
            "accepted_answer_id": item.get("accepted_answer_id"),
            "score": likes_val,
            "answer_count": comments_val,
            "view_count": views_val,
            "last_activity_date": _epoch_to_iso(item.get("last_activity_date")),
            "provenance": {
                "collector": "StackExchangeCollector",
                "api_endpoint": self.SEARCH_ENDPOINT,
                "site": self.site,
                "query": query,
                "retrieved_at": retrieved_at_iso,
                "question_id": q_id,
            },
        }

        return StackExchangeRecord(
            id=record_id,
            source="stackexchange",
            content=content,
            title=title,
            url=link,
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

    def collect(
        self,
        query: str,
        limit: int = 30,
        site: Optional[str] = None,
        deduplicate: bool = True,
        raise_on_error: bool = False,
    ) -> StackExchangeQueryResult:
        """
        Execute an investigation search query against Stack Exchange.

        Parameters
        ----------
        query : str
            Investigation search query.
        limit : int
            Maximum questions to retrieve (default 30).
        site : str, optional
            Stack Exchange site to query (overrides instance default).
        deduplicate : bool
            Deduplicate by question ID (default True).
        raise_on_error : bool
            Re-raise exceptions instead of returning partial results (default False).
        """
        active_site = (site or self.site).strip()
        retrieved_at_iso = datetime.now(timezone.utc).isoformat()
        errors: List[str] = []
        records: List[StackExchangeRecord] = []
        seen_ids: Set[str] = set()
        page = 1

        try:
            while len(records) < limit:
                remaining = limit - len(records)
                per_page = min(self.page_size, remaining, 100)

                params: Dict[str, Any] = {
                    "q": query.strip(),
                    "site": active_site,
                    "page": page,
                    "pagesize": per_page,
                    "order": "desc",
                    "sort": "relevance",
                    # "withbody" is not a valid SE named filter — omit body to avoid
                    # the API returning an error or silently downgrading the filter.
                    # Title + tags are sufficient and always returned by default.
                }

                data = self._request(params)
                items = data.get("items") or []
                if not items:
                    break

                # Relevance post-filter: at least one meaningful query token must
                # appear in the question title or its tags.
                q_tokens = [t.lower() for t in query.split() if len(t) >= 3]

                for item in items:
                    # Title-level relevance check
                    if q_tokens:
                        title_lower = (item.get("title") or "").lower()
                        tags_lower  = " ".join(item.get("tags") or []).lower()
                        haystack    = title_lower + " " + tags_lower
                        if not any(tok in haystack for tok in q_tokens):
                            continue

                    rec = self._normalize_question(item, query, retrieved_at_iso)
                    if rec is None:
                        continue
                    if deduplicate and rec.id in seen_ids:
                        continue
                    records.append(rec)
                    seen_ids.add(rec.id)
                    if len(records) >= limit:
                        break

                if not data.get("has_more", False):
                    break
                page += 1

            quota_remaining = None
            # quota_remaining is included in any response page
            # We store it from the last successful response (data may still be in scope)
            try:
                quota_remaining = data.get("quota_remaining")  # type: ignore[possibly-undefined]
            except Exception:
                pass

        except Exception as exc:
            log.warning("Stack Exchange collection failed for '%s': %s", query, exc)
            if raise_on_error:
                raise
            errors.append(str(exc))
            return StackExchangeQueryResult(
                query=query, site=active_site, total_records=0,
                retrieved_at=retrieved_at_iso, records=[], errors=errors,
                quota_remaining=None,
            )

        log.info(
            "Stack Exchange collection finished for '%s' on %s: %d records",
            query, active_site, len(records),
        )

        return StackExchangeQueryResult(
            query=query,
            site=active_site,
            total_records=len(records),
            retrieved_at=retrieved_at_iso,
            records=records,
            errors=errors,
            quota_remaining=quota_remaining,
        )
