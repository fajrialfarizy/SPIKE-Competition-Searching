"""
Unit tests for compmon modules: validator, dedup, matcher, eligibility, grading,
keyword evaluation, config and pending-range bookkeeping.
"""

import json
import unittest
from datetime import datetime, timezone, date

from compmon.models import (
    RawPost, SourceType, EligibilityStatus, AIExtractionResult, CompetitionRecord, PendingRange
)
from compmon.validator import (
    extract_shortcode, is_valid_instagram_url, parse_iso_timestamp, sanitize_caption,
    validate_raw_post_fields,
)
from compmon.dedup import deduplicate_post_batch, merge_raw_posts, partition_new_and_existing
from compmon.matcher import (
    are_attributes_equal, are_types_equal, are_dates_equal, match_competition_record,
    format_competition_id,
)
from compmon.eligibility import evaluate_eligibility_and_deadline
from compmon.grading_support import calculate_grade, is_entity_match, DEFAULT_GRADE_RULES
from compmon.keyword_eval import calculate_keyword_metrics, extract_candidate_keywords
from compmon.config import validate_and_parse_config, validate_keyword_string
from compmon.checkpoint import (
    merge_pending_ranges, parse_pending_ranges, serialize_pending_ranges,
    PENDING_RANGES_MAX_CHARS, MAX_REASON_CHARS,
)
from compmon.exec_log import determine_execution_status
from compmon.quota import ExecutionSafetyTracker
from compmon.retry_policy import parse_attempts, set_attempts, is_dead


TOP_UNIVS = [
    ("Universitas Indonesia", ["UI"]),
    ("Universitas Gadjah Mada", ["UGM"]),
    ("Universitas Airlangga", ["UNAIR"]),
    ("Institut Teknologi Bandung", ["ITB"]),
]
TOP_ORGS = [("Unilever", []), ("Gojek", []), ("Huawei", []), ("Pertamina", []), ("Tokopedia", [])]


class TestValidator(unittest.TestCase):

    def test_extract_shortcode(self):
        self.assertEqual(extract_shortcode("https://www.instagram.com/p/DdG4RIxIPyf/"), "DdG4RIxIPyf")
        self.assertEqual(extract_shortcode("https://instagram.com/reel/C123abc_-/"), "C123abc_-")
        self.assertEqual(extract_shortcode("DdG4RIxIPyf"), "DdG4RIxIPyf")
        self.assertIsNone(extract_shortcode("invalid_url"))

    def test_is_valid_instagram_url(self):
        self.assertTrue(is_valid_instagram_url("https://www.instagram.com/p/DdG4RIxIPyf/"))
        self.assertFalse(is_valid_instagram_url("https://facebook.com/p/123/"))
        self.assertFalse(is_valid_instagram_url(""))

    def test_parse_iso_timestamp_always_timezone_aware_utc(self):
        dt1 = parse_iso_timestamp("2026-10-06T12:00:00.000Z")
        self.assertEqual(dt1.tzinfo, timezone.utc)
        dt2 = parse_iso_timestamp("2026-11-17")
        self.assertEqual(dt2.tzinfo, timezone.utc)
        self.assertEqual(dt2.day, 17)
        naive = parse_iso_timestamp(datetime(2026, 5, 1, 10, 0, 0))
        self.assertEqual(naive.tzinfo, timezone.utc)

    def test_sanitize_caption(self):
        clean, truncated = sanitize_caption("Info lomba business plan 2026")
        self.assertEqual(clean, "Info lomba business plan 2026")
        self.assertFalse(truncated)
        clean_long, truncated_long = sanitize_caption("A" * 50000)
        self.assertEqual(len(clean_long), 45000)
        self.assertTrue(truncated_long)

    def test_validate_raw_post_fields(self):
        ok, _ = validate_raw_post_fields(
            "DdG4RIxIPyf", "https://www.instagram.com/p/DdG4RIxIPyf/", "2026-01-01T00:00:00Z"
        )
        self.assertTrue(ok)
        self.assertFalse(validate_raw_post_fields(
            "", "https://www.instagram.com/p/DdG4RIxIPyf/", "2026-01-01T00:00:00Z")[0])
        self.assertFalse(validate_raw_post_fields(
            "DdG4RIxIPyf", "https://google.com", "2026-01-01T00:00:00Z")[0])


class TestDedup(unittest.TestCase):

    def test_merge_raw_posts(self):
        p1 = RawPost("post_1", "https://www.instagram.com/p/post_1/", source_accounts=["lombabisnis"])
        p2 = RawPost("post_1", "https://www.instagram.com/p/post_1/", keyword_source=["lomba business plan"])
        merged = merge_raw_posts(p1, p2)
        self.assertIn("lombabisnis", merged.source_accounts)
        self.assertIn("lomba business plan", merged.keyword_source)
        self.assertEqual(merged.source_type, SourceType.BOTH)

    def test_deduplicate_post_batch(self):
        res = deduplicate_post_batch([
            RawPost("id_1", "https://instagram.com/p/id_1/", source_accounts=["acc1"]),
            RawPost("id_1", "https://instagram.com/p/id_1/", keyword_source=["kw1"]),
            RawPost("id_2", "https://instagram.com/p/id_2/", source_accounts=["acc2"]),
        ])
        self.assertEqual(len(res), 2)

    def test_partition_merges_attribution_of_known_posts(self):
        existing_index = {"post_dual": {"source_accounts": ["lombabisnis"], "keyword_source": []}}
        incoming = [RawPost("post_dual", "https://instagram.com/p/post_dual/", keyword_source=["lomba bmc"])]
        brand_new, updates = partition_new_and_existing(incoming, existing_index)
        self.assertEqual(brand_new, [])
        self.assertEqual(len(updates), 1)
        _, accs, kws, st = updates[0]
        self.assertIn("lombabisnis", accs)
        self.assertIn("lomba bmc", kws)
        self.assertEqual(st.value, "Account, Keyword")


class TestMatcher(unittest.TestCase):

    def test_matching_exact(self):
        existing = [CompetitionRecord("CMP-000001", "National Business Plan 2026", "Business Plan",
                                      penyelenggara="BEM FEB UI", deadline_pendaftaran="2026-12-01")]
        extracted = AIExtractionResult(nama_lomba="national business plan 2026",
                                       competition_type="Business Plan",
                                       penyelenggara="bem feb ui", deadline_pendaftaran="2026-12-01")
        comp_id, both_empty, _note, unid_idx = match_competition_record(extracted, existing)
        self.assertEqual(comp_id, "CMP-000001")
        self.assertFalse(both_empty)
        self.assertIsNone(unid_idx)

    def test_d3_empty_on_both_sides_is_a_match_with_note(self):
        existing = [CompetitionRecord("CMP-000002", "Essay Nasional", "Essay",
                                      penyelenggara="", deadline_pendaftaran="2026-12-01")]
        extracted = AIExtractionResult(nama_lomba="Essay Nasional", competition_type="Essay",
                                       penyelenggara="", deadline_pendaftaran="2026-12-01")
        comp_id, both_empty, note, _ = match_competition_record(extracted, existing)
        self.assertEqual(comp_id, "CMP-000002")
        self.assertTrue(both_empty)
        self.assertIn("Penyelenggara", note)

    def test_d2_different_deadline_creates_new_row_note(self):
        existing = [CompetitionRecord("CMP-000003", "Lomba BMC Mahasiswa", "Business Model Canvas",
                                      penyelenggara="HIMA S1 Telkom", deadline_pendaftaran="2026-10-10")]
        extracted = AIExtractionResult(nama_lomba="lomba bmc mahasiswa", competition_type="bmc",
                                       penyelenggara="hima s1 telkom", deadline_pendaftaran="2026-10-25")
        comp_id, _, note, _ = match_competition_record(extracted, existing)
        self.assertIsNone(comp_id)
        self.assertIn("mirip ID CMP-000003, deadline beda", note)

    def test_format_competition_id(self):
        self.assertEqual(format_competition_id(0), "CMP-000001")
        self.assertEqual(format_competition_id(42), "CMP-000043")

    def test_sheet_formatted_dates_equal_the_iso_deadline(self):
        """Fix 9: an id_ID sheet returns 01/12/2026 where the AI returns 2026-12-01."""
        for sheet_value in ("01/12/2026", "1/12/2026", "1 Desember 2026",
                            "2026-12-01", "01/12/2026 00:00:00"):
            with self.subTest(sheet_value=sheet_value):
                self.assertEqual(are_dates_equal(sheet_value, "2026-12-01"), (True, False))
        self.assertEqual(are_dates_equal("02/12/2026", "2026-12-01"), (False, False))
        self.assertEqual(are_dates_equal("bukan tanggal", "2026-12-01"), (False, False))
        self.assertEqual(are_dates_equal("bukan tanggal", "bukan tanggal"), (True, False))
        self.assertEqual(are_dates_equal("", "2026-12-01"), (False, False))
        self.assertEqual(are_dates_equal("", ""), (True, True))

    def test_sheet_formatted_deadline_does_not_create_a_duplicate(self):
        existing = [CompetitionRecord("CMP-000010", "Lomba Essay Nasional", "Essay",
                                      penyelenggara="BEM FEB UI",
                                      deadline_pendaftaran="01/12/2026")]
        extracted = AIExtractionResult(nama_lomba="Lomba Essay Nasional",
                                       competition_type="Essay",
                                       penyelenggara="BEM FEB UI",
                                       deadline_pendaftaran="2026-12-01")
        comp_id, _both_empty, _note, _idx = match_competition_record(extracted, existing)
        self.assertEqual(comp_id, "CMP-000010")


class TestEligibility(unittest.TestCase):

    def test_deadline_boundaries(self):
        today = date(2026, 11, 17)
        base = dict(is_competition_post=True, competition_type="Essay", nama_lomba="Lomba",
                    penyelenggara="BEM", eligibility="Eligible")
        for deadline, expected in {
            "2026-11-16": EligibilityStatus.INELIGIBLE,
            "2026-11-17": EligibilityStatus.ELIGIBLE,
            "2026-11-18": EligibilityStatus.ELIGIBLE,
        }.items():
            with self.subTest(deadline=deadline):
                status, _ = evaluate_eligibility_and_deadline(
                    AIExtractionResult(deadline_pendaftaran=deadline, **base), today)
                self.assertEqual(status, expected)

    def test_non_competition_is_ineligible(self):
        status, _ = evaluate_eligibility_and_deadline(
            AIExtractionResult(is_competition_post=False), date(2026, 11, 17))
        self.assertEqual(status, EligibilityStatus.INELIGIBLE)

    def test_missing_criteria_is_unknown(self):
        ext = AIExtractionResult(is_competition_post=True, competition_type="Essay",
                                 nama_lomba="Lomba Esai", penyelenggara="Komunitas",
                                 deadline_pendaftaran=None, eligibility="Unknown")
        status, _ = evaluate_eligibility_and_deadline(ext, date(2026, 11, 17))
        self.assertEqual(status, EligibilityStatus.UNKNOWN)


class TestGradingOracle(unittest.TestCase):

    def test_word_boundaries_reject_false_positives(self):
        self.assertFalse(is_entity_match("Private Equity Forum", "Universitas Indonesia", ["ui"]))
        self.assertFalse(is_entity_match("National Guidebook Conference", "Universitas Indonesia", ["ui"]))
        self.assertFalse(is_entity_match("Quiz Bowl 2026", "Universitas Indonesia", ["ui"]))
        self.assertTrue(is_entity_match("BEM UI Fest", "Universitas Indonesia", ["ui"]))
        self.assertTrue(is_entity_match("Kompetisi di UI Depok", "Universitas Indonesia", ["ui"]))

    def test_empty_alias_never_matches(self):
        self.assertFalse(is_entity_match("Komunitas Sains Luar", "Unilever", []))
        self.assertFalse(is_entity_match("Komunitas Sains Luar", "Unilever", ["", "  "]))

    def test_punctuation_and_phrase_matching(self):
        self.assertTrue(is_entity_match("PT. Pertamina (Persero)", "Pertamina", []))
        self.assertTrue(is_entity_match("Fakultas Ekonomi Universitas Indonesia",
                                        "Universitas Indonesia", []))

    def test_all_rule_combinations(self):
        cases = [
            ("BEM UI", "Internasional", "Online", "Universitas Indonesia", "Advance"),
            ("HIMA UGM", "Nasional", "Offline", "Universitas Gadjah Mada", "Advance"),
            ("BEM ITB", "Nasional", "Hybrid", "Institut Teknologi Bandung", "Advance"),
            ("PT Pertamina", "Nasional", "Offline", "", "Advance"),
            ("Huawei Technologies", "Internasional", "Online", "", "Advance"),
            ("Fakultas Ekonomi UI", "Nasional", "Online", "Universitas Indonesia", "Professional"),
            ("Tokopedia", "Nasional", "Online", "", "Professional"),
            ("Komunitas Sains Luar", "Internasional", "Online", "Kampus X", "Intermediate"),
            ("Himpunan Kampus Daerah", "Nasional", "Online", "Universitas Lokal", "Beginner"),
            ("", "Nasional", "Online", "UI", "Unknown"),
            ("BEM UI", "", "Online", "UI", "Unknown"),
            ("BEM UI", "Nasional", "", "UI", "Unknown"),
        ]
        for peny, skala, mode, univ, expected in cases:
            with self.subTest(case=(peny, skala, mode)):
                self.assertEqual(
                    calculate_grade(peny, skala, mode, univ, TOP_UNIVS, TOP_ORGS), expected)

    def test_grade_rules_are_data_driven(self):
        rules = [dict(DEFAULT_GRADE_RULES[0], Grade="Elite")]
        self.assertEqual(
            calculate_grade("BEM UI", "Internasional", "Online", "UI", TOP_UNIVS, TOP_ORGS,
                            grade_rules=rules + [dict(DEFAULT_GRADE_RULES[7])]),
            "Elite",
        )


class TestKeywordEvaluation(unittest.TestCase):

    def _rows(self, n, keyword="lomba bmc", status="Valid"):
        return [
            {"processing_status": "Success", "keyword_source": keyword,
             "eligibility_status": "Eligible", "competition_id": f"CMP-{i}"}
            for i in range(n)
        ]

    def test_below_minimum_reviewed_keeps_priority_none(self):
        comp = {f"CMP-{i}": "Valid" for i in range(5)}
        metrics = calculate_keyword_metrics(self._rows(5), comp, ["lomba bmc"])
        self.assertIsNone(metrics["lomba bmc"]["new_priority"])
        self.assertEqual(metrics["lomba bmc"]["evaluation_status"], "Menunggu Evaluasi")

    def test_thresholds_come_from_configuration(self):
        comp = {f"CMP-{i}": "Valid" for i in range(10)}
        # 10/10 valid = 100%; with threshold_high=101 nothing can be High.
        metrics = calculate_keyword_metrics(self._rows(10), comp, ["lomba bmc"],
                                            threshold_high=101, threshold_medium=40,
                                            min_reviewed=10)
        self.assertEqual(metrics["lomba bmc"]["new_priority"], "Medium")
        metrics2 = calculate_keyword_metrics(self._rows(10), comp, ["lomba bmc"],
                                             threshold_high=90, threshold_medium=40,
                                             min_reviewed=10)
        self.assertEqual(metrics2["lomba bmc"]["new_priority"], "High")

    def test_min_reviewed_is_configurable(self):
        comp = {f"CMP-{i}": "Valid" for i in range(5)}
        metrics = calculate_keyword_metrics(self._rows(5), comp, ["lomba bmc"],
                                            threshold_high=70, threshold_medium=40,
                                            min_reviewed=5)
        self.assertEqual(metrics["lomba bmc"]["new_priority"], "High")
        self.assertEqual(metrics["lomba bmc"]["evaluation_status"], "Sudah Dievaluasi")

    def test_invalid_posts_are_counted_as_invalid(self):
        rows = [{"processing_status": "Success", "keyword_source": "kw",
                 "eligibility_status": "Ineligible", "competition_id": ""} for _ in range(4)]
        metrics = calculate_keyword_metrics(rows, {}, ["kw"], min_reviewed=4)
        self.assertEqual(metrics["kw"]["invalid_count"], 4)
        self.assertEqual(metrics["kw"]["relevance_rate"], 0.0)
        self.assertEqual(metrics["kw"]["new_priority"], "Low")

    def test_candidates_drop_subphrases_and_existing_keywords(self):
        captions = [
            "lomba business model canvas competition mahasiswa",
            "business model canvas competition nasional",
            "ikut business model canvas competition yuk",
        ]
        candidates = extract_candidate_keywords(
            captions, existing_keywords=[], min_frequency=2)
        self.assertIn("business model canvas", " ".join(candidates))
        # The shorter fragment must not survive next to a longer candidate containing it.
        self.assertNotIn("canvas competition", candidates)
        self.assertNotIn("model canvas", candidates)

        covered = extract_candidate_keywords(
            captions, existing_keywords=["business model canvas competition"], min_frequency=2)
        self.assertEqual(covered, [])


class TestConfig(unittest.TestCase):

    def test_invalid_values_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_and_parse_config({"max_results_per_input": 0})
        with self.assertRaises(ValueError):
            validate_and_parse_config({"gemini_max_retries": 0})
        with self.assertRaises(ValueError):
            validate_and_parse_config({"threshold_high": 50, "threshold_medium": 60})
        with self.assertRaises(ValueError):
            validate_keyword_string("lomba bisnis, mahasiswa")

    def test_ceiling_must_be_positive_and_at_least_the_step(self):
        with self.assertRaises(ValueError):
            validate_and_parse_config({"max_results_ceiling_per_source": 0})
        with self.assertRaises(ValueError):
            validate_and_parse_config({"max_results_ceiling_per_source": -5})
        with self.assertRaises(ValueError):
            validate_and_parse_config({"max_results_per_input": 50,
                                       "max_results_ceiling_per_source": 20})
        cfg = validate_and_parse_config({})
        self.assertEqual(cfg.max_results_ceiling_per_source, 200)
        self.assertEqual(cfg.max_results_per_input, 20)

    def test_account_start_limits_default_validate_and_clamp(self):
        """Fix 6B: default behaviour unchanged (20), invalid values rejected, clamped."""
        cfg = validate_and_parse_config({})
        self.assertEqual(cfg.account_start_limit_initial, 20)
        self.assertEqual(cfg.account_start_limit_routine, 20)

        for bad in (0, -5, "text"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    validate_and_parse_config({"account_start_limit_initial": bad})
                with self.assertRaises(ValueError):
                    validate_and_parse_config({"account_start_limit_routine": bad})

        clamped = validate_and_parse_config({
            "max_results_ceiling_per_source": 60,
            "account_start_limit_initial": 500,
            "account_start_limit_routine": 100,
        })
        self.assertEqual(clamped.account_start_limit_initial, 60)
        self.assertEqual(clamped.account_start_limit_routine, 60)

    def test_max_failed_run_attempts_must_be_a_positive_integer(self):
        self.assertEqual(validate_and_parse_config({}).max_failed_run_attempts, 3)
        for bad in (0, -1, "x"):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    validate_and_parse_config({"max_failed_run_attempts": bad})


class TestRetryPolicy(unittest.TestCase):

    def test_missing_marker_is_zero_attempts(self):
        self.assertEqual(parse_attempts(""), 0)
        self.assertEqual(parse_attempts(None), 0)
        self.assertEqual(parse_attempts("Extraction Error: boom"), 0)

    def test_marker_is_read_from_the_end(self):
        self.assertEqual(parse_attempts("Extraction Error: boom [attempts=2]"), 2)
        # A marker quoted in the middle of a message is not the counter.
        self.assertEqual(parse_attempts("[attempts=9] something [attempts=1]"), 1)
        self.assertEqual(parse_attempts("boom [attempts=2] trailing words"), 0)

    def test_updates_do_not_stack_markers(self):
        once = set_attempts("Extraction Error: boom", 1)
        twice = set_attempts(once, 2)
        thrice = set_attempts(twice, 3)
        self.assertEqual(once, "Extraction Error: boom [attempts=1]")
        self.assertEqual(twice, "Extraction Error: boom [attempts=2]")
        self.assertEqual(thrice, "Extraction Error: boom [attempts=3]")
        self.assertEqual(thrice.count("[attempts="), 1)
        self.assertEqual(parse_attempts(thrice), 3)

    def test_set_attempts_keeps_long_notes_intact(self):
        long_note = "Extraction Error: " + ("z" * 5000)
        updated = set_attempts(long_note, 2)
        self.assertTrue(updated.startswith(long_note))
        self.assertTrue(updated.endswith("[attempts=2]"))
        self.assertEqual(parse_attempts(updated), 2)

    def test_is_dead_uses_status_and_limit(self):
        self.assertFalse(is_dead("Pending", "", 3))
        self.assertFalse(is_dead("Failed", "boom [attempts=2]", 3))
        self.assertTrue(is_dead("Failed", "boom [attempts=3]", 3))
        self.assertTrue(is_dead("Failed", "boom [attempts=4]", 3))


class TestPendingRanges(unittest.TestCase):

    def test_completed_source_is_removed(self):
        existing = [
            PendingRange("Keyword", "kw1", "2026-01-01", "2026-02-01", "old"),
            PendingRange("Account", "acc1", "2026-01-01", "2026-02-01", "old"),
        ]
        result = merge_pending_ranges(
            existing,
            [],
            completed_keys={("Account", "acc1", "2026-01-01", "2026-02-01")},
        )
        self.assertEqual([r.source for r in result.ranges], ["kw1"])
        self.assertFalse(result.dropped)
        self.assertEqual(result.dropped_entries, 0)

    def test_entries_are_collapsed_per_source_with_earliest_from_and_latest_to(self):
        existing = [PendingRange("Keyword", "kw1", "2026-01-10", "2026-01-20", "first")]
        new = [PendingRange("Keyword", "kw1", "2026-01-01", "2026-02-01", "second")]

        result = merge_pending_ranges(existing, new)

        self.assertEqual(len(result.ranges), 1)
        collapsed = result.ranges[0]
        self.assertEqual(collapsed.from_date, "2026-01-01")
        self.assertEqual(collapsed.to_date, "2026-02-01")
        self.assertEqual(collapsed.reason, "second")
        self.assertFalse(result.dropped)

    def test_repeated_identical_entries_do_not_multiply(self):
        same = [PendingRange("Keyword", "kw1", "2026-01-01", "2026-02-01", "r")] * 5
        result = merge_pending_ranges(same, same)
        self.assertEqual(len(result.ranges), 1)
        self.assertFalse(result.dropped)

    def test_reasons_are_truncated(self):
        result = merge_pending_ranges([], [
            PendingRange("Keyword", "kw1", "2026-01-01", "2026-02-01", "y" * 900)
        ])
        self.assertLessEqual(len(result.ranges[0].reason), MAX_REASON_CHARS)
        self.assertLess(len(serialize_pending_ranges(result.ranges)), PENDING_RANGES_MAX_CHARS)

    def test_caps_report_dropped_entries_instead_of_silently_losing_them(self):
        many = [PendingRange("Keyword", f"kw{i}", "2026-01-01", "2026-02-01", "y" * 900)
                for i in range(80)]

        result = merge_pending_ranges([], many)

        self.assertEqual(len(result.ranges), 50)
        self.assertEqual(result.dropped_entries, 30)
        self.assertTrue(result.dropped)
        self.assertTrue(all(len(p.reason) <= MAX_REASON_CHARS for p in result.ranges))
        self.assertLess(len(serialize_pending_ranges(result.ranges)), PENDING_RANGES_MAX_CHARS)

    def test_parse_round_trip(self):
        payload = json.dumps([{"source_type": "Keyword", "source": "kw", "from": "2026-01-01",
                               "to": "2026-02-01", "reason": "r"}])
        parsed = parse_pending_ranges(payload)
        self.assertEqual(parsed[0].source, "kw")
        self.assertEqual(json.loads(serialize_pending_ranges(parsed))[0]["to"], "2026-02-01")
        self.assertEqual(parse_pending_ranges("not json"), [])
        self.assertEqual(parse_pending_ranges(""), [])


class TestExecutionStatusAndQuota(unittest.TestCase):

    def test_success_partial_failed(self):
        self.assertEqual(determine_execution_status(4, 4, 10, 0), "Success")
        self.assertEqual(determine_execution_status(4, 3, 8, 0), "Partial")
        self.assertEqual(determine_execution_status(4, 4, 8, 1), "Partial")
        self.assertEqual(determine_execution_status(4, 4, 8, 0, hit_limit=True), "Partial")
        self.assertEqual(determine_execution_status(4, 0, 0, 0), "Failed")
        self.assertEqual(
            determine_execution_status(4, 4, 8, 0,
                                       pending_ranges=[PendingRange("Account", "a", "x", "y", "r")]),
            "Partial",
        )
        self.assertEqual(determine_execution_status(4, 4, 8, 0, halted=True), "Partial")

    def test_cap_counts_unique_new_post_ids_only(self):
        safety = ExecutionSafetyTracker(max_new_posts_per_run=3)
        known = {"known1", "known2"}
        self.assertTrue(safety.try_accept_new_post("known1", known))
        self.assertEqual(safety.new_posts_collected, 0)
        self.assertTrue(safety.try_accept_new_post("new1", known))
        self.assertTrue(safety.try_accept_new_post("new1", known))  # same post, same run
        self.assertEqual(safety.new_posts_collected, 1)
        self.assertTrue(safety.try_accept_new_post("new2", known))
        self.assertTrue(safety.try_accept_new_post("new3", known))
        self.assertFalse(safety.try_accept_new_post("new4", known))
        self.assertEqual(safety.new_posts_collected, 3)
        self.assertFalse(safety.can_accept_more_posts())


if __name__ == "__main__":
    unittest.main()
