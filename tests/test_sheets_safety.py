"""
tests/test_sheets_safety.py
Append safety, row-accurate Competition IDs, grid growth and RAW enforcement.
All tests call production `SheetsRepository` against the gspread-6-shaped fakes.
"""

import unittest
from typing import List
from unittest import mock

from compmon.models import CompetitionRecord, SystemLogRecord
from compmon.sheets_repo import SheetsRepository
from tests.fakes import FakeClient

COMP_HEADERS = [
    "Competition ID", "Nama Lomba", "Jenis Lomba", "Grade Lomba", "Universitas", "Penyelenggara",
    "Skala", "Pelaksanaan Final", "Tempat Final", "Tanggal Mulai Registrasi",
    "Deadline Pendaftaran", "Link Postingan", "Link Registrasi", "Link Guidebook",
    "Status Pemeriksaan",
]
SYS_HEADERS = [
    "Post ID", "URL Postingan", "Source Accounts", "Keyword Source", "Source Type",
    "Waktu Posting", "Caption", "First Seen", "Processing Status", "Processed At",
    "Eligibility Status", "Competition ID", "Error Notes",
]


def _row(comp_id: str, nama: str, **overrides) -> List[str]:
    row = [comp_id, nama, "Business Plan", "", "UI", "BEM UI", "Nasional", "Offline",
           "Depok", "2026-01-01", "2026-12-01", "https://instagram.com/p/x/", "", "", "Belum Diperiksa"]
    for key, value in overrides.items():
        row[int(key)] = value
    return row


class TestSheetsRepositoryAppendSafety(unittest.TestCase):

    def setUp(self):
        self.client = FakeClient({
            "Competition Report": [COMP_HEADERS, _row("CMP-000001", "Initial Comp")],
            "System Log": [SYS_HEADERS],
        })
        self.repo = SheetsRepository(self.client, "test_spreadsheet_id")
        self.comp_ws = self.repo.get_worksheet("Competition Report")

    def test_append_lands_after_bottom_row_and_keeps_column_d(self):
        """Admin filled rows 3-4 as gaps and row 5 manually; new rows go to row 6."""
        for _ in range(2):
            self.comp_ws.data.append([""] * 15)  # rows 3 and 4 empty
        self.comp_ws.data.append(_row("CMP-000005", "Admin Manual Comp", **{"4": "UGM"}))  # row 5

        _, highest, bottom = self.repo.load_all_competition_records()
        self.assertEqual(highest, 5)
        self.assertEqual(bottom, 5)

        new_rec = CompetitionRecord(competition_id="", nama_lomba="New Scraped Comp",
                                    jenis_lomba="Business Case")
        next_free = self.repo.append_competition_records([new_rec], start_row=bottom + 1)

        self.assertEqual(next_free, 7)
        self.assertEqual(new_rec.competition_id, "CMP-000006")
        self.assertEqual(new_rec.row_idx, 6)
        self.assertEqual(self.comp_ws.data[5][0], "CMP-000006")
        self.assertEqual(self.comp_ws.data[5][1], "New Scraped Comp")
        self.assertEqual(self.comp_ws.data[5][3], "")  # Column D never written
        self.assertEqual(self.comp_ws.data[4][0], "CMP-000005")  # admin row untouched
        self.assertEqual(self.comp_ws.value_input_options, {"RAW"})

    def test_first_competition_id_is_cmp_000001(self):
        self.client = FakeClient({"Competition Report": [COMP_HEADERS], "System Log": [SYS_HEADERS]})
        self.repo = SheetsRepository(self.client, "test_spreadsheet_id")
        rec = CompetitionRecord(competition_id="", nama_lomba="First Ever", jenis_lomba="Essay")
        self.repo.append_competition_records([rec])
        self.assertEqual(rec.competition_id, "CMP-000001")
        self.assertEqual(self.repo.get_worksheet("Competition Report").data[1][0], "CMP-000001")

    def test_assign_next_competition_id_uses_real_sheet_row_when_rows_are_missing(self):
        """A manual row at sheet row 7 (with blank rows above) gets its ID in A7 only."""
        ws = self.comp_ws
        ws.data.append([""] * 15)   # row 3
        ws.data.append([""] * 15)   # row 4
        ws.data.append([""] * 15)   # row 5
        ws.data.append([""] * 15)   # row 6
        ws.data.append(_row("", "Manual Row", **{"5": "HIMA"}))  # row 7

        records, _, _ = self.repo.load_all_competition_records()
        manual = [r for r in records if r.nama_lomba == "Manual Row"][0]
        self.assertEqual(manual.row_idx, 7)

        assigned = self.repo.assign_next_competition_id(manual.row_idx)
        self.assertEqual(assigned, "CMP-000002")
        self.assertEqual(ws.data[6][0], "CMP-000002")
        for blank_row in range(2, 6):
            self.assertEqual(ws.data[blank_row][0], "")
        self.assertEqual(ws.data[6][1], "Manual Row")

    def test_assign_next_competition_id_never_overwrites_an_admin_value(self):
        ws = self.comp_ws
        ws.data.append(["MANUAL-77", "Admin Row", "Essay"] + [""] * 12)  # row 3
        with self.assertLogs("compmon.sheets_repo", level="WARNING"):
            assigned = self.repo.assign_next_competition_id(3)
        self.assertIsNone(assigned)
        self.assertEqual(ws.data[2][0], "MANUAL-77")

    def test_assign_missing_competition_ids_only_fills_truly_empty_cells(self):
        """Task 30: a non-CMP admin value is kept and logged, never overwritten."""
        ws = self.comp_ws
        ws.data.append(["MANUAL-0001", "Admin Own ID", "Essay"] + [""] * 12)  # row 3
        ws.data.append(["", "Needs An ID", "Business Case"] + [""] * 12)      # row 4

        with self.assertLogs("compmon.sheets_repo", level="WARNING") as logs:
            assigned = self.repo.assign_missing_competition_ids()

        self.assertEqual(assigned, 1)
        self.assertEqual(ws.data[2][0], "MANUAL-0001")
        self.assertEqual(ws.data[3][0], "CMP-000002")
        self.assertTrue(any("non-CMP" in line for line in logs.output))

    def test_explicit_range_write_grows_the_grid_first(self):
        """Task 27: a 6-row sheet must be grown before writing row 7."""
        self.client = FakeClient({
            "Competition Report": [COMP_HEADERS, _row("CMP-000001", "Row Two")],
            "System Log": [SYS_HEADERS],
        }, row_count=6)
        self.repo = SheetsRepository(self.client, "test_spreadsheet_id")
        ws = self.repo.get_worksheet("Competition Report")

        new_rec = CompetitionRecord(competition_id="", nama_lomba="Beyond Grid", jenis_lomba="Essay")
        self.repo.append_competition_records([new_rec], start_row=7)

        self.assertGreater(ws.added_rows, 0)
        self.assertEqual(ws.data[6][1], "Beyond Grid")
        self.assertEqual(new_rec.competition_id, "CMP-000002")


class TestSystemLogWrites(unittest.TestCase):

    def setUp(self):
        self.client = FakeClient({"System Log": [SYS_HEADERS]})
        self.repo = SheetsRepository(self.client, "test_spreadsheet_id")
        self.ws = self.repo.get_worksheet("System Log")

    def _record(self, post_id: str, caption: str) -> SystemLogRecord:
        return SystemLogRecord(
            post_id=post_id, url=f"https://instagram.com/p/{post_id}/",
            source_accounts="acc", keyword_source="kw", source_type="Account",
            waktu_posting="2026-11-17T00:00:00+07:00", caption=caption,
            first_seen="2026-11-17T00:00:00+07:00", processing_status="Pending",
            processed_at="", eligibility_status="", competition_id="", error_notes="",
        )

    def test_nineteen_digit_post_id_round_trips_as_text_and_raw(self):
        post_id = "3983118388705688735"  # 19 digits: becomes 3.98E+18 without TEXT/RAW
        self.repo.append_raw_posts_to_system_log([self._record(post_id, "normal caption")])

        self.assertEqual(self.ws.data[-1][0], post_id)
        self.assertEqual(self.ws.value_input_options, {"RAW"})

    def test_caption_starting_with_equals_is_written_raw(self):
        caption = "=SUM(1,2) lomba business plan"
        self.repo.append_raw_posts_to_system_log([self._record("abc123", caption)])

        self.assertEqual(self.ws.data[-1][6], caption)
        self.assertEqual(self.ws.last_value_input_option, "RAW")

    def test_failed_write_retries_three_times_then_raises(self):
        self.ws.fail_times = 3
        with self.assertRaises(RuntimeError) as ctx:
            self.repo.update_system_log_status(
                row_idx=2, processing_status="Success", processed_at_iso="",
                eligibility_status="", competition_id="", error_notes="",
            )
        self.assertIn("failed after 3 retries", str(ctx.exception))
        # Nothing was partially written.
        self.assertEqual(self.ws.data, [SYS_HEADERS])

    def test_transient_read_failure_is_retried_before_the_append(self):
        """Reads now go through `_execute_with_retry` too, so one bad read is recovered."""
        self.ws.fail_times = 1
        with mock.patch("compmon.sheets_repo.time.sleep"):
            self.repo.append_raw_posts_to_system_log([self._record("abc123", "caption")])
        self.assertEqual(self.ws.data[-1][0], "abc123")

    def test_duplicate_post_id_is_not_written_twice(self):
        self.repo.append_raw_posts_to_system_log([self._record("dup1", "first")])
        self.repo.append_raw_posts_to_system_log([self._record("dup1", "second")])
        ids = [r[0] for r in self.ws.data[1:]]
        self.assertEqual(ids, ["dup1"])


class TestSheetsRateLimits(unittest.TestCase):
    """Fix 2: worksheet-handle caching and the long quota backoff."""

    def setUp(self):
        self.client = FakeClient({"System Log": [SYS_HEADERS]})
        self.repo = SheetsRepository(self.client, "test_spreadsheet_id")
        self.ws = self.repo.get_worksheet("System Log")

    def test_quota_error_retries_with_15s_and_30s_backoff(self):
        self.ws.fail_times = 2
        self.ws.fail_message = "429 Quota exceeded for quota metric 'Read requests'"

        with mock.patch("compmon.sheets_repo.time.sleep") as sleep_mock:
            index = self.repo.load_system_log_index()

        self.assertEqual(index, {})
        delays = [call.args[0] for call in sleep_mock.call_args_list]
        self.assertEqual(len(delays), 2)
        self.assertGreaterEqual(delays[0], 15.0)
        self.assertLessEqual(delays[0], 16.0)
        self.assertGreaterEqual(delays[1], 30.0)
        self.assertLessEqual(delays[1], 31.0)

    def test_resource_exhausted_text_also_uses_the_long_backoff(self):
        self.ws.fail_times = 1
        self.ws.fail_message = "RESOURCE_EXHAUSTED: Write requests per minute exceeded"

        with mock.patch("compmon.sheets_repo.time.sleep") as sleep_mock:
            self.repo.update_system_log_status(
                row_idx=2, processing_status="Success", processed_at_iso="",
                eligibility_status="", competition_id="", error_notes="",
            )

        self.assertEqual(sleep_mock.call_count, 1)
        self.assertGreaterEqual(sleep_mock.call_args.args[0], 15.0)

    def test_permission_denied_is_permanent_and_not_retried(self):
        self.ws.fail_times = 3
        self.ws.fail_message = "403 PERMISSION_DENIED: no access"

        with mock.patch("compmon.sheets_repo.time.sleep") as sleep_mock:
            with self.assertRaises(Exception):
                self.repo.load_system_log_index()

        self.assertEqual(sleep_mock.call_count, 0)

    def test_worksheet_handles_are_cached(self):
        client = FakeClient({"System Log": [SYS_HEADERS]})
        repo = SheetsRepository(client, "test_spreadsheet_id")
        calls = {"n": 0}
        original = client.spreadsheet.worksheet

        def counting(title):
            calls["n"] += 1
            return original(title)

        client.spreadsheet.worksheet = counting
        for _ in range(5):
            repo.get_worksheet("System Log")
        self.assertEqual(calls["n"], 1)

    def test_append_competition_records_reads_the_sheet_once(self):
        comp_client = FakeClient({
            "Competition Report": [COMP_HEADERS],
            "System Log": [SYS_HEADERS],
        })
        repo = SheetsRepository(comp_client, "test_spreadsheet_id")
        comp_ws = repo.get_worksheet("Competition Report")
        reads_before = comp_ws.api_calls

        rec = CompetitionRecord(competition_id="", nama_lomba="One", jenis_lomba="Essay")
        repo.append_competition_records([rec])

        # 1 read + 1 batch write, instead of read + read + write.
        self.assertEqual(comp_ws.api_calls - reads_before, 2)
        self.assertEqual(rec.competition_id, "CMP-000001")


if __name__ == "__main__":
    unittest.main()
