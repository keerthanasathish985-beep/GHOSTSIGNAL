"""
GhostSignal - Discussion Collectors Module
"""
from typing import Any


def __getattr__(name: str) -> Any:
    if name in ("HackerNewsCollector", "HackerNewsRecord", "HackerNewsQueryResult", "clean_html_text"):
        from src.collectors.discussions.hackernews_collector import (
            HackerNewsCollector, HackerNewsQueryResult, HackerNewsRecord, clean_html_text,
        )
        mapping = {
            "HackerNewsCollector": HackerNewsCollector,
            "HackerNewsRecord": HackerNewsRecord,
            "HackerNewsQueryResult": HackerNewsQueryResult,
            "clean_html_text": clean_html_text,
        }
        return mapping[name]
    if name in ("BlueskyCollector", "BlueskyRecord", "BlueskyQueryResult", "extract_external_links"):
        from src.collectors.discussions.bluesky_collector import (
            BlueskyCollector, BlueskyQueryResult, BlueskyRecord, extract_external_links,
        )
        mapping = {
            "BlueskyCollector": BlueskyCollector,
            "BlueskyRecord": BlueskyRecord,
            "BlueskyQueryResult": BlueskyQueryResult,
            "extract_external_links": extract_external_links,
        }
        return mapping[name]
    if name in ("DevCommunityCollector", "DevCommunityRecord", "DevCommunityQueryResult"):
        from src.collectors.discussions.devcommunity_collector import (
            DevCommunityCollector, DevCommunityRecord, DevCommunityQueryResult,
        )
        mapping = {
            "DevCommunityCollector": DevCommunityCollector,
            "DevCommunityRecord": DevCommunityRecord,
            "DevCommunityQueryResult": DevCommunityQueryResult,
        }
        return mapping[name]
    if name in ("StackExchangeCollector", "StackExchangeRecord", "StackExchangeQueryResult"):
        from src.collectors.discussions.stackexchange_collector import (
            StackExchangeCollector, StackExchangeRecord, StackExchangeQueryResult,
        )
        mapping = {
            "StackExchangeCollector": StackExchangeCollector,
            "StackExchangeRecord": StackExchangeRecord,
            "StackExchangeQueryResult": StackExchangeQueryResult,
        }
        return mapping[name]
    if name in ("DiscourseCollector", "DiscourseRecord", "DiscourseQueryResult"):
        from src.collectors.discussions.discourse_collector import (
            DiscourseCollector, DiscourseRecord, DiscourseQueryResult,
        )
        mapping = {
            "DiscourseCollector": DiscourseCollector,
            "DiscourseRecord": DiscourseRecord,
            "DiscourseQueryResult": DiscourseQueryResult,
        }
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "HackerNewsCollector", "HackerNewsRecord", "HackerNewsQueryResult", "clean_html_text",
    "BlueskyCollector", "BlueskyRecord", "BlueskyQueryResult", "extract_external_links",
    "DevCommunityCollector", "DevCommunityRecord", "DevCommunityQueryResult",
    "StackExchangeCollector", "StackExchangeRecord", "StackExchangeQueryResult",
    "DiscourseCollector", "DiscourseRecord", "DiscourseQueryResult",
]
