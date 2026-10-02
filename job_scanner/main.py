"""CLI entry point: python -m job_scanner.main [--max-jobs N]"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from job_scanner import service, sheets_repo

logger = logging.getLogger("job_scanner.main")

REQUIRED_ENV_VARS = ("SHEET_ID", "SERPER_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for name in ("httpx", "httpx2", "anthropic"):
        logging.getLogger(name).setLevel(logging.WARNING)

    parser = argparse.ArgumentParser(description="Scan for new jobs and append them to the sheet.")
    parser.add_argument(
        "--max-jobs", type=int, default=None, help="Override max_jobs_per_run for this run."
    )
    args = parser.parse_args(argv)

    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        logger.error("Missing required environment variable(s): %s", ", ".join(missing))
        return 1

    try:
        sheet_id = os.environ["SHEET_ID"]
        print(f"Writing to sheet: {sheets_repo.get_sheet_title(sheet_id)}", flush=True)
        summary = service.run_pipeline(args.max_jobs)
    except Exception:
        logger.exception("Fatal error, run aborted")
        return 1

    print(
        f"found {summary['found']}, new {summary['new']}, filtered {summary['filtered']}, "
        f"scored {summary['scored']}, failed {summary['failed']}, "
        f"skipped (cap): {summary['skipped_cap']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
