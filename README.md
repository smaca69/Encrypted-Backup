# Encrypted Backup

Encrypted Backup is a Windows desktop application for protecting folders before
copying them to a local or cloud-synchronized destination. Files are encrypted
individually, so a backup destination can be stored on OneDrive, Google Drive,
Proton Drive, an external disk, or another folder without exposing the originals.

## Features

- Fernet encryption for every backed-up file and the backup manifest
- Incremental backups that skip unchanged files
- Dry-run and force-backup modes
- Single-file and complete-source restore
- Multiple labeled source folders
- Optional unattended backups through Windows Task Scheduler
- Live progress and error logging in the desktop interface

## Requirements

- Windows with Python 3.10 or newer
- Packages listed in `requirements.txt`

## Installation

```powershell
cd D:\DATA\EncryptedBackup
python -m pip install -r requirements.txt
```

## Start the application

```powershell
python encrypted_backup_gui.py
```

On first launch, the application creates an encryption key at
`C:\Users\<YourName>\.encbackup_key`. The GUI stores source and destination
settings in `config.json` beside the application.

> **Protect the key.** A backup cannot be restored without it. Keep a secure,
> separate copy of the key and never place it in the backup destination.

## Configure a backup

1. In **Sources & Destination**, add each source folder with a short label and
   choose the backup destination.
2. In **Backup**, select all sources or one label and click **Run Backup**.
3. Use **Dry run** to preview changes. Use **Force** when every source file must
   be encrypted again.
4. In **Restore**, decrypt one file or restore an entire labeled source while
   preserving its folder structure.

The application writes its automated-run log to `backup_log.txt`.

## Automated backups

The headless entry point is designed for Windows Task Scheduler:

```powershell
python encrypted_backup_headless.py
```

Configure the schedule from the application's automated-backups settings. The
scheduled task reads the saved password from Windows Credential Manager and uses
the settings in `config.json`; it does not require the GUI to be open.

## Backup layout

```text
<destination>/
  <SourceLabel>/
    subfolder/
      report.pdf.enc
      photo.jpg.enc
    notes.txt.enc
  .manifest.enc
```

Do not delete `.manifest.enc`: it contains the encrypted index used to identify
changed files and restore source paths.

## Security

- Files are encrypted independently with Fernet from the `cryptography` package.
- The encrypted manifest contains backup metadata and file hashes.
- `config.json`, `backup_log.txt`, local environments, and Python caches are
  excluded from version control by `.gitignore`.
- Test restoration regularly and keep the encryption key separate from the
  backup destination.
