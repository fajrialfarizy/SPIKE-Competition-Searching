"""
compmon.apify_client
Wrapper for running Apify actors and normalizing datasets into RawPost objects.
Guarantees:
- Post ID is ALWAYS derived as shortcode from URL (Instruction 21).
- Checks run['status'] == 'SUCCEEDED' before processing dataset; otherwise treats as source failure (Instruction 15).
"""

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from compmon.models import RawPost, SourceType
from compmon.validator import extract_shortcode, parse_iso_timestamp, validate_raw_post_fields

logger = logging.getLogger(__name__)


def normalize_account_post(item: Dict[str, Any], account_username: str) -> Optional[RawPost]:
    """
    Normalizes a dataset item from apify/instagram-scraper to a RawPost.
    Outputs: id, shortCode, url, caption, timestamp, ownerUsername.
    ALWAYS derives post_id as shortcode from URL or shortCode (Instruction 21).
    """
    url = str(item.get("url") or "").strip()
    shortcode = extract_shortcode(url) or str(item.get("shortCode") or "").strip()

    if not url and shortcode:
        url = f"https://www.instagram.com/p/{shortcode}/"

    post_id = shortcode
    caption = str(item.get("caption") or "").strip()
    timestamp_raw = item.get("timestamp")

    is_valid, reason = validate_raw_post_fields(post_id, url, timestamp_raw)
    if not is_valid:
        logger.warning(f"Discarding invalid account post: {reason}")
        return None

    dt = parse_iso_timestamp(timestamp_raw)
    first_seen = datetime.now(timezone.utc)

    return RawPost(
        post_id=post_id,
        url=url,
        source_accounts=[account_username],
        keyword_source=[],
        source_type=SourceType.ACCOUNT,
        posted_at=dt,
        caption=caption,
        first_seen=first_seen,
    )


def normalize_keyword_post(item: Dict[str, Any], keyword: str) -> Optional[RawPost]:
    """
    Normalizes a dataset item from seemuapps/instagram-keyword-posts-scraper to a RawPost.
    ALWAYS derives post_id as shortcode from URL or shortcode (Instruction 21).
    """
    url = str(item.get("url") or "").strip()
    shortcode = extract_shortcode(url) or str(item.get("shortcode") or "").strip()

    if not url and shortcode:
        url = f"https://www.instagram.com/p/{shortcode}/"

    post_id = shortcode
    caption = str(item.get("caption") or "").strip()
    timestamp_raw = item.get("takenAt")

    is_valid, reason = validate_raw_post_fields(post_id, url, timestamp_raw)
    if not is_valid:
        logger.warning(f"Discarding invalid keyword post: {reason}")
        return None

    dt = parse_iso_timestamp(timestamp_raw)
    first_seen = datetime.now(timezone.utc)

    return RawPost(
        post_id=post_id,
        url=url,
        source_accounts=[],
        keyword_source=[keyword],
        source_type=SourceType.KEYWORD,
        posted_at=dt,
        caption=caption,
        first_seen=first_seen,
    )


class ApifyScraperClient:
    """Handles running Apify actors with status verification and error handling."""

    def __init__(self, api_token: str):
        self.api_token = api_token
        try:
            from apify_client import ApifyClient
            self.client = ApifyClient(api_token)
        except ImportError:
            self.client = None

    def scrape_account_posts(
        self,
        actor_id: str,
        account_username: str,
        results_limit: int,
        only_posts_newer_than: Optional[str] = None
    ) -> List[RawPost]:
        """
        Runs apify/instagram-scraper for a target account.
        Verifies run['status'] == 'SUCCEEDED' (Instruction 15).
        """
        if not self.client:
            raise RuntimeError("apify-client library is not installed")

        profile_url = f"https://www.instagram.com/{account_username.strip('/')}/"
        run_input = {
            "directUrls": [profile_url],
            "resultsType": "posts",
            "resultsLimit": results_limit,
        }
        if only_posts_newer_than:
            run_input["onlyPostsNewerThan"] = only_posts_newer_than

        logger.info(f"Running account scraper for {account_username} with limit {results_limit}")
        run = self.client.actor(actor_id).call(run_input=run_input)

        if not run:
            raise RuntimeError(f"Actor run returned None for account: {account_username}")

        run_status = run.get("status")
        if run_status != "SUCCEEDED":
            raise RuntimeError(f"Apify actor run failed for {account_username} with status: {run_status}")

        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            raise RuntimeError(f"No defaultDatasetId in run output for {account_username}")

        dataset_items = self.client.dataset(dataset_id).list_items().items
        posts: List[RawPost] = []
        
        cutoff_dt: Optional[datetime] = None
        if only_posts_newer_than:
            cutoff_dt = parse_iso_timestamp(only_posts_newer_than)

        for item in dataset_items:
            norm = normalize_account_post(item, account_username)
            if norm:
                if cutoff_dt and norm.posted_at and norm.posted_at < cutoff_dt:
                    continue
                posts.append(norm)

        return posts

    def scrape_keyword_posts(
        self,
        actor_id: str,
        keyword: str,
        max_posts: int,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[RawPost]:
        """
        Runs seemuapps/instagram-keyword-posts-scraper for a single keyword.
        Verifies run['status'] == 'SUCCEEDED' (Instruction 15).

        Verified actor inputs (apify.com/seemuapps/instagram-keyword-posts-scraper):
        `keywords`, `maxPostsPerKeyword`, `dateFrom`, `dateTo` and `pageId`.
        `pageId` must come from the run's key-value store (`NEXT_PAGE_ID`), which this
        wrapper does not read, so pagination is done by narrowing the date window in
        `compmon.pipeline` instead.
        """
        if not self.client:
            raise RuntimeError("apify-client library is not installed")

        run_input = {
            "keywords": [keyword],
            "maxPostsPerKeyword": max_posts,
            "postType": "all",
        }
        if date_from:
            run_input["dateFrom"] = date_from
        if date_to:
            run_input["dateTo"] = date_to

        logger.info(f"Running keyword scraper for '{keyword}' with limit {max_posts}")
        run = self.client.actor(actor_id).call(run_input=run_input)

        if not run:
            raise RuntimeError(f"Actor run returned None for keyword: {keyword}")

        run_status = run.get("status")
        if run_status != "SUCCEEDED":
            raise RuntimeError(f"Apify actor run failed for keyword '{keyword}' with status: {run_status}")

        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            raise RuntimeError(f"No defaultDatasetId in run output for keyword '{keyword}'")

        dataset_items = self.client.dataset(dataset_id).list_items().items
        posts: List[RawPost] = []

        cutoff_dt: Optional[datetime] = None
        if date_from:
            cutoff_dt = parse_iso_timestamp(date_from)

        for item in dataset_items:
            norm = normalize_keyword_post(item, keyword)
            if norm:
                if cutoff_dt and norm.posted_at and norm.posted_at < cutoff_dt:
                    continue
                posts.append(norm)

        return posts
