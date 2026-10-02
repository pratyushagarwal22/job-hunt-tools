# DESIGN.md — job-hunt-tools

## Goal

Find fresh US-based software engineering jobs, score them against my profile, and save the best to a Google Sheet. Separately, find contact emails for outreach.

## In Scope

- `job_scanner/` — automated pipeline (GitHub Actions cron, twice daily)
- `email_finder/` — manual CLI tool
- One Google Sheet, two tabs ("Jobs" and "Contacts")
- Two job sources: search API queries and direct ATS company board polling

## Out of Scope

- No UI, no server, no database
- No LinkedIn scraping
- No LangChain / agent framework
- No Google Custom Search API
- No phone numbers from Apollo (email only)
- No non-US jobs (F-1 OPT; US-only search)

## Architecture

```
job-hunt-tools/
├── .github/
│   └── workflows/
│       └── scan.yaml
├── config/
│   ├── profile.yaml             # personal match profile (gitignored)
│   ├── profile.example.yaml     # committed — shows format
│   ├── candidate.md             # one-page evidence doc for scorer (gitignored)
│   └── candidate.example.md    # committed — shows format
├── job_scanner/
│   ├── main.py                  # entry point, CLI args (--max-jobs)
│   ├── service.py               # pipeline orchestration logic
│   ├── search_client.py         # Serper API (serper.dev)
│   ├── ats_client.py            # Greenhouse/Lever/Ashby public APIs (list call returns jobs with JD text, one call per company)
│   ├── date_utils.py            # parsers → tz-aware datetimes; formatters → YYYY-MM-DD / YYYY-MM-DD HH:MM strings
│   ├── jd_client.py             # plain HTTP fetch for non-ATS job pages
│   ├── scoring_client.py        # Anthropic Haiku structured JSON call
│   ├── sheets_repo.py           # read/append Google Sheet (Jobs tab only)
│   ├── url_utils.py             # normalize URLs (strip tracking params)
│   ├── title_filter.py          # cheap keyword include/exclude
│   ├── location_filter.py       # drop clearly non-US jobs before scoring
│   ├── queries.yaml             # search queries (one per job board site + company_sites)
│   ├── companies.yaml           # tracked companies on Greenhouse/Lever/Ashby
│   └── settings.yaml            # committed config (caps, search params, filters, scoring model)
├── email_finder/
│   ├── main.py                  # CLI entry point
│   ├── service.py               # orchestration
│   ├── apollo_client.py         # Apollo people/match (email only)
│   ├── outreach_client.py       # Claude outreach line drafting
│   └── sheets_repo.py           # read/append Google Sheet (Contacts tab only)
├── shared/
│   ├── sheets.py                # Google Sheets auth + low-level read/append
│   └── linkedin_url.py          # normalize LinkedIn URLs for dedup
├── tests/
│   └── fixtures/                # scrubbed real API responses (JSON)
├── docs/
│   ├── DESIGN.md                # this file
│   ├── PLAN.md                  # build plan, one task per session
│   └── PROJECT_CONTEXT.md       # summary + "why" (gitignored)
├── _legacy/                     # old broader-search files (gitignored, not used by code)
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

**Layering rule**: `main.py` is thin (parse args, call service). `service.py` holds all logic. `*_client.py` files only call external APIs — one client per external system. `sheets_repo.py` is the only file that touches the sheet. Both `sheets_repo.py` files use `shared/sheets.py` for auth and raw sheet operations. Settings are passed into clients as arguments; clients never read `settings.yaml`.

**Run command**: `python -m job_scanner.main` from the repo root.

## Data Flow — job_scanner

1. Load `queries.yaml`, `companies.yaml`, `config/profile.yaml`, `config/candidate.md`, `settings.yaml`. Create `run_start` once, tz-aware in the configured `timezone`. Read existing URLs from the "Jobs" sheet (column J, normalized).
2. **Source B — ATS direct** (collected first): for each company in `companies.yaml` → call Greenhouse/Lever/Ashby public job list API → keep only jobs posted in the last N days (`ats_lookback_days` in `settings.yaml`, default 2). Date fields: Greenhouse `first_published` (fallback `updated_at`), Lever `createdAt` (Unix ms), Ashby `publishedAt`. `posted_date` is kept as the raw API value; `date_utils.py` parses it. Bad slug / non-2xx / non-JSON → warning, company returns no jobs.
3. **Source A — Search API (Serper)**: for each enabled query (`queries` + `company_sites`) → call Serper with the `search` settings: `tbs=qdr:d` (past 24 hours), `gl=us` (US-biased results — sets where the search appears to come from, does NOT filter by job location), `num=10` results per page. Paginate up to `max_pages_per_query` pages (each page is one paid Serper credit). Stop early if a page returns no results or errors.
4. Merge into one list, **ATS jobs first**, so when the same URL comes from both sources the ATS copy (structured title/company/location + full JD) wins dedup.
5. Normalize URLs (`url_utils.normalize_url`): strip `utm_*`, `ref`, `source`, `gh_src`, `team`, `page`, `location`, `action`, fragments, trailing slashes and a trailing `/apply` path segment. Lowercase scheme + domain, keep path case. Never strip `gh_jid` or `id` (job IDs on some career sites). The normalized URL is the dedup key and the value stored in the sheet. Jobs with no URL are dropped.
6. Deduplicate: against existing URLs in the "Jobs" sheet AND within the current run
7. Title keyword filter (`title_filter` in `settings.yaml`, case-insensitive, whole-word): at least one `include` keyword and no `exclude` keyword. `always_allow` phrases (currently "Member of Technical Staff") count as an include match and are removed from the title before the exclude check, so "Member of Technical Staff" passes but "Senior Member of Technical Staff" drops on "Senior". Include covers SWE / SDE / Developer / Frontend / Front End / UI / Mobile / iOS / Android / Backend / Full Stack and early-career titles; exclude covers seniority (Senior, Sr, Staff, Principal, Lead, …), interns, embedded/firmware, nursing, and non-engineering "Developer" roles (Developer Advocate, Developer Relations, Business Developer).
8. Location filter: drop jobs whose location is clearly outside the US (`location_filter.exclude` in `settings.yaml`), unless a US signal (`location_filter.us_signals`) appears in any checked field — so "New York or Zurich" or "San Francisco; London" is kept. US signals: "United States", "USA", "U.S.", "Remote - US", "Remote (US)", "US Remote", full state names (except Georgia) and major US tech cities. Never bare "US" or two-letter state codes ("us" matches "join us"). For ATS jobs, check the location field. For search results (no structured location field), check the title and the **raw** (pre-normalization, URL-decoded) URL — not the snippet, which often mentions other offices or teams (scorer's non-US hard blocker catches the rest) — normalization strips params like `?location=` that the filter needs (catches locations in URLs, e.g. NVIDIA's `Taiwan-Taipei` or Google's `?location=India`). Keep US locations, "Remote", "Remote - US", and blank/unknown.
9. Apply score cap (`max_jobs_per_run`, default 150, overridable via `--max-jobs`). Newest jobs first — sort on the full datetime (not just date); jobs without a posted_date go after all dated jobs, in their original order. Over-cap jobs are not written to the sheet.
10. For each surviving job → ATS jobs already have full JD text from the list call in step 2 (if an ATS job has no JD text, it is scored from title + location as `snippet`). Search-sourced jobs → fetch full JD via `jd_client.py` (plain HTTP fetch, browser User-Agent, strips script/style/noscript/svg/template). If the fetch fails, returns non-2xx, is not HTML, or has fewer than `jd_min_chars` (500) characters of visible text (common on JavaScript-heavy career sites), fall back to scoring from the search result's title + snippet instead.
11. **Experience filter** (`experience_filter.py`, after JD fetch, before scoring): find the smallest required years of experience in the JD (or the title + snippet fallback). Text is cleaned first (HTML entities unescaped, tags stripped, whitespace collapsed). Counts only experience requirements: digits or one–ten, `N+`, `N-M` / `N to M` (→ N), "at least N", "minimum (of) N", tied to experience ("years of experience", "years' experience", "years in/with/of <skill>", degree + N years, or "experience" later in the same sentence; `N+` and ranges count on their own). Ignores unrelated years ("founded 10 years ago", "10 years in business", "5 year old"). Drop when required years > `experience_filter.max_years_required` (2). No requirement found → keep. Dropped jobs are not scored or written and count in `filtered`. The filter gets text only; service compares against the setting.
12. For each remaining JD (or snippet fallback) → one Claude call (`scoring.model`, `scoring.max_tokens`; JD truncated to `scoring.max_jd_chars`) with `config/candidate.md` + `config/profile.yaml` context in a cached system prompt → scoring tool call returning structured JSON (forced or auto per `scoring.force_tool_choice`, see Scoring Rules)
13. Validate the model's tool input with a pydantic schema. Invalid response = failed job (skip, not written to sheet).
14. Append all scored rows to "Jobs" tab in **one append at the end of the run** (append-only, never overwrite)
15. Print summary: `"found 42, new 12, filtered 3, scored 8, failed 1, skipped (cap): 0"`. `new` = after dedup, before filters; `filtered` = dropped by the title, location or experience filter.

### title and company fields

- **ATS-sourced jobs**: use the title and company from the ATS API response (structured, reliable).
- **Search-sourced jobs**: use Haiku's extracted values (the search result snippet may not have clean title/company).

## Data Flow — email_finder

1. Take LinkedIn URL as CLI arg
2. Read existing LinkedIn URLs from "Contacts" sheet → skip if already present
3. Call Apollo people/match (email only, no phone)
4. If `--draft` flag → one Claude call for outreach line
5. Append row to "Contacts" tab (even if email not found, to prevent re-lookups)

## Sheet Schema

### Jobs tab

| Column | Type | Notes |
|---|---|---|
| applied | Checkbox | Code appends "" (blank). Marked by hand; never pre-fill checkboxes on empty rows (see Open Risks). Never overwritten by code. |
| match_score | Number | 1.0–10.0 from Haiku (one decimal, e.g. 7.5) |
| jd_source | String | "full" (fetched JD or ATS API) or "snippet" (scored from search title + snippet) |
| title | String | From ATS source data when available; from Haiku for search results |
| company | String | From ATS source data when available; from Haiku for search results |
| location | String | Includes remote/hybrid/onsite as plain text |
| visa_mention | String | From Haiku output |
| core_requirements | String | From Haiku output |
| reason | String | One-line match reason from Haiku |
| url | String | Normalized, used as dedup key |
| posted_date | String | `YYYY-MM-DD`. ATS: from ATS API date field. Search: converted from Serper's relative `date` string using the run's start time. Blank if missing or unparseable. |
| date_found | String | `YYYY-MM-DD HH:MM` (local time per `timezone` setting in settings.yaml). |
| source_query | String | Which query or company found this job |

### Contacts tab

| Column | Type | Notes |
|---|---|---|
| contacted | Checkbox | Code appends "" (blank). Marked by hand; never pre-fill checkboxes on empty rows (see Open Risks). Never overwritten by code. |
| name | String | From Apollo |
| company | String | From Apollo |
| title | String | From Apollo |
| email | String | Blank if not found |
| email_status | String | verified / unverified / not found |
| linkedin_url | String | Input URL, used as dedup key |
| outreach_line | String | Claude-drafted, blank if --draft not used |
| date_found | String | `YYYY-MM-DD HH:MM` (local time per `timezone` setting in settings.yaml). |

## Scoring Rules

Scorer receives two inputs: `config/candidate.md` (one-page evidence doc) and `config/profile.yaml` (match criteria and evaluation rules).

- **Hard blockers (score 1–2)** — override all score floors:
  - JD **explicitly** says no visa sponsorship or US citizens only. If the JD doesn't mention visas, that is NOT a blocker. Security clearance is also a blocker. Candidate is on F-1 OPT.
  - Job is based outside the US.
- **Location within the US is never a penalty.** Greater Seattle area (Seattle, Bellevue, Redmond, Kirkland, Tacoma, Everett) gets a small bonus (+0.25 to +0.5).
- **Score floor of 6** for early-career SWE/FDE/founding/product engineer roles (per `ai_evaluation` rules in `profile.yaml`). Only score below 6 when the JD clearly requires 3+ years AND targets mid/senior/staff/principal, or a hard blocker applies.
  - **Exception (3–5)**: the role's core is a specialty with no evidence in the candidate's background (e.g. embedded firmware, chip design), or the work is mostly outside general software engineering (e.g. QA-only, IT support, sales engineering).
- **Personal rules live in `profile.yaml`**, not the prompt. The system prompt holds only generic rules (hard blockers, US location never a penalty, facts-only) plus a generic **score calibration** section: bands 9–10 / 7–8 / 6 / 3–5 / 1–2, how to apply them, one example job per band, and snippet-only rules. Personal specifics (F-1 OPT, the Seattle bonus) come from `profile.yaml`; on conflict, profile rules win.
- **Prompt caching**: the system block (prompt + `candidate.md` + `profile.yaml`, ~4,600 tokens) is marked `cache_control: ephemeral`. Haiku 4.5's minimum cacheable prompt is 4,096 tokens, so the calibration section is padded with useful content, not filler. If both cache counters come back 0, the scorer logs a warning (prompt likely fell below the minimum).
- **Scoring model**: `scoring.model` in `settings.yaml`. **October 2026: Claude Opus (`claude-opus-5-5`)**; Haiku 4.5 and Sonnet 5.5 also work. Per-model prices (input, output, cache write, cache read per million tokens) live in the `prices` block.
- **Tool call mode** (`scoring.force_tool_choice`, passed in by service): `true` → forced tool call (`tool_choice` = the scoring tool; Haiku). `false` → `tool_choice: auto`, `strict: true` on the tool, and "Always respond by calling the scoring tool." appended to the user message (Sonnet/Opus 5.5 reject a forced `tool_choice`). A response with no tool call = failed job.
- **One tool schema for all models**: generated from the pydantic model with `additionalProperties: false` and no `minimum`/`maximum` (strict tools reject them).
- **Pydantic validation**: the tool input is validated against the pydantic schema (`match_score` 1.0–10.0, rounded to one decimal). Invalid response = failed job (skipped, not written to sheet, retried next run).

## US-Only Filtering (Two Layers)

1. **Cheap location filter** (in code, after title filter, before cap): drops a job only if a checked field (ATS: location; search: title + raw URL, never the snippet) contains a clearly non-US keyword (`location_filter.exclude`) AND no checked field contains a US signal (`location_filter.us_signals`). Same whole-word, case-insensitive matcher for both lists. Jobs with US locations, "Remote", "Remote - US", mixed US + non-US locations, or blank/unknown locations pass through.
2. **Scorer hard blocker**: a job based outside the US receives score 1–2 regardless of other fit signals.

## Error Handling

- **Per-job failure** (JD fetch timeout, scoring error, pydantic validation failure, any other exception while processing one job): caught per job, logged, job skipped, run continues. **Exception:** Anthropic authentication / permission errors are re-raised (a bad key would fail every job). Failed jobs are not written to the sheet, so they're retried on the next run (within the 24h search window).
- **Per-source failure**: one bad ATS slug or one failed Serper page logs a warning and is skipped; it never crashes the run.
- **No custom retry code.** Anthropic SDK handles its own retries. JD fetches, ATS calls and Serper calls: one request each with a timeout (`request_timeout_seconds` in `settings.yaml`).
- **Fatal errors** → crash with non-zero exit code → GitHub Actions goes red → GitHub emails you:
  - sheet unreachable (read, append or title; HTTP errors become `RuntimeError` with the status)
  - any of `SHEET_ID` / `SERPER_API_KEY` / `ANTHROPIC_API_KEY` / `GOOGLE_APPLICATION_CREDENTIALS` missing: `main.py` checks all four before doing any work and exits 1 with one error naming every missing variable
  - invalid Anthropic key
  - every search query failed
  - every scoring attempt failed (nothing written)
- **Single append at the end**: rows are written in one append after all scoring, so a crash mid-run writes nothing.
- **Summary line** printed at the end of every run.

## Logging

- **Storage**: console output only, kept by GitHub Actions in the run log (90-day default retention). No Runs/Errors tab, no log files, no artifacts.
- **Never log** JD text, `profile.yaml` / `candidate.md` contents, or secrets. Failed-job lines show the URL + error type + the exception message on one line, truncated to 150 characters.
- **Fixed prefixes**: key lines start with a fixed uppercase prefix so a month of logs can be grepped: `RUN`, `ATS`, `QUERY`, `FUNNEL`, `DROP`, `JD`, `FAIL`, `SCORES`, `COST`.
- **What each run logs**:

| Prefix | Content |
|---|---|
| `RUN` (start) | After the existing `Writing to sheet: <title>` line: run start (configured tz), effective cap, `ats_lookback_days`, scoring model, existing URL count in the sheet |
| `ATS` | Totals (companies polled, jobs within lookback); companies with 0 jobs; companies with errors (slug + HTTP status) |
| `QUERY` | One line per query: pages fetched, results, new (after dedup), passed filters, new+passed from page 2+. Then one line with total Serper calls this run |
| `FAIL` | Search errors (query + HTTP status); failed jobs (URL + error type + message truncated to 150 chars) |
| `FUNNEL` | found, dupes (sheet), dupes (in-run), no URL, new, title dropped, location dropped, eligible, over cap, experience dropped |
| `DROP` | One line per filtered job. Title drops: the matched exclude keyword, or `no include match`. Location drops: the matched keyword and where it matched (`location` / `title` / `url`). Experience drops: `DROP experience N \| <phrase> \| <title> \| <url>`. Over-cap jobs: `DROP cap` lines with titles, only when the over-cap count > 0. Any `\|` inside a title is written as `/` so the ` \| ` separators stay parseable |
| `JD` | Source counts: full fetched, snippet fallback, ATS full |
| `SCORES` | Histogram: 8–10, 6–7.9, 3–5.9, 1–2.9 |
| `COST` | Tokens: input, output, cache read, cache write; estimated cost from per-model prices in `settings.yaml` (never hardcoded) |
| `RUN` (end) | Elapsed time |

- **Summary line** (`found …, new …, filtered …`) stays unchanged and unprefixed.
- **`$GITHUB_STEP_SUMMARY`**: when the env var is set, append a short markdown summary (funnel, per-query table, score histogram, cost). Skip silently when unset (local runs).

## Secrets

- **Locally**: `.env` file (gitignored), loaded with `python-dotenv`. Service account JSON is a gitignored file; `.env` holds its path via `GOOGLE_APPLICATION_CREDENTIALS`.
- **Local .env only ever contains the TEST sheet ID.** The real (production) sheet ID exists only in GitHub secrets. There is no `--test` flag: the environment decides which sheet is used, so a local run can never write to the real sheet.
- **CI**: GitHub Actions secrets, injected as env vars. Service account JSON stored as base64-encoded secret, decoded at runtime.
- **Gitignored personal files** (`config/profile.yaml`, `config/candidate.md`): stored as GitHub secrets (base64), decoded to files at runtime. `.example` versions committed to show the format.
- **Google Sheet ID**: env var (`SHEET_ID`), not hardcoded.
- **SERPER_API_KEY**: env var for the Serper search API.
- **Never print secrets or file contents in logs.**

## Testing

- **Unit tests**: JSON fixtures (one real response per external API, scrubbed of personal data/keys). Tests replace `*_client.py` with fakes returning fixtures.
- **Local e2e**: separate "test" Google Sheet (`SHEET_ID` in local `.env` points to it). Real sheet only used by CI.
- **`--max-jobs` flag**: overrides the cap for one run (e.g., `--max-jobs 2`), keeping local runs cheap.
- No `--dry-run` flag. Test sheet + `--max-jobs` cover it.

## Key Decisions

| Decision | Why |
|---|---|
| Two job sources (search + ATS direct) | Search finds jobs across the web; ATS direct catches jobs on tracked company boards that search might miss or index late |
| `ats_lookback_days` default 2 | Prevents old jobs flooding the run; with twice-daily runs each ATS job gets ~4 chances to be scored |
| `max_jobs_per_run` 150 | First production run had ≈127 eligible jobs; over-cap search jobs are lost once they leave the 24h window |
| ATS merged before search | Same URL from both sources → the ATS copy (structured fields, full JD) wins dedup |
| Location filter uses the raw URL; normalized URL is the dedup key and stored value | Normalization strips params like `?location=` that the location filter needs |
| `-intitle:` exclusions in queries (Staff, Principal, Director, Senior, Sr), not bare `-word` | Bare `-staff` drops pages that merely mention "staff engineers" |
| Remote aggregator boards dropped (Remote Rocketship, Working Nomads, RemoteOK, We Work Remotely) | Duplicates under other URLs, non-US jobs, listing pages instead of job pages |
| SmartRecruiters / Jobvite / Breezy as search queries only | An ATS client for SmartRecruiters needs pagination + an extra call per JD + per-language dupes (parking lot) |
| Pinterest, Reddit, Spotify via ATS, not company_sites | Public ATS boards give full JDs and structured fields |
| Prompt caching + calibration section | Cuts input cost; calibration makes scores consistent and pushes the cached prompt above Haiku's 4,096-token minimum |
| No URL hash IDs | Equivalent to URL match. Cross-source dupes (same job, different URL) → parking lot |
| No NVIDIA path lowercasing | Risks breaking case-sensitive links |
| Logs: console only, no new storage | GitHub keeps run logs 90 days; a Runs/Errors tab is out of scope for v1 |
| `ats_client.py` separate from `jd_client.py` | One client per external system: ats_client handles Greenhouse/Lever/Ashby APIs (list call returns jobs with JD text, one call per company); jd_client handles plain HTTP fetches for search-sourced job pages |
| title/company from source when available | ATS APIs return structured data; Haiku extraction only needed for search results |
| Pydantic validation on scorer output | Catches malformed model responses before they hit the sheet |
| Scoring model is a setting; tool mode is a setting (`force_tool_choice`) | Sonnet/Opus 5.5 reject a forced `tool_choice`; one code path supports both. Opus for October |
| `config/` at repo root, gitignored | Personal files (profile.yaml, candidate.md) stay out of repo; .example versions document format |
| `settings.yaml` committed | Caps, filter keywords, and lookback days are config, not secrets |
| Append-only sheet writes | User's checkboxes and manual edits must survive every run |
| Code appends "" (blank) for checkbox columns | Pre-filled checkboxes on empty rows count as data, so the append API writes after them (row 1001 in Task 1 testing). Checkboxes are never pre-filled; marking applied/contacted is manual |
| No `remote` column | User is open to all work modes; included in location text |
| Write "not found" email rows | Prevents re-spending Apollo credits on same person |
| Score cap applied after dedup + title filter + location filter | Cap counts real US-based SWE candidates, not noise |
| Senior/Sr excluded in title filter and queries | Oct 2026 sheet review: mostly wasted scoring |
| Newest jobs scored first | If capped, better to score fresh postings |
| US-only: two-layer filtering | Cheap keyword filter catches obvious non-US before burning scorer credits; scorer catches the rest |
| Location within the US is never a penalty | Candidate is open to anywhere in the US; only Greater Seattle gets a small bonus |
| Serper (serper.dev) as search API, not Brave | Brave's "past day" filter returned far weaker results in manual testing; Serper wraps Google and gives better recency |
| No Google scraping with Selenium | Blocked quickly (CAPTCHAs, especially from GitHub Actions IPs), against Google ToS, and brittle. Serper is the paid, reliable alternative |
| JD snippet fallback for search results | Many career pages are JavaScript-heavy and return no text on plain fetch. Scoring from title + snippet is better than skipping the job entirely |
| `max_pages_per_query` (currently 4 in `settings.yaml`) | Each page is a paid Serper call (1 credit at `num=10`); pagination stops early on an empty page, so the cap is a ceiling, not the typical spend |
| Greenhouse `first_published` as posted_date | `updated_at` changes whenever an old posting is edited, making old jobs look new. `first_published` reflects the original publish date. Falls back to `updated_at` if `first_published` is missing. |
| All dates stored in one timezone | `timezone` setting in settings.yaml (default `America/Chicago`). CI runs in UTC; without conversion the 9 PM CT run would show the next day's date. Both `posted_date` and `date_found` use the configured timezone. |
| Serper dates converted to absolute dates | Serper returns relative strings ("21 hours ago") or absolute ones ("Sep 28, 2026"). Conversion uses the run's start time so all dates in a run are consistent. Unparseable dates → blank `posted_date`. |

## Open Risks

- **Serper query syntax**: Google accepts `site:` + `OR` + brackets in the browser. **Resolved in Task 2**: Serper passes them through correctly (returned links match the `site:` domain).
- **Serper API quota**: Serper has monthly credit limits. Each query page is one credit at `num=10`. Ceiling = enabled queries × `max_pages_per_query` × 2 runs/day (23 × 4 × 2 = 184 credits/day with current config). The `QUERY` log lines report total Serper calls per run.
- **ATS API rate limits**: Greenhouse/Lever/Ashby public APIs may have undocumented rate limits. If companies.yaml grows large, requests may get throttled.
- **ATS slug drift**: company slugs in companies.yaml can change if a company rebrands or migrates ATS. Broken slugs fail silently (empty results, not errors). Periodic manual check needed.
- **JD fetch failures**: some company career pages block automated requests or require JavaScript. No headless browser is in scope — those jobs fall back to snippet-based scoring instead.
- **Apollo credit burn**: each people/match call costs credits. No cap is built into email_finder (it's manual and one-at-a-time, so you're the cap).
- **Sheet row limits**: Google Sheets caps at ~10M cells. Not a near-term risk, but if you never clean up, the sheet grows forever.
- **Snippet-based scores are less reliable**: when the full JD can't be fetched, scoring from title + snippet may miss requirements or benefits. The `jd_source` column lets you see which scores are snippet-based.
- **Scorer output consistency**: tool call (forced, or strict + auto) + pydantic validation catches malformed responses. In auto mode the model could skip the tool; that response counts as a failed job. Invalid responses are skipped and retried next run.
- **Checkbox column append behavior**: if the whole `applied` / `contacted` column is pre-filled with checkboxes (e.g. via Google Sheets data validation on 1000 rows), those cells may be treated as containing FALSE. The append API would then add new rows after the last "filled" row (e.g. row 1001) instead of after the last data row (e.g. row 2). **Resolved in Task 1**: confirmed — with checkboxes pre-filled on rows 2–1000, the append landed on row 1001. Decision: code writes "" (blank) to `applied` / `contacted`, checkboxes are never pre-filled on empty rows, and marking is manual.
