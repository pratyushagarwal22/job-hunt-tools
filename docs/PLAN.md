# PLAN.md — Build Plan for job-hunt-tools

Each task ends in a working, testable, committable state.
Tasks are in strict dependency order.

---

## Task 1: Project Setup

**Goal**: Install dependencies, create `.env.example`, build `shared/sheets.py`, and prove Google Sheets auth works by reading the header row of the TEST sheet.

**Files touched**:
- `requirements.txt` (new)
- `.env.example` (new)
- `shared/__init__.py` (new, empty)
- `shared/sheets.py` (new)

**Out of scope**: No job_scanner or email_finder code. No business logic. sheets.py provides only three primitives: `read_rows(sheet_id, tab, range)`, `append_rows(sheet_id, tab, rows)`, and `get_sheet_title(sheet_id)`. No data models.

**Key detail**: `append_rows` must use `valueInputOption=USER_ENTERED` so that `FALSE` becomes a real boolean (required for checkbox columns to render correctly).

**Verification**:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Create .env from .env.example, fill in real values
python -c "
from shared.sheets import read_rows
import os, dotenv
dotenv.load_dotenv()
rows = read_rows(os.environ['SHEET_ID'], 'Jobs', 'A1:M1')
print(rows)
"
```
**Expected output**: A list containing one list — the 13 header column names from the Jobs tab (`['applied', 'match_score', 'jd_source', ...]`). If the sheet is unreachable, a clear error with the HTTP status.

**Checkbox append check** (see Open Risks in DESIGN.md):
```bash
python -c "
from shared.sheets import append_rows, read_rows
import os, dotenv
dotenv.load_dotenv()
sheet_id = os.environ['SHEET_ID']
# Append one test row
append_rows(sheet_id, 'Jobs', [[False, 7.5, 'full', 'Test Title', 'Test Co', 'Remote', '', '', 'test reason', 'https://example.com/test', '', '2026-09-29 21:00', 'test']])
# Read back the last row to check where it landed
rows = read_rows(sheet_id, 'Jobs', 'A1:M1000')
print(f'Total rows (including header): {len(rows)}')
print(f'Last row: {rows[-1]}')
"
```
**Expected**: The test row lands on row 2 (right after the header), not row 1001. The `applied` cell is a real boolean `FALSE` (or unchecked checkbox), not the text string "FALSE". Then delete the test row by hand. If either check fails, stop and report — do not attempt a fix.

**`.env.example` contents**:
```
SHEET_ID=               # Always the TEST sheet ID locally. The real sheet ID exists only in GitHub secrets.
GOOGLE_APPLICATION_CREDENTIALS=service_account.json
SERPER_API_KEY=
ANTHROPIC_API_KEY=
APOLLO_API_KEY=
```
`GOOGLE_APPLICATION_CREDENTIALS` is the same env var name used in Task 8 (GitHub Actions) — no separate path variable.

**Dependencies in requirements.txt** (pin majors, not patches):
- `google-api-python-client`
- `google-auth`
- `anthropic`
- `requests`
- `pydantic`
- `python-dotenv`
- `pyyaml`
- `beautifulsoup4`
- `pytest`

---

## Task 2: search_client.py (Serper)

**Goal**: Build the Serper API client. Confirm that `site:` + `OR` + brackets syntax works through Serper (resolves the open risk from DESIGN.md). Save scrubbed fixtures for tests.

**Files touched**:
- `job_scanner/__init__.py` (new, empty)
- `job_scanner/search_client.py` (new)
- `tests/__init__.py` (new, empty)
- `tests/test_search_client.py` (new)
- `tests/fixtures/serper_site_query.json` (new, scrubbed)
- `tests/fixtures/serper_company_sites_query.json` (new, scrubbed)

**Out of scope**: No URL normalization, no dedup, no title filtering. The client returns raw parsed results (list of dicts with `title`, `link`, `snippet`, `date`, `position`). No pagination logic yet (pagination loop lives in service.py, Task 7) — just a single-page call helper that accepts `tbs`, `gl`, `num`, and `page` params.

**What search_client.py exposes**:
- `search(query: str, tbs: str, gl: str, num: int, page: int) -> list[dict]`
- Each result dict includes `date` (Serper's relative date string, e.g. "21 hours ago", or absolute like "Sep 28, 2026") when available. The raw string is kept as-is here; conversion to `YYYY-MM-DD` happens in `date_utils.py` (Task 4).
- Reads `SERPER_API_KEY` from env.

**Verification** (two real API calls):
```bash
# 1. Site query — one Greenhouse query from queries.yaml
python -c "
from job_scanner.search_client import search
import os, dotenv, json
dotenv.load_dotenv()
results = search(
    query='site:job-boards.greenhouse.io (\"Software Engineer\" OR \"New Grad\")',
    tbs='qdr:d', gl='us', num=10, page=1
)
print(f'Got {len(results)} results')
print(json.dumps(results[0], indent=2) if results else 'No results')
"
# Expected: >0 results. Each result has 'title', 'link', 'snippet'. Links are greenhouse.io URLs.
# If 0 results: might just be a quiet day. Try without tbs to confirm syntax works.

# 2. Company sites query — Amazon
python -c "
from job_scanner.search_client import search
import os, dotenv, json
dotenv.load_dotenv()
results = search(
    query='site:amazon.jobs (\"Software Engineer\" OR \"New Grad\")',
    tbs='qdr:d', gl='us', num=10, page=1
)
print(f'Got {len(results)} results')
print(json.dumps(results[0], indent=2) if results else 'No results')
"
# Expected: >0 results from amazon.jobs.

# 3. Unit tests with saved fixtures
pytest tests/test_search_client.py -v
# Expected: all pass. Tests use fixtures, not live API.
```

**Risk check**: If Serper strips brackets or `OR`, the results will be unrelated. Verify the returned links match the `site:` domain. Log the resolution of this open risk.

**Fixture scrubbing**: Remove any personal data from saved responses. Keep structure, titles, links, snippets intact.

---

## Task 3: ats_client.py (Greenhouse, Lever, Ashby)

**Goal**: Build the ATS client that lists jobs and fetches JDs from Greenhouse, Lever, and Ashby public APIs. Apply lookback filter. Verify the flagged Ashby slugs.

**Files touched**:
- `job_scanner/ats_client.py` (new)
- `tests/test_ats_client.py` (new)
- `tests/fixtures/greenhouse_list.json` (new, scrubbed)
- `tests/fixtures/lever_list.json` (new, scrubbed)
- `tests/fixtures/ashby_list.json` (new, scrubbed)

**Out of scope**: No title filter, no location filter, no dedup, no scoring. Client returns a list of job dicts with normalized fields: `title`, `company`, `location`, `url`, `posted_date`, `jd_text`, `source` ("greenhouse"/"lever"/"ashby").

**What ats_client.py exposes**:
- `list_greenhouse_jobs(slug: str, lookback_days: int) -> list[dict]`
- `list_lever_jobs(slug: str, lookback_days: int) -> list[dict]`
- `list_ashby_jobs(slug: str, lookback_days: int) -> list[dict]`
- No separate `fetch_*_jd` functions. Each list call returns jobs with JD text included (one API call per company, not two).
- Each list function calls the public API, filters by `posted_date >= now - lookback_days`, and returns structured dicts.

**API endpoints** (public, no auth needed):
- Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` — single call returns list with JD HTML in each job's `content` field. Posted date field: `first_published` (preferred — reflects original publish date). Falls back to `updated_at` if `first_published` is missing. Do not use `updated_at` as primary — it changes whenever an old posting is edited, making old jobs look new.
- Lever: `GET https://api.lever.co/v0/postings/{slug}` — list already includes full posting content. Posted date field: `createdAt` (Unix ms).
- Ashby: `GET https://api.ashbyhq.com/posting-api/job-board/{slug}` — public posting API, not the internal GraphQL endpoint. Posted date field: `publishedDate`.

**Verification** (one real call per ATS):
```bash
# 1. Greenhouse — pick one slug from companies.yaml (e.g. anthropic)
python -c "
import requests, json
# First, check raw API response to see which date fields Greenhouse returns
raw = requests.get('https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=true').json()
if raw.get('jobs'):
    job = raw['jobs'][0]
    print(f'first_published: {job.get(\"first_published\", \"NOT PRESENT\")}')
    print(f'updated_at: {job.get(\"updated_at\", \"NOT PRESENT\")}')

# Then verify the normalized output from ats_client
from job_scanner.ats_client import list_greenhouse_jobs
jobs = list_greenhouse_jobs('anthropic', lookback_days=7)
print(f'Greenhouse (anthropic): {len(jobs)} jobs in last 7 days')
if jobs:
    j = jobs[0]
    print(json.dumps(j, indent=2))
    assert 'jd_text' in j and len(j['jd_text']) > 100, 'JD text missing or too short'
"
# Expected: >= 0 jobs. Each has title, company, location, url, posted_date, jd_text.
# JD text comes from the same list call (?content=true), not a second request.
# Raw response check: confirm first_published is present. If missing, updated_at is the fallback.

# 2. Lever — palantir
python -c "
from job_scanner.ats_client import list_lever_jobs
import json
jobs = list_lever_jobs('palantir', lookback_days=7)
print(f'Lever (palantir): {len(jobs)} jobs in last 7 days')
if jobs:
    j = jobs[0]
    print(json.dumps(j, indent=2))
    assert 'jd_text' in j and len(j['jd_text']) > 100, 'JD text missing or too short'
"

# 3. Ashby — cohere (known-good slug)
python -c "
from job_scanner.ats_client import list_ashby_jobs
import json
jobs = list_ashby_jobs('cohere', lookback_days=7)
print(f'Ashby (cohere): {len(jobs)} jobs in last 7 days')
if jobs:
    j = jobs[0]
    print(json.dumps(j, indent=2))
    assert 'jd_text' in j and len(j['jd_text']) > 100, 'JD text missing or too short'
"

# 4. Flagged Ashby slugs — check they resolve (non-empty or graceful empty, not crash)
python -c "
from job_scanner.ats_client import list_ashby_jobs
for slug in ['Valon', 'Aquant', 'Vic.ai', 'arch.co']:
    try:
        jobs = list_ashby_jobs(slug, lookback_days=30)
        print(f'{slug}: {len(jobs)} jobs (OK)')
    except Exception as e:
        print(f'{slug}: ERROR — {e}')
"
# Expected: each slug either returns jobs or empty list. Log which ones need slug corrections.

# 5. Unit tests with saved fixtures
pytest tests/test_ats_client.py -v
# Expected: all pass.
```

---

## Task 4: Pure Logic — URL Normalize, Dedup, Title Filter, Location Filter, Date Utils, Cap

**Goal**: Build all the cheap pre-scoring filters, the URL normalizer, and the date conversion module. 100% unit-tested, no external calls.

**Files touched**:
- `job_scanner/url_utils.py` (new)
- `job_scanner/title_filter.py` (new)
- `job_scanner/location_filter.py` (new)
- `job_scanner/date_utils.py` (new)
- `job_scanner/settings.yaml` (edit — add `timezone` key, value `America/Chicago`)
- `tests/test_url_utils.py` (new)
- `tests/test_title_filter.py` (new)
- `tests/test_location_filter.py` (new)
- `tests/test_date_utils.py` (new)

**Out of scope**: No dedup against the sheet (that requires sheets_repo.py, Task 7). In-memory dedup (within a single run's URL list) is in scope. No scoring, no JD fetching.

**What each module exposes**:

`url_utils.py`:
- `normalize_url(url: str) -> str` — strip `utm_*`, `ref`, `source`, `gh_src`, trailing slashes, fragments. Lowercase the domain. Keep path case. Do NOT strip `gh_jid` — it is the job ID on company-hosted Greenhouse pages; stripping it would collapse different jobs into one URL and break dedup.

`title_filter.py`:
- `passes_title_filter(title: str, include: list[str], exclude: list[str]) -> bool` — case-insensitive, whole-word matching. Must match at least one include keyword and zero exclude keywords.

`location_filter.py`:
- `passes_location_filter(location: str | None, title: str, snippet: str, exclude_keywords: list[str], is_ats: bool) -> bool` — for ATS jobs, check `location` field only. For search jobs, check `title + snippet`. Blank/unknown/Remote passes.

`date_utils.py`:
- `parse_relative_date(date_str: str, run_start: datetime) -> datetime | None` — converts Serper's `date` field to a timezone-aware `datetime`. `run_start` must be tz-aware. Handles: relative strings ("21 hours ago", "3 days ago", "1 week ago", "just now") and absolute strings ("Sep 28, 2026" → midnight in the configured timezone). Returns `None` for unparseable input.
- `parse_ats_date(value: str | int, tz_name: str) -> datetime | None` — accepts ISO 8601 strings and Unix ms ints (Lever's `createdAt` is Unix ms). Returns a tz-aware `datetime` in the configured timezone, or `None`.
- `format_posted_date(dt: datetime, tz_name: str) -> str` — returns `YYYY-MM-DD` in the configured timezone.
- `format_date_found(dt: datetime, tz_name: str) -> str` — returns `YYYY-MM-DD HH:MM` in the configured timezone.
- The `timezone` setting (e.g. `America/Chicago`) is read from `settings.yaml`. Add a `timezone` key to `settings.yaml` in this task.

**Verification**:
```bash
pytest tests/test_url_utils.py tests/test_title_filter.py tests/test_location_filter.py tests/test_date_utils.py -v
```
**Expected**: All tests pass. Test cases cover:
- `url_utils`: utm stripping, ref removal, source removal, gh_src removal, trailing slash, fragment removal, mixed case domain, idempotency. **Critical test**: two URLs differing only by `gh_jid` must stay different after normalization (e.g. `https://company.com/jobs?gh_jid=123` vs `https://company.com/jobs?gh_jid=456` → two distinct normalized URLs).
- `title_filter`: exact match, partial non-match (e.g., "Lead" does not match "Leadership"), case insensitivity, exclude overrides include, no include match → fail.
- `location_filter`: "London" → fail, "Remote" → pass, "New York, NY" → pass, blank → pass, "Bangalore, India" → fail, ATS mode checks only location field, search mode checks title+snippet.
- `date_utils`: all parsers return tz-aware datetimes (not bare dates) so Task 7 can sort on full datetime. "21 hours ago" at a 9:00 AM CT run → tz-aware datetime of the previous day at 6 PM CT. "3 days ago" → 3 days before run_start. "1 week ago" → 7 days before. "just now" → run_start. Absolute date "Sep 28, 2026" → midnight CT on that date. Unparseable string → `None`. `parse_ats_date` with a UTC ISO string → CT datetime. `parse_ats_date` with a Unix ms int (Lever) → CT datetime. `format_posted_date` → `YYYY-MM-DD`.

---

## Task 5: jd_client.py (Plain HTTP JD Fetch)

**Goal**: Build the plain HTTP client that fetches a job description page. Detect "too little text" and signal that the caller should fall back to snippet-based scoring.

**Files touched**:
- `job_scanner/jd_client.py` (new)
- `tests/test_jd_client.py` (new)
- `tests/fixtures/jd_fetch_success.html` (new, scrubbed)
- `tests/fixtures/jd_fetch_empty.html` (new — a minimal JS-only page)

**Out of scope**: No scoring, no sheet writes. Does not handle ATS JDs (those come from ats_client.py). No retry logic — one request with `request_timeout_seconds` from settings.yaml.

**What jd_client.py exposes**:
- `fetch_jd(url: str, timeout: int) -> tuple[str | None, str]` — returns `(text_content, source)` where `source` is `"full"` or `"snippet"`. Uses `beautifulsoup4` with the built-in `html.parser` (no lxml) to strip HTML tags and extract visible text. If fetch fails or returns too little text (< threshold, e.g. 200 chars of visible text after stripping), returns `(None, "snippet")` to signal fallback.

**Verification**:
```bash
# 1. Real fetch — pick a known good Greenhouse job page URL from a recent search result
python -c "
from job_scanner.jd_client import fetch_jd
text, source = fetch_jd('https://job-boards.greenhouse.io/anthropic/jobs/4148108008', timeout=15)
print(f'Source: {source}, Text length: {len(text) if text else 0}')
if text: print(text[:500])
"
# Expected: source="full", text has readable JD content (> 200 chars).

# 2. JS-heavy page — try a page known to return little text
python -c "
from job_scanner.jd_client import fetch_jd
text, source = fetch_jd('https://careers.google.com/jobs/results/', timeout=15)
print(f'Source: {source}, Text length: {len(text) if text else 0}')
"
# Expected: source="snippet" (too little text or fetch blocked).

# 3. Unit tests
pytest tests/test_jd_client.py -v
# Expected: all pass. Tests use fixture HTML files, not live HTTP.
```

---

## Task 6: scoring_client.py (Anthropic Haiku)

**Goal**: Build the Haiku scoring client with pydantic validation. Support both full-JD and snippet modes. Save a scrubbed fixture.

**Files touched**:
- `job_scanner/scoring_client.py` (new)
- `job_scanner/settings.yaml` (edit — add `scoring.model` key)
- `tests/test_scoring_client.py` (new)
- `tests/fixtures/haiku_score_response.json` (new, scrubbed)

**Out of scope**: No pipeline orchestration. No sheet writes. The client takes a JD (or title+snippet), candidate.md content, and profile.yaml content, and returns a validated pydantic model or raises on validation failure.

**Pydantic schema** (from DESIGN.md sheet schema):
```
ScoreResult:
  match_score: float        # 1.0–10.0, validated by pydantic (ge=1.0, le=10.0)
  title: str                # Haiku-extracted (used for search results)
  company: str              # Haiku-extracted (used for search results)
  location: str
  visa_mention: str
  core_requirements: str
  reason: str               # one-line match reason
```

**What scoring_client.py exposes**:
- `score_job(jd_text: str, jd_source: str, candidate_md: str, profile_yaml: str, title_hint: str = "", company_hint: str = "") -> ScoreResult` — one Haiku call with structured JSON output. `title_hint` / `company_hint` passed for ATS jobs so Haiku doesn't need to extract them.
- The Haiku model ID is read from `settings.yaml` under `scoring.model` (e.g. `claude-haiku-...`), not hardcoded. Add a `scoring.model` key to `settings.yaml` in this task.

**Verification**:
```bash
# 1. Real Haiku call — full JD mode (use a JD fetched in Task 5 verification)
python -c "
from job_scanner.scoring_client import score_job
import os, dotenv
dotenv.load_dotenv()
jd = '''[paste a short JD or use a known one]'''
candidate = open('config/candidate.md').read()
profile = open('config/profile.yaml').read()
result = score_job(jd, 'full', candidate, profile)
print(result)
"
# Expected: ScoreResult with match_score between 1.0 and 10.0, all fields populated.

# 2. Unit tests with saved fixture
pytest tests/test_scoring_client.py -v
# Expected: all pass. Tests patch the Anthropic client to return the fixture.
```

---

## Task 7: sheets_repo.py + service.py + main.py (Full Pipeline)

**Goal**: Wire everything together. `sheets_repo.py` reads existing URLs and appends scored rows. `service.py` orchestrates the full pipeline. `main.py` is the thin CLI entry point with `--max-jobs`.

**Files touched**:
- `job_scanner/sheets_repo.py` (new)
- `job_scanner/service.py` (new)
- `job_scanner/main.py` (new)
- `tests/test_service.py` (new)

**Out of scope**: No GitHub Actions yet. No email_finder. No README.

**What each module exposes**:

`sheets_repo.py`:
- `get_existing_urls(sheet_id: str) -> set[str]` — reads the `url` column from the Jobs tab.
- `append_jobs(sheet_id: str, rows: list[list]) -> None` — appends rows to the Jobs tab.
- Uses `shared/sheets.py` for auth and raw operations.

`service.py`:
- `run_pipeline(max_jobs: int | None) -> dict` — full pipeline:
  1. Load configs (queries.yaml — both `queries` and `company_sites` sections, companies.yaml, profile.yaml, candidate.md, settings.yaml)
  2. Source A: run all enabled search queries AND company_sites queries. Pagination loop lives here in service.py (not in search_client.py): for each query, call `search_client.search()` up to `max_pages_per_query` times, stopping early if a page returns no results.
  3. Source B: poll all companies from companies.yaml via ats_client (JD text is already included in each list call — no separate fetch step for ATS jobs)
  4. Merge, normalize URLs, dedup (against sheet + within run)
  5. Title filter, location filter
  6. Convert all dates using `date_utils`: Serper relative dates → `YYYY-MM-DD`, ATS dates → `YYYY-MM-DD`. Sort by full datetime (newest first); jobs without a posted_date go after all dated jobs, in their original order. Apply cap (`max_jobs` or `max_jobs_per_run`).
  7. For each job: ATS jobs already have JD text; search-sourced jobs → fetch JD via jd_client (with snippet fallback). Score via scoring_client.
  8. Use ATS title/company for ATS jobs, Haiku title/company for search jobs
  9. Format `date_found` as `YYYY-MM-DD HH:MM` (local time per `timezone` setting). Append to sheet.
  10. Return summary dict: `{found, new, scored, failed, skipped_cap}`
- Per-job errors are caught and logged; the job is skipped.

`main.py`:
- Parse `--max-jobs` arg (optional, overrides `max_jobs_per_run` from settings.yaml)
- At the start of every run, print the target sheet's title (read from the sheet via `shared/sheets.get_sheet_title()`), e.g. `"Writing to sheet: Job Tracker TEST"`. This makes it immediately obvious whether you're hitting the test or production sheet.
- Call `service.run_pipeline(max_jobs)`
- Print summary line

**Verification**:
```bash
# Real run on the TEST sheet with --max-jobs 2
python -m job_scanner.main --max-jobs 2
```
**Expected output** (approximate):
```
found 42, new 12, scored 2, failed 0, skipped (cap): 10
```
- The TEST sheet has 2 new rows appended (or fewer if < 2 new jobs found).
- Each row has all 13 columns populated (applied=FALSE, match_score is a number, url is normalized, etc.).
- No crashes. Failed jobs (if any) are logged but don't stop the run.
- Run completes in under 5 minutes (2 jobs = 2 Haiku calls + JD fetches).

**Additional check**:
```bash
# Idempotency — run again immediately
python -m job_scanner.main --max-jobs 2
```
**Expected**: `new 0` (or close to 0) — previously scored jobs are deduped by URL.

---

## Task 8: GitHub Actions Workflow

**Goal**: Create the cron workflow that runs the scanner twice daily, plus `workflow_dispatch` for manual runs. Decode base64 secrets to files at runtime.

**Files touched**:
- `.github/workflows/scan.yaml` (new)

**Out of scope**: No email_finder in CI. No README yet.

**Secrets needed** (to be set in GitHub repo settings):
- `SERPER_API_KEY`
- `ANTHROPIC_API_KEY`
- `SHEET_ID` (production sheet, not test)
- `GOOGLE_SERVICE_ACCOUNT_B64` (base64-encoded service account JSON)
- `PROFILE_YAML_B64` (base64-encoded config/profile.yaml)
- `CANDIDATE_MD_B64` (base64-encoded config/candidate.md)

**Workflow behavior**:
1. Cron: runs at two times daily (e.g. `0 14 * * *` and `0 2 * * *` UTC = 9 AM and 9 PM CT).
2. `workflow_dispatch`: manual trigger with optional `max_jobs` input.
3. `concurrency`: group `job-scanner` with `cancel-in-progress: false`. Prevents two runs (e.g. a cron trigger and a manual "Run workflow" click) from overlapping and both appending the same jobs to the real sheet. The second run queues until the first finishes.
4. `timeout-minutes: 20` — prevents a hung run from burning Actions minutes indefinitely.
5. Setup: checkout, Python 3.12, pip install.
6. Decode secrets: write `GOOGLE_SERVICE_ACCOUNT_B64` → `service_account.json`, `PROFILE_YAML_B64` → `config/profile.yaml`, `CANDIDATE_MD_B64` → `config/candidate.md`.
7. Set env vars: `SERPER_API_KEY`, `ANTHROPIC_API_KEY`, `SHEET_ID`, `GOOGLE_APPLICATION_CREDENTIALS=service_account.json`.
8. Run: `python -m job_scanner.main` (or with `--max-jobs ${{ inputs.max_jobs }}` if provided).
9. On failure: GitHub sends default email notification.

**Verification**:
```bash
# 1. Validate YAML syntax locally
python -c "import yaml; yaml.safe_load(open('.github/workflows/scan.yaml'))"
# Expected: no error.

# 2. Push to GitHub, then trigger manually via Actions tab:
#    - Go to Actions → "Job Scanner" → "Run workflow"
#    - Set max_jobs = 2
#    - Watch the run log
# Expected: run completes green. Prod sheet gets new rows.
#           Log starts with "Writing to sheet: Job Tracker" (the prod sheet title).
# If secrets are missing, the run fails with a clear error about the missing env var.

# 3. Concurrency check — trigger a second manual run while the first is still running.
# Expected: second run queues (status "queued"), does NOT cancel the first.
```

**Cron times note**: `0 14 * * *` = 9:00 AM CT (CDT, UTC-5) / 8:00 AM CT (CST, UTC-6). `0 2 * * *` = 9:00 PM CT (CDT) / 8:00 PM CT (CST). Adjust if you want different local times.

---

## Task 9: email_finder (Apollo + Outreach + CLI)

**Goal**: Build the full email_finder module: Apollo client, Claude outreach line drafter, sheets_repo for Contacts tab, service, and CLI.

**Files touched**:
- `email_finder/__init__.py` (new, empty)
- `email_finder/apollo_client.py` (new)
- `email_finder/outreach_client.py` (new)
- `email_finder/sheets_repo.py` (new)
- `email_finder/service.py` (new)
- `email_finder/main.py` (new)
- `tests/test_apollo_client.py` (new)
- `tests/test_outreach_client.py` (new)
- `shared/linkedin_url.py` (new)
- `tests/test_linkedin_url.py` (new)
- `tests/fixtures/apollo_match_response.json` (new, scrubbed)
- `tests/fixtures/outreach_response.json` (new, scrubbed)

**Out of scope**: No changes to job_scanner. No phone numbers from Apollo.

**What each module exposes**:

`apollo_client.py`:
- `find_email(linkedin_url: str) -> dict` — calls Apollo people/match API. Returns `{name, company, title, email, email_status}`. `email` is blank and `email_status` is "not found" if no match.

`outreach_client.py`:
- `draft_outreach(name: str, company: str, title: str, candidate_md: str) -> str` — one Claude call to draft a short outreach line.

`sheets_repo.py` (email_finder's):
- `get_existing_linkedin_urls(sheet_id: str) -> set[str]` — reads the `linkedin_url` column from the Contacts tab. URLs are normalized before being added to the set (see below).
- `append_contact(sheet_id: str, row: list) -> None` — appends one row to the Contacts tab.

`shared/linkedin_url.py`:
- `normalize_linkedin_url(url: str) -> str` — lowercase, strip `www.`, trailing slash, all query params. E.g. `https://www.LinkedIn.com/in/Jane-Doe/?utm_source=foo` → `https://linkedin.com/in/jane-doe`. Lives in `shared/` because email_finder must not import from job_scanner. Used for dedup: both the input URL and existing sheet URLs are normalized before comparison.

`service.py`:
- `find_and_save(linkedin_url: str, draft: bool) -> dict` — normalizes the URL, then dedup check → Apollo → optional outreach draft → append to sheet.

`main.py`:
- CLI: `python -m email_finder.main <linkedin_url> [--draft]`
- At the start of every run, print the target sheet's title (read from the sheet via `shared/sheets.get_sheet_title()`), e.g. `"Writing to sheet: Job Tracker TEST"`. Same pattern as job_scanner main.py (Task 7).

**Verification**:
```bash
# 1. Unit tests (fixtures only, no real Apollo call)
pytest tests/test_apollo_client.py tests/test_outreach_client.py tests/test_linkedin_url.py -v
# Expected: all pass.
# LinkedIn URL normalization tests cover: lowercase, www stripping, trailing slash,
# query param removal, and the critical case: two variant URLs for the same profile
# normalize to the same string.

# 2. ONE real Apollo lookup (burns 1 credit)
python -m email_finder.main "https://www.linkedin.com/in/some-test-person/" --draft
# Expected: prints "Writing to sheet: Job Tracker TEST" first,
#           then result (name, email or "not found", outreach line if --draft).
# TEST sheet Contacts tab has one new row.

# 3. Idempotency — run same URL again (with different casing/trailing slash)
python -m email_finder.main "https://www.LinkedIn.com/in/Some-Test-Person"
# Expected: "Already exists in sheet, skipping." (normalized URL matches)
```

**Apollo API note**: `APOLLO_API_KEY` env var. Endpoint: `POST https://api.apollo.io/api/v1/people/match` with `linkedin_url` in body. Returns person object with `email` and `email_status`.

---

## Task 10: README

**Goal**: Write a clear README covering setup, secrets, run commands, and how to verify everything works.

**Files touched**:
- `README.md` (new)

**Out of scope**: No code changes. No DESIGN.md changes.

**README sections**:
1. **What this does** — one paragraph
2. **Setup** — clone, venv, pip install, .env from .env.example
3. **Secrets list** — table of every env var / file with description and where to get it
4. **Local run** — `python -m job_scanner.main --max-jobs 2` and `python -m email_finder.main <url> --draft`
5. **GitHub Actions** — how to set secrets, how to trigger manually, cron schedule
6. **Testing** — `pytest` command, what the test sheet is for
7. **Config files** — what profile.yaml, candidate.md, settings.yaml, queries.yaml, companies.yaml do
8. **Architecture** — brief pointer to DESIGN.md

**Verification**:
```bash
# Follow the README's setup instructions from scratch on a clean checkout
# (or mentally walk through each step to confirm nothing is missing)
cat README.md
```
**Expected**: A reader with the right API keys can go from clone to a working `--max-jobs 2` run by following the README alone.

---

## Dependency Graph

```
Task 1 (setup)
├── Task 2 (search_client) ─┐
├── Task 3 (ats_client) ────┤
│                            ├── Task 4 (pure logic)
│                            │      │
│                            │      ├── Task 5 (jd_client)
│                            │      │      │
│                            │      │      ├── Task 6 (scoring_client)
│                            │      │      │      │
│                            │      │      │      └── Task 7 (pipeline: sheets_repo + service + main)
│                            │      │      │             │
│                            │      │      │             ├── Task 8 (GitHub Actions)
│                            │      │      │             │
Task 9 (email_finder) ◄─────┘      │      │             │
   depends on: Task 1 (shared/sheets.py)                 │
                                                         │
Task 10 (README) ◄── all above ──────────────────────────┘
```

Tasks 2 and 3 are independent of each other but both depend on Task 1. Task 9 depends on Task 1 (shared/sheets.py) and is sequenced after Task 7 per preference, not strict dependency. Task 4 has no external dependencies but is sequenced after 2+3 because the tests benefit from understanding the real data shapes from those fixtures.
