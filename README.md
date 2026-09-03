# Encrypted Backup

A Python desktop app that encrypts your files individually before storing them in a
backup destination (local drive, OneDrive, Google Drive, etc.).

## Requirements

- Python 3.10+
- Dependencies in `requirements.txt`

## Setup

```powershell
cd D:\DATA\EncryptedBackup
pip install -r requirements.txt
```

## Launch

```powershell
python encrypted_backup_gui.py
```

## First run

A master encryption key is generated automatically on first launch and saved to:

```
C:\Users\<YourName>\.encbackup_key
```

> **Important — keep this file safe.**
> Without the key you cannot decrypt any of your backups.
> Consider copying it to a USB drive or password manager.
> Never store the key inside your backup destination folder.

## How to use

### 1. Sources & Destination (📁 tab)

- **Add Source** — choose a folder to back up and give it a short label
  (e.g. label `Documents`, path `C:\Users\Sam\Documents`).
  You can add as many sources as you like.
- **Remove** — click a row to select it, then click Remove. This does **not**
  delete already-backed-up files.
- **Backup Destination** — click Browse and point it at your cloud-synced folder
  (e.g. `C:\Users\Sam\OneDrive\Backups`).

### 2. Backup (🔄 tab)

- The status table shows how many files each source has and when it was last backed up.
- **Dry run** — tick this to preview what would be backed up without writing any files.
- **Force** — tick this to re-encrypt all files even if they haven't changed.
- Choose **All sources** or a specific label from the dropdown.
- Click **▶ Run Backup**. Progress appears in the log area in real time.
- Files that haven't changed since the last backup are automatically skipped.

### 3. Restore (📤 tab)

**Single File** — decrypt one `.enc` file back to a folder you choose.

**Entire Source** — decrypt every backed-up file for a source label back to a
folder, preserving the original subfolder structure.

## Backup destination structure

```
<destination>/
  <SourceLabel>/
    subfolder/
      report.pdf.enc
      photo.jpg.enc
    notes.txt.enc
  .manifest.enc          ← encrypted index (do not delete)
```

## Security notes

- Encryption: **Fernet** (AES-128-CBC + HMAC-SHA256) from the `cryptography` package.
- Each file is encrypted independently — cloud-sync tools only upload changed files.
- The manifest (file index / hash list) is also encrypted with the same key.
- The key file uses OS-level read permissions (owner-only on Unix/Linux).
