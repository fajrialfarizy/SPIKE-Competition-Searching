"""
tests/test_scenarios.py
End-to-end acceptance scenarios that are not covered by the pagination suite:
Routine guards, zero-post success, and the keyword evaluation write-back used by
notebook 03 (`compmon.keyword_eval.run_keyword_evaluation`).
"""

import unittest

from compmon.pipeline import CompetitionPipeline
from compmon.sheets_repo import SheetsRepository
from compmon.keyword_eval import run_keyword_evaluation
from tests.fakes import FakeClient, build_system_log_row
from tests.test_pipeline import WindowApify, MockGenAIClient, extraction_json


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
KW_HEADERS = [
    "Keyword", "Category", "Priority", "Active Status", "Frequency", "Last Evaluated",
    "Total Found", "Total Reviewed", "Valid Count", "Invalid Count", "Relevance Rate",
    "Evaluation Status",
]


def always_valid(model, contents, config):
    class R:
        text = extraction_json()
    return R()


class TestRoutineScenarios(unittest.TestCase):

    def _repo(self, run_state_rows=None, system_log_rows=None):
        rows = {
            "System Log": system_log_rows or [SYS_HEADERS],
            "Run State": run_state_rows,
        }
        rows = {k: v for k, v in rows.items() if v is not None}
        client = FakeClient(rows)
        return client, SheetsRepository(client, "test_sheet")

    def test_routine_refuses_to_run_without_checkpoint(self):
        """D13: Routine stops and asks for Initial Scraping first."""
        client, repo = self._repo()
        pipeline = CompetitionPipeline(
            sheets_repo=repo, apify_token="t", gemini_api_key="k",
            notebook_name="ROUTINE", apify_client=WindowApify(),
            gemini_client=MockGenAIClient(always_valid),
        )
        result = pipeline.run_routine_scraping()

        self.assertEqual(result["status"], "Error")
        self.assertIn("Initial", result["message"])
        exec_log = repo.get_worksheet("Execution Log").data
        self.assertEqual(len(exec_log), 2)
        self.assertEqual(exec_log[1][4], "Failed")

    def test_routine_success_with_zero_posts_increments_counter(self):
        """A successful Routine run with no new posts still advances the run counter."""
        client, repo = self._repo(run_state_rows=[
            ["Key", "Value", "Updated At"],
            ["global_checkpoint", "2026-11-01T00:00:00+00:00", ""],
            ["pending_ranges", "[]", ""],
            ["keyword_run_counter", "0", ""],
        ])
        pipeline = CompetitionPipeline(
            sheets_repo=repo, apify_token="t", gemini_api_key="k",
            notebook_name="ROUTINE", apify_client=WindowApify(),
            gemini_client=MockGenAIClient(always_valid),
        )
        pipeline.config.gemini_delay_seconds = 0

        summary = pipeline.run_routine_scraping()

        self.assertEqual(summary["status"], "Success")
        self.assertEqual(summary["new_posts"], 0)
        state = repo.load_run_state()
        self.assertEqual(state["keyword_run_counter"], "1")
        self.assertTrue(state["global_checkpoint"])
        self.assertEqual(state["pending_ranges"], "[]")

    def test_keyword_frequency_skips_non_due_keywords(self):
        """D6: a keyword with Frequency 3 only runs when counter % 3 == 0."""
        client, repo = self._repo(run_state_rows=[
            ["Key", "Value", "Updated At"],
            ["global_checkpoint", "2026-11-01T00:00:00+00:00", ""],
            ["pending_ranges", "[]", ""],
            ["keyword_run_counter", "1", ""],
        ])
        kw_ws = repo.get_worksheet("Search Keywords")
        kw_ws.data.append(["kw sering", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"])
        kw_ws.data.append(["kw jarang", "Core", "Medium", "Aktif", "3", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"])
        apify = WindowApify(keyword_posts={"kw sering": [], "kw jarang": []})

        pipeline = CompetitionPipeline(
            sheets_repo=repo, apify_token="t", gemini_api_key="k",
            notebook_name="ROUTINE", apify_client=apify,
            gemini_client=MockGenAIClient(always_valid),
        )
        pipeline.config.gemini_delay_seconds = 0
        pipeline.run_routine_scraping()

        scraped_keywords = [call[0] for call in apify.keyword_calls]
        self.assertIn("kw sering", scraped_keywords)
        self.assertNotIn("kw jarang", scraped_keywords)


class TestKeywordEvaluationNotebookFlow(unittest.TestCase):

    def _repo_with_data(self):
        comp_rows = [COMP_HEADERS]
        sys_rows = [SYS_HEADERS]
        caption = "lomba startup inovasi digital mahasiswa"

        for i in range(12):
            comp_id = f"CMP-{i + 1:06d}"
            comp_rows.append([comp_id, f"BMC {i}", "Business Model Canvas", "Advance", "",
                              "BEM UI", "Nasional", "Online", "", "", "2026-12-01", "", "", "",
                              "Valid"])
            sys_rows.append(build_system_log_row(
                f"bmc_{i}", caption=caption, keyword="lomba bmc", source_type="Keyword",
                status="Success", eligibility="Eligible", competition_id=comp_id,
            ))

        for i in range(3):
            comp_id = f"CMP-{100 + i:06d}"
            comp_rows.append([comp_id, f"Essay {i}", "Essay", "Advance", "", "BEM UI",
                              "Nasional", "Online", "", "", "2026-12-01", "", "", "", "Valid"])
            sys_rows.append(build_system_log_row(
                f"essay_{i}", caption="lomba essay", keyword="essay comp",
                source_type="Keyword", status="Success", eligibility="Eligible",
                competition_id=comp_id,
            ))

        client = FakeClient({"Competition Report": comp_rows, "System Log": sys_rows})
        repo = SheetsRepository(client, "test_sheet")

        kw_ws = repo.get_worksheet("Search Keywords")
        kw_ws.data.append(["lomba bmc", "Core", "Medium", "Aktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"])
        kw_ws.data.append(["essay comp", "Core", "High", "Aktif", "2", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"])
        kw_ws.data.append(["kw nonaktif", "Discovery", "Low", "Nonaktif", "1", "", "0", "0", "0", "0", "0.0", "Menunggu Evaluasi"])
        return client, repo, kw_ws

    def _keyword_row(self, ws, name):
        for idx, row in enumerate(ws.data):
            if row and row[0] == name:
                return idx, row
        raise AssertionError(f"{name} not found")

    def test_evaluation_writes_metrics_priority_and_candidates(self):
        client, repo, kw_ws = self._repo_with_data()
        keyword_row_count = len([r for r in kw_ws.data[1:] if r and r[0].strip()])

        result = run_keyword_evaluation(repo, notebook_name="KEYWORD-EVAL")

        # lomba bmc: 12 reviewed, 100% valid -> High
        _, bmc = self._keyword_row(kw_ws, "lomba bmc")
        self.assertEqual(bmc[2], "High")
        self.assertEqual(bmc[3], "Aktif")           # Active Status untouched
        self.assertEqual(bmc[6], "12")              # Total Found
        self.assertEqual(bmc[7], "12")              # Total Reviewed
        self.assertEqual(bmc[8], "12")              # Valid Count
        self.assertEqual(bmc[10], "100.0")          # Relevance Rate
        self.assertEqual(bmc[11], "Sudah Dievaluasi")
        self.assertTrue(bmc[5])                     # Last Evaluated

        # essay comp: 3 reviewed < minimum -> priority preserved, still pending
        _, essay = self._keyword_row(kw_ws, "essay comp")
        self.assertEqual(essay[2], "High")
        self.assertEqual(essay[7], "3")
        self.assertEqual(essay[11], "Menunggu Evaluasi")

        # Candidates are appended as inactive Discovery rows
        candidates = [row for row in kw_ws.data[1:] if row[3] == "Kandidat"]
        self.assertTrue(candidates)
        for row in candidates:
            self.assertEqual(row[1], "Discovery")
            self.assertEqual(row[2], "Low")
            self.assertEqual(row[4], "1")
        self.assertIn("startup inovasi digital", [row[0] for row in candidates])

        # No keyword was activated or deleted
        names = [row[0] for row in kw_ws.data[1:]]
        self.assertIn("kw nonaktif", names)

        # Exactly one Execution Log row
        exec_log = repo.get_worksheet("Execution Log").data
        self.assertEqual(len(exec_log), 2)
        self.assertEqual(exec_log[1][4], "Success")
        self.assertIn("Evaluated", exec_log[1][10])

        # All writes used RAW
        self.assertEqual(kw_ws.value_input_options, {"RAW"})
        self.assertEqual(result["keywords_written"], keyword_row_count)


if __name__ == "__main__":
    unittest.main()
