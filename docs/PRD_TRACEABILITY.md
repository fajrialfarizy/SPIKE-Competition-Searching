# PRD TRACEABILITY MATRIX
## Instagram Competition Monitoring & Curation System

Statuses are exactly one of: `Passed (automated, mocked)`, `Not tested (needs real services)`, `Failed`.
See `docs/TEST_REPORT.md` for the test names behind each row.

| PRD / Requirement | Topic | Implementation | Test status |
|---|---|---|---|
| 1.3 | 5 competition types | `compmon/normalization.py` (`normalize_competition_type`) | Passed (automated, mocked) |
| 1.4 | Eligibility rules | `compmon/eligibility.py` | Passed (automated, mocked) |
| 2.1 | Python + Apify + Gemini + Sheets stack | `compmon/`, `requirements.txt` (lower bounds; only gspread exact) | Passed (automated, mocked) |
| 4.1 | Competition Report: 15 columns, append-only, column D never written, RAW writes | `compmon/sheets_repo.py`, `templates/build_template.py` | Passed (automated, mocked) |
| 4.1 | Grade ARRAYFORMULA (generic rules, non-empty aliases, blank/Unknown, Hybrid=Offline) plus named ranges `TOP_UNIV_PATTERN` / `TOP_ORG_PATTERN` over generated helper cells | `templates/build_template.py` | Passed (automated, mocked) — formula structure and the Python oracle; live sheet evaluation of the formula and named ranges is `Not tested (needs real services)` |
| 4.1 | Status dropdown, Post ID text format, protected ranges (full sheet, warning-only, idempotent) | `templates/build_template.py` | Passed (automated, mocked); live Google API behaviour is `Not tested (needs real services)` |
| 4.2 | System Log: 13 columns, one row per Post ID, RAW, retry de-dup | `compmon/sheets_repo.py` (`append_raw_posts_to_system_log`) | Passed (automated, mocked) |
| 4.3 | Execution Log row per run, EXE-… id, Success/Partial/Failed | `compmon/exec_log.py`, `compmon/pipeline.py` | Passed (automated, mocked) |
| 5.x | Config / Run State / Master data / Rules sheets | `compmon/config.py`, `templates/build_template.py` | Passed (automated, mocked) |
| 6.1 | Initial scraping: source selection, 60/30/30 day ranges, pagination, unfinished ranges | `compmon/pipeline.py` (`run_initial_scraping`) | Passed (automated, mocked); live Apify is `Not tested (needs real services)` |
| 6.2 | Routine scraping: checkpoint guard, due keywords, pending first | `compmon/pipeline.py` (`run_routine_scraping`) | Passed (automated, mocked); live Apify is `Not tested (needs real services)` |
| 6.2 / 6.13 (overridden by D18) | `global_checkpoint` describes scraping coverage, not extraction success; advances on the four D18 facts, value = run start time (UTC) | `compmon/checkpoint.py` (`checkpoint_should_advance`), `compmon/pipeline.py` | Passed (automated, mocked) |
| D17 (Round 2) | Extraction attempt limit (`max_failed_run_attempts`, default 3), dead posts, `[attempts=N]` marker in `Error Notes` | `compmon/retry_policy.py`, `compmon/pipeline.py`, `compmon/config.py`, `templates/build_template.py` | Passed (automated, mocked) |
| 6.5 (revised) | First `resultsLimit` per account is configurable (`account_start_limit_initial` / `account_start_limit_routine`), clamped to the ceiling | `compmon/config.py`, `compmon/pipeline.py` | Passed (automated, mocked) |
| 6.5 (Fix 8) | Cost control: a saturated first run jumps straight to `max_results_ceiling_per_source` (≤2 actor runs per source), and a pending entry whose reason records `ceiling=<value>` is not re-scraped until the ceiling is raised | `compmon/pipeline.py`, `compmon/checkpoint.py` | Passed (automated, mocked); real Apify cost is `Not tested (needs real services)` |
| 6.5 (Fix 3) | Pagination completeness is decided from the RAW actor item count, before date filtering/dedup | `compmon/apify_client.py`, `compmon/pipeline.py` | Passed (automated, mocked) |
| 12 (Fix 2) | Google Sheets read/write quota: worksheet-handle cache, all reads retried, 429/RESOURCE_EXHAUSTED use a 15/30/60 s ladder, one read per Competition Report append | `compmon/sheets_repo.py` | Passed (automated, mocked); real API quota behaviour is `Not tested (needs real services)` |
| 4.1 / 8 (Fix 9) | Formulas are written in the spreadsheet's own locale (`;` on `id_ID`, `,` elsewhere), verified after writing with one retry with the other separator, and D2 / G2:G3 are read with the FORMULA render option so a computed `""` never hides a custom formula; the locale and time zone are only read | `templates/formula_locale.py`, `templates/build_template.py` | Passed (automated, mocked); real `id_ID` formula evaluation is `Not tested (needs real services)` |
| 9.2 (Fix 9) | Admin-entered dates (`01/12/2026`, `1 Desember 2026`) are normalized before comparison with the AI's `YYYY-MM-DD` deadline, so no duplicates and no crash on unparseable text | `compmon/matcher.py` | Passed (automated, mocked) |
| 4.1 / 8 (Fix 1) | Grade formula splits comma-separated aliases on a flattened 1-D list, no `IFERROR` masking errors, unrecognised Skala/Mode -> `Unknown` like the oracle | `templates/build_template.py`, `compmon/grading_support.py` | Passed (automated, mocked); live formula evaluation is `Not tested (needs real services)` |
| 6.5 (Fix 6A) | Apify account-pagination cost experiment (measure only, opt-in) | `notebooks/test_apify_actors_phase0.ipynb` | `Not tested (needs real services)` |
| 6.3–6.4 | Post ID / URL / timestamp validation | `compmon/validator.py` | Passed (automated, mocked) |
| 6.5 | Hard limits: ≤100 new posts per run, configurable first `resultsLimit` then a single jump to the ceiling (max 2 runs per source), sequential Gemini with 6 s delay, ≤3 retries | `compmon/quota.py`, `compmon/pipeline.py`, `compmon/config.py` | Passed (automated, mocked) |
| 7.1–7.3 | Gemini extraction, schema, anti-hallucination URLs, strict dates, permanent-error halt | `compmon/gemini_extractor.py` | Passed (automated, mocked); live Gemini is `Not tested (needs real services)` |
| 7.4–7.5 | Deadline yesterday/today/tomorrow, missing criteria → Unknown | `compmon/eligibility.py` | Passed (automated, mocked) |
| 8.1–8.5 | Grade rules (Advance/Professional/Intermediate/Beginner) | `compmon/grading_support.py`, `templates/build_template.py` | Passed (automated, mocked); live formula parity `Not tested (needs real services)` |
| 9.1 | Post-level de-duplication & attribution merge | `compmon/dedup.py` | Passed (automated, mocked) |
| 9.2 | 4-attribute matching, D2 deadline-diff note, D3 both-empty note | `compmon/matcher.py` | Passed (automated, mocked) |
| 9.3 | Admin rows never overwritten, gap-safe row targeting, ID assignment | `compmon/sheets_repo.py` | Passed (automated, mocked) |
| 10.1 | Candidate keyword discovery (n-grams, sub-phrase removal) | `compmon/keyword_eval.py` | Passed (automated, mocked) |
| 10.2–10.3 | Cumulative relevance evaluation with configurable thresholds | `compmon/keyword_eval.py` | Passed (automated, mocked) |
| 11 | Notebook 03 writes metrics/candidates, one Execution Log row, never activates/deletes | `compmon/keyword_eval.py` (`run_keyword_evaluation`), `notebooks/03_keyword_evaluation.ipynb` | Passed (automated, mocked) |
| 12.1–12.4 | Retry/backoff, pending-range recovery, interrupted-run safety | `compmon/pipeline.py`, `docs/ERROR_HANDLING_RECOVERY.md` | Passed (automated, mocked) |
| 13 | Admin workflow guide | `docs/ADMIN_GUIDE.md` | Documentation only |
| 14 | Credentials from Colab Secrets, never logged | `docs/CREDENTIALS.md`, notebooks | Documentation only; live Colab auth is `Not tested (needs real services)` |
