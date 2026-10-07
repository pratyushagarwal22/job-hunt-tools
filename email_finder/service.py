"""email_finder pipeline: input -> normalize/dedup -> cap -> provider chain -> draft -> append.

Never logs or returns names, emails, notes or outreach text; output lines carry only the
normalized LinkedIn URL, the provider and the email status.
"""

import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import anthropic
import yaml

from email_finder import (
    apify_client,
    apollo_client,
    outreach_client,
    prospeo_client,
    sheets_repo,
)
from email_finder.input_file import InputEntry, parse_line, read_file
from shared.linkedin_url import normalize_linkedin_url

logger = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).parent
_CANDIDATE_PATH = _PACKAGE_DIR.parent / "config" / "candidate.md"
_EXAMPLES_PATH = _PACKAGE_DIR.parent / "config" / "outreach_examples.md"
DEFAULT_MAX_CHARS = 200
DEFAULT_INPUT = _PACKAGE_DIR / "linkedin-profiles.txt"

PROVIDER_ENV_VARS = {
    "prospeo": "PROSPEO_API_KEY",
    "apify": "APIFY_TOKEN",
    "apollo": "APOLLO_API_KEY",
}

_PROFILE_URL = re.compile(r"^https://linkedin\.com/in/[^/\s]+$")


class TooManyUrlsError(ValueError):
    pass


def load_settings() -> dict:
    return yaml.safe_load((_PACKAGE_DIR / "settings.yaml").read_text()) or {}


def required_env_vars(settings: dict, draft: bool) -> list[str]:
    names = ["SHEET_ID", "GOOGLE_APPLICATION_CREDENTIALS"]
    names += [PROVIDER_ENV_VARS[p] for p in settings.get("providers") or [] if p in PROVIDER_ENV_VARS]
    if draft:
        names.append("ANTHROPIC_API_KEY")
    return names


def _now(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def _read_input(urls: list[str], file_path: str | None) -> list[InputEntry]:
    entries = [entry for entry in map(parse_line, urls) if entry is not None]
    if file_path:
        entries += read_file(file_path)
    elif not urls:
        entries = read_file(DEFAULT_INPUT)
    return entries


def _error_text(err: BaseException) -> str:
    # Client RuntimeErrors carry only provider + HTTP status / error code; other exception
    # messages could hold response data, so only their type is logged.
    return str(err) if isinstance(err, RuntimeError) else type(err).__name__


# Each runner calls record(url, match_or_None) as soon as a URL is resolved by that provider,
# so a stop part-way keeps what was already resolved.

def _run_prospeo(urls, settings, sleep, record):
    cfg = settings["prospeo"]
    for i, url in enumerate(urls):
        if i:
            sleep(cfg["min_interval_seconds"])
        record(url, prospeo_client.enrich_person(url, timeout=cfg["timeout_seconds"]))


def _run_apify(urls, settings, sleep, record):
    cfg = settings["apify"]
    items = apify_client.find_emails(urls, actor_id=cfg["actor_id"], timeout=cfg["timeout_seconds"])
    by_url = {normalize_linkedin_url(url): match for url, match in items.items()}
    missing = [url for url in urls if url not in by_url]
    for url in urls:
        if url in by_url:
            record(url, by_url[url])
    if missing:
        raise RuntimeError(f"Apify returned no item for {len(missing)} submitted URL(s)")


def _run_apollo(urls, settings, sleep, record):
    cfg = settings["apollo"]
    size = cfg["batch_size"]
    for start in range(0, len(urls), size):
        if start:
            sleep(cfg["min_interval_seconds"])
        batch = urls[start:start + size]
        records = apollo_client.bulk_match(batch, timeout=cfg["timeout_seconds"])
        for url in batch:
            record(url, records.get(url))


_RUNNERS = {"prospeo": _run_prospeo, "apify": _run_apify, "apollo": _run_apollo}


def _run_chain(urls: list[str], settings: dict, sleep) -> tuple[dict, list[str], list[str], bool]:
    """Returns (found {url: (provider, match)}, not_found urls, pending urls, stopped)."""
    providers = settings["providers"]
    found = {}
    remaining = list(urls)
    for position, provider in enumerate(providers):
        if not remaining:
            break
        no_match = []

        def record(url, match, provider=provider, no_match=no_match):
            if match is None:
                no_match.append(url)
            else:
                found[url] = (provider, match)

        logger.info("%s: looking up %d URL(s)", provider, len(remaining))
        try:
            _RUNNERS[provider](remaining, settings, sleep, record)
        except Exception as err:
            logger.error("STOP %s: %s", provider, _error_text(err))
            is_last = position == len(providers) - 1
            not_found = no_match if is_last else []
            pending = [u for u in remaining if u not in found and u not in not_found]
            return found, not_found, pending, True
        remaining = no_match
    return found, remaining, [], False


def _first_name(name: str) -> str:
    parts = (name or "").split()
    return parts[0] if parts else ""


def _draft_lines(found: dict, notes: dict, settings: dict) -> tuple[dict[str, str], bool]:
    """Returns ({url: outreach_line}, auth_failed)."""
    cfg = settings["outreach"]
    candidate_md = _CANDIDATE_PATH.read_text()
    examples_md = _EXAMPLES_PATH.read_text() if _EXAMPLES_PATH.is_file() else ""
    lines = {}
    for url, (_, match) in found.items():
        try:
            lines[url] = outreach_client.draft_outreach(
                first_name=_first_name(match["name"]), company=match["company"],
                title=match["title"], notes=notes.get(url, ""),
                candidate_md=candidate_md, examples_md=examples_md,
                model=cfg["model"], max_tokens=cfg["max_tokens"],
                max_chars=cfg.get("max_chars", DEFAULT_MAX_CHARS),
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as err:
            logger.error("DRAFT stopped: %s; remaining outreach lines left blank", type(err).__name__)
            return lines, True
        except Exception as err:
            logger.warning("DRAFT failed for %s: %s; outreach line left blank", url, type(err).__name__)
    return lines, False


def run(urls: list[str], file_path: str | None, draft: bool, sleep=time.sleep) -> dict:
    """Returns {lines, summary, exit_code}. Raises before any lookup on bad settings,
    a missing Contacts header or too many URLs."""
    settings = load_settings()
    providers = settings.get("providers") or []
    unknown = [p for p in providers if p not in _RUNNERS]
    if not providers or unknown:
        raise ValueError(f"Invalid providers in settings.yaml: {unknown or 'none configured'}")

    sheet_id = os.environ["SHEET_ID"]
    entries = _read_input(urls, file_path)
    columns = sheets_repo.get_columns(sheet_id)
    existing_urls, _ = sheets_repo.get_existing_keys(sheet_id, columns)

    # (kind, url) per input entry, in input order; ignored entries keep the text as given.
    outcomes = []
    notes = {}
    seen = set()
    to_look_up = []
    for entry in entries:
        url = normalize_linkedin_url(entry.url)
        if " " in entry.url or not _PROFILE_URL.match(url):
            outcomes.append(("ignored", entry.url))
        elif url in seen:
            outcomes.append(("duplicate", url))
        else:
            seen.add(url)
            if url in existing_urls:
                outcomes.append(("existing", url))
            else:
                outcomes.append(("lookup", url))
                notes[url] = entry.note
                to_look_up.append(url)

    cap = settings["max_urls_per_run"]
    if len(to_look_up) > cap:
        raise TooManyUrlsError(
            f"{len(to_look_up)} URLs to look up exceeds max_urls_per_run {cap}; nothing looked up"
        )

    found, not_found, pending, stopped = (
        _run_chain(to_look_up, settings, sleep) if to_look_up else ({}, [], [], False)
    )

    outreach, draft_auth_failed = _draft_lines(found, notes, settings) if draft and found else ({}, False)

    date_found = _now(settings["timezone"]).strftime("%Y-%m-%d %H:%M")
    not_found_set = set(not_found)
    pending_set = set(pending)
    rows = []
    lines = []
    for kind, display in outcomes:
        if kind == "ignored":
            lines.append(f"ignored {display} (not a profile URL)")
        elif kind == "duplicate":
            lines.append(f"duplicate {display}")
        elif kind == "existing":
            lines.append(f"{display} exists")
        elif display in pending_set:
            lines.append(f"{display} pending")
        elif display in found:
            provider, match = found[display]
            lines.append(f"{display} found via {provider} {match['email_status']}".rstrip())
            rows.append({
                "contacted": "",
                "name": match["name"],
                "company": match["company"],
                "title": match["title"],
                "email": match["email"],
                "email_status": match["email_status"],
                "email_source": provider,
                "linkedin_url": display,
                "notes": notes[display],
                "outreach_line": outreach.get(display, ""),
                "date_found": date_found,
            })
        elif display in not_found_set:
            lines.append(f"{display} not found")
            rows.append({
                "contacted": "", "name": "", "company": "", "title": "", "email": "",
                "email_status": "not found", "email_source": "", "linkedin_url": display,
                "notes": notes[display], "outreach_line": "", "date_found": date_found,
            })

    if rows:
        sheets_repo.append_contacts(sheet_id, rows, columns)

    counts = {
        kind: sum(1 for k, _ in outcomes if k == kind) for kind in ("ignored", "duplicate", "existing")
    }
    found_per_provider = {p: 0 for p in providers}
    for provider, _ in found.values():
        found_per_provider[provider] += 1
    summary = {
        "input": len(entries),
        **counts,
        "found": found_per_provider,
        "not_found": len(not_found),
        "pending": len(pending),
    }
    return {"lines": lines, "summary": summary, "exit_code": 1 if stopped or draft_auth_failed else 0}
