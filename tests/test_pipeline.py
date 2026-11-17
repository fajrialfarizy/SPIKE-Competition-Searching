"""
tests/test_pipeline.py
Pagination, per-run cap, pending_ranges consumption, ID assignment and Gemini error
behaviour, exercised through the real `CompetitionPipeline` against in-memory fakes.
"""

import json
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from compmon.models import RawPost
from compmon.pipeline import CompetitionPipeline
from compmon.sheets_repo import SheetsRepository
from compmon.checkpoint import parse_pending_ranges
from compmon.eligibility import evaluate_eligibility_and_deadline
from compmon.models import AIExtractionResult, EligibilityStatus
from compmon.gemini_extractor import GeminiExtractor, GeminiPermanentError
from tests.fakes import FakeClient, build_system_log_row

NOW = datetime.now(timezone.utc)

SYS_HEADERS = [
    "Post ID", "URL Postingan", "Source Accounts", "Keyword Source", "Source Type",
    "Waktu Posting", "Caption", "First Seen", "Processing Status", "Processed At",
    "Eligibility Status", "Competition ID", "Error Notes",
]
COMP_HEADERS = [
    "Competition ID", "Nama Lomba", "Jenis Lomba", "Grade Lomba", "Universitas",
    "Penyelenggara", "Skala", "Pelaksanaan Final", "Tempat Final",
    "Tanggal Mulai Registrasi", "Deadline Pendaftaran", "Link Postingan",
    "Link Registrasi", "Link Guidebook", "Status Pemeriksaan",
]


def future_deadline(days: int = 30) -> str:
    return (NOW + timedelta(days=days)).date().isoformat()


def extraction_json(**overrides) -> str:
    data = {
        "is_competition_post": True,
        "competition_type": "Business Plan",
        "nama_lomba": "Lomba Test",
        "universitas": "",
        "penyelenggara": "BEM Kampus",
        "skala": "Nasional",
        "pelaksanaan_final": "Online",
        "tempat_final": None,
        "tanggal_mulai_registrasi": None,
        "deadline_pendaftaran": future_deadline(),
        "registration_closed_explicit": False,
        "link_registrasi": None,
        "link_guidebook": None,
        "eligibility": "Eligible",
    }
    data.update(overrides)
    return json.dumps(data)


class MockGeminiResponse:
    def __init__(self, text: str):
        self.text = text


class MockGeminiModels:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents})
        return self.behavior(model, contents, config)


class MockGenAIClient:
    def __init__(self, behavior):
        self.models = MockGeminiModels(behavior)


class WindowApify:
    """
    Deterministic stand-in for the two Apify actors.

    - accounts honor `resultsLimit` and `onlyPostsNewerThan` (like `apify/instagram-scraper`);
    - keywords honor `maxPostsPerKeyword`, `dateFrom` and `dateTo` (like
      `seemuapps/instagram-keyword-posts-scraper`).
    """

    def __init__(self, account_posts=None, keyword_posts=None):
        self.account_posts = account_posts or {}
        self.keyword_posts = keyword_posts or {}
        self.account_calls = []
        self.keyword_calls = []
        self.client = None

    def scrape_account_posts(self, actor_id, account_username, results_limit, only_posts_newer_than=None):
        self.account_calls.append((account_username, results_limit))
        posts = sorted(self.account_posts.get(account_username, []),
                       key=lambda p: p.posted_at, reverse=True)
        if only_posts_newer_than:
            cutoff = date.fromisoformat(str(only_posts_newer_than)[:10])
            posts = [p for p in posts if p.posted_at.date() >= cutoff]
        return posts[:results_limit]

    def scrape_keyword_posts(self, actor_id, keyword, max_posts, date_from=None, date_to=None):
        self.keyword_calls.append((keyword, max_posts, date_from, date_to))
        posts = self.keyword_posts.get(keyword, [])
        if date_from:
            start = date.fromisoformat(str(date_from)[:10])
            posts = [p for p in posts if p.posted_at.date() >= start]
        if date_to:
            end = date.fromisoformat(str(date_to)[:10])
            posts = [p for p in posts if p.posted_at.date() <= end]
        posts = sorted(posts, key=lambda p: p.posted_at, reverse=True)
        return posts[:max_posts]


def make_post(post_id: str, days_ago: int = 5, caption: str = "Lomba bisnis",
              account: str = None, keyword: str = None) -> RawPost:
    posted = NOW - timedelta(days=days_ago)
    return RawPost(
        post_id=post_id,
        url=f"https://www.instagram.com/p/{post_id}/",
        source_accounts=[account] if account else [],
        keyword_source=[keyword] if keyword else [],
        posted_at=posted.replace(hour=7, minute=0, second=0, microsecond=0),
        caption=caption,
        first_seen=NOW,
    )


class PipelineTestCase(unittest.TestCase):
    def build(self, apify, gemini_behavior, competition_rows=None, system_log_rows=None,
              notebook="INITIAL", row_count=2000):
        rows = {
            "Competition Report": competition_rows,
            "System Log": system_log_rows,
        }
        rows = {k: v for k, v in rows.items() if v is not None}
        client = FakeClient(rows, row_count=row_count)
        repo = SheetsRepository(client, "test_sheet")
        pipeline = CompetitionPipeline(
            sheets_repo=repo,
            apify_token="token",
            gemini_api_key="key",
            notebook_name=notebook,
            apify_client=apify,
            gemini_client=MockGenAIClient(gemini_behavior),
        )
        pipeline.config.gemini_delay_seconds = 0
        return client, repo, pipeline

    @staticmethod
    def success_gemini(model, contents, config):
        return MockGeminiResponse(extraction_json())

    @staticmethod
    def header_system_log():
        return [SYS_HEADERS]

    @staticmethod
    def add_keyword(repo, keyword: str, active: bool = True):
        ws = repo.get_worksheet("Search Keywords")
        ws.data.append([
            keyword, "Core", "Medium", "Aktif" if active else "Nonaktif", "1", "",
            "0", "0", "0", "0", "0.0", "Menunggu Evaluasi",
        ])


class TestAccountPagination(PipelineTestCase):

    def test_account_with_more_than_one_page_reaches_success(self):
        """Task 23: 45 posts with a 20-post step must be fully covered -> Success."""
        posts = [make_post(f"acct_post_{i:03d}", days_ago=i + 1, account="lombabisnis")
                 for i in range(45)]
        apify = WindowApify({"lombabisnis": posts})
        client, repo, pipeline = self.build(apify, self.success_gemini)
        pipeline.config.max_results_per_input = 20
        pipeline.config.max_results_ceiling_per_source = 200

        calls = {"n": 0}

        def unique(model, contents, config):
            calls["n"] += 1
            return MockGeminiResponse(extraction_json(nama_lomba=f"Lomba Acc {calls['n']}"))

        pipeline.gemini = GeminiExtractor("key", client=MockGenAIClient(unique))

        summary = pipeline.run_initial_scraping(selected_accounts=["lombabisnis"], selected_keywords=[])

        self.assertEqual(summary["status"], "Success")
        self.assertEqual(summary["new_posts"], 45)
        self.assertEqual(len(repo.load_system_log_index()), 45)
        self.assertEqual([limit for _, limit in apify.account_calls], [20, 40, 60])

        state = repo.load_run_state()
        self.assertTrue(state["global_checkpoint"])
        self.assertEqual(parse_pending_ranges(state["pending_ranges"]), [])

    def test_ceiling_hit_records_pending_and_marks_partial(self):
        """Task 23: reaching the ceiling leaves the source unfinished and the run Partial."""
        posts = [make_post(f"acct_many_{i:03d}", days_ago=i + 1, account="lombabisnis")
                 for i in range(60)]
        apify = WindowApify({"lombabisnis": posts})
        client, repo, pipeline = self.build(apify, self.success_gemini)
        pipeline.config.max_results_per_input = 20
        pipeline.config.max_results_ceiling_per_source = 40

        summary = pipeline.run_initial_scraping(selected_accounts=["lombabisnis"], selected_keywords=[])

        self.assertEqual(summary["status"], "Partial")
        self.assertEqual(len(repo.load_system_log_index()), 40)
        state = repo.load_run_state()
        pending = parse_pending_ranges(state["pending_ranges"])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].source, "lombabisnis")
        self.assertIn("max_results_ceiling_per_source=40", pending[0].reason)
        self.assertEqual(state["global_checkpoint"], "")


class TestKeywordPagination(PipelineTestCase):

    def test_keyword_date_windows_cover_the_whole_range(self):
        """Task 23: keyword results are paginated by splitting dateFrom/dateTo windows."""
        posts = []
        for day in range(1, 5):
            for n in range(2):
                posts.append(make_post(
                    f"kw_{day}_{n}", days_ago=day + 1, keyword="lomba essay",
                    caption="lomba essay mahasiswa",
                ))
        apify = WindowApify(keyword_posts={"lomba essay": posts})
        client, repo, pipeline = self.build(apify, self.success_gemini)
        self.add_keyword(repo, "lomba essay")
        pipeline.config.max_results_per_input = 2
        pipeline.config.max_results_ceiling_per_source = 10

        summary = pipeline.run_initial_scraping(selected_accounts=[], selected_keywords=["lomba essay"])

        self.assertEqual(summary["status"], "Success")
        self.assertEqual(summary["new_posts"], 8)
        self.assertEqual(len(repo.load_system_log_index()), 8)
        # Every actor run must carry an explicit window, and windows must actually differ.
        windows = {(c[2], c[3]) for c in apify.keyword_calls}
        self.assertGreater(len(windows), 1)
        self.assertTrue(all(c[2] and c[3] for c in apify.keyword_calls))
        for _, _, d_from, d_to in apify.keyword_calls:
            self.assertLessEqual(d_from, d_to)

    def test_single_day_window_at_ceiling_is_pending(self):
        posts = [make_post(f"kw_busy_{i}", days_ago=3, keyword="lomba bmc",
                           caption="lomba bmc") for i in range(10)]
        apify = WindowApify(keyword_posts={"lomba bmc": posts})
        client, repo, pipeline = self.build(apify, self.success_gemini)
        self.add_keyword(repo, "lomba bmc")
        pipeline.config.max_results_per_input = 2
        pipeline.config.max_results_ceiling_per_source = 4

        summary = pipeline.run_initial_scraping(selected_accounts=[], selected_keywords=["lomba bmc"])

        self.assertEqual(summary["status"], "Partial")
        pending = parse_pending_ranges(repo.load_run_state()["pending_ranges"])
        self.assertEqual(len(pending), 1)
        self.assertIn("max_results_ceiling_per_source=4", pending[0].reason)
        self.assertEqual(len(repo.load_system_log_index()), 4)


class TestNewPostCap(PipelineTestCase):

    def test_cap_counts_only_posts_missing_from_system_log(self):
        """Task 24: 90 already-known posts cost no budget; only 10 new ones are stored."""
        existing = [build_system_log_row(f"known_{i:03d}", status="Success") for i in range(90)]
        posts = [make_post(f"known_{i:03d}", days_ago=(i % 50) + 1, account="lombabisnis")
                 for i in range(90)]
        posts += [make_post(f"fresh_{i:02d}", days_ago=i + 1, account="lombabisnis")
                  for i in range(10)]
        apify = WindowApify({"lombabisnis": posts})
        client, repo, pipeline = self.build(
            apify, self.success_gemini,
            system_log_rows=[SYS_HEADERS] + existing,
        )
        pipeline.config.max_new_posts_per_run = 10
        pipeline.config.max_results_per_input = 50
        pipeline.config.max_results_ceiling_per_source = 200

        summary = pipeline.run_initial_scraping(selected_accounts=["lombabisnis"], selected_keywords=[])

        self.assertEqual(summary["new_posts"], 10)
        self.assertEqual(summary["status"], "Success")
        self.assertEqual(len(repo.load_system_log_index()), 100)

    def test_cap_hit_defers_posts_and_forces_partial(self):
        """Task 24/25 + 32: hitting the cap writes pending_ranges and forces Partial."""
        posts = [make_post(f"cap_post_{i:03d}", days_ago=(i % 55) + 1, account="lombabisnis")
                 for i in range(130)]
        apify = WindowApify({"lombabisnis": posts})
        client, repo, pipeline = self.build(apify, self.success_gemini)
        pipeline.config.max_new_posts_per_run = 100
        pipeline.config.max_results_per_input = 20
        pipeline.config.max_results_ceiling_per_source = 200

        summary = pipeline.run_initial_scraping(selected_accounts=["lombabisnis"], selected_keywords=[])

        self.assertEqual(summary["status"], "Partial")
        self.assertEqual(summary["new_posts"], 100)
        self.assertEqual(len(repo.load_system_log_index()), 100)
        state = repo.load_run_state()
        pending = parse_pending_ranges(state["pending_ranges"])
        self.assertTrue(pending)
        self.assertEqual(pending[0].source, "lombabisnis")
        self.assertIn("post baru per run", pending[0].reason)
        self.assertEqual(state["global_checkpoint"], "")

    def test_second_run_finishes_the_deferred_source_and_sets_the_checkpoint(self):
        """Task 25 acceptance: the capped source is retried first and then reaches Success."""
        posts = [make_post(f"two_run_{i:03d}", days_ago=(i % 55) + 1, account="lombabisnis")
                 for i in range(130)]
        apify = WindowApify({"lombabisnis": posts})
        client, repo, pipeline = self.build(apify, self.success_gemini)
        pipeline.config.max_new_posts_per_run = 100
        pipeline.config.max_results_per_input = 20
        pipeline.config.max_results_ceiling_per_source = 200

        first = pipeline.run_initial_scraping(selected_accounts=["lombabisnis"], selected_keywords=[])
        self.assertEqual(first["status"], "Partial")
        self.assertEqual(len(repo.load_system_log_index()), 100)

        second = pipeline.run_initial_scraping(selected_accounts=["lombabisnis"], selected_keywords=[])

        self.assertEqual(second["status"], "Success")
        self.assertEqual(len(repo.load_system_log_index()), 130)
        state = repo.load_run_state()
        self.assertEqual(parse_pending_ranges(state["pending_ranges"]), [])
        self.assertTrue(state["global_checkpoint"])


class TestPendingRanges(PipelineTestCase):

    def test_pending_source_runs_first_and_is_removed_when_finished(self):
        """Task 25: pending keyword is scraped before new keywords and then consumed."""
        cutoff = (NOW - timedelta(days=30)).date().isoformat()
        today = NOW.date().isoformat()
        pending_json = json.dumps([{
            "source_type": "Keyword", "source": "kw_lama", "from": cutoff, "to": today,
            "reason": "Reached max_results_ceiling_per_source=20",
        }])
        apify = WindowApify(keyword_posts={"kw_lama": [], "kw_baru": []})
        client, repo, pipeline = self.build(
            apify, self.success_gemini, system_log_rows=[SYS_HEADERS]
        )
        repo.update_run_state({"pending_ranges": pending_json}, "2026-01-01T00:00:00+07:00")
        # Keyword sheet already has kw_baru as active; add kw_lama as an inactive pending source.
        kw_ws = repo.get_worksheet("Search Keywords")
        kw_ws.data.append(["kw_lama", "Core", "Medium", "Nonaktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"])
        kw_ws.data.append(["kw_baru", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"])

        summary = pipeline.run_initial_scraping(selected_accounts=[], selected_keywords=None)

        scraped_keywords = [call[0] for call in apify.keyword_calls]
        self.assertEqual(scraped_keywords[0], "kw_lama")
        self.assertIn("kw_baru", scraped_keywords)
        self.assertEqual(summary["status"], "Success")
        state = repo.load_run_state()
        self.assertEqual(parse_pending_ranges(state["pending_ranges"]), [])
        self.assertTrue(state["global_checkpoint"])

    def test_unfinished_pending_entry_is_not_duplicated_and_stays_small(self):
        cutoff = (NOW - timedelta(days=30)).date().isoformat()
        today = NOW.date().isoformat()
        long_reason = "x" * 800
        pending_json = json.dumps([{
            "source_type": "Keyword", "source": "lomba essay", "from": cutoff, "to": today,
            "reason": long_reason,
        }])
        posts = [make_post(f"busy_{i}", days_ago=2, keyword="lomba essay") for i in range(10)]
        apify = WindowApify(keyword_posts={"lomba essay": posts})
        client, repo, pipeline = self.build(apify, self.success_gemini)
        self.add_keyword(repo, "lomba essay")
        repo.update_run_state({"pending_ranges": pending_json}, "2026-01-01T00:00:00+07:00")
        pipeline.config.max_results_per_input = 2
        pipeline.config.max_results_ceiling_per_source = 2

        summary = pipeline.run_initial_scraping(selected_accounts=[], selected_keywords=["lomba essay"])

        pending = parse_pending_ranges(repo.load_run_state()["pending_ranges"])
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].source, "lomba essay")
        self.assertLessEqual(len(pending[0].reason), 240)
        self.assertEqual(summary["status"], "Partial")
        self.assertLess(len(repo.load_run_state()["pending_ranges"]), 30000)


class TestCompetitionReportWrites(PipelineTestCase):

    def _single_post_apify(self):
        posts = [make_post("only_post", days_ago=1, account="lombabisnis",
                           caption="Lomba Business Plan Nasional")]
        return WindowApify({"lombabisnis": posts})

    def test_first_assigned_competition_id_is_cmp_000001(self):
        client, repo, pipeline = self.build(self._single_post_apify(), self.success_gemini)
        pipeline.run_initial_scraping(selected_accounts=["lombabisnis"], selected_keywords=[])

        report = repo.get_worksheet("Competition Report").data
        self.assertEqual(len(report), 2)
        self.assertEqual(report[1][0], "CMP-000001")

    def test_id_is_written_to_the_real_row_when_gaps_exist(self):
        """Task 28: matched manual row at sheet row 5 must get its ID in A5, not A3."""
        sys_headers = SYS_HEADERS
        comp_headers = COMP_HEADERS
        existing_comp = ["CMP-000005", "Lomba Lain", "Essay", "", "", "BEM Lain",
                         "Nasional", "Online", "", "", "2026-12-01", "", "", "", "Valid"]
        manual_row = ["", "Manual Case Competition", "Business Case", "", "", "BEM Manual",
                      "Nasional", "Online", "", "", future_deadline(), "", "", "", "Belum Diperiksa"]
        competition_rows = [comp_headers, existing_comp, [""] * 15, [""] * 15, manual_row]

        def gemini(model, contents, config):
            return MockGeminiResponse(extraction_json(
                competition_type="Business Case",
                nama_lomba="Manual Case Competition",
                penyelenggara="BEM Manual",
                deadline_pendaftaran=future_deadline(),
            ))

        client, repo, pipeline = self.build(
            self._single_post_apify(), gemini,
            competition_rows=competition_rows,
            system_log_rows=[sys_headers],
        )
        pipeline.run_initial_scraping(selected_accounts=["lombabisnis"], selected_keywords=[])

        report = repo.get_worksheet("Competition Report").data
        self.assertEqual(report[4][0], "CMP-000006")  # row 5
        self.assertEqual(report[2][0], "")            # row 3 untouched
        self.assertEqual(report[3][0], "")            # row 4 untouched
        self.assertEqual(len(report), 5)              # no new row appended
        sys_index = repo.load_system_log_index()
        self.assertEqual(sys_index["only_post"]["competition_id"], "CMP-000006")

    def test_ineligible_post_is_not_written_to_competition_report(self):
        """Task 32: Ineligible posts stay in System Log only."""
        def gemini(model, contents, config):
            return MockGeminiResponse(extraction_json(
                is_competition_post=False, competition_type="null",
                nama_lomba=None, penyelenggara=None,
            ))

        client, repo, pipeline = self.build(self._single_post_apify(), gemini)
        summary = pipeline.run_initial_scraping(selected_accounts=["lombabisnis"], selected_keywords=[])

        self.assertEqual(len(repo.get_worksheet("Competition Report").data), 1)
        index = repo.load_system_log_index()
        self.assertEqual(index["only_post"]["eligibility_status"], "Ineligible")
        self.assertEqual(index["only_post"]["processing_status"], "Success")
        self.assertEqual(summary["status"], "Success")


class TestGeminiBehaviourInPipeline(PipelineTestCase):

    def _three_posts(self):
        posts = [make_post(f"g_{i}", days_ago=i + 1, account="lombabisnis") for i in range(3)]
        return WindowApify({"lombabisnis": posts})

    def _headers_only(self):
        return [SYS_HEADERS]

    def test_invalid_json_retries_then_marks_failed(self):
        calls = {"n": 0}

        def broken(model, contents, config):
            calls["n"] += 1
            return MockGeminiResponse("THIS IS NOT JSON {")

        client, repo, pipeline = self.build(
            self._three_posts(), broken, system_log_rows=self._headers_only()
        )
        pipeline.config.gemini_max_retries = 3
        summary = pipeline.run_initial_scraping(selected_accounts=["lombabisnis"], selected_keywords=[])

        index = repo.load_system_log_index()
        self.assertEqual(calls["n"], 9)  # 3 retries per post
        self.assertTrue(all(v["processing_status"] == "Failed" for v in index.values()))
        self.assertTrue(all(v["eligibility_status"] != "Ineligible" for v in index.values()))
        self.assertEqual(summary["status"], "Partial")
        self.assertEqual(summary["failed_posts"], 3)

    def test_invalid_api_key_halts_and_leaves_remaining_posts_pending(self):
        calls = {"n": 0}

        def invalid_key(model, contents, config):
            calls["n"] += 1
            if calls["n"] == 1:
                return MockGeminiResponse(extraction_json(nama_lomba="Lomba Satu"))
            raise Exception("API_KEY_INVALID: API key not valid")

        client, repo, pipeline = self.build(
            self._three_posts(), invalid_key, system_log_rows=self._headers_only()
        )
        summary = pipeline.run_initial_scraping(selected_accounts=["lombabisnis"], selected_keywords=[])

        index = repo.load_system_log_index()
        statuses = sorted(v["processing_status"] for v in index.values())
        self.assertEqual(statuses, ["Pending", "Pending", "Success"])
        self.assertEqual(calls["n"], 2)
        self.assertEqual(summary["status"], "Partial")
        self.assertEqual(repo.load_run_state()["global_checkpoint"], "")

    def test_gemini_delay_is_applied_between_calls_only(self):
        client, repo, pipeline = self.build(
            self._three_posts(), self.success_gemini, system_log_rows=self._headers_only()
        )
        pipeline.config.gemini_delay_seconds = 6
        with mock.patch("compmon.pipeline.time.sleep") as sleep_mock:
            pipeline.run_initial_scraping(selected_accounts=["lombabisnis"], selected_keywords=[])
        self.assertEqual([c.args[0] for c in sleep_mock.call_args_list], [6, 6])

    def test_deadline_yesterday_today_tomorrow(self):
        today = date(2026, 11, 17)
        base = dict(is_competition_post=True, competition_type="Essay", nama_lomba="Lomba",
                    penyelenggara="BEM", eligibility="Eligible")
        cases = {
            "2026-11-16": EligibilityStatus.INELIGIBLE,
            "2026-11-17": EligibilityStatus.ELIGIBLE,
            "2026-11-18": EligibilityStatus.ELIGIBLE,
        }
        for deadline, expected in cases.items():
            with self.subTest(deadline=deadline):
                status, _note = evaluate_eligibility_and_deadline(
                    AIExtractionResult(deadline_pendaftaran=deadline, **base), today
                )
                self.assertEqual(status, expected)

    def test_registration_url_not_in_caption_is_blanked(self):
        def gemini(model, contents, config):
            return MockGeminiResponse(extraction_json(
                link_registrasi="https://example.com/daftar",
                link_guidebook="https://example.com/guidebook",
            ))

        extractor = GeminiExtractor("key", client=MockGenAIClient(gemini))
        result, _notes = extractor.extract_competition_info(
            caption="Lomba essay tanpa link apa pun",
            posted_at_iso="2026-11-10T00:00:00Z",
            today_wib="2026-11-17",
            eligibility_rules=[],
            max_retries=1,
            delay_seconds=0,
        )
        self.assertIsNone(result.link_registrasi)
        self.assertIsNone(result.link_guidebook)

    def test_url_mentioned_in_caption_is_kept(self):
        def gemini(model, contents, config):
            return MockGeminiResponse(extraction_json(
                link_registrasi="https://example.com/daftar",
            ))

        extractor = GeminiExtractor("key", client=MockGenAIClient(gemini))
        result, _notes = extractor.extract_competition_info(
            caption="Daftar di https://example.com/daftar sekarang",
            posted_at_iso="2026-11-10T00:00:00Z",
            today_wib="2026-11-17",
            eligibility_rules=[],
            max_retries=1,
            delay_seconds=0,
        )
        self.assertEqual(result.link_registrasi, "https://example.com/daftar")


class TestGeminiQuotaClassification(unittest.TestCase):

    @staticmethod
    def _extractor(behavior):
        return GeminiExtractor("key", client=MockGenAIClient(behavior))

    def _call(self, extractor, max_retries=3):
        return extractor.extract_competition_info(
            caption="caption",
            posted_at_iso="2026-11-10T00:00:00Z",
            today_wib="2026-11-17",
            eligibility_rules=[],
            max_retries=max_retries,
            delay_seconds=0,
        )

    def test_per_day_quota_metric_is_permanent_and_not_retried(self):
        """A 429 whose quota metric is xxxPerDay can never succeed again today."""
        calls = {"n": 0}

        def behavior(model, contents, config):
            calls["n"] += 1
            raise Exception(
                "429 RESOURCE_EXHAUSTED: Quota exceeded for quota metric "
                "'Generate requests per day' and limit 'Generate requests per day per project' "
                "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier"
            )

        with mock.patch("compmon.gemini_extractor.time.sleep") as sleep_mock:
            with self.assertRaises(GeminiPermanentError):
                self._call(self._extractor(behavior))

        self.assertEqual(calls["n"], 1)
        self.assertEqual(sleep_mock.call_count, 0)

    def test_daily_quota_phrase_is_still_permanent(self):
        calls = {"n": 0}

        def behavior(model, contents, config):
            calls["n"] += 1
            raise Exception("429 RESOURCE_EXHAUSTED: daily quota exceeded for this project")

        with self.assertRaises(GeminiPermanentError):
            self._call(self._extractor(behavior))
        self.assertEqual(calls["n"], 1)

    def test_per_minute_429_keeps_retrying(self):
        calls = {"n": 0}
        per_minute = (
            "429 RESOURCE_EXHAUSTED: Quota exceeded for quota metric "
            "'Generate requests per minute' and limit 'Generate requests per minute per project' "
            "quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
        )

        def behavior(model, contents, config):
            calls["n"] += 1
            if calls["n"] < 3:
                raise Exception(per_minute)
            return MockGeminiResponse(extraction_json())

        with mock.patch("compmon.gemini_extractor.time.sleep") as sleep_mock:
            result, _ = self._call(self._extractor(behavior))

        self.assertEqual(calls["n"], 3)
        self.assertEqual(sleep_mock.call_count, 2)
        self.assertEqual(result.nama_lomba, "Lomba Test")

    def test_per_minute_429_exhausting_retries_is_not_permanent_error(self):
        def behavior(model, contents, config):
            raise Exception("429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerMinutePerProjectPerModel")

        with mock.patch("compmon.gemini_extractor.time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                self._call(self._extractor(behavior), max_retries=2)
        self.assertNotIsInstance(ctx.exception, GeminiPermanentError)
        self.assertIn("failed after 2 attempts", str(ctx.exception))


class TestGeminiBackoff(unittest.TestCase):

    def test_429_uses_exponential_backoff_with_jitter(self):
        calls = {"n": 0}

        def behavior(model, contents, config):
            calls["n"] += 1
            if calls["n"] == 1:
                raise Exception("429 RESOURCE_EXHAUSTED quota")
            return MockGeminiResponse(extraction_json())

        extractor = GeminiExtractor("key", client=MockGenAIClient(behavior))
        with mock.patch("compmon.gemini_extractor.time.sleep") as sleep_mock:
            result, _ = extractor.extract_competition_info(
                caption="caption",
                posted_at_iso="2026-11-10T00:00:00Z",
                today_wib="2026-11-17",
                eligibility_rules=[],
                max_retries=3,
                delay_seconds=6,
            )
        self.assertEqual(calls["n"], 2)
        self.assertEqual(sleep_mock.call_count, 1)
        delay = sleep_mock.call_args.args[0]
        # 6 * 2^0 plus jitter in [0.1, 0.5]
        self.assertGreaterEqual(delay, 6.1)
        self.assertLessEqual(delay, 6.5)
        self.assertEqual(result.nama_lomba, "Lomba Test")


class TestPendingMergeWiring(PipelineTestCase):

    def test_run_without_dropped_entries_is_success(self):
        apify = WindowApify({"lombabisnis": []})
        client, repo, pipeline = self.build(apify, self.success_gemini)
        summary = pipeline.run_initial_scraping(
            selected_accounts=["lombabisnis"], selected_keywords=[]
        )
        self.assertEqual(summary["status"], "Success")

    def test_dropped_pending_entries_force_partial(self):
        """A pending-range entry lost to the size cap must never look like a clean run."""
        from compmon.checkpoint import merge_pending_ranges as real_merge, PendingMergeResult

        apify = WindowApify({"lombabisnis": []})
        client, repo, pipeline = self.build(apify, self.success_gemini)

        def merged_with_loss(existing, new_ranges, completed_keys=None):
            result = real_merge(existing, new_ranges, completed_keys)
            return PendingMergeResult(ranges=result.ranges, dropped_entries=1)

        with mock.patch("compmon.pipeline.merge_pending_ranges", side_effect=merged_with_loss):
            summary = pipeline.run_initial_scraping(
                selected_accounts=["lombabisnis"], selected_keywords=[]
            )

        self.assertEqual(summary["status"], "Partial")
        self.assertEqual(repo.load_run_state()["global_checkpoint"], "")


if __name__ == "__main__":
    unittest.main()
