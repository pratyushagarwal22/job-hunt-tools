"""CLI entry point: python -m email_finder.main [urls ...] [--file PATH] [--draft]"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from email_finder import service, sheets_repo

logger = logging.getLogger("email_finder.main")


def _summary_line(summary: dict) -> str:
    found = ", ".join(f"found {provider} {count}" for provider, count in summary["found"].items())
    return (
        f"input {summary['input']}, ignored {summary['ignored']}, "
        f"duplicate {summary['duplicate']}, skipped existing {summary['existing']}, "
        f"{found}, not found {summary['not_found']}, pending {summary['pending']}"
    )


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpx2", "anthropic", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(
        description="Look up emails for LinkedIn profile URLs and append them to the Contacts tab."
    )
    parser.add_argument("urls", nargs="*", help="LinkedIn profile URLs.")
    parser.add_argument(
        "--file", default=None,
        help="File with one URL per line (default when no URLs: email_finder/linkedin-profiles.txt).",
    )
    parser.add_argument("--draft", action="store_true", help="Draft an outreach line for found rows.")
    args = parser.parse_args(argv)

    try:
        settings = service.load_settings()
    except Exception:
        logger.exception("Could not read email_finder/settings.yaml")
        return 1
    missing = [
        name for name in service.required_env_vars(settings, args.draft) if not os.environ.get(name)
    ]
    if missing:
        logger.error("Missing required environment variable(s): %s", ", ".join(missing))
        return 1

    try:
        print(f"Writing to sheet: {sheets_repo.get_sheet_title(os.environ['SHEET_ID'])}", flush=True)
        result = service.run(args.urls, args.file, args.draft)
    except Exception:
        logger.exception("Fatal error, run aborted")
        return 1

    for line in result["lines"]:
        print(line)
    print(_summary_line(result["summary"]))
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
