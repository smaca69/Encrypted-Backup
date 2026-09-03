"""
encrypted_backup_headless.py
============================
Non-interactive backup script invoked by Windows Task Scheduler.

Retrieves the encryption password from Windows Credential Manager (keyring)
so the backup can run unattended without storing the password in plaintext.

The password is saved there via the Automated Backups section in Settings.

Log file: <app folder>/backup_log.txt
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

# Ensure the app folder is on the path regardless of where schtasks launches from
APP_DIR = Path(__file__).parent
sys.path.insert(0, str(APP_DIR))

import encrypted_backup as bk

LOG_FILE = APP_DIR / "backup_log.txt"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    encoding="utf-8",
)


def main() -> int:
    logging.info("=" * 60)
    logging.info("Automated backup started")

    # ── Load config ────────────────────────────────────────────────
    cfg = bk.load_config()

    if not cfg.get("sources"):
        logging.error("No source folders configured. Open the app and add sources.")
        return 1

    if not cfg.get("destination"):
        logging.error("No backup destination configured. Open the app and set a destination.")
        return 1

    # ── Load password from Credential Manager ──────────────────────
    password = bk.load_password_from_keyring()
    if not password:
        logging.error(
            "No password found in Windows Credential Manager.\n"
            "Open the app → Settings → Automated Backups and click 'Set Up Schedule'."
        )
        return 1

    # ── Run backup ─────────────────────────────────────────────────
    try:
        totals = bk.backup(
            cfg,
            password=password,
            progress=lambda msg: logging.info(msg.strip()),
        )
        logging.info(
            "Backup complete — backed up: %d, skipped: %d, errors: %d, total: %d",
            totals["backed_up"], totals["skipped"],
            totals["errors"], totals["total"],
        )
        if totals["errors"]:
            logging.warning("%d file(s) had errors — check the log above.", totals["errors"])
            return 2
        return 0

    except Exception as exc:
        logging.exception("Backup failed with an unexpected error: %s", exc)
        return 1

    finally:
        logging.info("Automated backup finished")
        logging.info("=" * 60)


if __name__ == "__main__":
    sys.exit(main())
