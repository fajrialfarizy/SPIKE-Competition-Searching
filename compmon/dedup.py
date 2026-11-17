"""
compmon.dedup
Deduplicates posts by Post ID and merges source attributions.
"""

from typing import List, Dict, Tuple, Any
from compmon.models import RawPost, SourceType


def merge_raw_posts(existing: RawPost, incoming: RawPost) -> RawPost:
    """
    Merges two RawPost instances with the same canonical Post ID.
    Merges source_accounts and keyword_source lists and updates source_type.
    """
    merged_accounts = list(set(existing.source_accounts + incoming.source_accounts))
    merged_keywords = list(set(existing.keyword_source + incoming.keyword_source))

    if merged_accounts and merged_keywords:
        st = SourceType.BOTH
    elif merged_accounts:
        st = SourceType.ACCOUNT
    else:
        st = SourceType.KEYWORD

    # Retain the earliest first_seen and best available caption/url
    first_seen = existing.first_seen or incoming.first_seen
    posted_at = existing.posted_at or incoming.posted_at
    caption = existing.caption if len(existing.caption) >= len(incoming.caption) else incoming.caption
    url = existing.url or incoming.url

    return RawPost(
        post_id=existing.post_id,
        url=url,
        source_accounts=merged_accounts,
        keyword_source=merged_keywords,
        source_type=st,
        posted_at=posted_at,
        caption=caption,
        first_seen=first_seen,
    )


def deduplicate_post_batch(posts: List[RawPost]) -> List[RawPost]:
    """
    Deduplicates a list of newly scraped RawPost objects in-memory,
    merging their sources if the same Post ID appears multiple times.
    """
    post_map: Dict[str, RawPost] = {}
    for p in posts:
        pid = str(p.post_id).strip()
        if not pid:
            continue
        if pid in post_map:
            post_map[pid] = merge_raw_posts(post_map[pid], p)
        else:
            post_map[pid] = p
    return list(post_map.values())


def partition_new_and_existing(
    incoming_posts: List[RawPost],
    existing_post_ids: Dict[str, Dict[str, Any]]
) -> Tuple[List[RawPost], List[Tuple[str, List[str], List[str], SourceType]]]:
    """
    Partitions incoming posts against posts already recorded in System Log.
    Returns:
    - brand_new_posts: RawPost items that are NOT in System Log.
    - attribution_updates: List of (post_id, new_accounts, new_keywords, merged_source_type)
      for posts that already exist and need their source attribution updated.
    """
    brand_new: List[RawPost] = []
    updates: List[Tuple[str, List[str], List[str], SourceType]] = []

    for p in incoming_posts:
        pid = str(p.post_id).strip()
        if pid in existing_post_ids:
            cur = existing_post_ids[pid]
            # cur is a dict with keys: 'source_accounts', 'keyword_source', 'source_type'
            existing_acc = set(cur.get("source_accounts", []))
            existing_kw = set(cur.get("keyword_source", []))

            new_acc = set(p.source_accounts)
            new_kw = set(p.keyword_source)

            merged_acc = sorted(list(existing_acc | new_acc))
            merged_kw = sorted(list(existing_kw | new_kw))

            if merged_acc and merged_kw:
                st = SourceType.BOTH
            elif merged_acc:
                st = SourceType.ACCOUNT
            else:
                st = SourceType.KEYWORD

            # Only flag as update if there are actually new sources added
            if (len(merged_acc) > len(existing_acc)) or (len(merged_kw) > len(existing_kw)):
                updates.append((pid, merged_acc, merged_kw, st))
        else:
            brand_new.append(p)

    return brand_new, updates
