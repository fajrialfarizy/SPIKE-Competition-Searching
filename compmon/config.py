"""
compmon.config
Reads, validates, and parses configuration values from Google Sheets and Run State.
Validates ranges, positive limits, threshold comparisons, and bans commas in keywords (Instruction 18).
"""

from typing import Dict, Any, List
from zoneinfo import ZoneInfo
from dataclasses import dataclass


DEFAULT_CONFIG: Dict[str, Any] = {
    "apify_actor_accounts": "apify/instagram-scraper",
    "apify_actor_keyword": "seemuapps/instagram-keyword-posts-scraper",
    "gemini_model": "gemini-3.5-flash-lite",
    "max_new_posts_per_run": 100,
    "max_results_per_input": 20,
    # Ceiling for the pagination loop (Task 23).
    # The pipeline increases resultsLimit in multiples of max_results_per_input up to this ceiling.
    "max_results_ceiling_per_source": 200,
    # First resultsLimit of the account pagination loop (Task 23 keeps the escalation).
    "account_start_limit_initial": 20,
    "account_start_limit_routine": 20,
    "gemini_delay_seconds": 6,
    "gemini_max_retries": 3,
    "max_failed_run_attempts": 3,
    "batch_size": 10,
    "initial_days_accounts": 60,
    "initial_days_core_keywords": 30,
    "initial_days_discovery_keywords": 30,
    "min_reviewed_for_priority": 10,
    "threshold_high": 70,
    "threshold_medium": 40,
    "timezone": "Asia/Jakarta",
}


@dataclass
class AppConfig:
    apify_actor_accounts: str
    apify_actor_keyword: str
    gemini_model: str
    max_new_posts_per_run: int
    max_results_per_input: int
    max_results_ceiling_per_source: int
    account_start_limit_initial: int
    account_start_limit_routine: int
    gemini_delay_seconds: int
    gemini_max_retries: int
    max_failed_run_attempts: int
    batch_size: int
    initial_days_accounts: int
    initial_days_core_keywords: int
    initial_days_discovery_keywords: int
    min_reviewed_for_priority: int
    threshold_high: int
    threshold_medium: int
    timezone: str


def validate_keyword_string(keyword: str):
    """Rejects keywords containing commas (Instruction 18)."""
    if "," in keyword:
        raise ValueError(f"Keyword tidak boleh mengandung tanda koma: '{keyword}'")


def validate_and_parse_config(raw_config: Dict[str, Any]) -> AppConfig:
    """
    Validates types and ranges of configuration values (Instruction 18):
    - max_results_per_input > 0
    - gemini_max_retries >= 1
    - threshold_high > threshold_medium
    - batch_size > 0
    - Positive integers for counts, days, delays
    - Valid timezone name
    """
    merged = {**DEFAULT_CONFIG, **raw_config}

    int_fields = [
        "max_new_posts_per_run", "max_results_per_input", "max_results_ceiling_per_source",
        "account_start_limit_initial", "account_start_limit_routine",
        "gemini_delay_seconds", "gemini_max_retries", "max_failed_run_attempts", "batch_size",
        "initial_days_accounts", "initial_days_core_keywords", "initial_days_discovery_keywords",
        "min_reviewed_for_priority", "threshold_high", "threshold_medium"
    ]
    parsed_ints = {}
    for f in int_fields:
        val = merged.get(f)
        try:
            int_val = int(val)
            parsed_ints[f] = int_val
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid integer for config field '{f}': {val}") from e

    # Specific range checks (Instruction 18)
    if parsed_ints["max_results_per_input"] <= 0:
        raise ValueError(f"Config 'max_results_per_input' must be > 0, got {parsed_ints['max_results_per_input']}")

    # max_results_ceiling_per_source must be a positive integer and >= max_results_per_input
    ceiling = parsed_ints["max_results_ceiling_per_source"]
    if ceiling <= 0:
        raise ValueError(
            f"Config 'max_results_ceiling_per_source' must be a positive integer, got {ceiling}"
        )
    if ceiling < parsed_ints["max_results_per_input"]:
        raise ValueError(
            f"Config 'max_results_ceiling_per_source' ({ceiling}) must be >= 'max_results_per_input' "
            f"({parsed_ints['max_results_per_input']})"
        )

    for start_key in ("account_start_limit_initial", "account_start_limit_routine"):
        if parsed_ints[start_key] <= 0:
            raise ValueError(
                f"Config '{start_key}' must be a positive integer, got {parsed_ints[start_key]}"
            )

    if parsed_ints["max_failed_run_attempts"] <= 0:
        raise ValueError(
            f"Config 'max_failed_run_attempts' must be a positive integer, got "
            f"{parsed_ints['max_failed_run_attempts']}"
        )

    if parsed_ints["max_new_posts_per_run"] <= 0:
        raise ValueError(
            f"Config 'max_new_posts_per_run' must be > 0, got {parsed_ints['max_new_posts_per_run']}"
        )

    if parsed_ints["gemini_delay_seconds"] < 0:
        raise ValueError(
            f"Config 'gemini_delay_seconds' must be >= 0, got {parsed_ints['gemini_delay_seconds']}"
        )

    if parsed_ints["min_reviewed_for_priority"] <= 0:
        raise ValueError(
            f"Config 'min_reviewed_for_priority' must be > 0, got "
            f"{parsed_ints['min_reviewed_for_priority']}"
        )

    if parsed_ints["gemini_max_retries"] < 1:
        raise ValueError(f"Config 'gemini_max_retries' must be >= 1, got {parsed_ints['gemini_max_retries']}")

    if parsed_ints["batch_size"] <= 0:
        raise ValueError(f"Config 'batch_size' must be > 0, got {parsed_ints['batch_size']}")

    if parsed_ints["threshold_high"] <= parsed_ints["threshold_medium"]:
        raise ValueError(
            f"Config 'threshold_high' ({parsed_ints['threshold_high']}) must be strictly greater than "
            f"'threshold_medium' ({parsed_ints['threshold_medium']})"
        )

    # Validate timezone
    tz_str = str(merged.get("timezone", "Asia/Jakarta")).strip()
    try:
        ZoneInfo(tz_str)
    except Exception as e:
        raise ValueError(f"Invalid timezone in config: '{tz_str}'") from e

    return AppConfig(
        apify_actor_accounts=str(merged.get("apify_actor_accounts", DEFAULT_CONFIG["apify_actor_accounts"])).strip(),
        apify_actor_keyword=str(merged.get("apify_actor_keyword", DEFAULT_CONFIG["apify_actor_keyword"])).strip(),
        gemini_model=str(merged.get("gemini_model", DEFAULT_CONFIG["gemini_model"])).strip(),
        max_new_posts_per_run=parsed_ints["max_new_posts_per_run"],
        max_results_per_input=parsed_ints["max_results_per_input"],
        max_results_ceiling_per_source=ceiling,
        # A start limit above the ceiling is clamped, never rejected (Fix 6B).
        account_start_limit_initial=min(parsed_ints["account_start_limit_initial"], ceiling),
        account_start_limit_routine=min(parsed_ints["account_start_limit_routine"], ceiling),
        gemini_delay_seconds=parsed_ints["gemini_delay_seconds"],
        gemini_max_retries=parsed_ints["gemini_max_retries"],
        max_failed_run_attempts=parsed_ints["max_failed_run_attempts"],
        batch_size=parsed_ints["batch_size"],
        initial_days_accounts=parsed_ints["initial_days_accounts"],
        initial_days_core_keywords=parsed_ints["initial_days_core_keywords"],
        initial_days_discovery_keywords=parsed_ints["initial_days_discovery_keywords"],
        min_reviewed_for_priority=parsed_ints["min_reviewed_for_priority"],
        threshold_high=parsed_ints["threshold_high"],
        threshold_medium=parsed_ints["threshold_medium"],
        timezone=tz_str,
    )
