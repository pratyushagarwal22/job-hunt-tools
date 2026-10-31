# DESIGN.md — job-hunt-tools

## Goal

Find fresh US-based software engineering jobs, score them against my profile, and save the best to a Google Sheet. Separately, find contact emails for outreach.

## In Scope

- `job_scanner/` — automated pipeline (GitHub Actions cron, twice daily on UTC crons `'7 10 * * *'` (5:07 AM CDT / 4:07 AM CST) and `'7 20 * * *'` (3:07 PM CDT / 2:07 PM CST). GitHub scheduled runs are best-effort and often start hours late, so runs are set early; manual runs start immediately.)
- `email_finder/` — manual CLI tool
- One Google Sheet, two tabs ("Jobs" and "Contacts")
- Two job sources: search API queries and direct ATS company board polling

## Out of Scope

- No UI, no server, no database
- No LinkedIn scraping: our code never fetches LinkedIn pages; email providers receive the URL.
- No LangChain / agent framework
- No Google Custom Search API
- No phone numbers from any email provider (email only)
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
│   ├── candidate.example.md    # committed — shows format
│   ├── outreach_examples.md     # connection note style examples for --draft (gitignored, optional)
│   └── outreach_examples.example.md  # committed — shows format
├── job_scanner/
│   ├── main.py                  # entry point, CLI args (--max-jobs)
│   ├── service.py               # pipeline orchestration logic
│   ├── search_client.py         # Serper API (serper.dev)
│   ├── ats_client.py            # Greenhouse/Lever/Ashby public APIs (list call returns jobs with JD text, one call per company)
│   ├── date_utils.py            # parsers → tz-aware datetimes; formatters → YYYY-MM-DD / YYYY-MM-DD HH:MM strings
│   ├── jd_client.py             # plain HTTP fetch for non-ATS job pages
│   ├── scoring_client.py        # Anthropic structured JSON call with the scoring model
│   ├── sheets_repo.py           # read/append Google Sheet (Jobs tab only; header-driven columns)
│   ├── url_utils.py             # normalize URLs (strip tracking params); platform_for_url
│   ├── title_filter.py          # cheap keyword include/exclude
│   ├── location_filter.py       # drop clearly non-US jobs before scoring
│   ├── experience_filter.py     # smallest required years of experience in a JD
│   ├── queries.yaml             # search queries (one per job board site + company_sites)
│   ├── companies.yaml           # tracked companies on Greenhouse/Lever/Ashby
│   └── settings.yaml            # committed config (caps, search params, filters, scoring model)
├── email_finder/
│   ├── main.py                  # CLI entry point (URLs, --file, --draft)
│   ├── service.py               # normalize + dedup input, provider chain, row building
│   ├── input_file.py            # input line parsing (comments, blanks, trailing # note)
│   ├── prospeo_client.py        # Prospeo enrich-person (provider 1)
│   ├── apify_client.py          # Apify actor vulnv/linkedin-email-finder (provider 2)
│   ├── apollo_client.py         # Apollo people/bulk_match (disabled; paid plan only)
│   ├── outreach_client.py       # Claude LinkedIn connection note drafting
│   ├── sheets_repo.py           # read/append Google Sheet (Contacts tab only; header-driven columns)
│   ├── settings.yaml            # committed config (cap, provider order, rate limits, outreach model)
│   ├── linkedin-profiles.txt    # input URLs, one per line (gitignored)
│   └── linkedin-profiles.example.txt  # committed — shows format
├── shared/
│   ├── sheets.py                # Google Sheets auth + low-level read/append + header_positions / column_letter
│   └── linkedin_url.py          # normalize LinkedIn URLs for dedup
├── tests/
│   ├── test_experience_filter.py  # experience filter unit tests (one test_*.py per module)
│   └── fixtures/                # scrubbed real API responses (JSON)
├── docs/
│   ├── DESIGN.md                # this file
│   ├── PLAN.md                  # build plan, one task per session
│   └── PROJECT_CONTEXT.md       # summary + "why" (gitignored)
├── _legacy/                     # old broader-search files (gitignored, not used by code)
├── _scratch/                    # local-only one-off scripts, e.g. backfill_platform.py (gitignored, not in the repo)
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

**Layering rule**: `main.py` is thin (parse args, call service). `service.py` holds all logic. `*_client.py` files only call external APIs — one client per external system. `sheets_repo.py` is the only file that touches the sheet. Both `sheets_repo.py` files use `shared/sheets.py` for auth and raw sheet operations. Settings are passed into clients as arguments; clients never read `settings.yaml`. `email_finder` never imports from `job_scanner`; code both need lives in `shared/`.

**Run commands** (from the repo root): `python -m job_scanner.main`, `python -m email_finder.main [urls ...] [--file PATH] [--draft]`.

## Data Flow — job_scanner

1. Load `queries.yaml`, `companies.yaml`, `config/profile.yaml`, `config/candidate.md`, `settings.yaml`. Create `run_start` once, tz-aware in the configured `timezone`. Read the "Jobs" header row once and map columns by header name; if any of the 14 Jobs headers is missing, stop with a fatal error naming them (nothing is written). Read existing URLs from the column headed `url` (normalized).
2. **Source B — ATS direct** (collected first): for each company in `companies.yaml` → call Greenhouse/Lever/Ashby public job list API → keep only jobs posted in the last N days (`ats_lookback_days` in `settings.yaml`, default 2). Date fields: Greenhouse `first_published` (fallback `updated_at`), Lever `createdAt` (Unix ms), Ashby `publishedAt`. `posted_date` is kept as the raw API value; `date_utils.py` parses it. Bad slug / non-2xx / non-JSON → warning, company returns no jobs.
3. **Source A — Search API (Serper)**: for each enabled query (`queries` + `company_sites`) → call Serper with the `search` settings: `tbs=qdr:d` (past 24 hours), `gl=us` (US-biased results — sets where the search appears to come from, does NOT filter by job location), `num=10` results per page. Paginate up to `max_pages_per_query` pages (each page is one paid Serper credit). Stop early if a page returns no results or errors. **Site guard**: a `google.com` or `www.google.com` result with path `/goto`, or `/url` whose `url`/`q` value does not start with `http`, is an unresolved redirect (`DROP redirect`): counted as `redirect` on the query and in `FUNNEL` (`found = redirect + off-site + dupes + no URL + new`), not off-site, and not scored or written; above 30% of search results, one `SEARCH` warning names the Serper-side skip. A `/url` link whose param starts with `http` is still unwrapped (counted as `QUERY total unwrapped google redirects`) before the host check. A query containing `site:<domain/path>` then drops any result whose host is neither that domain nor a subdomain of it (the path after the domain is ignored). Those results stay in `found`, count as `off-site` on that query and in `FUNNEL`, and are logged `DROP offsite | <title> | <url>`. They are not deduped, filtered further, scored, or written.
4. Merge into one list, **ATS jobs first**, so when the same URL comes from both sources the ATS copy (structured title/company/location + full JD) wins dedup.
5. Normalize URLs (`url_utils.normalize_url`): strip `utm_*`, `ref`, `source`, `gh_src`, `team`, `page`, `location`, `action`, fragments, trailing slashes and a trailing `/apply` path segment. Lowercase scheme + domain, keep path case. Never strip `gh_jid` or `id` (job IDs on some career sites). The normalized URL is the dedup key and the value stored in the sheet. Jobs with no URL are dropped.
6. Deduplicate: against existing URLs in the "Jobs" sheet AND within the current run
7. Title keyword filter (`title_filter` in `settings.yaml`, case-insensitive, whole-word): at least one `include` keyword and no `exclude` keyword. `always_allow` phrases (currently "Member of Technical Staff") count as an include match and are removed from the title before the exclude check, so "Member of Technical Staff" passes but "Senior Member of Technical Staff" drops on "Senior". Include covers SWE / SDE / Developer / Frontend / Front End / UI / Mobile / iOS / Android / Backend / Full Stack and early-career titles; exclude covers seniority (Senior, Sr, Staff, Principal, Lead, …), interns, embedded/firmware, nursing, and non-engineering "Developer" roles (Developer Advocate, Developer Relations, Business Developer).
8. Location filter: drop jobs whose location is clearly outside the US (`location_filter.exclude` in `settings.yaml`), unless a US signal (`location_filter.us_signals`) appears in any checked field — so "New York or Zurich" or "San Francisco; London" is kept. US signals: "United States", "USA", "U.S.", "Remote - US", "Remote (US)", "US Remote", full state names (except Georgia) and major US tech cities. Never bare "US" or two-letter state codes ("us" matches "join us"). For ATS jobs, check the location field. For search results (no structured location field), check the title and the **raw** (pre-normalization, URL-decoded) URL — not the snippet, which often mentions other offices or teams (scorer's non-US hard blocker catches the rest) — normalization strips params like `?location=` that the filter needs (catches locations in URLs, e.g. NVIDIA's `Taiwan-Taipei` or Google's `?location=India`). Keep US locations, "Remote", "Remote - US", and blank/unknown.
9. Apply score cap (`max_jobs_per_run`, default 150, overridable via `--max-jobs`). Newest jobs first — sort on the full datetime (not just date); jobs without a posted_date go after all dated jobs, in their original order. Over-cap jobs are not written to the sheet.
10. For each surviving job → ATS jobs already have full JD text from the list call in step 2 (if an ATS job has no JD text, it is scored from title + location as `snippet`). Search-sourced jobs on Ashby, Greenhouse, or Lever get JD and location from the public posting API, counted as "full fetched": `jobs.ashbyhq.com/<slug>/<job id>` via `ats_client.get_ashby_board` (no lookback filter; board fetched once per slug per run, job matched by id; `descriptionPlain`, else `descriptionHtml` as text; each posting includes `location`); a `greenhouse.io` job URL (`/<slug>/jobs/<id>`) via `ats_client.get_greenhouse_job` (`boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}`); `jobs.lever.co/<slug>/<id>` via `ats_client.get_lever_job` (`api.lever.co/v0/postings/{slug}/{id}`). Any API failure, missing posting, or empty text falls through to the normal path (Ashby board unavailable or job id not on the board → an `ASHBY` log line; Greenhouse/Lever errors → a `GREENHOUSE` or `LEVER` log line). Other search-sourced jobs, and those fallbacks, fetch full JD via `jd_client.py` (plain HTTP fetch, browser User-Agent, strips script/style/noscript/svg/template). If the fetch fails, returns non-2xx, is not HTML, or has fewer than `jd_min_chars` (500) characters of visible text (common on JavaScript-heavy career sites), fall back to scoring from the search result's title + snippet instead. When a structured location came back, run `location_drop_reason` on it in ATS mode (`is_ats=True`, same `location_filter` settings). A drop is not scored or written: `DROP location <keyword> (ats-location) | <title> | <url>`, and it counts as `location dropped post-fetch` inside the `eligible` equation.
11. **Experience filter** (`experience_filter.py`, after JD fetch, before scoring): find the smallest required years of experience in the JD (or the title + snippet fallback). Text is cleaned first (HTML entities unescaped, tags stripped, whitespace collapsed). Counts only experience requirements: digits or one–ten, `N+`, `N-M` / `N to M` (→ N), "at least N", "minimum (of) N", tied to experience ("years of experience", "years' experience", "years in/with/of <skill>", degree + N years, or "experience" later in the same sentence; `N+` and ranges count on their own). Ignores unrelated years ("founded 10 years ago", "10 years in business", "5 year old", "years of age" / "aged N", and any number >= 15). Drop when required years > `experience_filter.max_years_required` (2). No requirement found → keep. Dropped jobs are not scored or written and count in `filtered`. The filter gets text only; service compares against the setting.
12. For each remaining JD (or snippet fallback) → one Claude call (`scoring.model`, `scoring.max_tokens`; JD truncated to `scoring.max_jd_chars`) with `config/candidate.md` + `config/profile.yaml` context in a cached system prompt → scoring tool call returning structured JSON (forced or auto per `scoring.force_tool_choice`, see Scoring Rules)
13. Validate the model's tool input with a pydantic schema. Invalid response = failed job (skip, not written to sheet).
14. Append all scored rows to "Jobs" tab in **one append at the end of the run** (append-only, never overwrite)
15. Print summary: `"found 42, new 12, filtered 3, scored 8, failed 1, skipped (cap): 0"`. `new` = after dedup, before filters; `filtered` = dropped by the title, location or experience filter.

### title and company fields

- **ATS-sourced jobs**: use the title and company from the ATS API response (structured, reliable).
- **Search-sourced jobs**: use the scoring model's extracted values (the search result snippet may not have clean title/company).

## Data Flow — email_finder

0. **Sheet target**: email_finder runs only locally, so it writes to the TEST sheet in `.env` by default. Real use: `SHEET_ID=<prod id> python -m email_finder.main ...`.
1. **Env check** (`main.py`): `SHEET_ID`, `GOOGLE_APPLICATION_CREDENTIALS`, plus the key of each enabled provider only (`PROSPEO_API_KEY`, `APIFY_TOKEN`, `APOLLO_API_KEY`); `ANTHROPIC_API_KEY` only with `--draft`. Any missing → one error naming all of them, exit 1. Then print `Writing to sheet: <title>`.
2. **Input**: `python -m email_finder.main [urls ...] [--file PATH] [--draft]`. No URLs → read `email_finder/linkedin-profiles.txt` (gitignored; `linkedin-profiles.example.txt` is committed). One URL per line, `#` lines ignored, optional trailing `# note` → `notes` column.
3. Read the "Contacts" header row once and map columns by header name; any missing header → fatal, nothing written. Read existing keys once (`get_existing_keys`): normalized `linkedin_url` values and lowercase `email` values. email_finder dedups on the URL set; the Task 9b import uses both.
4. Normalize every input URL (`shared/linkedin_url.py`: always `https://`, scheme added if missing, any `linkedin.com` subdomain such as `www.`, `uk.` or `m.` → `linkedin.com`, lowercase, no query, fragment or trailing slash; extra path after `/in/<slug>` such as `/recent-activity/`, `/overlay/contact-info/` or `/details/experience/` is trimmed to `https://linkedin.com/in/<slug>`; `/pub/` and other non-`/in/` paths are not trimmed and get ignored), keep only `/in/` profile URLs (others → `ignored <url part> (not a profile URL)`, the line's URL part, never its trailing note), dedup within the input (later copies → `duplicate <url>`) and against the Contacts tab (already present → `exists`).
5. `max_urls_per_run` (20) counts the URLs left after removing existing and in-input duplicates; more than that → exit 1 before any provider call.
6. **Provider chain**: providers from `settings.yaml` `providers`, in order (default `[prospeo, apify]`; `apollo` available). Each provider runs once over all URLs still unmatched and passes its no-matches to the next.
   - **Prospeo** — `POST https://api.prospeo.io/enrich-person`, header `X-KEY`, body `{"data": {"linkedin_url": ...}}`. One call per URL, `prospeo.timeout_seconds` (15), `prospeo.min_interval_seconds` (3.1) between calls — under 20/min for any cap (limits: 1/s, 20/min, 50/day; 100 free credits/month); hitting the 50/day limit returns 429, which the stop rule in step 7 covers. Match (`error: false`): email `person.email.email`, status `person.email.status`, name `person.full_name`, title `person.current_job_title`, company `company.name`; the response also has `person.mobile`, which is never read or stored. No match: `{"error": true, "error_code": "NO_MATCH"}` at any HTTP status, or a match with a null `person.email`, an empty/null `person.email.email`, `person.email.revealed` false, or an email containing `*` (masked). A missing `revealed` is not a no-match.
   - **Apify** — actor `vulnv/linkedin-email-finder` via `POST https://api.apify.com/v2/acts/vulnv~linkedin-email-finder/run-sync-get-dataset-items`, header `Authorization: Bearer <APIFY_TOKEN>`, body `{"urls": [...]}`, HTTP 201. One actor run for all its URLs, `apify.timeout_seconds` (300). One item per URL (`name`, `company`, `domain`, `email`, `found`, `linkedin_url`; no status, no title), mapped back by normalized `linkedin_url`. No match: `found: false` with `error` exactly `"No email found in response"`. An item with `found: true` and a blank email, a non-boolean `found`, or a missing `linkedin_url` is an error. Pay per email found (~$0.026, from the $5/month free credit).
   - **Apollo** (disabled; free plan returns 403 `API_INACCESSIBLE`) — `people/bulk_match` in batches of `apollo.batch_size` (10), `apollo.timeout_seconds` (15), `apollo.min_interval_seconds` (1.2) between batches, `reveal_personal_emails: false` and `reveal_phone_number: false` sent explicitly. Returned records mapped by normalized `linkedin_url`; a submitted URL with no returned record is a no-match, and so are `null` entries and records with a blank email or URL (unverified against a real response, see Open Risks).
   - A no-match falls through to the next provider. Matched by none → row with blank `email`, `email_status` `"not found"`.
   - `email_status` is stored as the provider returns it, lowercased; Apify rows get `"unverified"`.
7. **Errors**: only the no-match cases above count as no-match. Anything else (non-2xx, other error codes, 401/403/429, timeout, malformed body, an Apify item missing for a submitted URL) stops the run: append the rows already resolved, do **not** write the unresolved URLs (retried next run), print which URLs are pending, exit 1.
8. `--draft` → one Claude call per found row (`outreach_client.py`, `outreach.model` / `outreach.max_tokens` / `outreach.max_chars`) that writes a **LinkedIn connection note** into `outreach_line`: plain text, one line, "Hi <first name>," on the same line, no sign-off. Model input: the contact's first name, company and title, the row's `# note` text (may be empty), `config/candidate.md`, and `config/outreach_examples.md` if it exists (gitignored, optional; copy from `config/outreach_examples.example.md`; missing → no examples, no error). Notes that mention a role → the note references it; no notes → a networking note. Prompt rules: only facts from candidate.md; never invent facts about the person or company beyond the given fields; never mention visa, sponsorship, phone numbers, email addresses or salary; examples are for style, not to copy. **Length**: a reply longer than `max_chars` is trimmed at the last sentence end (`.`, `!` or `?` followed by a space or the end) within the limit; no sentence end within it → draft failure. `stop_reason` `max_tokens` → draft failure (never a cut-off note). Never logged. A failed draft → that row gets a blank `outreach_line` and a warning; the run continues. An Anthropic authentication / permission error → drafting stops, all rows are still appended, exit 1.
9. Build row dicts: `email_source` = provider name; `contacted` blank; `date_found` = `YYYY-MM-DD HH:MM` in `timezone`, formatted inline with `zoneinfo`. **One append at the end** (or at the stop in step 7). Rows are written even when no email was found, to prevent re-lookups.
10. **Output**: log lines (stderr) `<provider>: looking up N URL(s)` before each provider runs and `STOP <provider>: <message>` when a provider error stops the run (message = provider + HTTP status / error code, or the exception type). Then one line per input line — `ignored <url part> (not a profile URL)` (never the trailing note), `duplicate <url>`, or the URL + outcome (`exists` / `found via <provider> <status>` / `not found` / `pending`). Never print emails, names or keys. End with a summary line: input, ignored, duplicate, skipped existing, found per provider, not found, pending.

**Clients** (`prospeo_client.py`, `apify_client.py`, `apollo_client.py`): one request per call, no retries, timeout passed in, never read settings. Keys only in headers (`X-KEY`, `Authorization: Bearer`, `x-api-key`), never in URLs or logs. Error messages carry only the provider name and HTTP status / error code, never the response body (it can hold personal data).

**`email_finder/settings.yaml`**: `timezone`, `max_urls_per_run` (20), `providers`, `prospeo.timeout_seconds` (15), `prospeo.min_interval_seconds` (3.1), `apify.actor_id`, `apify.timeout_seconds` (300), `apollo.batch_size` (10), `apollo.timeout_seconds` (15), `apollo.min_interval_seconds` (1.2), `outreach.model` (`claude-sonnet-5-5`), `outreach.max_tokens` (4096; thinking tokens count against it, so a `max_chars` note never hits it), `outreach.max_chars` (200, also the default when the key is missing).

**Legacy import** (Task 9b, one-off): `_scratch/import_legacy_contacts.py` (gitignored) copies old Apollo contacts from `_legacy/contacts-master-latest.json` into the Contacts tab with `email_source` `"apollo-legacy"`, using email_finder's Contacts repo. `email_status` = legacy `email_confidence`, lowercased (legacy `email_status` is always empty, not used); `notes` = kind (`recruiter` / `hiring manager`) and `emailed YYYY-MM-DD` from `last_email_sent_at`, joined with `"; "` (`last_contacted_at` and the rest of legacy `notes` are not copied); unparseable `contact_id` → blank `date_found`. A row is skipped if its normalized LinkedIn URL or its lowercase email was already seen (Contacts tab or earlier in the file); first row wins. Dry run by default, `--apply` to write.

## Sheet Schema

### Jobs tab

Header row (tab-separated, paste into A1):

```text
applied	match_score	platform	url	title	company	location	visa_mention	jd_source	core_requirements	reason	posted_date	date_found	source_query
```

Columns are matched by header name (trimmed, case-insensitive), not position, so they can be in any order. All 14 headers must exist in row 1 or the run fails before writing anything. `sheets_repo` takes rows as dicts keyed by header name and writes each value in its header's column; columns with other headers stay blank. Appends use `RAW` + `INSERT_ROWS`.

| Column | Type | Notes |
|---|---|---|
| applied | Checkbox | Code appends "" (blank). Marked by hand; never pre-fill checkboxes on empty rows (see Open Risks). Never overwritten by code. |
| match_score | Number | 1.0–10.0 from the scoring model (one decimal, e.g. 7.5) |
| platform | String | ATS jobs: the ATS name (`greenhouse` / `lever` / `ashby`). Search jobs: `url_utils.platform_for_url` by host — `ashby`, `greenhouse` (also any URL with `gh_jid`), `lever`, `workable`, `wellfound`, `workatastartup`, `welcometothejungle`, `smartrecruiters`, `jobvite`, `breezy`, `a16z`, `workday`, `company_site` (big-company career hosts), else `other`. Older rows: `_scratch/backfill_platform.py` (local-only: gitignored, not in the repo). |
| url | String | Normalized, used as dedup key |
| title | String | From ATS source data when available; from the scoring model for search results |
| company | String | From ATS source data when available; from the scoring model for search results |
| location | String | Includes remote/hybrid/onsite as plain text |
| visa_mention | String | From the scoring model's output |
| jd_source | String | "full" (fetched JD or ATS API) or "snippet" (scored from search title + snippet) |
| core_requirements | String | From the scoring model's output |
| reason | String | One-line match reason from the scoring model |
| posted_date | String | `YYYY-MM-DD`. ATS: from ATS API date field. Search: converted from Serper's relative `date` string using the run's start time. Blank if missing or unparseable. |
| date_found | String | `YYYY-MM-DD HH:MM` (local time per `timezone` setting in settings.yaml). |
| source_query | String | Which query or company found this job |

### Contacts tab

Header row (tab-separated, paste into A1):

```text
contacted	name	company	title	email	email_status	email_source	linkedin_url	notes	outreach_line	date_found
```

Header-driven like Jobs: `CONTACTS_HEADERS` in `email_finder/sheets_repo.py` lists the 11 headers; columns are matched by header name (trimmed, case-insensitive) using `shared/sheets.header_positions`, so any order works. All 11 must exist or the run fails before writing anything. Rows are dicts keyed by header name; other columns stay blank. Appends use `RAW` + `INSERT_ROWS`.

| Column | Type | Notes |
|---|---|---|
| contacted | Checkbox | Code appends "" (blank). Marked by hand; never pre-fill checkboxes on empty rows (see Open Risks). Never overwritten by code. |
| name | String | From the provider that matched (blank if not found) |
| company | String | From the provider that matched (blank if not found) |
| title | String | From the provider that matched; Apify returns no title, so blank for Apify rows |
| email | String | Blank if not found |
| email_status | String | The provider's status as returned, lowercased (Prospeo `person.email.status`, Apollo `email_status`); `unverified` for Apify rows; `not found` when no provider matched; legacy rows: legacy `email_confidence` (`high` / `low` / blank) |
| email_source | String | Provider name: `prospeo` / `apify` / `apollo`; `apollo-legacy` for rows imported in Task 9b; blank when no provider found an email |
| linkedin_url | String | Normalized input URL, used as dedup key (legacy rows may be blank; the import also dedups them by lowercase email) |
| notes | String | Optional trailing `# note` from the input file; for imported rows the kind (`recruiter` / `hiring manager`) and `emailed YYYY-MM-DD`, joined with `"; "` |
| outreach_line | String | Claude-drafted LinkedIn connection note (≤ `outreach.max_chars`), blank if --draft not used or the draft failed |
| date_found | String | `YYYY-MM-DD HH:MM` (local time per `timezone` in `email_finder/settings.yaml`). |

## Scoring Rules

Scorer receives two inputs: `config/candidate.md` (one-page evidence doc) and `config/profile.yaml` (match criteria and evaluation rules).

- **Hard blockers (score 1–2)** — override all score floors:
  - JD **explicitly** says no visa sponsorship or US citizens only. If the JD doesn't mention visas, that is NOT a blocker. Security clearance is also a blocker. Candidate is on F-1 OPT.
  - Job is based outside the US.
- **Location within the US is never a penalty.** Greater Seattle area (Seattle, Bellevue, Redmond, Kirkland, Tacoma, Everett) gets a small bonus (+0.25 to +0.5).
- **Score floor of 6** for early-career SWE/FDE/founding/product engineer roles (per `ai_evaluation` rules in `profile.yaml`). Only score below 6 when the JD clearly requires 3+ years AND targets mid/senior/staff/principal, or a hard blocker applies.
  - **Exception (3–5)**: the role's core is a specialty with no evidence in the candidate's background (e.g. embedded firmware, chip design), or the work is mostly outside general software engineering (e.g. QA-only, IT support, sales engineering).
- **Personal rules live in `profile.yaml`**, not the prompt. The system prompt holds only generic rules (hard blockers, US location never a penalty, facts-only) plus a generic **score calibration** section: bands 9–10 / 7–8 / 6 / 3–5 / 1–2, how to apply them, one example job per band, and snippet-only rules. Personal specifics (F-1 OPT, the Seattle bonus) come from `profile.yaml`; on conflict, profile rules win.
- **Prompt caching**: the system block (prompt + `candidate.md` + `profile.yaml`, ~6,900 tokens on Opus 5.5 (measured; varies by model tokenizer)) is marked `cache_control: ephemeral`. Haiku 4.5's minimum cacheable prompt is 4,096 tokens, so the calibration section is padded with useful content, not filler. If both cache counters come back 0, the scorer logs a warning (prompt likely fell below the minimum).
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
- **email_finder**: no-match is only Prospeo `NO_MATCH` or a Prospeo match with an empty/null, unrevealed (`revealed` false) or masked (contains `*`) email, Apify `found: false` + `"No email found in response"`, or Apollo returning no record for the URL. A failed `--draft` call leaves that row's `outreach_line` blank with a warning; an Anthropic auth/permission error stops drafting, appends all rows, exits 1. Any other provider error stops the run: rows already resolved are appended, unresolved URLs are not written (retried next run) and are printed as `pending`, exit 1. Missing env vars, unreadable settings, invalid `providers`, a missing or unreadable input file, a missing or unreadable `config/candidate.md` (or an unreadable `config/outreach_examples.md`) with `--draft`, a missing Contacts header, or more than `max_urls_per_run` URLs → one plain error line (no traceback), exit 1 before any provider call. Unexpected exceptions are logged with a traceback (`Fatal error, run aborted`), exit 1.

## Logging

- **Storage**: console output only, kept by GitHub Actions in the run log (90-day default retention). No Runs/Errors tab, no log files, no artifacts.
- **Never log** JD text, `profile.yaml` / `candidate.md` contents, or secrets. Failed-job lines show the URL + error type + the exception message on one line, truncated to 150 characters.
- **Fixed prefixes**: key lines start with a fixed uppercase prefix so a month of logs can be grepped: `RUN`, `ATS`, `QUERY`, `FUNNEL`, `DROP`, `JD`, `FAIL`, `SCORES`, `COST`, `ASHBY`.
- **What each run logs**:

| Prefix | Content |
|---|---|
| `RUN` (start) | After the existing `Writing to sheet: <title>` line: run start (configured tz), effective cap, `ats_lookback_days`, scoring model, existing URL count in the sheet |
| `ATS` | Totals (companies polled, jobs within lookback); companies with 0 jobs; companies with errors (slug + HTTP status) |
| `QUERY` | One line per query: pages fetched, results, redirect, off-site, new (after dedup), passed filters, new+passed from page 2+. Then total Serper calls and unwrapped Google redirects |
| `FAIL` | Search errors (query + HTTP status); failed jobs (URL + error type + message truncated to 150 chars) |
| `FUNNEL` | found = redirect + off-site + dupes (sheet) + dupes (in-run) + no URL + new; new = title dropped + location dropped + eligible; eligible = location dropped post-fetch + over cap + experience dropped + scored + failed |
| `DROP` | One line per filtered job. Title drops: the matched exclude keyword, or `no include match`. Location drops: the matched keyword and where it matched (`location` / `title` / `url`). Off-site search hits: `DROP offsite \| <title> \| <url>`. Post-fetch ATS location drops: `DROP location <keyword> (ats-location) \| <title> \| <url>`. Experience drops: `DROP experience N \| <phrase> \| <title> \| <url>`. Over-cap jobs: `DROP cap` lines with titles, only when the over-cap count > 0. Any `\|` inside a title is written as `/` so the ` \| ` separators stay parseable |
| `JD` | Source counts: full fetched, snippet fallback, ATS full |
| `ASHBY` | A search-found Ashby job whose board was unavailable or whose job id was not on the board; it falls back to a plain page fetch |
| `SCORES` | Histogram: 8–10, 6–7.9, 3–5.9, 1–2.9 |
| `COST` | Tokens: input, output, cache read, cache write; estimated cost from per-model prices in `settings.yaml` (never hardcoded) |
| `RUN` (end) | Elapsed time |

- **Summary line** (`found …, new …, filtered …`) stays unchanged and unprefixed.
- **`$GITHUB_STEP_SUMMARY`**: when the env var is set, append a short markdown summary (funnel, per-query table, score histogram, cost). Skip silently when unset (local runs).

## Secrets

- **Locally**: `.env` file (gitignored), loaded with `python-dotenv`. Service account JSON is a gitignored file; `.env` holds its path via `GOOGLE_APPLICATION_CREDENTIALS`.
- **Local .env only ever contains the TEST sheet ID.** The real (production) sheet ID exists only in GitHub secrets. There is no `--test` flag: the environment decides which sheet is used, so a local run writes to TEST unless the prod ID is passed explicitly. The only intended local prod writes are email_finder and the Task 9b import, via a `SHEET_ID=<prod id>` command prefix (never stored in `.env`).
- **CI**: GitHub Actions secrets, injected as env vars. Service account JSON stored as base64-encoded secret, decoded at runtime.
- **Gitignored personal files** (`config/profile.yaml`, `config/candidate.md`): stored as GitHub secrets (base64), decoded to files at runtime. `.example` versions committed to show the format.
- **Google Sheet ID**: env var (`SHEET_ID`), not hardcoded.
- **SERPER_API_KEY**: env var for the Serper search API.
- **email_finder keys** (`PROSPEO_API_KEY`, `APIFY_TOKEN`, optional `APOLLO_API_KEY`): local `.env` only (email_finder does not run in CI). Sent only in request headers, never in URLs or logs.
- **Never print secrets or file contents in logs.**

## Testing

- **Unit tests**: JSON fixtures (one real response per external API, scrubbed of personal data/keys). Tests replace `*_client.py` with fakes returning fixtures.
- **Local e2e**: separate "test" Google Sheet (`SHEET_ID` in local `.env` points to it). CI writes job_scanner rows to the real sheet; email_finder and the Task 9b import write to it only via an explicit `SHEET_ID=<prod id>` prefix.
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
| title/company from source when available | ATS APIs return structured data; scoring-model extraction only needed for search results |
| Pydantic validation on scorer output | Catches malformed model responses before they hit the sheet |
| Scoring model is a setting; tool mode is a setting (`force_tool_choice`) | Sonnet/Opus 5.5 reject a forced `tool_choice`; one code path supports both. Opus for October |
| `config/` at repo root, gitignored | Personal files (profile.yaml, candidate.md) stay out of repo; .example versions document format |
| `settings.yaml` committed | Caps, filter keywords, and lookback days are config, not secrets |
| Append-only sheet writes | User's checkboxes and manual edits must survive every run |
| Code appends "" (blank) for checkbox columns | Pre-filled checkboxes on empty rows count as data, so the append API writes after them (row 1001 in Task 1 testing). Checkboxes are never pre-filled; marking applied/contacted is manual |
| No `remote` column | User is open to all work modes; included in location text |
| Write "not found" email rows | Prevents re-spending provider credits on the same person |
| Email providers: Prospeo → Apify, Apollo optional (`providers` in `email_finder/settings.yaml`) | Oct 5 2026 tests: Prospeo is free (100 credits/month) and returns an email status; Apify's `vulnv/linkedin-email-finder` catches Prospeo's no-matches, pay per email found (~$0.026) from the $5/month free credit, but returns no status or title (rows marked `unverified`). Apollo `people/match` and `bulk_match` return 403 `API_INACCESSIBLE` on the free plan, so it stays a disabled provider for a future paid plan |
| email_finder stops on unexpected provider errors; unresolved URLs not written | Only a real no-match becomes a "not found" row, so a bad key, 429 or outage never marks people as not found; pending URLs are retried next run |
| `--draft` writes LinkedIn connection notes only | Outreach emails are written by hand: they need the JD and company research |
| `outreach.max_tokens` 4096 | Sonnet 5.5 thinking tokens count against it; a `max_tokens` stop is a draft failure, never a cut-off note |
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
| All dates stored in one timezone | `timezone` setting in settings.yaml (default `America/Chicago`). CI runs in UTC; without conversion an evening CT run (e.g. a late-starting scheduled run) would show the next day's date. Both `posted_date` and `date_found` use the configured timezone. |
| Serper dates converted to absolute dates | Serper returns relative strings ("21 hours ago") or absolute ones ("Sep 28, 2026"). Conversion uses the run's start time so all dates in a run are consistent. Unparseable dates → blank `posted_date`. |

## Open Risks

- **Serper query syntax**: Google accepts `site:` + `OR` + brackets in the browser. **Resolved in Task 2**: Serper passes them through correctly (returned links match the `site:` domain).
- **Serper API quota**: Serper has monthly credit limits. Each query page is one credit at `num=10`. Ceiling = enabled queries × `max_pages_per_query` × 2 runs/day (23 × 4 × 2 = 184 credits/day with current config). The `QUERY` log lines report total Serper calls per run.
- **ATS API rate limits**: Greenhouse/Lever/Ashby public APIs may have undocumented rate limits. If companies.yaml grows large, requests may get throttled.
- **ATS slug drift**: company slugs in companies.yaml can change if a company rebrands or migrates ATS. Broken slugs fail silently (empty results, not errors). Periodic manual check needed.
- **JD fetch failures**: some company career pages block automated requests or require JavaScript. No headless browser is in scope — those jobs fall back to snippet-based scoring instead.
- **Email provider quotas**: Prospeo free plan is 100 credits/month and 50 enrich calls/day (1/s, 20/min); Apify spends ~$0.026 per email found from the $5/month free credit. `max_urls_per_run` (20) caps one run and `prospeo.min_interval_seconds` (3.1) keeps under 20/min; the 50/day limit returns 429, which stops the run with the remaining URLs pending.
- **Apollo bulk_match mapping unverified**: records are mapped by returned normalized `linkedin_url` and a missing record counts as no-match, based on Apollo's docs and a synthetic fixture only (the free plan returns 403). Verify against a real response before adding `apollo` to `providers`.
- **Legacy rows without a URL**: email_finder dedups only on `linkedin_url`, so a legacy contact imported in Task 9b with a blank URL does not block a later lookup of the same person; that lookup spends a credit and can add a second row for them (same email). Check by hand if it matters.
- **Outdated email domains**: providers can return an email at a company's old domain after a rebrand. Check manually when the email domain differs from the company's current site.
- **Sheet row limits**: Google Sheets caps at ~10M cells. Not a near-term risk, but if you never clean up, the sheet grows forever.
- **Snippet-based scores are less reliable**: when the full JD can't be fetched, scoring from title + snippet may miss requirements or benefits. The `jd_source` column lets you see which scores are snippet-based.
- **Scorer output consistency**: tool call (forced, or strict + auto) + pydantic validation catches malformed responses. In auto mode the model could skip the tool; that response counts as a failed job. Invalid responses are skipped and retried next run.
- **Checkbox column append behavior**: if the whole `applied` / `contacted` column is pre-filled with checkboxes (e.g. via Google Sheets data validation on 1000 rows), those cells may be treated as containing FALSE. The append API would then add new rows after the last "filled" row (e.g. row 1001) instead of after the last data row (e.g. row 2). **Resolved in Task 1**: confirmed — with checkboxes pre-filled on rows 2–1000, the append landed on row 1001. Decision: code writes "" (blank) to `applied` / `contacted`, checkboxes are never pre-filled on empty rows, and marking is manual.
