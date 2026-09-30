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
│   ├── ats_client.py            # Greenhouse/Lever/Ashby public APIs (list jobs + fetch JDs)
│   ├── jd_client.py             # plain HTTP fetch for non-ATS job pages
│   ├── scoring_client.py        # Anthropic Haiku structured JSON call
│   ├── sheets_repo.py           # read/append Google Sheet (Jobs tab only)
│   ├── url_utils.py             # normalize URLs (strip tracking params)
│   ├── title_filter.py          # cheap keyword include/exclude
│   ├── location_filter.py       # drop clearly non-US jobs before scoring
│   ├── queries.yaml             # search queries (one per job board site)
│   ├── companies.yaml           # tracked companies on Greenhouse/Lever/Ashby
│   └── settings.yaml            # committed config (caps, filters)
├── email_finder/
│   ├── main.py                  # CLI entry point
│   ├── service.py               # orchestration
│   ├── apollo_client.py         # Apollo people/match (email only)
│   ├── outreach_client.py       # Claude outreach line drafting
│   └── sheets_repo.py           # read/append Google Sheet (Contacts tab only)
├── shared/
│   └── sheets.py                # Google Sheets auth + low-level read/append
├── tests/
│   └── fixtures/                # scrubbed real API responses (JSON)
├── _legacy/                     # old broader-search files (gitignored, not used by code)
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
└── DESIGN.md
```

**Layering rule**: `main.py` is thin (parse args, call service). `service.py` holds all logic. `*_client.py` files only call external APIs — one client per external system. `sheets_repo.py` is the only file that touches the sheet. Both `sheets_repo.py` files use `shared/sheets.py` for auth and raw sheet operations.

**Run command**: `python -m job_scanner.main` from the repo root.

## Data Flow — job_scanner

1. Load `queries.yaml`, `companies.yaml`, `config/profile.yaml`, `config/candidate.md`, `settings.yaml`
2. **Source A — Search API (Serper)**: for each query → call Serper with `tbs=qdr:d` (past 24 hours) and `gl=us` (US-biased results — sets where the search appears to come from, does NOT filter by job location). Paginate up to `max_pages_per_query` pages (default 2, each page is a paid API call). Stop early if a page returns no results.
3. **Source B — ATS direct**: for each company in `companies.yaml` → call Greenhouse/Lever/Ashby public job list API → keep only jobs posted or updated in the last N days (`ats_lookback_days` in `settings.yaml`, default 3)
4. Merge both sources into one list
5. Normalize URLs (strip utm_, ref, etc.)
6. Deduplicate: against existing URLs in the "Jobs" sheet AND within the current run
7. Title keyword filter (include/exclude lists from `settings.yaml`, case-insensitive, whole-word)
8. Location filter: drop jobs whose location is clearly outside the US (non-US keyword list in `settings.yaml`). For ATS jobs, check the location field. For search results (no structured location field), check the title + snippet text. Keep US locations, "Remote", "Remote - US", and blank/unknown.
9. Apply score cap (default 50, overridable via `--max-jobs`). Newest jobs first (by `posted_date` if available, else search result order). Over-cap jobs are not written to the sheet.
10. For each surviving job → fetch full JD (`ats_client.py` for Greenhouse/Lever/Ashby jobs, `jd_client.py` plain HTTP fetch for others). If the JD page can't be fetched or returns almost no text (common on JavaScript-heavy career sites), fall back to scoring from the search result's title + snippet instead. ATS API jobs always have the full JD.
11. For each JD (or snippet fallback) → one Haiku call with `config/candidate.md` + `config/profile.yaml` context → structured JSON response
12. Validate Haiku's JSON response with a pydantic schema. Invalid response = failed job (skip, not written to sheet).
13. Append new rows to "Jobs" tab (append-only, never overwrite)
14. Print summary: `"found 42, new 12, scored 8, failed 1, skipped (cap): 3"`

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
| applied | Checkbox | Code appends FALSE. Checkbox formatting set once by hand on the whole column. Never overwritten by code. |
| match_score | Number | 1.0–10.0 from Haiku (one decimal, e.g. 7.5) |
| jd_source | String | "full" (fetched JD or ATS API) or "snippet" (scored from search title + snippet) |
| title | String | From ATS source data when available; from Haiku for search results |
| company | String | From ATS source data when available; from Haiku for search results |
| location | String | Includes remote/hybrid/onsite as plain text |
| visa_mention | String | From Haiku output |
| core_requirements | String | From Haiku output |
| reason | String | One-line match reason from Haiku |
| url | String | Normalized, used as dedup key |
| posted_date | String | From ATS APIs when available, blank otherwise |
| date_found | String | Timestamp of this run |
| source_query | String | Which query or company found this job |

### Contacts tab

| Column | Type | Notes |
|---|---|---|
| contacted | Checkbox | Code appends FALSE. Checkbox formatting set once by hand on the whole column. Never overwritten by code. |
| name | String | From Apollo |
| company | String | From Apollo |
| title | String | From Apollo |
| email | String | Blank if not found |
| email_status | String | verified / unverified / not found |
| linkedin_url | String | Input URL, used as dedup key |
| outreach_line | String | Claude-drafted, blank if --draft not used |
| date_found | String | Timestamp of this run |

## Scoring Rules

Scorer receives two inputs: `config/candidate.md` (one-page evidence doc) and `config/profile.yaml` (match criteria and evaluation rules).

- **Hard blockers (score 1–2)** — override all score floors:
  - JD **explicitly** says no visa sponsorship or US citizens only. If the JD doesn't mention visas, that is NOT a blocker. Security clearance is also a blocker. Candidate is on F-1 OPT.
  - Job is based outside the US.
- **Location within the US is never a penalty.** Greater Seattle area (Seattle, Bellevue, Redmond, Kirkland, Tacoma, Everett) gets a small bonus (+0.25 to +0.5).
- **Score floor of 6** for early-career SWE/FDE/founding/product engineer roles (per `ai_evaluation` rules in `profile.yaml`). Only score below 6 when the JD clearly requires 3+ years AND targets mid/senior/staff/principal, or a hard blocker applies.
- **Pydantic validation**: Haiku's JSON response is validated against a schema. Invalid response = failed job (skipped, not written to sheet, retried next run).

## US-Only Filtering (Two Layers)

1. **Cheap location filter** (in code, after title filter, before cap): drops a job only if its location string contains a clearly non-US keyword. Keyword list lives in `settings.yaml` under `location_filter.exclude` — whole-word, case-insensitive matching. Jobs with US locations, "Remote", "Remote - US", or blank/unknown locations pass through.
2. **Scorer hard blocker**: a job based outside the US receives score 1–2 regardless of other fit signals.

## Error Handling

- **Per-job failure** (JD fetch timeout, scoring error, pydantic validation failure): log the error, skip the job, continue. Failed jobs are not written to the sheet, so they're retried on the next run (within the 24h search window).
- **No custom retry code.** Anthropic SDK handles its own retries. JD fetches: one request with a timeout (`request_timeout_seconds` in `settings.yaml`).
- **Fatal errors** (sheet unreachable, invalid API key): crash with non-zero exit code → GitHub Actions goes red → GitHub emails you.
- **Summary line** printed at the end of every run.

## Secrets

- **Locally**: `.env` file (gitignored), loaded with `python-dotenv`. Service account JSON is a gitignored file; `.env` holds its path.
- **CI**: GitHub Actions secrets, injected as env vars. Service account JSON stored as base64-encoded secret, decoded at runtime.
- **Gitignored personal files** (`config/profile.yaml`, `config/candidate.md`): stored as GitHub secrets (base64), decoded to files at runtime. `.example` versions committed to show the format.
- **Google Sheet ID**: env var, not hardcoded.
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
| `ats_lookback_days` default 3 | Prevents old jobs flooding the first run; wide enough to catch anything posted over a weekend |
| `ats_client.py` separate from `jd_client.py` | One client per external system: ats_client handles Greenhouse/Lever/Ashby APIs (list + fetch), jd_client handles plain HTTP fetches |
| title/company from source when available | ATS APIs return structured data; Haiku extraction only needed for search results |
| Pydantic validation on scorer output | Catches malformed Haiku responses before they hit the sheet |
| `config/` at repo root, gitignored | Personal files (profile.yaml, candidate.md) stay out of repo; .example versions document format |
| `settings.yaml` committed | Caps, filter keywords, and lookback days are config, not secrets |
| Append-only sheet writes | User's checkboxes and manual edits must survive every run |
| Code appends FALSE for checkbox columns | Checkbox formatting set once by hand on the column; code only writes the boolean |
| No `remote` column | User is open to all work modes; included in location text |
| Write "not found" email rows | Prevents re-spending Apollo credits on same person |
| Score cap applied after dedup + title filter + location filter | Cap counts real US-based SWE candidates, not noise |
| Newest jobs scored first | If capped, better to score fresh postings |
| US-only: two-layer filtering | Cheap keyword filter catches obvious non-US before burning scorer credits; scorer catches the rest |
| Location within the US is never a penalty | Candidate is open to anywhere in the US; only Greater Seattle gets a small bonus |
| Serper (serper.dev) as search API, not Brave | Brave's "past day" filter returned far weaker results in manual testing; Serper wraps Google and gives better recency |
| No Google scraping with Selenium | Blocked quickly (CAPTCHAs, especially from GitHub Actions IPs), against Google ToS, and brittle. Serper is the paid, reliable alternative |
| JD snippet fallback for search results | Many career pages are JavaScript-heavy and return no text on plain fetch. Scoring from title + snippet is better than skipping the job entirely |
| `max_pages_per_query` default 2 | Each page is a paid Serper call; 2 pages balances coverage vs. cost |

## Open Risks

- **Serper query syntax**: Google accepts `site:` + `OR` + brackets in the browser. Still to confirm that Serper passes them through correctly in build task 2.
- **Serper API quota**: Serper has monthly credit limits. Each query page is one credit. With pagination and company_sites queries, monitor usage to stay within plan.
- **ATS API rate limits**: Greenhouse/Lever/Ashby public APIs may have undocumented rate limits. If companies.yaml grows large, requests may get throttled.
- **ATS slug drift**: company slugs in companies.yaml can change if a company rebrands or migrates ATS. Broken slugs fail silently (empty results, not errors). Periodic manual check needed.
- **JD fetch failures**: some company career pages block automated requests or require JavaScript. No headless browser is in scope — those jobs fall back to snippet-based scoring instead.
- **Apollo credit burn**: each people/match call costs credits. No cap is built into email_finder (it's manual and one-at-a-time, so you're the cap).
- **Sheet row limits**: Google Sheets caps at ~10M cells. Not a near-term risk, but if you never clean up, the sheet grows forever.
- **Snippet-based scores are less reliable**: when the full JD can't be fetched, scoring from title + snippet may miss requirements or benefits. The `jd_source` column lets you see which scores are snippet-based.
- **Haiku output consistency**: structured JSON mode + pydantic validation catches malformed responses. Invalid responses are skipped and retried next run.
