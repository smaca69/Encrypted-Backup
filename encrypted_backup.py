"""
encrypted_backup.py  v2.0
=========================
Core logic for the Encrypted Backup application.

Security model (inspired by VeraCrypt):
  Key file  : 32-byte random salt — stored on disk or a USB drive
  Password  : user-supplied at runtime — never stored anywhere
  Master key: PBKDF2-HMAC-SHA256(password, salt, iterations=600_000) -> 32 bytes
              -> base64url-encode -> Fernet key

Neither the key file alone NOR the password alone can decrypt any data.
Both are ALWAYS required.

600,000 PBKDF2 iterations impose ~0.5-2 s of computation per attempt on
modern hardware, making password brute-force extremely expensive.
VeraCrypt uses a similar approach with its own PRF.
"""

import base64
import hashlib
import json
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# ─── Constants ────────────────────────────────────────────────────────────────

VERSION           = "2.0.0"
DEFAULT_KEY_FILE  = Path.home() / ".encbackup_key"
CONFIG_FILE       = Path(__file__).parent / "config.json"
MANIFEST_FILENAME = ".manifest.enc"
ENC_SUFFIX        = ".enc"

SALT_SIZE      = 32        # bytes of random salt stored in the key file
KDF_ITERATIONS = 600_000   # PBKDF2 iterations — increases brute-force cost


# ─── Key file management ──────────────────────────────────────────────────────

def init_key(key_path: Optional[str | Path] = None) -> Path:
    """
    Create the key file (32-byte random salt) if it does not already exist.
    The salt is NOT the encryption key — it is combined with the user's password
    via PBKDF2 at runtime to derive the actual Fernet key.
    """
    key_path = Path(key_path or DEFAULT_KEY_FILE)
    if not key_path.exists():
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(os.urandom(SALT_SIZE))
        try:
            key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
    return key_path


def regenerate_key(key_path: Optional[str | Path] = None) -> Path:
    """
    Replace the key file with a fresh random salt.

    WARNING: Any backups encrypted with the OLD key file will be permanently
    inaccessible unless you still have that old file and remember its password.
    """
    key_path = Path(key_path or DEFAULT_KEY_FILE)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(os.urandom(SALT_SIZE))
    try:
        key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass
    return key_path


def _is_old_format(key_path: Path) -> bool:
    """Return True if the key file looks like a v1 Fernet key (44-byte base64)."""
    try:
        data = key_path.read_bytes()
        if len(data) == 44:
            base64.urlsafe_b64decode(data)
            return True
    except Exception:
        pass
    return False


def derive_key(password: str, key_path: Path) -> Fernet:
    """
    Derive the Fernet key from the user's password and the key-file salt.

    Dual-factor: password (something you know) + key file (something you have).
    Uses PBKDF2-HMAC-SHA256 with 600,000 iterations.
    """
    salt = Path(key_path).read_bytes()

    if len(salt) != SALT_SIZE:
        if _is_old_format(Path(key_path)):
            raise ValueError(
                "This key file is in the old v1 format (no password protection).\n"
                "Old backups cannot be decrypted with the new dual-factor model.\n"
                "Use 'Regenerate Key File' to create a new key, then re-run backups."
            )
        raise ValueError(
            f"Key file has unexpected size ({len(salt)} bytes) — it may be corrupted."
        )

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
    return Fernet(key)


def load_key(key_path: Optional[str | Path] = None,
             password: Optional[str] = None) -> Fernet:
    """
    Load the key file and derive the encryption key.
    Both key_path and password are required — neither alone is sufficient.
    """
    key_path = Path(key_path or DEFAULT_KEY_FILE)
    if not key_path.exists():
        raise FileNotFoundError(f"Key file not found: {key_path}")
    if not password:
        raise ValueError("A password is required to unlock the encryption key.")
    return derive_key(password, key_path)


# ─── Config ───────────────────────────────────────────────────────────────────

def _default_config() -> dict:
    return {
        "sources":     [],
        "destination": "",
        "key_file":    str(DEFAULT_KEY_FILE),
    }


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in _default_config().items():
            cfg.setdefault(k, v)
        return cfg
    return _default_config()


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ─── Manifest ─────────────────────────────────────────────────────────────────

def _manifest_path(cfg: dict) -> Optional[Path]:
    dest = cfg.get("destination", "")
    return Path(dest) / MANIFEST_FILENAME if dest else None


def has_existing_backup(cfg: dict) -> bool:
    """Return True if an encrypted manifest exists at the destination."""
    path = _manifest_path(cfg)
    return path is not None and path.exists()


def load_manifest(cfg: dict, password: str) -> dict:
    """
    Decrypt and load the backup manifest.
    Raises ValueError with a clear message if the password is wrong.
    """
    path = _manifest_path(cfg)
    if not path or not path.exists():
        return {}
    fernet = load_key(cfg.get("key_file"), password)
    try:
        return json.loads(fernet.decrypt(path.read_bytes()).decode("utf-8"))
    except InvalidToken:
        raise ValueError(
            "Incorrect password or wrong key file.\n"
            "The backup manifest could not be decrypted."
        )


def save_manifest(cfg: dict, manifest: dict, password: str) -> None:
    path = _manifest_path(cfg)
    if not path:
        raise RuntimeError("No backup destination configured.")
    Path(cfg["destination"]).mkdir(parents=True, exist_ok=True)
    fernet = load_key(cfg.get("key_file"), password)
    path.write_bytes(fernet.encrypt(json.dumps(manifest, indent=2).encode("utf-8")))


def verify_password(cfg: dict, password: str) -> bool:
    """
    Test whether password + key file correctly decrypts the manifest.
    Returns True on success, False if the password is wrong.
    Call before starting a long backup/restore to catch typos early.
    """
    if not has_existing_backup(cfg):
        return True   # no manifest yet — can't verify, proceed
    try:
        load_manifest(cfg, password)
        return True
    except ValueError:
        return False


# ─── File encryption / decryption ─────────────────────────────────────────────

def compute_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def encrypt_file(source: Path, dest: Path, fernet: Fernet) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(fernet.encrypt(source.read_bytes()))


def decrypt_file(enc_path: Path, output_path: Path, fernet: Fernet) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_path.write_bytes(fernet.decrypt(enc_path.read_bytes()))
    except InvalidToken:
        raise ValueError(
            f"Failed to decrypt {enc_path.name}.\n"
            "Wrong password or key file, or the file is corrupted."
        )


# ─── Source management ────────────────────────────────────────────────────────

def add_source(cfg: dict, label: str, path: str) -> None:
    label = label.strip()
    if not label:
        raise ValueError("Label cannot be empty.")
    for s in cfg["sources"]:
        if s["label"].lower() == label.lower():
            raise ValueError(f"A source labelled '{label}' already exists.")
    cfg["sources"].append({"label": label, "path": str(Path(path).resolve())})
    save_config(cfg)


def remove_source(cfg: dict, label: str) -> None:
    before = len(cfg["sources"])
    cfg["sources"] = [s for s in cfg["sources"] if s["label"] != label]
    if len(cfg["sources"]) == before:
        raise ValueError(f"No source labelled '{label}' found.")
    save_config(cfg)


def set_destination(cfg: dict, path: str) -> None:
    cfg["destination"] = str(Path(path).resolve())
    save_config(cfg)


def set_key_file(cfg: dict, path: str) -> None:
    cfg["key_file"] = str(Path(path).resolve())
    save_config(cfg)


# ─── Status ───────────────────────────────────────────────────────────────────

def get_status(cfg: dict, password: Optional[str] = None) -> list[dict]:
    """
    Return per-source status dicts.
    If password is provided, backed_up_count and last_backup are read from the
    (encrypted) manifest.  Without a password those fields are None.
    """
    manifest: dict = {}
    if password:
        try:
            manifest = load_manifest(cfg, password)
        except Exception:
            pass   # wrong password or no manifest — show partial info only

    result = []
    for s in cfg["sources"]:
        p = Path(s["path"])
        file_count = 0
        if p.exists():
            try:
                file_count = sum(1 for f in p.rglob("*") if f.is_file())
            except PermissionError:
                file_count = -1

        src_m = manifest.get("sources", {}).get(s["label"], {})
        result.append({
            "label":           s["label"],
            "path":            s["path"],
            "exists":          p.exists(),
            "file_count":      file_count,
            "backed_up_count": len(src_m.get("files", {})) if password else None,
            "last_backup":     src_m.get("last_backup") if password else None,
        })
    return result


# ─── Backup ───────────────────────────────────────────────────────────────────

def backup(
    cfg: dict,
    password: str,
    source_label: Optional[str] = None,
    dry_run: bool = False,
    force: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Encrypt and copy files to the destination.
    password is combined with the key file via PBKDF2 to produce the Fernet key.
    """
    def log(msg: str):
        if progress:
            progress(msg)

    if not cfg.get("destination"):
        raise RuntimeError("No backup destination configured.")
    if not cfg.get("sources"):
        raise RuntimeError("No source folders configured.")

    log("Deriving encryption key (PBKDF2)…")
    fernet = load_key(cfg.get("key_file"), password)

    log("Loading backup manifest…")
    manifest = load_manifest(cfg, password)
    manifest.setdefault("sources", {})

    sources = cfg["sources"]
    if source_label:
        sources = [s for s in sources if s["label"] == source_label]
        if not sources:
            raise ValueError(f"Source '{source_label}' not found.")

    dest_root = Path(cfg["destination"])
    totals = {"backed_up": 0, "skipped": 0, "errors": 0, "total": 0}
    tag = "[DRY RUN] " if dry_run else ""

    for src in sources:
        label    = src["label"]
        src_path = Path(src["path"])
        if not src_path.exists():
            log(f"[WARN] Source not found, skipping: {src_path}")
            continue

        log(f"\n{tag}▶  {label}   ({src_path})")
        manifest["sources"].setdefault(label, {"files": {}, "last_backup": None})
        src_manifest = manifest["sources"][label]

        all_files = [f for f in src_path.rglob("*") if f.is_file()]
        totals["total"] += len(all_files)

        for file in all_files:
            rel     = file.relative_to(src_path)
            rel_str = str(rel)
            enc_dst = dest_root / label / (rel_str + ENC_SUFFIX)

            file_hash = compute_hash(file)
            prev      = src_manifest["files"].get(rel_str)

            if prev and prev.get("hash") == file_hash and not force:
                log(f"  [skip]   {rel}")
                totals["skipped"] += 1
                continue

            log(f"  {'[dry] ' if dry_run else ''}[backup] {rel}")
            if not dry_run:
                try:
                    encrypt_file(file, enc_dst, fernet)
                    src_manifest["files"][rel_str] = {
                        "hash":      file_hash,
                        "size":      file.stat().st_size,
                        "backed_up": datetime.now().isoformat(timespec="seconds"),
                    }
                    totals["backed_up"] += 1
                except Exception as e:
                    log(f"  [ERROR]  {rel}: {e}")
                    totals["errors"] += 1
            else:
                totals["backed_up"] += 1

        if not dry_run:
            src_manifest["last_backup"] = datetime.now().isoformat(timespec="seconds")

    if not dry_run:
        save_manifest(cfg, manifest, password)

    log(
        f"\n{tag}Finished — backed up: {totals['backed_up']}, "
        f"skipped: {totals['skipped']}, errors: {totals['errors']}, "
        f"total: {totals['total']}"
    )
    return totals


# ─── Restore ──────────────────────────────────────────────────────────────────

def restore_file(enc_path: Path, output_dir: Path, cfg: dict,
                 password: str) -> Path:
    """Decrypt a single .enc file. Requires both password and key file."""
    enc_path = Path(enc_path)
    if not enc_path.exists():
        raise FileNotFoundError(f"Encrypted file not found: {enc_path}")
    if not enc_path.name.endswith(ENC_SUFFIX):
        raise ValueError(f"File does not have the {ENC_SUFFIX} suffix.")

    original_name = enc_path.name[: -len(ENC_SUFFIX)]
    output_path   = Path(output_dir) / original_name
    fernet        = load_key(cfg.get("key_file"), password)
    decrypt_file(enc_path, output_path, fernet)
    return output_path


# ─── Password storage for automated backups (Windows Credential Manager) ──────

KEYRING_SERVICE = "EncryptedBackupApp"
KEYRING_USER    = "backup_password"


def save_password_to_keyring(password: str) -> None:
    """
    Store the backup password in Windows Credential Manager.
    The credential is encrypted by Windows and is only accessible
    to the currently logged-in user account.
    """
    import keyring
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, password)


def load_password_from_keyring() -> Optional[str]:
    """Retrieve the stored backup password, or None if not set."""
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        return None


def clear_password_from_keyring() -> None:
    """Remove the stored backup password from Credential Manager."""
    try:
        import keyring
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        pass


def keyring_has_password() -> bool:
    """Return True if a password is currently stored."""
    return load_password_from_keyring() is not None


# ─── Windows Task Scheduler ───────────────────────────────────────────────────

TASK_NAME = "EncryptedBackup"


def create_schedule(schedule_type: str, time_str: str = "02:00") -> tuple:
    """
    Create or overwrite a Windows Task Scheduler task.

    schedule_type : "daily"  — runs every day at time_str (HH:MM)
                   "login"  — runs each time the current user logs in
    Returns (success: bool, message: str).
    """
    import subprocess
    import sys

    script  = str(Path(__file__).parent / "encrypted_backup_headless.py")
    python  = sys.executable
    cmd = [
        "schtasks", "/CREATE", "/F",
        "/TN",  TASK_NAME,
        "/TR",  f'"{python}" "{script}"',
    ]
    if schedule_type == "daily":
        cmd += ["/SC", "DAILY", "/ST", time_str]
    elif schedule_type == "login":
        cmd += ["/SC", "ONLOGON"]
    else:
        return False, f"Unknown schedule type: {schedule_type!r}"

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return True, "Scheduled task created."
    return False, (result.stderr or result.stdout).strip() or "Unknown error from schtasks."


def delete_schedule() -> tuple:
    """Remove the scheduled task. Returns (success: bool, message: str)."""
    import subprocess
    result = subprocess.run(
        ["schtasks", "/DELETE", "/F", "/TN", TASK_NAME],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return True, "Scheduled task removed."
    return False, (result.stderr or result.stdout).strip() or "Task not found."


def get_schedule_info() -> Optional[str]:
    """
    Query the Windows scheduled task.
    Returns a human-readable description (e.g. "Daily at 2:00 AM") or None.
    """
    import subprocess
    result = subprocess.run(
        ["schtasks", "/QUERY", "/TN", TASK_NAME, "/FO", "LIST", "/V"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None

    info: dict = {}
    for line in result.stdout.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            k, v = key.strip(), val.strip()
            if k and v:
                info[k] = v

    stype = info.get("Schedule Type", "").lower()
    stime = info.get("Start Time", "")

    if "daily" in stype:
        return f"Daily at {stime}" if stime else "Daily"
    if "log" in stype:       # "At log on"
        return "On Login"
    if stype:
        return stype.title()
    return "Active"


def restore_source(
    cfg: dict,
    source_label: str,
    output_dir: Path,
    password: str,
    progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """Decrypt all backed-up files for a source. Requires both password and key file."""
    def log(msg: str):
        if progress:
            progress(msg)

    if not cfg.get("destination"):
        raise RuntimeError("No backup destination configured.")

    dest_root       = Path(cfg["destination"])
    enc_source_root = dest_root / source_label
    if not enc_source_root.exists():
        raise FileNotFoundError(
            f"No backup found for '{source_label}' at {enc_source_root}."
        )

    log("Deriving encryption key (PBKDF2)…")
    fernet    = load_key(cfg.get("key_file"), password)
    output_dir = Path(output_dir)
    enc_files  = [f for f in enc_source_root.rglob(f"*{ENC_SUFFIX}") if f.is_file()]
    totals     = {"restored": 0, "errors": 0}

    log(f"Restoring {len(enc_files)} file(s) for '{source_label}' → {output_dir}")

    for enc_path in enc_files:
        rel_enc      = enc_path.relative_to(enc_source_root)
        original_rel = Path(str(rel_enc)[: -len(ENC_SUFFIX)])
        output_path  = output_dir / original_rel
        try:
            decrypt_file(enc_path, output_path, fernet)
            log(f"  [ok]     {original_rel}")
            totals["restored"] += 1
        except Exception as e:
            log(f"  [ERROR]  {original_rel}: {e}")
            totals["errors"] += 1

    log(f"\nDone — restored: {totals['restored']}, errors: {totals['errors']}")
    return totals
