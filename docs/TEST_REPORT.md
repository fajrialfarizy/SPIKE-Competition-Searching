# TEST REPORT
## Instagram Competition Monitoring & Curation System

This report lists what was actually executed. Only three status labels are used:

- `Passed (automated, mocked)` — executed automatically against production code, with only the external Google Sheets / Apify / Gemini clients replaced by in-memory doubles.
- `Not tested (needs real services)` — requires a live Apify token, Gemini key or a real (test) Google spreadsheet; it was **not** run here.
- `Failed` — the test did not meet its specification.

Command used:

```bash
python -m unittest discover tests -v
python -m unittest templates.test_grade_parity -v
```

Result of the last run:

```
Ran 91 tests in 6.861s
OK

Ran 3 tests in 0.003s
OK (skipped=1)
```

---

## 1. Tasks 23–32 (supervisor defects)

| Task | What was fixed | Proof (test) | Status |
|---|---|---|---|
| 23 | Account scraping escalates `resultsLimit` by `max_results_per_input` up to `max_results_ceiling_per_source`; a source is done only when a run returns fewer rows than requested. Keyword scraping splits `dateFrom`/`dateTo` windows and only escalates `maxPostsPerKeyword` on a saturated single-day window. New `Config` key `max_results_ceiling_per_source` (default 200) with validation. | `test_pipeline.TestAccountPagination.test_account_with_more_than_one_page_reaches_success` (45 posts, step 20 → limits 20/40/60 → `Success`, checkpoint set, no pending) and `test_ceiling_hit_records_pending_and_marks_partial`; `test_pipeline.TestKeywordPagination.test_keyword_date_windows_cover_the_whole_range` and `test_single_day_window_at_ceiling_is_pending`; `test_compmon.TestConfig.test_ceiling_must_be_positive_and_at_least_the_step` | Passed (automated, mocked) |
| 24 | The per-run cap counts only Post IDs absent from System Log, and counts one Post ID once even when two sources find it. Pending sources are scraped before fresh ones, so deferred keywords do not always lose. | `test_pipeline.TestNewPostCap.test_cap_counts_only_posts_missing_from_system_log` (90 known + 10 new, cap 10 → 10 stored, `Success`); `test_compmon.TestExecutionStatusAndQuota.test_cap_counts_unique_new_post_ids_only`; `test_pipeline.TestPendingRanges.test_pending_source_runs_first_and_is_removed_when_finished` | Passed (automated, mocked) |
| 25 | `pending_ranges` is read at start, completed entries removed, duplicates never added (reason refreshed in place), reasons truncated to 240 chars, at most 50 entries, JSON kept far under the 50 000-char cell limit. Status can return to `Success` once nothing is pending. | `test_compmon.TestPendingRanges.test_completed_entries_are_removed_and_duplicates_not_added`, `test_reasons_are_truncated_and_entries_capped`; `test_pipeline.TestPendingRanges.test_unfinished_pending_entry_is_not_duplicated_and_stays_small`; `test_pipeline.TestPendingRanges.test_pending_source_runs_first_and_is_removed_when_finished` | Passed (automated, mocked) |
| 26 | Grade formula rebuilt: patterns come from `FILTER(range, LEN(range)>0)` so empty aliases can no longer create `\b\b`; token normalization instead of regex-escaping; rules are read from the `Grade Rules` sheet ordered by Priority (no `B$4`-style rows), grades looked up by Priority; blank row → `""`, missing required input → `Unknown`; Hybrid counts as Offline; no `OR()` and no self-reference. Python oracle uses the same token matching and the same rule table. | `test_template_builder.TestGradeFormula` (all 8 structural checks); `test_compmon.TestGradingOracle` (including `ui` vs `equity`/`guide`/`quiz` and `BEM UI`); `templates.test_grade_parity.TestOracleMatchesPrdTable` (72 combinations) | Passed (automated, mocked) |
| 26 (live) | Sheet ARRAYFORMULA vs. Python oracle on a real TEST spreadsheet | `templates/test_grade_parity.py::run_grade_parity` | Not tested (needs real services) |
| 27 | Sheets created with 2 000 rows; `sheets_repo` calls `add_rows` before any write outside the grid; template never clears or overwrites existing data and no longer swallows exceptions silently; protected ranges are idempotent, cover the whole sheet and are warning-only (no 403 for a second admin). | `test_sheets_safety.TestSheetsRepositoryAppendSafety.test_explicit_range_write_grows_the_grid_first`; `test_template_builder.TestPopulateTemplate.test_creates_all_sheets_with_2000_rows`, `test_existing_data_is_never_cleared`, `test_custom_d2_formula_is_not_overwritten`, `test_protected_ranges_are_full_sheet_and_not_duplicated`, `test_outdated_protected_range_is_replaced` | Passed (automated, mocked) |
| 28 | `CompetitionRecord.row_idx` carries the real sheet row; ID assignment moved into `sheets_repo.assign_next_competition_id(row_idx)` with a single-cell write, emptiness check and retry. The pipeline never touches a worksheet. | `test_pipeline.TestCompetitionReportWrites.test_id_is_written_to_the_real_row_when_gaps_exist` (manual row at sheet row 5 gets A5, A3/A4 stay empty); `test_sheets_safety...test_assign_next_competition_id_uses_real_sheet_row_when_rows_are_missing` | Passed (automated, mocked) |
| 29 | Off-by-one removed: the first ID is `CMP-000001`. The highest number is re-read from column A immediately before every write, and used IDs are never reused or overwritten. | `test_pipeline.TestCompetitionReportWrites.test_first_assigned_competition_id_is_cmp_000001`; `test_sheets_safety...test_first_competition_id_is_cmp_000001`; `test_compmon.TestMatcher.test_format_competition_id` | Passed (automated, mocked) |
| 30 | `assign_missing_competition_ids` only fills truly empty column-A cells; a non-empty non-`CMP-` value is left alone and logged. | `test_sheets_safety...test_assign_missing_competition_ids_only_fills_truly_empty_cells` (asserts the warning log and that `MANUAL-0001` is preserved) | Passed (automated, mocked) |
| 31 | Thresholds and `min_reviewed_for_priority` come from `Config`; below the minimum the new priority is `None` (existing priority untouched); candidates drop sub-phrases of longer candidates and phrases covered by existing keywords, sorted by frequency; notebook 03 writes metrics, appends candidates as Discovery/Kandidat/Low/1, never changes Active Status, and appends one Execution Log row. | `test_compmon.TestKeywordEvaluation` (thresholds, minimum, sub-phrase filtering); `test_scenarios.TestKeywordEvaluationNotebookFlow.test_evaluation_writes_metrics_priority_and_candidates` (runs `run_keyword_evaluation` end to end on fakes) | Passed (automated, mocked) |
| 32 | Hollow tests replaced: invalid JSON → `Failed` after the configured retries (never `Ineligible`); 429 backoff with jitter measured; invalid API key halts and leaves the rest `Pending`; Sheets write retries then raises; 19-digit Post ID round-trips; `=`-caption written RAW; yesterday/today/tomorrow deadlines; URL not in caption blanked; 6 s delay only between calls; Ineligible not written to Competition Report (through the pipeline); cap hit → `pending_ranges` + `Partial`. Fakes mirror gspread 6.1.4 and reject non-RAW data writes. | `test_pipeline.TestGeminiBehaviourInPipeline`, `TestGeminiBackoff`, `TestNewPostCap.test_cap_hit_defers_posts_and_forces_partial`, `TestNewPostCap.test_second_run_finishes_the_deferred_source_and_sets_the_checkpoint` (two runs: `Partial` then `Success` + checkpoint), `TestCompetitionReportWrites.test_ineligible_post_is_not_written_to_competition_report`; `test_sheets_safety.TestSystemLogWrites` | Passed (automated, mocked) |

---

## 1b. Follow-up fixes

| Item | What was fixed | Proof (test) | Status |
|---|---|---|---|
| Gemini quota classification | A 429 whose quota metric contains `PerDay` (e.g. `GenerateRequestsPerDayPerProjectPerModel-FreeTier`) is now a `GeminiPermanentError` and is never retried; the `daily quota` wording still behaves the same. Per-minute 429s keep the retry/backoff path. | `test_pipeline.TestGeminiQuotaClassification.test_per_day_quota_metric_is_permanent_and_not_retried`, `test_daily_quota_phrase_is_still_permanent`, `test_per_minute_429_keeps_retrying`, `test_per_minute_429_exhausting_retries_is_not_permanent_error` | Passed (automated, mocked) |
| `pending_ranges` collapse + drop flag | Entries are collapsed to one per `(source_type, source)` (earliest `from`, latest `to`, newest reason). If the count/size cap still drops entries, `merge_pending_ranges` returns `dropped_entries` and the pipeline forces `Partial` instead of silently losing them. | `test_compmon.TestPendingRanges.test_entries_are_collapsed_per_source_with_earliest_from_and_latest_to`, `test_repeated_identical_entries_do_not_multiply`, `test_caps_report_dropped_entries_instead_of_silently_losing_them`; `test_pipeline.TestPendingMergeWiring.test_dropped_pending_entries_force_partial` and `test_run_without_dropped_entries_is_success` | Passed (automated, mocked) |

---

## 2. PRD / regression coverage

| Area | Module under test | Proof | Status |
|---|---|---|---|
| Post ID = shortcode, URL validation, UTC timestamps | `validator.py` | `test_compmon.TestValidator` | Passed (automated, mocked) |
| Post dedup + source attribution merge | `dedup.py` | `test_compmon.TestDedup` | Passed (automated, mocked) |
| 4-attribute matching (D2, D3) | `matcher.py` | `test_compmon.TestMatcher` | Passed (automated, mocked) |
| Eligibility hierarchy incl. D4 deadline boundary | `eligibility.py` | `test_compmon.TestEligibility`, `test_pipeline...test_deadline_yesterday_today_tomorrow` | Passed (automated, mocked) |
| Routine guard without checkpoint (D13) | `pipeline.py` | `test_scenarios.TestRoutineScenarios.test_routine_refuses_to_run_without_checkpoint` | Passed (automated, mocked) |
| Frequency scheduling (D6) | `pipeline.py` | `test_scenarios.TestRoutineScenarios.test_keyword_frequency_skips_non_due_keywords` | Passed (automated, mocked) |
| Zero-post Routine still advances counter/checkpoint | `pipeline.py` | `test_scenarios...test_routine_success_with_zero_posts_increments_counter` | Passed (automated, mocked) |
| Execution status rules | `exec_log.py` | `test_compmon.TestExecutionStatusAndQuota.test_success_partial_failed` | Passed (automated, mocked) |
| Config validation | `config.py` | `test_compmon.TestConfig` | Passed (automated, mocked) |
| Template structure | `templates/build_template.py` | `test_template_builder.TestTemplateData` | Passed (automated, mocked) |

---

## 3. Not tested (needs real services)

| Item | Why | How to run |
|---|---|---|
| Live Apify actor runs (`apify/instagram-scraper`, `seemuapps/instagram-keyword-posts-scraper`) | Needs a paid Apify token and network | `notebooks/test_apify_actors_phase0.ipynb` |
| Live Gemini extraction | Needs a Gemini API key / quota | Run notebook 01/02 with Colab Secrets |
| Live Google Sheets writes, protected ranges and permissions | Needs a real spreadsheet and OAuth | Run `templates/build_template.py` + notebook 01 against a TEST spreadsheet |
| Grade formula parity on a real spreadsheet | Needs a real TEST spreadsheet (ARRAYFORMULA evaluation) | `COMPMON_TEST_SPREADSHEET_ID=<test sheet id> python -m unittest templates.test_grade_parity -v` |

Never point these at the production spreadsheet.

---

## 4. Known limitations / notes

- The keyword actor is reset per run (no `pageId` continuation: the actor requires `NEXT_PAGE_ID` from its key-value store, which the wrapper does not read). Pagination therefore uses documented `dateFrom`/`dateTo` windows; verified actor inputs are documented in `compmon/apify_client.py`.
- The keyword actor ranks results by relevance, not by date. Splitting the window narrows the result set but cannot guarantee that every historical post inside the window is returned; the ceiling reason in `pending_ranges` makes such a source visible to the admin instead of silently claiming success.
- The Grade formula reads rules, grades and criteria from `Grade Rules`, but **adding a new rule row requires re-running `populate_template`** (the branch order is generated from Priority). This is documented in the template docstring.
- A run that ends with leftover `Pending`/`Failed` rows from earlier runs is reported `Partial` even though it wrote no new pending range; those rows are retried on the next run.
- `templates/test_grade_parity.py` is outside `tests/`, so `unittest discover tests` does not collect its live test; it is listed separately above.
