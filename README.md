# job-hunt-tools

## What this does

Two small Python tools that feed one Google Sheet. **`job_scanner`** (live) runs twice a day on GitHub Actions: it polls tracked company boards on Greenhouse, Lever and Ashby, runs `site:` searches through the Serper API, dedups against the sheet, drops jobs by title, location and required years of experience, scores the rest with Claude Sonnet (`claude-sonnet-5-5`) against your profile, and appends the scored rows to the sheet's `Jobs` tab. Unresolved Google redirects (`google.com/goto`, or a `/url` link whose target does not start with `http`) are counted as redirects and skipped. After a Greenhouse, Lever, or Ashby single-job fetch, the structured location is checked and a non-US job is dropped before scoring. **`email_finder`** (built, local CLI) takes LinkedIn profile URLs (arguments, `--file`, or `email_finder/linkedin-profiles.txt`), looks up each email through Prospeo and then Apify for Prospeo's misses (Apollo is supported but disabled; its API needs a paid plan), and appends one row per URL to the `Contacts` tab. With `--draft` it also writes a short LinkedIn connection note into each found row's `outreach_line`; outreach emails are written by hand.

All commands below are run from the repo root with the virtual environment active.

## Command cheat sheet

| Task | Command | Section |
|---|---|---|
| Activate the venv | `source .venv/bin/activate` | [Setup](#setup) |
| Small local run (TEST sheet) | `python -m job_scanner.main --max-jobs 2` | [Local run and tests](#local-run-and-tests) |
| Run tests | `pytest` | [Local run and tests](#local-run-and-tests) |
| Local run with a saved log | `python -m job_scanner.main --max-jobs 5 2>&1 \| tee run.log` | [Local run and tests](#local-run-and-tests) |
| Email lookup, one URL (TEST sheet) | `python -m email_finder.main <url>` | [Email finder](#email-finder) |
| Email lookup, several URLs (TEST sheet) | `python -m email_finder.main <url> <url>` | [Email finder](#email-finder) |
| Email lookup from `email_finder/linkedin-profiles.txt` (TEST sheet) | `python -m email_finder.main` | [Email finder](#email-finder) |
| Email lookup from another file | `python -m email_finder.main --file <path>` | [Email finder](#email-finder) |
| Email lookup + connection-note drafts | `python -m email_finder.main --draft` | [Email finder](#email-finder) |
| Real (prod) email lookup | `SHEET_ID=<prod id> python -m email_finder.main` | [Email finder](#email-finder) |
| Real (prod) email lookup + drafts | `SHEET_ID=<prod id> python -m email_finder.main --draft` | [Email finder](#email-finder) |
| Set a file secret | `base64 -i <file> \| gh secret set <NAME>` | [GitHub Actions](#github-actions) |
| Set a plain secret | `gh secret set <NAME>` | [GitHub Actions](#github-actions) |
| Manual CI run | `gh workflow run scan.yaml -f max_jobs=2` | [GitHub Actions](#github-actions) |
| Pause / resume the schedule | `gh workflow disable scan.yaml` / `gh workflow enable scan.yaml` | [GitHub Actions](#github-actions) |
| List recent runs / view a log | `gh run list` / `gh run view <id> --log` | [Logs](#logs) |
| Check ignored files before commit | `git check-ignore -v ...` | [Before every commit](#before-every-commit) |

## Setup

### 1. Clone and create the virtual environment

```bash
git clone <repo-url> job-hunt-tools
```

```bash
cd job-hunt-tools
```

```bash
python3.13 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

To leave the virtual environment later:

```bash
deactivate
```

### 2. Environment file

```bash
cp .env.example .env
```

Fill in `SERPER_API_KEY`, `ANTHROPIC_API_KEY` and `SHEET_ID`. **`SHEET_ID` in `.env` is always the TEST sheet ID.** The production sheet ID lives only in GitHub secrets, so a local run writes to TEST unless you pass the production ID yourself. There is no `--test` flag; the environment decides which sheet is used. `email_finder` runs only locally, so it writes to TEST by default; for real use, prefix the command with the production ID: `SHEET_ID=<prod id> python -m email_finder.main ...` (never put the production ID in `.env`). Every run prints `Writing to sheet: <title>` first so you can see which sheet it hit.

| Variable | Where it is set | When it is required |
|---|---|---|
| `SHEET_ID` | `.env` holds the TEST sheet ID. The production ID is a GitHub secret, or a one-off command prefix | Every run |
| `GOOGLE_APPLICATION_CREDENTIALS` | `.env` (already `service_account.json` in the example) | Every run |
| `SERPER_API_KEY` | `.env` and the GitHub secret of the same name | `job_scanner` |
| `ANTHROPIC_API_KEY` | `.env` and the GitHub secret of the same name | `job_scanner`; `email_finder` only with `--draft` |
| `PROSPEO_API_KEY` | `.env` only | `email_finder` when `prospeo` is in `providers` (it is, by default) |
| `APIFY_TOKEN` | `.env` only | `email_finder` when `apify` is in `providers` (it is, by default) |
| `APOLLO_API_KEY` | `.env` only | `email_finder` only when `apollo` is in `providers` (it is not; paid plan) |

### 3. Personal config files

```bash
cp config/profile.example.yaml config/profile.yaml
```

```bash
cp config/candidate.example.md config/candidate.md
```

Edit both. `config/profile.yaml` holds your match criteria and evaluation rules; `config/candidate.md` is a one-page evidence document for the scorer. Both are gitignored.

Optional, for `email_finder --draft`: example LinkedIn connection notes in your own voice.

```bash
cp config/outreach_examples.example.md config/outreach_examples.md
```

It is gitignored. Without it, notes are drafted with no examples.

### 4. Google service account

1. In the Google Cloud console, create (or pick) a project and enable the **Google Sheets API**.
2. Create a service account under **IAM & Admin → Service Accounts**. It needs no project roles.
3. On the service account, open **Keys → Add key → Create new key → JSON** and download it.
4. Save the file at the repo root as `service_account.json` (gitignored).
5. Copy the service account's email address (`...@<project>.iam.gserviceaccount.com`).
6. Open each Google Sheet (TEST and production), click **Share**, add that email as **Editor**, and untick "Notify people".

### 5. Sheet tabs and headers

Create the TEST and production sheets with the same layout. Row 1 holds the headers below, starting at column A.

`Jobs` tab (14 columns, A–N):

```text
applied	match_score	platform	url	title	company	location	visa_mention	jd_source	core_requirements	reason	posted_date	date_found	source_query
```

The scanner matches `Jobs` columns by header name (trimmed, case-insensitive), not by position, so they can be in any order and you can add your own. All 14 headers above must exist in row 1; if any is missing, the run stops with an error naming the missing headers and nothing is written. Columns with other headers are left blank on new rows. `platform` is the job board or ATS (`greenhouse`, `lever`, `ashby`, `workday`, `company_site`, …, else `other`); to fill it on older rows, run `python _scratch/backfill_platform.py` (dry run) and then with `--apply`. That script is local-only: `_scratch/` is gitignored, so it is not in the repo.

`Contacts` tab (11 columns, A–K, used by `email_finder`; matched by header name like `Jobs`, and all 11 must exist):

```text
contacted	name	company	title	email	email_status	email_source	linkedin_url	notes	outreach_line	date_found
```

The values above are tab-separated, so you can paste the line into cell A1.

**Never pre-fill checkboxes.** The `applied` and `contacted` columns are checkboxes you tick by hand, but only insert a checkbox on a row after the code has written it. If the whole column is pre-filled with checkboxes on empty rows, the Sheets append API treats those rows as data and writes new rows after them (for example at row 1001). The code always writes a blank to these columns and never overwrites existing rows.

## Local run and tests

A small run against the TEST sheet:

```bash
python -m job_scanner.main --max-jobs 2
```

`--max-jobs` overrides `max_jobs_per_run` for one run, keeping local runs cheap. The run ends with a summary line like `found 42, new 12, filtered 2, scored 2, failed 0, skipped (cap): 8`.

Unit tests (fixtures only, no real API calls):

```bash
pytest
```

To keep a log for grepping (see [Logs](#logs)). Log lines go to stderr, so redirect it into the pipe:

```bash
python -m job_scanner.main --max-jobs 5 2>&1 | tee run.log
```

`run.log` is gitignored. Delete it when you are done to keep the folder tidy:

```bash
rm run.log
```

## Email finder

`email_finder` looks up emails for LinkedIn profile URLs and appends one row per URL to the `Contacts` tab. It runs only locally (never in CI).

### Setup

Copy the input file and add your profile URLs:

```bash
cp email_finder/linkedin-profiles.example.txt email_finder/linkedin-profiles.txt
```

Optional, for `--draft` (see [Personal config files](#3-personal-config-files)):

```bash
cp config/outreach_examples.example.md config/outreach_examples.md
```

Both copies are gitignored. The `Contacts` tab needs all 11 headers in row 1 (see [Sheet tabs and headers](#5-sheet-tabs-and-headers)).

Required keys in `.env`, checked before any work (all missing ones are named in one error, exit 1):

- Always: `SHEET_ID`, `GOOGLE_APPLICATION_CREDENTIALS`.
- `PROSPEO_API_KEY` when `prospeo` is in `providers`, `APIFY_TOKEN` when `apify` is, `APOLLO_API_KEY` only when `apollo` is (it is not by default).
- `ANTHROPIC_API_KEY` only with `--draft`. `--draft` also reads `config/candidate.md` (and `config/outreach_examples.md` if present) before any lookup; a missing or unreadable `candidate.md` is an error, exit 1.

### Input file format

`email_finder/linkedin-profiles.txt` is copied from `email_finder/linkedin-profiles.example.txt`. One LinkedIn profile URL per line:

```text
# Copy to email_finder/linkedin-profiles.txt (gitignored) and add real profile URLs.
# One LinkedIn profile URL per line. Blank lines and lines starting with # are ignored.
# An optional trailing "# note" goes into the notes column.

# https://www.linkedin.com/in/jane-doe-example/
# https://www.linkedin.com/in/john-roe-example  # recruiter at Example Corp
```

- Blank lines and lines starting with `#` are skipped.
- A trailing `# note` (whitespace before the `#`) goes into the row's `notes` column and is the context for the `--draft` note (a role mentioned there gets referenced; no note → a networking note).
- URLs are normalized: scheme becomes `https://` if missing, any `linkedin.com` subdomain (`www.`, `uk.`, `m.`, …) becomes `linkedin.com`, lowercase, no query, fragment or trailing slash. Deeper `/in/` paths are trimmed, so `https://www.linkedin.com/in/Jane-Doe/details/experience/` becomes `https://linkedin.com/in/jane-doe`.
- Anything that isn't a `linkedin.com/in/<slug>` profile URL (`/pub/` links, company pages, other sites, text) is printed as `ignored` and never looked up.

URLs passed on the command line follow the same rules. With URLs and no `--file`, the default file is not read. Passing URLs and `--file` together reads both, URLs first. `--draft` can be added to any of the commands below. `main.py` accepts only those two flags.

### Commands

Look up every URL in `email_finder/linkedin-profiles.txt` (TEST sheet):

```bash
python -m email_finder.main
```

Look up one URL:

```bash
python -m email_finder.main https://www.linkedin.com/in/jane-doe-example/
```

Look up several URLs:

```bash
python -m email_finder.main https://www.linkedin.com/in/jane-doe-example/ https://www.linkedin.com/in/john-roe-example
```

Read a different input file:

```bash
python -m email_finder.main --file path/to/profiles.txt
```

Also draft a LinkedIn connection note for each person found:

```bash
python -m email_finder.main --draft
```

Real use, against the production sheet (never put the production ID in `.env`):

```bash
SHEET_ID=<prod id> python -m email_finder.main
```

```bash
SHEET_ID=<prod id> python -m email_finder.main --draft
```

Without the prefix every run writes to the TEST sheet from `.env`. Check the first line, `Writing to sheet: <title>`.

### Providers

`providers` in `email_finder/settings.yaml` sets the order, `[prospeo, apify]` by default. Each provider runs once over the URLs still unmatched and passes its no-matches to the next. Prospeo makes one call per URL; Apify runs its actor once for all remaining URLs and returns no title or status (rows get `email_status` `unverified`).

Apollo is supported but disabled: its free plan returns 403 `API_INACCESSIBLE`. To enable it on a paid plan, first verify the `bulk_match` mapping against a real response (see Open Risks in [`docs/DESIGN.md`](docs/DESIGN.md)), then set `APOLLO_API_KEY` in `.env` and add `apollo` to `providers` (for example `[prospeo, apify, apollo]`).

### Rate limits and the per-run cap

- `max_urls_per_run` (20) counts the URLs left after removing ones already in the `Contacts` tab and in-input duplicates. More than that → an error, nothing looked up, exit 1. Split the file and run again.
- Prospeo's limits are 1/s, 20/min and 50/day. Calls are spaced `prospeo.min_interval_seconds` (3.1 s) apart, so a 20-URL Prospeo pass takes about a minute. Hitting the 50/day limit returns 429, which stops the run (see below).

### Output

Log lines go to stderr with a timestamp, level and logger name:

- `<provider>: looking up N URL(s)` before each provider runs.
- `STOP <provider>: <message>` when a provider error stops the run. The message is only the provider, the HTTP status or error code, or the exception type.
- `DRAFT failed for <url>: <error type>; connection note left blank` for one failed draft.
- `DRAFT stopped: <error type>; remaining connection notes left blank` on an Anthropic authentication or permission error.

On stdout, after `Writing to sheet: <title>`, there is one line per input URL, in input order:

| Line | Meaning |
|---|---|
| `ignored <url part> (not a profile URL)` | The line's URL part (never its trailing note) is not a `linkedin.com/in/<slug>` URL; not looked up, not written |
| `duplicate <url>` | Same profile earlier in this input; not looked up again |
| `<url> exists` | Already in the `Contacts` tab; not looked up, not written |
| `<url> found via <provider> <status>` | Email found; row written |
| `<url> not found` | No provider found an email; row written with a blank email, so it is never looked up again |
| `<url> pending` | Not resolved because a provider stopped the run; not written |

Then one summary line, for example:

```text
input 5, ignored 1, duplicate 1, skipped existing 1, found prospeo 1, found apify 0, not found 1, pending 0
```

Emails, names and drafted notes are never printed.

### Exit codes and reruns

- **0**: every URL was resolved. Not-found rows and single failed drafts still exit 0.
- **1**: missing env vars, unreadable settings or bad `providers`, a missing input file, a missing `config/candidate.md` with `--draft`, a missing `Contacts` header or too many URLs (one plain error line, nothing looked up); a provider `STOP` (resolved rows are appended, `pending` URLs are not written); an Anthropic authentication or permission error while drafting (all rows are appended, the remaining `outreach_line`s stay blank); any other fatal error.

To retry `pending` URLs, rerun the same command: rows written last time now print `exists`, so only the pending URLs are looked up, starting from the first provider again.

### Rows and drafts

Rows are append-only: the code never edits a row it wrote. One consequence is that `--draft` only drafts notes for people found in that run; a contact already in the sheet prints `exists` and gets no draft. A failed draft leaves that row's `outreach_line` blank with a `DRAFT failed` warning, and the row is still written. What goes into a note is described under [`email_finder/settings.yaml`](#email_findersettingsyaml-committed).

## GitHub Actions

The workflow lives in `.github/workflows/scan.yaml` and is named **Job Scanner**.

### Secrets

| Secret | What it is |
|---|---|
| `SERPER_API_KEY` | Serper API key, from the serper.dev dashboard |
| `ANTHROPIC_API_KEY` | Anthropic API key, from console.anthropic.com → API Keys |
| `SHEET_ID` | **Production** sheet ID (the long ID in the sheet URL). Never the TEST one |
| `GOOGLE_SERVICE_ACCOUNT_B64` | `service_account.json`, base64-encoded |
| `PROFILE_YAML_B64` | `config/profile.yaml`, base64-encoded |
| `CANDIDATE_MD_B64` | `config/candidate.md`, base64-encoded |

The workflow decodes the three `_B64` secrets back into files at runtime and fails with a clear error if any decoded file is empty.

### Setting secrets with the gh CLI

Install and sign in once:

```bash
brew install gh
```

```bash
gh auth login
```

File secrets (piped, so the value never touches your shell history):

```bash
base64 -i service_account.json | gh secret set GOOGLE_SERVICE_ACCOUNT_B64
```

```bash
base64 -i config/profile.yaml | gh secret set PROFILE_YAML_B64
```

```bash
base64 -i config/candidate.md | gh secret set CANDIDATE_MD_B64
```

Plain-value secrets. Each command prompts you to paste the value:

```bash
gh secret set SERPER_API_KEY
```

```bash
gh secret set ANTHROPIC_API_KEY
```

Paste the **production** sheet ID here, not the TEST one:

```bash
gh secret set SHEET_ID
```

`email_finder` does not run in CI. `PROSPEO_API_KEY`, `APIFY_TOKEN`, and optional `APOLLO_API_KEY` stay in `.env` and are not Actions secrets.

Confirm all six are set (values are never shown):

```bash
gh secret list
```

### Setting secrets in the web UI instead

Copy a file's base64 to the clipboard:

```bash
base64 -i service_account.json | pbcopy
```

Then go to the repo on GitHub → **Settings → Secrets and variables → Actions → New repository secret**, enter the name (e.g. `GOOGLE_SERVICE_ACCOUNT_B64`) and paste. Repeat for `config/profile.yaml` and `config/candidate.md`; paste the plain values directly.

### Keep the profile secrets in sync

CI never sees your local files. **After every local edit to `config/profile.yaml` or `config/candidate.md`, re-upload `PROFILE_YAML_B64` / `CANDIDATE_MD_B64`** with the commands above, or CI keeps scoring against the old version.

### Schedule, concurrency, timeout

- **Schedule**: twice a day on UTC crons `cron: '7 10 * * *'` (5:07 AM CDT / 4:07 AM CST) and `cron: '7 20 * * *'` (3:07 PM CDT / 2:07 PM CST); the CST times apply after DST ends on Nov 1. GitHub scheduled runs are best-effort and often start hours late, so runs are set early; manual runs start immediately. Scheduled runs only fire from the workflow file on the **default branch (`master`)**; changes to `scan.yaml` on other branches don't affect the schedule until merged.
- **Concurrency**: group `job-scanner`, `cancel-in-progress: false`. A second run (e.g. a manual click during a scheduled run) queues until the first finishes, so two runs never append the same jobs.
- **Timeout**: 45 minutes per run.
- **Failure**: the run goes red and GitHub emails you.

### Manual run

In the UI: **Actions → Job Scanner → Run workflow**, pick the branch, and set **max_jobs** to a plain number (e.g. `2`). Leave it blank to use `max_jobs_per_run` from `settings.yaml`. Click **Run workflow**.

From the terminal:

```bash
gh workflow run scan.yaml -f max_jobs=2
```

### Pause and resume the schedule

```bash
gh workflow disable scan.yaml
```

```bash
gh workflow enable scan.yaml
```

## Logs

**Where**: **Actions → pick a run → `scan` job → `Run scanner` step**. The run's page also shows a markdown step summary (funnel table, per-query table, score histogram, cost). GitHub keeps run logs for **90 days** by default. There are no log files, artifacts or log tabs in the sheet.

Logs never contain JD text, profile/candidate contents, or secrets.

### Prefixes

Key lines start with a fixed uppercase prefix (after the timestamp and logger name), so logs can be grepped.

| Prefix | Meaning |
|---|---|
| `RUN` | Start: run time, effective cap, `ats_lookback_days`, scoring model, existing URL count. End: elapsed time |
| `ATS` | Companies polled and jobs within the lookback; companies with 0 jobs; companies with errors (slug + HTTP status or error type) |
| `QUERY` | Per search query: pages fetched, results, redirect, off-site, new after dedup, passed filters, new+passed from page 2+. Then total Serper calls and unwrapped Google redirects |
| `FUNNEL` | found = redirect + off-site + dupes (sheet) + dupes (in-run) + no URL + new; new = title dropped + location dropped + eligible; eligible = location dropped post-fetch + over cap + experience dropped + scored + failed |
| `DROP` | One line per filtered job: `DROP title <keyword or "no include match">`, `DROP location <keyword> (<location/title/url>)`, `DROP location <keyword> (ats-location)`, `DROP redirect`, `DROP offsite`, `DROP experience <years> \| <phrase>`, `DROP cap`. Fields are separated by ` \| ` |
| `SEARCH` | Warning when unresolved Google `/goto` (and opaque `/url`) redirects are more than 30% of search results. Those results are skipped |
| `GREENHOUSE` / `LEVER` | A search-found Greenhouse or Lever job whose single-job API call failed; the run falls back to a plain page fetch |
| `JD` | JD source counts: full fetched, snippet fallback, ATS full |
| `FAIL` | `FAIL search` (query, page, HTTP status) or `FAIL job` (URL, error type, message truncated to 150 chars) |
| `SCORES` | Score histogram: 8-10, 6-7.9, 3-5.9, 1-2.9 |
| `COST` | Input, output, cache read and cache write tokens, plus estimated USD cost for the model |
| `ASHBY` | A search-found Ashby job whose board was unavailable or whose job ID was not on the board; it falls back to a plain page fetch |

The `Writing to sheet: <title>` line and the final summary line have no prefix.

### Reading logs with gh

```bash
gh run list --workflow scan.yaml
```

```bash
gh run view <id> --log
```

Export the key lines from the last 60 runs into one file for review:

```bash
gh run list --workflow scan.yaml --limit 60 --json databaseId -q '.[].databaseId' | xargs -I{} gh run view {} --log | grep -E "RUN|ATS|QUERY|FUNNEL|DROP|JD|FAIL|SCORES|COST|ASHBY|SEARCH|GREENHOUSE|LEVER" > review.txt
```

Like `run.log`, `review.txt` is gitignored; delete it when done to keep the folder tidy.

### Useful greps on a log file

Title drops by reason, most common first:

```bash
grep -o "DROP title [^|]*" run.log | sort | uniq -c | sort -rn
```

Location drops by keyword and field, most common first:

```bash
grep -o "DROP location [^|]*" run.log | sort | uniq -c | sort -rn
```

Titles that matched no include keyword (candidates for new include keywords):

```bash
grep "DROP title no include match" run.log | cut -d'|' -f2
```

## Config files

### `job_scanner/settings.yaml` (committed)

- `max_jobs_per_run`: score cap per run (150). Applied after dedup and the title/location filters, newest jobs first. Over-cap jobs are not written. `--max-jobs` overrides it.
- `max_pages_per_query`: Serper pages per query (4). Stops early on an empty page.
- `search`: `tbs: "qdr:d"` (past 24 hours), `gl: us` (search origin, not a location filter), `num: 10` results per page.
- `ats_lookback_days`: how far back ATS jobs count as new (2).
- `request_timeout_seconds`, `jd_min_chars` (a fetched page with less visible text falls back to title + snippet), `timezone` (all dates are written in this zone).
- `title_filter`: a title must match at least one `include` keyword and no `exclude` keyword (case-insensitive, whole-word, so "Lead" does not match "Leadership"). `always_allow` phrases count as an include match and are removed before the exclude check: "Member of Technical Staff" passes, "Senior Member of Technical Staff" drops.
- `location_filter`: drops a job only if an `exclude` keyword matches and no `us_signals` entry appears in any checked field. ATS jobs check the location field; search jobs check the title and raw URL, never the snippet. "New York or Zurich" passes; blank, "Remote" and US locations pass. Search hits on Greenhouse, Lever, or Ashby are checked again after the single-job API returns a structured location (`DROP location <keyword> (ats-location)`). Those drops are not scored.
- `experience_filter.max_years_required`: drop a job whose JD requires more years than this (2). No stated requirement means keep. "years of age", "aged N", and any number >= 15 are ignored.
- `scoring`: current `model` is `claude-sonnet-5-5` with `force_tool_choice: false`. Also `max_tokens` and `max_jd_chars` (JD truncation).
- `prices`: USD per million tokens per model ID, used for the `COST` line. A model with no entry logs "cost unknown" and never fails the run.

**Switching the scoring model** is a one-line change to `scoring.model`, as long as the new model has a `prices` entry. Haiku also needs `force_tool_choice: true` (forced tool call). Sonnet and Opus 5.5 reject a forced tool choice, so keep `force_tool_choice: false` for them.

### `job_scanner/queries.yaml` (committed)

Serper search queries: `queries` (one per job board site) and `company_sites` (big company career sites). Each entry has `name`, `query` and `enabled`. Queries use `-intitle:` exclusions for seniority instead of bare `-word`.

### `job_scanner/companies.yaml` (committed)

Companies polled directly through ATS public APIs. Each entry has `name`, `ats` (`greenhouse`, `lever` or `ashby`) and `slug` (from the careers URL path). A bad slug logs an `ATS` error and never crashes the run.

### `config/profile.yaml` (gitignored)

Your match criteria and evaluation rules for the scorer. Personal scoring rules live here, not in the prompt; on conflict, the profile wins. Copied from `config/profile.example.yaml`.

### `config/candidate.md` (gitignored)

A one-page evidence document the scorer reads alongside the profile. Copied from `config/candidate.example.md`. `email_finder --draft` also uses it as the only source of facts about you.

### `email_finder/settings.yaml` (committed)

- `timezone`: `America/Chicago`; `date_found` is written in this zone.
- `max_urls_per_run`: 20 lookups per run, counted after removing existing and duplicate URLs.
- `providers`: `[prospeo, apify]`, run in this order (`apollo` available, paid plan only).
- `prospeo.timeout_seconds`: 15 per call.
- `prospeo.min_interval_seconds`: 3.1 between calls (limits 1/s, 20/min, 50/day).
- `apify.actor_id`: `vulnv/linkedin-email-finder`.
- `apify.timeout_seconds`: 300 for the one synchronous actor run.
- `apollo.batch_size`: 10 URLs per `bulk_match` call.
- `apollo.timeout_seconds`: 15 per call.
- `apollo.min_interval_seconds`: 1.2 between batches.
- `outreach.model`, `outreach.max_chars`, `outreach.max_tokens`: see below.

`--draft` writes a LinkedIn connection note into each found row's `outreach_line`: plain text, one line, "Hi <first name>," and no sign-off. It uses the contact's first name, company and title, the row's `# note` from the input file (a role mentioned there gets referenced; no note → a networking note), `config/candidate.md`, and `config/outreach_examples.md` if present. The note never mentions visa, sponsorship, phone numbers, email addresses or salary.

- `model`: `claude-sonnet-5-5`.
- `max_chars`: note length limit (200). A longer reply is trimmed at the last sentence end within the limit; if no sentence ends within it, `outreach_line` stays blank and a warning is logged.
- `max_tokens`: 4096. Thinking tokens count against it, so a 200-character note never hits it; a reply that does hit it is a draft failure (blank + warning), never a cut-off note.

### `config/outreach_examples.md` (gitignored, optional)

Example connection notes, used for style only (the drafter is told not to copy them). Copied from `config/outreach_examples.example.md`. A missing file is not an error.

### `email_finder/linkedin-profiles.txt` (gitignored)

The default `email_finder` input: one LinkedIn profile URL per line with an optional trailing `# note`. Copied from `email_finder/linkedin-profiles.example.txt`; format in [Input file format](#input-file-format).

## Costs

### Scoring (Anthropic)

The `COST` line in each run's log reports actual tokens and estimated cost. Scoring uses `claude-sonnet-5-5`. The token counts below were measured on a 25-job Opus run: about **6,875 cache-read, 1,550 input and 470 output tokens per job**. The Opus row is that measurement. The Sonnet row (current model) and the Haiku row apply the same token counts to their `prices` entries. Each run also pays one cache write of the system prompt (about 6,875 tokens), shown separately as an estimate.

| Model | Input / output / cache write / cache read (USD per 1M tokens) | Per job | Cache write per run (est.) | Full run, 150 jobs |
|---|---|---|---|---|
| `claude-sonnet-5-5` (current) | 2.00 / 10.00 / 2.50 / 0.20 | ~$0.009 (estimate) | $0.017 | ~$1.39 (estimate) |
| `claude-opus-5-5` | 4.00 / 20.00 / 5.00 / 0.20 | **$0.017 (measured)** | $0.034 | ~$2.58 |
| `claude-haiku-4-5-20251001` | 1.00 / 5.00 / 1.25 / 0.10 | ~$0.005 (estimate) | $0.009 | ~$0.70 (estimate) |

Newer models' tokenizers count more tokens for the same text than older ones, so the Haiku 4.5 estimate (built from Opus 5.5 counts) is likely on the high side. Snippet-scored jobs are cheaper (less input); long JDs are capped at `max_jd_chars`. Two runs a day roughly doubles the daily figure.

**Set a monthly spend limit** in the Anthropic Console so a misconfiguration can't run up a bill.

### Search (Serper)

Each query page is **1 Serper credit at `num=10`**. The ceiling is enabled queries × `max_pages_per_query` × 2 runs a day: 20 × 4 × 2 = 160 credits/day with the current config (Sequoia Capital, Insight Partners, and Microsoft are disabled). Pagination stops early on empty pages, so typical spend is lower. The `QUERY total Serper calls` line reports the real count per run.

ATS polling (Greenhouse, Lever, Ashby) uses free public APIs.

### Email lookups

- **Prospeo**: free plan, 100 credits/month; limits 1/s, 20/min, 50/day.
- **Apify** (`vulnv/linkedin-email-finder`): about $0.026 per email found, paid from the $5/month free credit.
- **Apollo**: paid plans only, and disabled in `providers`, so it costs nothing today.
- **Drafts** (`--draft`): one `claude-sonnet-5-5` call per found person. Thinking tokens count as output tokens, but a note costs a few cents at most.

## Before every commit

Confirm every secret and personal path is ignored:

```bash
git check-ignore -v .env service_account.json config/profile.yaml config/candidate.md config/outreach_examples.md _legacy _scratch docs/PROJECT_CONTEXT.md .venv email_finder/linkedin-profiles.txt
```

Expect **10 lines**, one `.gitignore` rule per path. A missing line means that path is not ignored; fix `.gitignore` before committing.

Then review exactly what will be committed:

```bash
git status
```

These must never appear in `git status` or a commit:

- `.env`
- `service_account.json` (or any `service_account*.json` / `credentials*.json`)
- any `*.json.b64` file
- `config/profile.yaml`
- `config/candidate.md`
- `config/outreach_examples.md`
- `docs/PROJECT_CONTEXT.md`
- anything under `_legacy/`
- anything under `_scratch/`
- anything under `.venv/`
- `email_finder/linkedin-profiles.txt`
- `run.log` and `review.txt` (gitignored, but worth a glance since they hold full run logs)

## Architecture

See [`docs/DESIGN.md`](docs/DESIGN.md) for the design: data flow, sheet schema, scoring rules, US-only filtering, error handling, logging and key decisions. See [`docs/PLAN.md`](docs/PLAN.md) for the task-by-task build plan and verification steps.
