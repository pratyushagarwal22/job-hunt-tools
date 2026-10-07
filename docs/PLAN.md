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

**Key detail**: `append_rows` must use `valueInputOption=RAW`. Callers write `""` (blank) to the `applied` / `contacted` checkbox columns, not `False` (see the checkbox decision in docs/DESIGN.md). `RAW` avoids two problems: `USER_ENTERED` would parse text starting with `=` or `+` as a formula, and would coerce date strings into date values.

**Verification**:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Create .env from .env.example, fill in real values
python -c "
from shared.sheets import read_rows
import os, dotenv
dotenv.load_dotenv()
rows = read_rows(os.environ['SHEET_ID'], 'Jobs', 'A1:N1')
print(rows)
"
```
**Expected output**: A list containing one list — the 14 header column names from the Jobs tab (`['applied', 'match_score', 'platform', 'url', 'title', 'company', 'location', 'visa_mention', 'jd_source', 'core_requirements', 'reason', 'posted_date', 'date_found', 'source_query']`; the scanner matches columns by header name, so any order works). If the sheet is unreachable, a clear error with the HTTP status.

**Checkbox append check** (see Open Risks in docs/DESIGN.md):
```bash
python -c "
from shared.sheets import append_rows, read_rows
import os, dotenv
dotenv.load_dotenv()
sheet_id = os.environ['SHEET_ID']
# Append one test row
append_rows(sheet_id, 'Jobs', [['', 7.5, 'other', 'https://example.com/test', 'Test Title', 'Test Co', 'Remote', '', 'full', '', 'test reason', '', '2026-09-29 21:00', 'test']])
# Read back the last row to check where it landed
rows = read_rows(sheet_id, 'Jobs', 'A1:N1000')
print(f'Total rows (including header): {len(rows)}')
print(f'Last row: {rows[-1]}')
"
```
**Expected**: The test row lands on row 2 (right after the header), not row 1001. The `applied` cell is blank (`''`). This requires that no checkboxes are pre-filled on empty rows. `read_rows` uses `valueRenderOption=UNFORMATTED_VALUE`, so read-back values are raw, not display strings. Then delete the test row by hand. If either check fails, stop and report — do not attempt a fix.

**`.env.example` contents**:
```
SHEET_ID=               # Always the TEST sheet ID locally. The real sheet ID exists only in GitHub secrets.
GOOGLE_APPLICATION_CREDENTIALS=service_account.json
SERPER_API_KEY=
ANTHROPIC_API_KEY=
APOLLO_API_KEY=
```
(As of Task 9, `.env.example` also has `PROSPEO_API_KEY` and `APIFY_TOKEN`; `APOLLO_API_KEY` is optional, paid plan only.)
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

**Goal**: Build the Serper API client. Confirm that `site:` + `OR` + brackets syntax works through Serper (resolves the open risk from docs/DESIGN.md — **resolved**: Serper passes the syntax through). Save scrubbed fixtures for tests.

**Files touched**:
- `job_scanner/__init__.py` (new, empty)
- `job_scanner/search_client.py` (new)
- `tests/__init__.py` (new, empty)
- `tests/test_search_client.py` (new)
- `tests/fixtures/serper_site_query.json` (new, scrubbed)
- `tests/fixtures/serper_company_sites_query.json` (new, scrubbed)

**Out of scope**: No URL normalization, no dedup, no title filtering. The client returns raw parsed results (list of dicts with `title`, `link`, `snippet`, `date`, `position`). No pagination logic yet (pagination loop lives in service.py, Task 7) — just a single-page call helper that accepts `tbs`, `gl`, `num`, `page` and `timeout` params (values come from `settings.yaml` `search` block and `request_timeout_seconds`, passed in by service.py).

**What search_client.py exposes**:
- `search(query: str, tbs: str, gl: str, num: int, page: int, timeout: float = 15) -> list[dict]`
- Missing `SERPER_API_KEY` or non-2xx → `RuntimeError` (non-2xx message includes the HTTP status).
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
- `list_greenhouse_jobs(slug: str, company: str, lookback_days: int, timeout: float = 15) -> list[dict]`
- `list_lever_jobs(slug: str, company: str, lookback_days: int, timeout: float = 15) -> list[dict]`
- `list_ashby_jobs(slug: str, company: str, lookback_days: int, timeout: float = 15) -> list[dict]`
- `company` is the display name from `companies.yaml`. `timeout` is wired to `request_timeout_seconds` in settings later.
- No separate `fetch_*_jd` functions. Each list call returns jobs with JD text included (one API call per company, not two).
- Each list function calls the public API, filters by `posted_date >= now - lookback_days`, and returns structured dicts.
- `posted_date` is the raw API value (ISO string, or Unix ms int for Lever). A temporary private `_parse_posted` is used only for the lookback filter; `date_utils` (Task 4) replaces it. Jobs with no date are dropped.
- Bad slug / non-2xx / request error: log a warning with slug + status and return `[]`. One company never crashes the run.

**API endpoints** (public, no auth needed):
- Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` — single call returns list with JD HTML in each job's `content` field. Posted date field: `first_published` (preferred — reflects original publish date). Falls back to `updated_at` if `first_published` is missing. Do not use `updated_at` as primary — it changes whenever an old posting is edited, making old jobs look new.
- Lever: `GET https://api.lever.co/v0/postings/{slug}` — list already includes full posting content. Posted date field: `createdAt` (Unix ms).
- Ashby: `GET https://api.ashbyhq.com/posting-api/job-board/{slug}` — public posting API, not the internal GraphQL endpoint. Posted date field: `publishedAt`.

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
jobs = list_greenhouse_jobs('anthropic', 'Anthropic', lookback_days=7)
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
jobs = list_lever_jobs('palantir', 'Palantir', lookback_days=7)
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
jobs = list_ashby_jobs('cohere', 'Cohere', lookback_days=7)
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
        jobs = list_ashby_jobs(slug, slug, lookback_days=30)
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

## Task 4: Pure Logic — URL Normalize, Title Filter, Location Filter, Date Utils

**Goal**: Build all the cheap pre-scoring filters, the URL normalizer, and the date conversion module. 100% unit-tested, no external calls.

**Files touched**:
- `job_scanner/url_utils.py` (new)
- `job_scanner/title_filter.py` (new)
- `job_scanner/location_filter.py` (new)
- `job_scanner/date_utils.py` (new)
- `job_scanner/ats_client.py` (edit — replace `_parse_posted` with `date_utils.parse_ats_date`)
- `job_scanner/settings.yaml` (edit — add `timezone` key, value `America/Chicago`)
- `tests/test_url_utils.py` (new)
- `tests/test_title_filter.py` (new)
- `tests/test_location_filter.py` (new)
- `tests/test_date_utils.py` (new)

**Out of scope**: No dedup against the sheet (that requires sheets_repo.py, Task 7). In-memory dedup (within a single run's URL list) is in scope. No scoring, no JD fetching.

**What each module exposes**:

`url_utils.py`:
- `normalize_url(url: str) -> str` — strip `utm_*`, `ref`, `source`, `gh_src`, `team`, `page`, `location`, `action`, fragments, trailing slashes, and a trailing `/apply` path segment. Lowercase scheme + domain. Keep path case. Do NOT strip `gh_jid` or `id` — they are job IDs on some career sites (e.g. company-hosted Greenhouse pages); stripping them would collapse different jobs into one URL and break dedup.
- `platform_for_url(url: str) -> str` — the job platform by host, case-insensitive (added after Task 8): `ashby`, `greenhouse` (`*.greenhouse.io` or any URL with a `gh_jid` param), `lever`, `workable`, `wellfound`, `workatastartup`, `welcometothejungle`, `smartrecruiters`, `jobvite`, `breezy`, `a16z`, `workday`, `company_site` (big-company career hosts), else `other`. One dict holds the host mapping.

`title_filter.py`:
- `title_drop_reason(title: str, include: list[str], exclude: list[str], always_allow: list[str] = ()) -> str | None` — case-insensitive, whole-word matching. Must match at least one include keyword and zero exclude keywords. Returns `None` (passes), the matched exclude keyword, or `"no include match"` (signature as of Task 8 + filter tuning). `always_allow` phrases (`title_filter.always_allow`, e.g. "Member of Technical Staff") count as an include match and are removed from the title before the exclude check ("Senior Member of Technical Staff" still drops on "Senior").

`location_filter.py`:
- `location_drop_reason(location: str | None, title: str, url: str, exclude_keywords: list[str], is_ats: bool, us_signals: list[str] = ()) -> tuple[str, str] | None` — for ATS jobs, check `location` field only. For search jobs, check title and url separately — not the snippet, which often mentions other offices or teams (URL-decoded; catches locations in URLs, e.g. NVIDIA's `Taiwan-Taipei` path or Google's `?location=India`). The caller passes the **raw** URL, not the normalized one (normalization strips `?location=`). Drops (returns `(keyword, where)`) only when an exclude keyword matches AND no `us_signals` entry (`location_filter.us_signals`: "United States", "USA", "U.S.", "Remote - US", "Remote (US)", "US Remote", state names except Georgia, major US tech cities; never bare "US" or state codes) appears in any checked field. Blank/unknown/Remote passes.
- Both filters use the same whole-word matcher: `(?<!\w)…(?!\w)` lookarounds instead of `\b`, so keywords like `C++` or `AI/ML` match correctly.

`date_utils.py`:
- `parse_relative_date(date_str: str, run_start: datetime) -> datetime | None` — converts Serper's `date` field to a timezone-aware `datetime`. `run_start` must be tz-aware. Handles: relative strings ("21 hours ago", "3 days ago", "1 week ago", "just now") and absolute strings ("Sep 28, 2026" → midnight in the configured timezone). Returns `None` for unparseable input.
- `parse_ats_date(value: str | int, tz_name: str) -> datetime | None` — accepts ISO 8601 strings and Unix ms ints (Lever's `createdAt` is Unix ms). Returns a tz-aware `datetime` in the configured timezone, or `None`. Takes the raw `posted_date` from `ats_client` and replaces its temporary `_parse_posted` (switch `ats_client`'s lookback filter to this function in this task).
- `format_posted_date(dt: datetime, tz_name: str) -> str` — returns `YYYY-MM-DD` in the configured timezone.
- `format_date_found(dt: datetime, tz_name: str) -> str` — returns `YYYY-MM-DD HH:MM` in the configured timezone.
- The `timezone` setting (e.g. `America/Chicago`) is read from `settings.yaml`. Add a `timezone` key to `settings.yaml` in this task.

**Verification**:
```bash
pytest tests/test_url_utils.py tests/test_title_filter.py tests/test_location_filter.py tests/test_date_utils.py -v
```
**Expected**: All tests pass. Test cases cover:
- `url_utils`: utm stripping, ref / source / gh_src / team / page / location / action removal, trailing slash, trailing `/apply`, fragment removal, mixed case domain, idempotency. **Critical test**: two URLs differing only by `gh_jid` (or `id`) must stay different after normalization (e.g. `https://company.com/jobs?gh_jid=123` vs `https://company.com/jobs?gh_jid=456` → two distinct normalized URLs).
- `title_filter`: exact match, partial non-match (e.g., "Lead" does not match "Leadership"), case insensitivity, exclude overrides include, no include match → fail. "Member of Technical Staff" → pass, "Senior Member of Technical Staff" → fail, "Java Developer" → pass, "Developer Advocate" → fail.
- `location_filter`: "London" → fail, "Remote" → pass, "New York, NY" → pass, blank → pass, "Bangalore, India" → fail, ATS mode checks only location field, search mode checks title+url only (e.g. URL containing `Taiwan-Taipei` or `?location=India` → fail; a non-US place only in the snippet → pass). US signals: title "New York or Zurich" → pass, title "Zurich, Switzerland" → fail, title "join us in London" → fail, ATS "Toronto" → fail, ATS "San Francisco; London" → pass.
- `date_utils`: all parsers return tz-aware datetimes (not bare dates) so Task 7 can sort on full datetime. "21 hours ago" at a 9:00 AM CT run → tz-aware datetime of the previous day at noon CT. "3 days ago" → 3 days before run_start. "1 week ago" → 7 days before. "just now" → run_start. Absolute date "Sep 28, 2026" → midnight CT on that date. Unparseable string → `None`. `parse_ats_date` with a UTC ISO string → CT datetime. `parse_ats_date` with a Unix ms int (Lever) → CT datetime. `format_posted_date` → `YYYY-MM-DD`.

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
- `fetch_jd(url: str, timeout: float, min_chars: int) -> tuple[str | None, str]` — returns `(text_content, source)` where `source` is `"full"` or `"snippet"`. Sends a browser User-Agent. Uses `beautifulsoup4` with the built-in `html.parser` (no lxml), drops `script` / `style` / `noscript` / `svg` / `template`, and extracts visible text. If the request fails, returns non-2xx, is not HTML, or returns fewer than `min_chars` of visible text (`jd_min_chars` in settings.yaml, 500), returns `(None, "snippet")` to signal fallback.

**Verification**:
```bash
# 1. Real fetch — pick a known good Greenhouse job page URL from a recent search result
python -c "
from job_scanner.jd_client import fetch_jd
text, source = fetch_jd('https://job-boards.greenhouse.io/anthropic/jobs/4148108008', timeout=15, min_chars=500)
print(f'Source: {source}, Text length: {len(text) if text else 0}')
if text: print(text[:500])
"
# Expected: source="full", text has readable JD content (>= 500 chars).

# 2. JS-heavy page — try a page known to return little text
python -c "
from job_scanner.jd_client import fetch_jd
text, source = fetch_jd('https://careers.google.com/jobs/results/', timeout=15, min_chars=500)
print(f'Source: {source}, Text length: {len(text) if text else 0}')
"
# Expected: source="snippet" (too little text or fetch blocked).

# 3. Unit tests
pytest tests/test_jd_client.py -v
# Expected: all pass. Tests use fixture HTML files, not live HTTP.
```

---

## Task 6: scoring_client.py (Anthropic scoring model)

**Goal**: Build the scoring client (model from `scoring.model`) with pydantic validation. Support both full-JD and snippet modes. Save a scrubbed fixture.

**Files touched**:
- `job_scanner/scoring_client.py` (new)
- `job_scanner/settings.yaml` (edit — add `scoring.model`, `scoring.force_tool_choice`, `scoring.max_tokens`, `scoring.max_jd_chars` keys, and the `prices` block per model)
- `tests/test_scoring_client.py` (new)
- `tests/fixtures/haiku_score_response.json` (new, scrubbed)

**Out of scope**: No pipeline orchestration. No sheet writes. The client takes a JD (or title+snippet), candidate.md content, and profile.yaml content, and returns a validated pydantic model or raises on validation failure.

**Pydantic schema** (from docs/DESIGN.md sheet schema):
```
ScoreResult:
  match_score: float        # 1.0–10.0, validated by pydantic (ge=1.0, le=10.0)
  title: str                # extracted by the scoring model (used for search results)
  company: str              # extracted by the scoring model (used for search results)
  location: str
  visa_mention: str
  core_requirements: str
  reason: str               # one-line match reason
```

**What scoring_client.py exposes**:
- `score_job(jd_text: str, jd_source: str, candidate_md: str, profile_yaml: str, model: str, max_tokens: int, max_jd_chars: int, force_tool_choice: bool, title_hint: str = "", company_hint: str = "") -> ScoreResult` — one Claude call to the scoring tool, validated with pydantic. `force_tool_choice=True`: forced tool call (Haiku). `False`: `tool_choice: auto`, `strict: true` on the tool, and "Always respond by calling the scoring tool." appended to the user message (Sonnet/Opus 5.5 reject forced tool_choice); no tool call → failed job. One tool schema for all models: `additionalProperties: false`, no `minimum`/`maximum` (pydantic still enforces 1.0–10.0). Scoring model for October: `claude-opus-5-5`. JD truncated to `max_jd_chars`. `title_hint` / `company_hint` passed for ATS jobs so the scoring model doesn't need to extract them. Snippet inputs get an extra note in the user message.
- `model`, `force_tool_choice`, `max_tokens`, `max_jd_chars` come from `settings.yaml` `scoring` block, passed in by service.py (the client never reads settings.yaml). Add these keys in this task.
- **Prompt caching**: the system block (generic rules + calibration + `candidate.md` + `profile.yaml`, ~6,900 tokens on Opus 5.5 (measured; varies by model tokenizer)) has `cache_control: ephemeral`. Haiku 4.5's cache minimum is 4,096 tokens. Each call logs input / output / cache-creation / cache-read tokens; a warning is logged when both cache counters are 0.
- **Calibration section** in the prompt: bands (9–10, 7–8, 6, 3–5, 1–2), how to apply them, one example per band, snippet-only rules. Generic only — personal rules (F-1 OPT, Seattle bonus) live in `profile.yaml`; profile wins on conflict.

**Verification**:
```bash
# 1. Real scoring-model call — full JD mode (use a JD fetched in Task 5 verification)
python -c "
from job_scanner.scoring_client import score_job
import os, dotenv
dotenv.load_dotenv()
jd = '''[paste a short JD or use a known one]'''
candidate = open('config/candidate.md').read()
profile = open('config/profile.yaml').read()
result = score_job(jd, 'full', candidate, profile,
                   model='claude-opus-5-5', max_tokens=2048, max_jd_chars=15000,
                   force_tool_choice=False)
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
- `get_columns(sheet_id: str) -> dict[str, int]` — reads the Jobs header row once per run and maps the 14 headers by name (trimmed, case-insensitive); raises a fatal error naming any missing headers.
- `get_existing_urls(sheet_id: str, columns) -> set[str]` — reads the column headed `url` from the Jobs tab, normalized.
- `append_jobs(sheet_id: str, rows: list[dict], columns) -> None` — appends rows (dicts keyed by header name) to the Jobs tab, each value in its header's column.
- `get_sheet_title(sheet_id: str) -> str` — passthrough to `shared/sheets.get_sheet_title`.
- Uses `shared/sheets.py` for auth and raw operations.

`service.py`:
- `run_pipeline(max_jobs: int | None) -> dict` — full pipeline:
  1. Load configs (queries.yaml — both `queries` and `company_sites` sections, companies.yaml, profile.yaml, candidate.md, settings.yaml). Create `run_start` once, tz-aware in the configured timezone: `datetime.now(ZoneInfo(settings["timezone"]))`. Pass it to every `date_utils.parse_relative_date` call (absolute Serper dates become midnight in `run_start`'s timezone, so it must not be UTC or naive).
  2. Source B: poll all companies from companies.yaml via ats_client (JD text is already included in each list call — no separate fetch step for ATS jobs)
  3. Source A: run all enabled search queries AND company_sites queries. Pagination loop lives here in service.py (not in search_client.py): for each query, call `search_client.search()` up to `max_pages_per_query` times, stopping early if a page returns no results or errors.
  4. Merge (**ATS first, so ATS wins dedup**), normalize URLs, dedup (against sheet + within run). Keep the raw URL alongside for the location filter.
  5. Title filter, location filter (location filter gets the raw URL)
  6. Convert all dates using `date_utils`: Serper relative dates → `YYYY-MM-DD`, ATS dates → `YYYY-MM-DD`. Sort by full datetime (newest first); jobs without a posted_date go after all dated jobs, in their original order. Apply cap (`max_jobs` or `max_jobs_per_run`).
  7. For each job: ATS jobs already have JD text; search-sourced jobs with a `jobs.ashbyhq.com/<slug>/<job id>` URL → JD from the Ashby public posting API (`ats_client.get_ashby_board`, board cached per slug for the run, job matched by id; counted as "full fetched"); any Ashby failure (AtsError, job not on board, empty text) and all other search-sourced jobs → fetch JD via jd_client (with snippet fallback). Score via scoring_client.
  8. Use ATS title/company/location for ATS jobs (the scoring model's location if the ATS one is blank), the scoring model's title/company/location for search jobs. `source_query` = the query string, or `<ats>:<company>` for ATS jobs.
  9. Format `date_found` as `YYYY-MM-DD HH:MM` (local time per `timezone` setting). **One append at the end** with all scored rows.
  10. Return summary dict: `{found, new, filtered, scored, failed, skipped_cap}`
- Per-job: every exception is caught and logged; the job is skipped. Anthropic `AuthenticationError` / `PermissionDeniedError` are re-raised.
- Fatal (raise): sheet errors, every search query failed, every scoring attempt failed.

`main.py`:
- Parse `--max-jobs` arg (optional, overrides `max_jobs_per_run` from settings.yaml)
- `load_dotenv()` first.
- At the start of every run, print the target sheet's title (read via `sheets_repo.get_sheet_title()`), e.g. `"Writing to sheet: Job Tracker TEST"`. This makes it immediately obvious whether you're hitting the test or production sheet.
- Call `service.run_pipeline(max_jobs)`
- Print summary line (includes `filtered`). Any exception → log it and exit 1.

**Verification**:
```bash
# Real run on the TEST sheet with --max-jobs 2
python -m job_scanner.main --max-jobs 2
```
**Expected output** (approximate):
```
found 42, new 12, filtered 2, scored 2, failed 0, skipped (cap): 8
```
- The TEST sheet has 2 new rows appended (or fewer if < 2 new jobs found).
- Each row has all 14 columns in place (applied is blank, match_score is a number, url is normalized, etc.).
- New rows land directly after the last data row, not after row 1000.
- No crashes. Failed jobs (if any) are logged but don't stop the run.
- Run completes in under 5 minutes (2 jobs = 2 scoring-model calls + JD fetches).

**Additional check**:
```bash
# Idempotency — run again immediately
python -m job_scanner.main --max-jobs 2
```
**Expected**: new drops by number scored; no URL written twice. (Over-cap jobs aren't written, so they count as new again.)

---

## Task 8: GitHub Actions Workflow

**Goal**: Create the cron workflow that runs the scanner twice daily, plus `workflow_dispatch` for manual runs. Decode base64 secrets to files at runtime. Add the run logging from the "Logging" section of docs/DESIGN.md.

**Files touched**:
- `.github/workflows/scan.yaml` (new)
- `job_scanner/main.py` (edit — fail fast on missing env vars)
- `job_scanner/service.py` (edit — funnel / per-query / DROP / JD / SCORES / COST / RUN lines, step summary)
- `job_scanner/title_filter.py` (edit — return the drop reason)
- `job_scanner/location_filter.py` (edit — return the drop reason)
- `job_scanner/ats_client.py` (edit — surface per-company errors to service, see below)
- `job_scanner/scoring_client.py` (edit — return token usage to service, see below)
- `job_scanner/settings.yaml` (edit — add per-model prices)
- `tests/test_service.py`, `tests/test_title_filter.py`, `tests/test_location_filter.py`, `tests/test_ats_client.py`, `tests/test_scoring_client.py` (edit)

**Out of scope**: No email_finder in CI. No README yet. No Runs/Errors tab, no log files, no artifacts. No change to the final summary line.

**Logging build details** (spec in docs/DESIGN.md "Logging"):
- **Prefixes**: each key line's message starts with `RUN`, `ATS`, `QUERY`, `FUNNEL`, `DROP`, `JD`, `FAIL`, `SCORES`, `COST` or `ASHBY` (added after Task 8: a search-found Ashby job whose board was unavailable or whose job id was not on the board; it falls back to a plain page fetch). The existing `Writing to sheet: <title>` line and the final summary line stay as they are.
- **Header (`RUN`)**: run start (configured tz), effective cap (after `--max-jobs`), `ats_lookback_days`, `scoring.model`, existing URL count from the sheet.
- **ATS**: totals (companies polled, jobs within lookback); list of companies with 0 jobs; list of companies with errors (slug + HTTP status, or error type for request/JSON failures). Today `ats_client` logs the warning and returns `[]` for both "no jobs" and "error", so service can't tell them apart: change ats_client to report the error to the caller, keeping "one company never crashes the run".
- **QUERY**: per query: pages fetched, results, new (after dedup), passed filters, new+passed from page 2+ (tag each search job with its page number). Search errors as `FAIL search` lines (query name + HTTP status). One closing line with total Serper calls this run (every request sent).
- **FUNNEL**: found, dupes (sheet), dupes (in-run), no URL, new, title dropped, location dropped, eligible, over cap, experience dropped. Numbers must add up (`found = dupes + no URL + new`, `new = title dropped + location dropped + eligible`, `eligible = over cap + experience dropped + scored + failed`). Experience drops already log `DROP experience N | <phrase> | <title> | <url>`; keep that format. `_dedup` must count sheet vs in-run dupes separately.
- **DROP**: one line per filtered job with title + URL. Title drops: the matched exclude keyword, or `no include match`. Location drops: matched keyword + where it matched (`location` / `title` / `url`). A `|` inside a title is written as `/` in DROP lines. `title_filter` and `location_filter` return the matched reason (`None` = passes) instead of a bool; update their tests to assert the reason. Over-cap: count in `FUNNEL`; `DROP cap` lines with titles only when the count > 0.
- **FAIL (per job)**: URL + error type + exception message, collapsed to one line and truncated to 150 characters.
- **Missing env vars** (`job_scanner/main.py`): before any work, check `SHEET_ID`, `SERPER_API_KEY`, `ANTHROPIC_API_KEY` and `GOOGLE_APPLICATION_CREDENTIALS`; if any are unset or empty, log one error naming every missing variable and exit 1.
- **JD**: counts of full fetched, snippet fallback (search fetch fallbacks + ATS jobs with empty JD text), ATS full.
- **SCORES**: histogram buckets 8–10, 6–7.9, 3–5.9, 1–2.9.
- **COST**: summed input, output, cache read, cache write tokens; estimated cost. `score_job` currently only logs usage per call, so it must also return it (e.g. alongside the `ScoreResult`). Prices per model (per million tokens: input, output, cache read, cache write) go in a new `settings.yaml` block keyed by model ID — never hardcoded. If the configured model has no price entry, log cost as unknown; never fail the run.
- **RUN (end)**: elapsed time.
- **`$GITHUB_STEP_SUMMARY`**: if the env var is set, append a short markdown summary (funnel table, per-query table, score histogram, cost). If unset, skip silently. A write failure logs a warning and never fails the run.
- **Never log** JD text, profile/candidate contents, or secrets.

**Secrets needed** (to be set in GitHub repo settings):
- `SERPER_API_KEY`
- `ANTHROPIC_API_KEY`
- `SHEET_ID` (production sheet, not test)
- `GOOGLE_SERVICE_ACCOUNT_B64` (base64-encoded service account JSON)
- `PROFILE_YAML_B64` (base64-encoded config/profile.yaml)
- `CANDIDATE_MD_B64` (base64-encoded config/candidate.md)

**Workflow behavior**:
1. Cron: two UTC schedule entries — `cron: '7 10 * * *'` (5:07 AM CDT / 4:07 AM CST) and `cron: '7 20 * * *'` (3:07 PM CDT / 2:07 PM CST); CST applies after DST ends on Nov 1. GitHub scheduled runs are best-effort and often start hours late, so runs are set early; manual runs start immediately.
2. `workflow_dispatch`: manual trigger with optional `max_jobs` input.
3. `concurrency`: group `job-scanner` with `cancel-in-progress: false`. Prevents two runs (e.g. a cron trigger and a manual "Run workflow" click) from overlapping and both appending the same jobs to the real sheet. The second run queues until the first finishes.
4. `timeout-minutes: 45` — prevents a hung run from burning Actions minutes indefinitely (cap 150 = up to 150 JD fetches + scoring calls).
5. Setup: checkout, Python 3.13, pip install.
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
# If secrets are missing, the run fails with one clear error naming every missing env var.

# 3. Concurrency check — trigger a second manual run while the first is still running.
# Expected: second run queues (status "queued"), does NOT cancel the first.

# 4. Logging — unit tests + the max_jobs=2 run above
pytest -v
# Expected: all pass, including filter tests asserting the drop reason.
# In the Actions run log: RUN, ATS, QUERY, FUNNEL, JD, SCORES, COST lines present;
# FUNNEL numbers add up; no JD text or profile/candidate content anywhere.
# The run page shows the markdown step summary.
# Locally (GITHUB_STEP_SUMMARY unset): same log lines, no error about the summary.
```

**Cron times note**: The crons are plain UTC, so the local run time shifts by one hour with DST (5:07 AM / 3:07 PM CDT in summer, 4:07 AM / 2:07 PM CST in winter).

### Post-Task-8 changes

Built after Task 8; DESIGN.md has the details.
- `job_scanner/experience_filter.py` (+ `tests/test_experience_filter.py`): drops jobs whose JD requires more than `experience_filter.max_years_required` years; adds `DROP experience` lines and the `experience dropped` funnel stage.
- `url_utils.platform_for_url`: fills the new `platform` column (ATS jobs use the ATS name).
- Header-driven Jobs columns: `sheets_repo.get_columns` reads the header row once per run and maps the 14 headers by name; a missing header is fatal before anything is written. `get_existing_urls` reads the `url` column; `append_jobs` takes rows as dicts keyed by header name.
- Ashby API JD for search jobs: `jobs.ashbyhq.com/<slug>/<job id>` search results get their JD from `ats_client.get_ashby_board` (board cached per slug per run), falling back to a plain fetch with an `ASHBY` log line.

---

## Task 9: email_finder (Prospeo → Apify chain + Outreach + CLI)

**Goal**: Build the email_finder module: a batch CLI that takes LinkedIn profile URLs, looks each one up through an ordered chain of email providers (Prospeo, then Apify; Apollo available but disabled), optionally drafts an outreach line with Claude, and appends one row per URL to the Contacts tab.

**Provider facts** (from real tests, Oct 5 2026; details in docs/DESIGN.md "Data Flow — email_finder"):
- **Prospeo** (free, 100 credits/month; enrich limits 1/s, 20/min, 50/day): `POST https://api.prospeo.io/enrich-person`, header `X-KEY`, body `{"data": {"linkedin_url": ...}}`. Match (`error: false`): email at `person.email.email`, status at `person.email.status`, name at `person.full_name`, title at `person.current_job_title`, company at `company.name`. No match: `{"error": true, "error_code": "NO_MATCH"}`, or a match whose email is not revealed (`person.email.revealed` false, or an email containing `*`, i.e. masked). The match response also carries a `person.mobile` object; it is never read or stored (and is scrubbed in fixtures).
- **Apify** actor `vulnv/linkedin-email-finder` (pay per email found, ~$0.026, from the $5/month free credit): `POST https://api.apify.com/v2/acts/vulnv~linkedin-email-finder/run-sync-get-dataset-items`, header `Authorization: Bearer <APIFY_TOKEN>`, body `{"urls": [...]}`, HTTP 201. One item per URL with `name`, `company`, `domain`, `email`, `found`, `linkedin_url` (no status, no title). No match = `found: false` with `error` exactly `"No email found in response"`.
- **Apollo**: `people/match` and `people/bulk_match` return 403 `API_INACCESSIBLE` on the free plan. Kept as a disabled provider for when a paid plan exists.

**Files touched**:
- `shared/sheets.py` (edit — move in `header_positions` and `column_letter` from `job_scanner/sheets_repo.py`)
- `job_scanner/sheets_repo.py` (edit — import `header_positions` / `column_letter` from `shared/sheets.py`; allowed exception to "no job_scanner changes", behavior unchanged)
- `shared/linkedin_url.py` (new)
- `email_finder/__init__.py` (new, empty)
- `email_finder/settings.yaml` (new)
- `email_finder/linkedin-profiles.example.txt` (new, committed; the real `email_finder/linkedin-profiles.txt` is gitignored)
- `email_finder/prospeo_client.py` (new)
- `email_finder/apify_client.py` (new)
- `email_finder/apollo_client.py` (new)
- `email_finder/outreach_client.py` (new)
- `email_finder/sheets_repo.py` (new)
- `email_finder/service.py` (new)
- `email_finder/main.py` (new)
- `tests/test_linkedin_url.py`, `tests/test_prospeo_client.py`, `tests/test_apify_client.py`, `tests/test_apollo_client.py`, `tests/test_outreach_client.py`, `tests/test_email_finder_sheets_repo.py`, `tests/test_email_finder_service.py` (new)
- `tests/fixtures/prospeo_enrich_match.json`, `prospeo_enrich_nomatch.json`, `apify_match.json`, `apify_nomatch.json`, `apollo_403.json`, `apollo_bulk_match.json`, `outreach_response.json` (new, scrubbed — see Fixtures)

**Out of scope**: No other job_scanner changes. No phone numbers from any provider. No LinkedIn scraping: our code never fetches LinkedIn pages; email providers receive the URL. No retry logic. No CI workflow for email_finder (manual CLI only). `email_finder` never imports from `job_scanner`.

**Input**:
- CLI: `python -m email_finder.main [urls ...] [--file PATH] [--draft]`.
- No URLs and no `--file` → read `email_finder/linkedin-profiles.txt` (gitignored; copy from `linkedin-profiles.example.txt`).
- File format: one URL per line; blank lines and lines starting with `#` are ignored; an optional trailing `# note` on a URL line goes into the `notes` column.
- Every URL is normalized with `shared/linkedin_url.normalize_linkedin_url`; only `/in/` profile URLs are kept (anything else prints `ignored <line> (not a profile URL)`). Dedup within the input (later copies print `duplicate <url>`) and against the Contacts tab's `linkedin_url` column (prints `<url> exists`).
- `max_urls_per_run` (20) counts the URLs left **after** removing existing and in-input duplicates. More than that → exit 1 before any provider call.

**Sheet target**: email_finder runs only locally, so it writes to the TEST sheet from `.env` by default. Real use: `SHEET_ID=<prod id> python -m email_finder.main ...`. The `Writing to sheet: <title>` line shows which sheet was hit.

**Provider chain** (`service.py`):
- Providers run in the order of the `providers` list in `email_finder/settings.yaml` (default `[prospeo, apify]`; `apollo` available).
- Each provider runs **once over all URLs still unmatched**, then passes its no-matches to the next provider.
- **Prospeo**: one call per URL, `prospeo.timeout_seconds` (15), `prospeo.min_interval_seconds` (3.1) sleep between calls — keeps under 20/min for any cap. The 50/day limit is covered by the stop rule (429 → stop, rest pending).
- **Apify**: one actor run for all its URLs with its own `apify.timeout_seconds` (300). Results are mapped back by normalized `linkedin_url`.
- **Apollo**: `people/bulk_match` in batches of `apollo.batch_size` (10), `apollo.timeout_seconds` (15), `apollo.min_interval_seconds` (1.2) between batches, `reveal_personal_emails: false`, never phone. Results are mapped back by the returned record's normalized `linkedin_url`; a submitted URL with no returned record is a no-match. Verify this against a real response before enabling Apollo.
- `email_status` is stored as the provider returns it, lowercased; Apify rows get `"unverified"`.
- A URL matched by no provider → row with blank `email` and `email_status` `"not found"` (so the same person is never looked up twice).

**Errors**:
- **No-match** is only: Prospeo `error_code: NO_MATCH`, or a Prospeo match whose `person.email.email` is empty/null, whose `person.email.revealed` is false, or whose email contains `*` (masked); Apify `found: false` with `error` exactly `"No email found in response"`; Apollo no record returned for that URL. A no-match falls through to the next provider.
- **Anything else** (non-2xx, other error codes, 401/403/429, timeout, malformed body, an Apify item missing for a submitted URL) → stop the whole run: append the rows already resolved (found + not found), do **not** write the unresolved URLs (they are retried next run), print which URLs are pending, exit 1.
- **`--draft` failures**: a failed Claude call → that row is written with a blank `outreach_line` and a warning (no name or email in it); the run continues. An Anthropic authentication / permission error → stop drafting, still append all rows (remaining `outreach_line`s blank), exit 1.

**What each module exposes**:

`shared/linkedin_url.py`:
- `normalize_linkedin_url(url: str) -> str` — lowercase, strip `www.`, trailing slash, all query params and fragments. E.g. `https://www.LinkedIn.com/in/Jane-Doe/?utm_source=foo` → `https://linkedin.com/in/jane-doe`. Lives in `shared/` because email_finder must not import from job_scanner. Both input URLs and existing sheet URLs are normalized before comparison.

`shared/sheets.py` (moved in):
- `header_positions(header_row: list) -> dict[str, int]` and `column_letter(index: int) -> str`, unchanged. `job_scanner/sheets_repo.py` imports them from here; its tests keep passing unchanged.

`prospeo_client.py`, `apify_client.py`, `apollo_client.py`:
- One request per call, no retries, timeout passed in. Clients never read `settings.yaml` (values passed in by service). API keys are read from env and sent **only in headers** (`X-KEY`, `Authorization: Bearer`, `x-api-key`), never in URLs, exception messages or logs.
- Each client returns a match as `{name, company, title, email, email_status}` (fields a provider doesn't return are blank), returns "no match" for exactly the no-match cases above, and raises `RuntimeError` (provider name + HTTP status or error code, never the key) for everything else.
- Prospeo: `enrich_person(linkedin_url, timeout)` → match or `None` (`None` for `NO_MATCH`, an empty/null `person.email.email`, `person.email.revealed` false, or an email containing `*`). Fields: `person.full_name`, `company.name`, `person.current_job_title`, `person.email.email`, `person.email.status` (lowercased).
- Apify: `find_emails(urls, actor_id, timeout)` → one entry per returned item, keyed by the item's `linkedin_url`; `email_status` is `"unverified"` (Apify returns no status). Service maps entries to submitted URLs by normalized URL; a submitted URL with no item is an error.
- Apollo: `bulk_match(linkedin_urls, timeout)` → returned records keyed by normalized `linkedin_url` (a submitted URL with no record = no-match); `email_status` lowercased; body sets `reveal_personal_emails: false` and never requests phone numbers.

`outreach_client.py`:
- `draft_outreach(name, company, title, candidate_md, model, max_tokens) -> str` — one Claude call per found row. `model` / `max_tokens` from `outreach` in settings, passed in by service. Uses `config/candidate.md`. The prompt and the drafted line are never logged. Errors propagate; service applies the `--draft` failure rules above.

`sheets_repo.py` (email_finder's, header-driven like Jobs):
- `CONTACTS_HEADERS = (contacted, name, company, title, email, email_status, email_source, linkedin_url, notes, outreach_line, date_found)`.
- `get_columns(sheet_id) -> dict[str, int]` — reads the Contacts header row once, maps headers by name (trimmed, case-insensitive) via `shared/sheets.header_positions`; any missing header → fatal error naming them, nothing written.
- `get_existing_keys(sheet_id, columns) -> tuple[set[str], set[str]]` — (the `linkedin_url` column, normalized; the `email` column, lowercased), blanks skipped. Task 9 dedups on the URL set only; Task 9b uses both.
- `append_contacts(sheet_id, rows: list[dict], columns) -> None` — rows as dicts keyed by header name, one append call, `contacted` written as `""`.
- `get_sheet_title(sheet_id) -> str` — passthrough to `shared/sheets.get_sheet_title`.

`service.py`:
- Reads input, normalizes, dedups, enforces the cap, runs the provider chain, drafts outreach (`--draft`), builds row dicts, and does **one append at the end** (or at the stop, per Errors). `email_source` = provider name (`prospeo` / `apify` / `apollo`), blank for not-found rows. `date_found` = `YYYY-MM-DD HH:MM` in `timezone`, formatted inline with `zoneinfo` (no `job_scanner.date_utils`).

`email_finder/settings.yaml`:
- `timezone`, `max_urls_per_run` (20), `providers` (`[prospeo, apify]`), `prospeo.timeout_seconds` (15), `prospeo.min_interval_seconds` (3.1), `apify.actor_id` (`vulnv/linkedin-email-finder`), `apify.timeout_seconds` (300), `apollo.batch_size` (10), `apollo.timeout_seconds` (15), `apollo.min_interval_seconds` (1.2), `outreach.model`, `outreach.max_tokens`.

`main.py`:
- `load_dotenv()`, then check env before any work: `SHEET_ID`, `GOOGLE_APPLICATION_CREDENTIALS`, plus the key of each **enabled** provider only (`PROSPEO_API_KEY`, `APIFY_TOKEN`, `APOLLO_API_KEY`); `ANTHROPIC_API_KEY` only with `--draft`. One error naming every missing variable, exit 1.
- Prints `Writing to sheet: <title>` first.
- One line per input line: `ignored <line> (not a profile URL)`, `duplicate <url>`, or the URL + outcome (`exists` / `found via <provider> <status>` / `not found` / `pending`). Never prints emails, names or keys.
- Ends with a summary line: input, ignored, duplicate, skipped existing, found per provider, not found, pending. Any exception → logged, exit 1.

**Fixtures** (scrub every name, email, phone, URL, domain and company to fake values; keep structure):
- `prospeo_enrich_match.json`, `prospeo_enrich_nomatch.json`, `apify_match.json`, `apify_nomatch.json` — from `_scratch/*`.
- `apollo_403.json` — from `_scratch/apollo_test.json`.
- `apollo_bulk_match.json` — **synthetic**, built from Apollo's documented `bulk_match` shape and labelled synthetic in the file.
- `outreach_response.json` — scrubbed Claude response.

**Tests** (fixtures only, no real API calls):
- Each client: match, no-match, and error cases (non-2xx, other error codes, 401/403/429, timeout, malformed body); keys appear only in headers; `email_status` lowercased.
- Prospeo: a match with empty/null `person.email.email`, `person.email.revealed` false, or an email containing `*` is a no-match; `person.mobile` is never read.
- Apify: exact `"No email found in response"` string is no-match; any other `found: false` error is an error; a missing item for a submitted URL stops the run.
- Apollo: records mapped by normalized `linkedin_url`; a submitted URL with no record is a no-match.
- Service: chain order (Prospeo no-matches go to Apify; matched URLs never reach the next provider); partial failure appends resolved rows, writes no pending URLs, reports them, exits 1; rate-limit spacing (patched `sleep`, `min_interval_seconds` between Prospeo calls and Apollo batches); cap counted after existing + duplicate removal, exceeded → exit before any lookup; `--draft` failure → blank `outreach_line` + warning, run continues; Anthropic auth/permission error → drafting stops, all rows appended, exit 1.
- Input file parsing: `#` lines and blanks ignored, trailing `# note` → `notes`, non-`/in/` lines reported as `ignored`, in-input dupes reported as `duplicate`, sheet dupes as `exists`; summary counts match.
- Contacts repo: missing header is fatal and writes nothing; rows land in the right columns by header name; `get_existing_keys` returns normalized URLs and lowercase emails, blanks skipped.
- `linkedin_url`: lowercase, `www.` stripping, trailing slash, query param removal; two variant URLs for the same profile normalize to the same string.

**Verification**:
```bash
# 0. Manual, before any verification: update row 1 of BOTH the TEST and prod Contacts tabs
#    to the 11 headers in docs/DESIGN.md.

# 1. Unit tests
pytest -v
# Expected: all pass, including the existing job_scanner tests (header helpers moved, behavior unchanged).

# 2. Dry check on the TEST sheet — a file with 2 URLs: one real profile, one fake
python -m email_finder.main --file /tmp/two-profiles.txt
# Expected: "Writing to sheet: Job Tracker TEST" first; one URL "found via <provider> <status>",
# the fake one "not found"; summary line; 2 new Contacts rows directly after the last data row,
# `contacted` blank. No emails or names in the output.

# 3. Idempotency — rerun the same file
python -m email_finder.main --file /tmp/two-profiles.txt
# Expected: both URLs "exists"; nothing appended; no provider calls.

# 4. One live --draft on a third real profile URL (not in /tmp/two-profiles.txt; no rows deleted)
python -m email_finder.main "<a third real profile URL>" --draft
# Expected: "found via <provider> <status>"; the new row has a non-blank outreach_line;
# the drafted line is not printed.
```

Real use after verification: `SHEET_ID=<prod id> python -m email_finder.main ...` (local `.env` always points at TEST).

---

## Task 9b: Legacy contacts import (one-off)

**Goal**: Import the old Apollo contacts from `_legacy/contacts-master-latest.json` into the Contacts tab, once, without duplicates.

**Files touched**:
- `_scratch/import_legacy_contacts.py` (new, local-only: `_scratch/` is gitignored, not in the repo)

**Out of scope**: No changes to email_finder or shared code. No provider calls. No committed tests (one-off script).

**Input**: `_legacy/contacts-master-latest.json` — `values[0]` is the header, `values[1:]` are rows. Rows may be shorter than the header (missing cells = empty). Map by header name, not position.

**Mapping** (rows with an empty `email` are skipped):
- `name`, `company`, `title`, `email` — copied.
- `linkedin_url` — normalized with `shared/linkedin_url.normalize_linkedin_url`.
- `email_source` — `"apollo-legacy"`.
- `email_status` — legacy `email_status` if present, else `email_confidence`, lowercased (same rule as the providers).
- `notes` — the kind from legacy `notes` (`kind=RECRUITER|HIRING_MANAGER`) and `contacted YYYY-MM-DD` when `last_contacted_at` is set, joined with `"; "`.
- `contacted` — blank.
- `date_found` — the date in `contact_id` (`CT-YYYYMMDD-…`) as `YYYY-MM-DD 00:00`; blank if `contact_id` doesn't parse.
- `outreach_line` — blank.

**Behavior**:
- Uses email_finder's Contacts repo (`get_columns`, `get_existing_keys`, `append_contacts`) and the shared normalizer.
- Dedup key = normalized LinkedIn URL, else lowercase email (rows with an empty URL are imported). Keys are compared against the Contacts tab and within the legacy file; for duplicate keys the first row is kept and the rest count as duplicates. Safe to rerun.
- Dry run by default: prints counts only (rows, skipped no-email, skipped duplicate, to write). `--apply` appends all rows in one call.
- Never prints names or emails.

**Verification**:
```bash
# 1. Dry run on the TEST sheet
python _scratch/import_legacy_contacts.py
# Expected: rows / skipped no-email / skipped duplicate / to write counts; nothing written.

# 2. Apply on the TEST sheet, then rerun
python _scratch/import_legacy_contacts.py --apply
python _scratch/import_legacy_contacts.py
# Expected: rows appended once; the rerun reports every row as a duplicate, "to write 0".

# 3. Production (only after the TEST sheet looks right)
SHEET_ID=<prod id> python _scratch/import_legacy_contacts.py
SHEET_ID=<prod id> python _scratch/import_legacy_contacts.py --apply
```

---

## Task 10: README

**Goal**: Write a clear README covering setup, secrets, run commands, and how to verify everything works.

**Files touched**:
- `README.md` (new)

**Out of scope**: No code changes. No docs/DESIGN.md changes.

**README sections**:
1. **What this does** — one paragraph
2. **Setup** — clone, venv, pip install, .env from .env.example
3. **Secrets list** — table of every env var / file with description and where to get it
4. **Local run** — `python -m job_scanner.main --max-jobs 2` and `python -m email_finder.main [urls ...] [--file PATH] [--draft]` (TEST by default; prod via a `SHEET_ID=<prod id>` prefix)
5. **GitHub Actions** — how to set secrets, how to trigger manually, cron schedule
6. **Testing** — `pytest` command, what the test sheet is for
7. **Config files** — what profile.yaml, candidate.md, settings.yaml, queries.yaml, companies.yaml do
8. **Logs** — where to find run logs (Actions tab → run → job log, plus the run's step summary), retention (90 days by default), the fixed prefixes (`QUERY`, `DROP`, `FUNNEL`, `SCORES`, `COST`, …) and what each means, and this export command for a multi-run review:
   ```bash
   gh run list --workflow scan.yaml --limit 60 --json databaseId -q '.[].databaseId' | xargs -I{} gh run view {} --log | grep -E "RUN|ATS|QUERY|FUNNEL|DROP|JD|FAIL|SCORES|COST|ASHBY" > review.txt
   ```
9. **Architecture** — brief pointer to docs/DESIGN.md

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
   └── Task 9b (legacy contacts import, one-off)         │
                                                         │
Task 10 (README) ◄── all above ──────────────────────────┘
```

Tasks 2 and 3 are independent of each other but both depend on Task 1. Task 9 depends on Task 1 (shared/sheets.py) and is sequenced after Task 7 per preference, not strict dependency. Task 9b depends on Task 9 (Contacts repo + LinkedIn normalizer). Task 4 has no external dependencies but is sequenced after 2+3 because the tests benefit from understanding the real data shapes from those fixtures.
