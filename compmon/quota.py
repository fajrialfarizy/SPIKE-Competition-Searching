"""
compmon.quota
Checks Apify credits and limits, Gemini quotas, and monitors post budgets per execution run.
Handles safe termination on quota exhaustion (Instruction 18).
"""

import logging
from typing import Optional, Dict, Any, Set

logger = logging.getLogger(__name__)


class ExecutionSafetyTracker:
    """Tracks new post count and API budgets during a single notebook execution run."""

    def __init__(self, max_new_posts_per_run: int = 100):
        self.max_new_posts = max_new_posts_per_run
        self.new_posts_collected = 0
        self.posts_processed = 0
        self.posts_failed = 0
        # Task 24: the cap counts genuinely new Post IDs only, so the same post discovered
        # by two sources in one run must not be counted twice.
        self.counted_post_ids: Set[str] = set()

    def can_accept_more_posts(self) -> bool:
        """Returns True if new posts collected is below the maximum allowed per run."""
        return self.new_posts_collected < self.max_new_posts

    def record_new_posts(self, count: int) -> int:
        """
        Records a number of new posts and returns how many fit under the quota.

        Prefer `try_accept_new_post(post_id, known_post_ids)` in real scraping code: it
        counts genuinely new Post IDs only, so a post found by two sources is counted once
        (Task 24).
        """
        available = max(0, self.max_new_posts - self.new_posts_collected)
        accepted = min(count, available)
        self.new_posts_collected += accepted
        return accepted

    def try_accept_new_post(self, post_id: str, known_post_ids: Optional[set] = None) -> bool:
        """
        Decides whether a scraped post may be collected under the per-run cap (Task 24).
        A post that is already in System Log (or was already counted in this run) costs no
        quota and is always accepted. A genuinely new Post ID is accepted only while the
        budget lasts; otherwise it is left for the next run via `pending_ranges`.
        """
        pid = str(post_id).strip()
        if not pid:
            return False
        if known_post_ids is not None and pid in known_post_ids:
            return True
        if pid in self.counted_post_ids:
            return True
        if self.can_accept_more_posts():
            self.counted_post_ids.add(pid)
            self.new_posts_collected += 1
            return True
        return False

    def is_post_limit_reached(self) -> bool:
        """Returns True if the max new posts limit has been reached."""
        return self.new_posts_collected >= self.max_new_posts


def check_apify_quota_available(apify_client: Any) -> bool:
    """
    Checks Apify account user limits/credits via ApifyClient user().get() if available (Instruction 18).
    Returns False if monthly credit limit is exhausted.
    """
    if not apify_client:
        return True
    try:
        if hasattr(apify_client, "user"):
            user_info = apify_client.user().get()
            if isinstance(user_info, dict):
                # Check limits if returned by API
                limits = user_info.get("limits", {})
                if limits.get("monthlyUsageUsd", 0) >= limits.get("monthlyLimitUsd", 9999):
                    logger.error("Apify monthly compute quota limit exhausted!")
                    return False
        return True
    except Exception as e:
        logger.warning(f"Unable to read Apify user quota (proceeding with caution): {e}")
        return True


def is_gemini_quota_exhausted_error(error: Exception) -> bool:
    """
    Detects if a Gemini error represents quota exhaustion / rate exhaustion (Instruction 18).
    """
    err_str = str(error).lower()
    return (
        "resource_exhausted" in err_str or
        "quota exceeded" in err_str or
        "daily quota" in err_str or
        getattr(error, "code", None) == 429
    )
