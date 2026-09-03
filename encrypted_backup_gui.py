"""
encrypted_backup_gui.py  v2.0
==============================
Modern customtkinter GUI for the Encrypted Backup application.

Security model (like VeraCrypt):
  - Session-based unlock: enter password + present key file ONCE per session.
  - 'Lock Session' button clears the in-memory password immediately.
  - Missing key file triggers a file-picker dialog before any operation.
  - Backup status (backed-up counts / timestamps) requires the password because
    they are stored inside the encrypted manifest — nothing leaks without it.

Launch with:
    python encrypted_backup_gui.py
"""

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

import encrypted_backup as bk

# ── Theme ─────────────────────────────────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

SIDEBAR_W    = 215
WIN_W, WIN_H = 980, 700

COL_ACCENT   = "#2563eb"
COL_ACCENT_H = "#1d4ed8"
COL_DANGER   = "#dc2626"
COL_DANGER_H = "#991b1b"
COL_GREEN    = "#16a34a"
COL_MUTED    = "gray"
COL_HDR_BG   = ("gray80", "gray22")
COL_ROW_BG   = ("gray87", "gray18")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _section(parent, text: str) -> ctk.CTkLabel:
    lbl = ctk.CTkLabel(parent, text=text,
                       font=ctk.CTkFont(size=10, weight="bold"),
                       text_color=COL_MUTED)
    lbl.pack(anchor="w", pady=(12, 2))
    return lbl


def _divider(parent) -> ctk.CTkFrame:
    d = ctk.CTkFrame(parent, height=1, fg_color=("gray75", "gray30"))
    d.pack(fill="x", pady=8)
    return d


# ─── Password dialog ──────────────────────────────────────────────────────────

class PasswordDialog(ctk.CTkToplevel):
    """
    Prompt for the encryption password.
    If confirm=True a 'Confirm password' field is shown (for first-time setup).

    Attributes after close:
      password : str | None — None means the user cancelled
    """

    def __init__(self, parent, confirm: bool = False,
                 title: str = "Enter Password"):
        super().__init__(parent)
        self.title(title)
        self.geometry("440x310" if confirm else "440x240")
        self.resizable(False, False)
        self.password: str | None = None
        self._confirm = confirm
        self._build()
        self.grab_set()
        self.focus()

    def _build(self):
        ctk.CTkLabel(self, text="🔐  Password Required",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 4))

        desc = (
            "Enter your password AND present the key file to decrypt.\n"
            "Neither alone is sufficient (like VeraCrypt)."
            if not self._confirm else
            "Choose a strong password for your new backup.\n"
            "You will need BOTH this password AND the key file to decrypt.\n"
            "Neither alone is sufficient."
        )
        ctk.CTkLabel(self, text=desc, text_color=COL_MUTED,
                     font=ctk.CTkFont(size=11),
                     wraplength=390, justify="center").pack(pady=(0, 12))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=32)

        self._pw_var  = tk.StringVar()
        self._pw2_var = tk.StringVar()
        self._show_var = tk.BooleanVar(value=False)

        pw_row = ctk.CTkFrame(form, fg_color="transparent")
        pw_row.pack(fill="x", pady=4)
        ctk.CTkLabel(pw_row, text="Password:", width=110, anchor="w").pack(side="left")
        self._pw_entry = ctk.CTkEntry(pw_row, textvariable=self._pw_var,
                                       show="●", width=220)
        self._pw_entry.pack(side="left", padx=6)
        ctk.CTkCheckBox(pw_row, text="Show", variable=self._show_var, width=60,
                        command=self._toggle_show).pack(side="left")

        if self._confirm:
            pw2_row = ctk.CTkFrame(form, fg_color="transparent")
            pw2_row.pack(fill="x", pady=4)
            ctk.CTkLabel(pw2_row, text="Confirm:", width=110, anchor="w").pack(side="left")
            self._pw2_entry = ctk.CTkEntry(pw2_row, textvariable=self._pw2_var,
                                            show="●", width=220)
            self._pw2_entry.pack(side="left", padx=6)

        btns = ctk.CTkFrame(self, fg_color="transparent")
        btns.pack(pady=16)
        ctk.CTkButton(btns, text="Unlock" if not self._confirm else "Set Password",
                      width=130, fg_color=COL_ACCENT, hover_color=COL_ACCENT_H,
                      command=self._ok).pack(side="left", padx=8)
        ctk.CTkButton(btns, text="Cancel", width=90,
                      fg_color=("gray65", "gray35"),
                      command=self.destroy).pack(side="left", padx=4)

        self._pw_entry.focus()
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self.destroy())

    def _toggle_show(self):
        ch = "" if self._show_var.get() else "●"
        self._pw_entry.configure(show=ch)
        if self._confirm:
            self._pw2_entry.configure(show=ch)

    def _ok(self):
        pw = self._pw_var.get()
        if not pw:
            messagebox.showwarning("Empty password",
                                   "Please enter a password.", parent=self)
            return
        if self._confirm:
            if pw != self._pw2_var.get():
                messagebox.showwarning("Mismatch",
                                       "Passwords do not match.", parent=self)
                return
        self.password = pw
        self.destroy()


# ─── Locate key file dialog ───────────────────────────────────────────────────

class LocateKeyDialog(ctk.CTkToplevel):
    """
    Shown when the configured key file cannot be found.

    Attributes after close:
      new_path  : str | None — path the user browsed to, or None
      regenerate: bool       — True if the user wants a fresh key (breaking change)
    """

    def __init__(self, parent, missing_path: str):
        super().__init__(parent)
        self.title("Key File Not Found")
        self.geometry("500x280")
        self.resizable(False, False)
        self.new_path: str | None = None
        self.regenerate = False
        self._build(missing_path)
        self.grab_set()
        self.focus()

    def _build(self, missing_path: str):
        ctk.CTkLabel(self, text="🔑  Key File Not Found",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 4))
        ctk.CTkLabel(
            self,
            text=f"Expected location:\n{missing_path}",
            text_color=COL_MUTED,
            font=ctk.CTkFont(size=11),
            wraplength=460, justify="center",
        ).pack(pady=(0, 16))

        ctk.CTkButton(
            self,
            text="📂  Browse for Existing Key File",
            width=300, height=38,
            fg_color=COL_ACCENT, hover_color=COL_ACCENT_H,
            command=self._browse,
        ).pack(pady=4)

        ctk.CTkLabel(
            self,
            text="— or —",
            text_color=COL_MUTED, font=ctk.CTkFont(size=11),
        ).pack(pady=4)

        ctk.CTkButton(
            self,
            text="⚠  Generate New Key File  (existing backups become unreadable)",
            width=400, height=38,
            fg_color=("gray65", "gray35"), hover_color=COL_DANGER,
            font=ctk.CTkFont(size=11),
            command=self._gen_new,
        ).pack(pady=4)

        ctk.CTkButton(self, text="Cancel", width=90,
                      fg_color=("gray60", "gray30"),
                      command=self.destroy).pack(pady=(12, 0))

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Locate Key File",
            filetypes=[("Key files", "*.key *.bin *"), ("All files", "*.*")],
        )
        if path:
            self.new_path = path
            self.destroy()

    def _gen_new(self):
        if messagebox.askyesno(
            "Confirm",
            "Generate a new key file?\n\n"
            "Any backups made with the OLD key file will be permanently inaccessible "
            "unless you still have that file.\n\n"
            "Only proceed if you are starting fresh.",
            parent=self,
        ):
            self.regenerate = True
            self.destroy()


# ─── Regenerate key confirmation dialog ───────────────────────────────────────

def _confirm_regenerate(parent) -> bool:
    """Ask for explicit double-confirmation before regenerating the key file."""
    return messagebox.askyesno(
        "Regenerate Key File — WARNING",
        "This will create a brand-new key file.\n\n"
        "Any backups encrypted with the CURRENT key file will be permanently "
        "inaccessible unless you keep a copy of the old file.\n\n"
        "Are you absolutely sure?",
        parent=parent,
    )


# ─── Main window ──────────────────────────────────────────────────────────────

class App(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("Encrypted Backup")
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.minsize(840, 580)

        self.cfg = bk.load_config()
        self._session_pw: str | None = None   # in-memory password for the session
        self._prompt_new_key = False           # set True when a new key is just created

        self._check_key_file_on_start()
        self._build_layout()
        self.show_frame("sources")

        # If a brand-new key was just generated, ask the user to set their
        # password immediately so they understand the dual-factor model
        # from the start (like VeraCrypt prompting when creating a new volume).
        if self._prompt_new_key:
            self.after(400, self._setup_new_key_password)

    # ── Key file check at startup ───────────────────────────────────────────────

    def _check_key_file_on_start(self):
        key_path = Path(self.cfg.get("key_file", str(bk.DEFAULT_KEY_FILE)))

        if not key_path.exists():
            # Key file missing — offer to browse or generate
            dlg = LocateKeyDialog(self, str(key_path))
            self.wait_window(dlg)
            if dlg.new_path:
                bk.set_key_file(self.cfg, dlg.new_path)
                self.cfg = bk.load_config()
                key_path = Path(self.cfg.get("key_file", str(bk.DEFAULT_KEY_FILE)))
                # User found an existing key file — they already know its password
            elif dlg.regenerate:
                bk.regenerate_key(key_path)
                self._prompt_new_key = True   # ask for password once UI is ready
            else:
                return   # cancelled — will error on first operation

        # Detect v1 format key file (44-byte base64 Fernet key, no password support).
        # This happens when upgrading from the previous version of the app.
        if key_path.exists() and bk._is_old_format(key_path):
            messagebox.showwarning(
                "Key File Update Required",
                f"The key file at:\n{key_path}\n\n"
                "was created by an older version of this app that stored the "
                "encryption key directly in the file (no password protection).\n\n"
                "It will be automatically replaced with a new secure key file "
                "that requires BOTH a password AND the key file to decrypt data.\n\n"
                "Any previous backups will need to be re-created with the new key.",
            )
            bk.regenerate_key(key_path)
            self._prompt_new_key = True   # ask for password once UI is ready

    def _setup_new_key_password(self):
        """
        Called immediately after a brand-new key file is generated.
        Walks the user through setting their password so they understand
        the dual-factor model from the start.
        """
        key_path = self.cfg.get("key_file", str(bk.DEFAULT_KEY_FILE))
        messagebox.showinfo(
            "Set Up Your Password",
            f"Key file created at:\n{key_path}\n\n"
            "Now choose a strong password.  You will need BOTH this password "
            "AND the key file to access your backups — neither alone is "
            "sufficient (like VeraCrypt).\n\n"
            "Tip: keep a backup copy of the key file somewhere separate from "
            "your backups (e.g. a USB drive).",
        )
        self._unlock_session()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_layout(self):
        # Sidebar
        self.sidebar = ctk.CTkFrame(self, width=SIDEBAR_W, corner_radius=0,
                                    fg_color=("gray92", "gray11"))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        ctk.CTkLabel(self.sidebar, text="🔒  EncBackup",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(26, 2))
        ctk.CTkLabel(self.sidebar, text=f"v{bk.VERSION}",
                     font=ctk.CTkFont(size=11), text_color=COL_MUTED).pack(pady=(0, 20))

        # Nav buttons
        self._nav_btns: dict[str, ctk.CTkButton] = {}
        for key, icon, label in [
            ("sources",  "📁", "Sources"),
            ("backup",   "🔄", "Backup"),
            ("restore",  "📤", "Restore"),
            ("settings", "⚙",  "Settings"),
        ]:
            btn = ctk.CTkButton(
                self.sidebar, text=f"  {icon}  {label}",
                anchor="w", fg_color="transparent",
                hover_color=("gray82", "gray25"),
                command=lambda k=key: self.show_frame(k),
                height=42, corner_radius=8,
                font=ctk.CTkFont(size=13),
            )
            btn.pack(fill="x", padx=10, pady=3)
            self._nav_btns[key] = btn

        _divider(self.sidebar)

        # ── Session section ───────────────────────────────────────────────────
        ctk.CTkLabel(self.sidebar, text="SESSION",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=COL_MUTED).pack(anchor="w", padx=14)

        self._lock_status_var = tk.StringVar()
        self._lock_status_lbl = ctk.CTkLabel(
            self.sidebar, textvariable=self._lock_status_var,
            font=ctk.CTkFont(size=12), anchor="w",
        )
        self._lock_status_lbl.pack(fill="x", padx=14, pady=(4, 2))

        self._lock_btn = ctk.CTkButton(
            self.sidebar, text="", width=SIDEBAR_W - 20,
            height=34, corner_radius=8,
            command=self._toggle_lock,
        )
        self._lock_btn.pack(padx=10, pady=4)

        self._update_lock_ui()

        # ── Key file indicator (bottom of sidebar, read-only) ────────────────
        key_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        key_frame.pack(side="bottom", fill="x", padx=14, pady=14)

        ctk.CTkLabel(key_frame, text="🔑  Key File",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=COL_MUTED).pack(anchor="w")

        self._key_name_var = tk.StringVar()
        self._update_key_label()
        ctk.CTkLabel(key_frame, textvariable=self._key_name_var,
                     font=ctk.CTkFont(size=10), text_color="gray50",
                     wraplength=SIDEBAR_W - 28, anchor="w").pack(anchor="w")
        ctk.CTkLabel(key_frame, text="Change in Settings  →",
                     font=ctk.CTkFont(size=9), text_color="gray40",
                     cursor="hand2").pack(anchor="w", pady=(2, 0))

        # Content area
        self.content = ctk.CTkFrame(self, fg_color=("gray93", "gray14"),
                                    corner_radius=0)
        self.content.pack(side="right", fill="both", expand=True)

        self._frames: dict[str, ctk.CTkFrame] = {
            "sources":  SourcesFrame(self.content, self),
            "backup":   BackupFrame(self.content, self),
            "restore":  RestoreFrame(self.content, self),
            "settings": SettingsFrame(self.content, self),
        }

    # ── Navigation ────────────────────────────────────────────────────────────

    def show_frame(self, key: str):
        for f in self._frames.values():
            f.pack_forget()
        self._frames[key].pack(fill="both", expand=True, padx=26, pady=22)
        self._frames[key].refresh()
        for k, btn in self._nav_btns.items():
            btn.configure(
                fg_color=(COL_ACCENT if k == key else "transparent"),
                text_color=("white" if k == key else ("gray20", "white")),
            )

    def reload_config(self):
        self.cfg = bk.load_config()

    # ── Session / lock management ─────────────────────────────────────────────

    def _update_lock_ui(self):
        if self._session_pw:
            self._lock_status_var.set("🔓  Session unlocked")
            self._lock_status_lbl.configure(text_color=COL_GREEN)
            self._lock_btn.configure(text="🔒  Lock Session",
                                     fg_color=("gray65", "gray35"),
                                     hover_color=("gray55", "#7f1d1d"))
        else:
            self._lock_status_var.set("🔒  Session locked")
            self._lock_status_lbl.configure(text_color="#f87171")
            self._lock_btn.configure(text="🔓  Unlock Session",
                                     fg_color=COL_ACCENT, hover_color=COL_ACCENT_H)

    def _toggle_lock(self):
        if self._session_pw:
            self._lock_session()
        else:
            self._unlock_session()

    def _lock_session(self):
        self._session_pw = None
        self._update_lock_ui()
        # Refresh current frame to hide manifest-dependent data
        for key, frame in self._frames.items():
            if frame.winfo_ismapped():
                frame.refresh()

    def _unlock_session(self) -> bool:
        """Prompt for password. Returns True if the session is now unlocked."""
        key_path = Path(self.cfg.get("key_file", str(bk.DEFAULT_KEY_FILE)))
        if not self._ensure_key_file(key_path):
            return False

        # First backup? → show confirm-password dialog
        first_time = not bk.has_existing_backup(self.cfg)
        dlg = PasswordDialog(
            self,
            confirm=first_time,
            title="Create Password" if first_time else "Enter Password",
        )
        self.wait_window(dlg)
        if not dlg.password:
            return False

        # Verify against existing manifest (if one exists)
        if not first_time:
            if not bk.verify_password(self.cfg, dlg.password):
                self._wrong_password_recovery()
                return False

        self._session_pw = dlg.password
        self._update_lock_ui()
        # Refresh the current frame so it picks up the password
        for frame in self._frames.values():
            if frame.winfo_ismapped():
                frame.refresh()
        return True

    def _wrong_password_recovery(self):
        """
        Called when verify_password fails.  Offers a recovery path so the user
        can start fresh if the manifest is from an older version of the app
        (v1 manifests are incompatible with the new PBKDF2-based key derivation).
        """
        manifest_path = bk._manifest_path(self.cfg)
        has_old_manifest = manifest_path and manifest_path.exists()

        if has_old_manifest:
            reset = messagebox.askyesno(
                "Cannot Decrypt Backup — Reset?",
                "The encrypted backup manifest could not be decrypted.\n\n"
                "Possible causes:\n"
                "  • Wrong password entered\n"
                "  • Wrong key file is configured\n"
                "  • Manifest was created by an older version of this app\n"
                "    (v1 backups are not compatible with the new password model)\n\n"
                "Would you like to reset the backup state?\n\n"
                "The old manifest will be deleted and you can set a new password.\n"
                "Encrypted files already at the destination are NOT deleted,\n"
                "but they will be re-encrypted on the next backup.",
                icon="warning",
            )
            if reset:
                manifest_path.unlink(missing_ok=True)
                messagebox.showinfo(
                    "Reset Complete",
                    "The old manifest has been cleared.\n\n"
                    "Click 'Unlock Session' again to set your new password "
                    "and run a fresh backup.",
                )
        else:
            messagebox.showerror(
                "Wrong Password or Key File",
                "The backup manifest could not be decrypted.\n"
                "Please check your password and key file.",
            )

    def require_unlock(self) -> bool:
        """Ensure the session is unlocked. Returns True if we have a password."""
        if self._session_pw:
            return True
        return self._unlock_session()

    # ── Key file helpers ──────────────────────────────────────────────────────

    def _update_key_label(self):
        key_path = self.cfg.get("key_file", str(bk.DEFAULT_KEY_FILE))
        name = Path(key_path).name
        self._key_name_var.set(name)

    def _ensure_key_file(self, key_path: Path) -> bool:
        """If the key file is missing, prompt the user to locate or regenerate it."""
        if key_path.exists():
            return True
        dlg = LocateKeyDialog(self, str(key_path))
        self.wait_window(dlg)
        if dlg.new_path:
            bk.set_key_file(self.cfg, dlg.new_path)
            self.reload_config()
            self._update_key_label()
            return True
        if dlg.regenerate:
            bk.regenerate_key(key_path)
            self._lock_session()   # old session password no longer valid
            return True
        return False   # user cancelled

    def _locate_key(self):
        path = filedialog.askopenfilename(
            title="Locate Key File",
            filetypes=[("All files", "*.*")],
        )
        if path:
            bk.set_key_file(self.cfg, path)
            self.reload_config()
            self._update_key_label()
            self._lock_session()   # password was for the old key file

    def _regenerate_key(self):
        if not _confirm_regenerate(self):
            return
        key_path = Path(self.cfg.get("key_file", str(bk.DEFAULT_KEY_FILE)))
        bk.regenerate_key(key_path)
        self._lock_session()   # must re-unlock with new key + new password
        messagebox.showinfo(
            "Key Regenerated",
            f"A new key file has been written to:\n{key_path}\n\n"
            "Any previous backups are no longer accessible with this key.\n"
            "Please re-run your backups.",
        )


# ─── Sources & Destination ────────────────────────────────────────────────────

class SourcesFrame(ctk.CTkFrame):
    def __init__(self, parent, app: App):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._selected: str | None = None
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="Sources & Destination",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(
            self,
            text="Configure which folders to back up and where to store encrypted copies.",
            text_color=COL_MUTED, font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(2, 0))

        _section(self, "SOURCE FOLDERS")

        hdr = ctk.CTkFrame(self, fg_color=COL_HDR_BG, height=30, corner_radius=6)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        for txt, w in [("Label", 130), ("Path", 0)]:
            ctk.CTkLabel(hdr, text=txt, width=w, anchor="w",
                         font=ctk.CTkFont(size=11, weight="bold")).pack(side="left", padx=10)

        self.list_frame = ctk.CTkScrollableFrame(self, height=200)
        self.list_frame.pack(fill="x", pady=(2, 0))

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(anchor="w", pady=8)
        ctk.CTkButton(btn_row, text="＋  Add Source", width=140,
                      fg_color=COL_ACCENT, hover_color=COL_ACCENT_H,
                      command=self._add_source).pack(side="left", padx=(0, 10))
        ctk.CTkButton(btn_row, text="✕  Remove", width=110,
                      fg_color=("gray70", "gray35"), hover_color=COL_DANGER,
                      command=self._remove_source).pack(side="left")

        _divider(self)
        _section(self, "BACKUP DESTINATION")

        dest_row = ctk.CTkFrame(self, fg_color="transparent")
        dest_row.pack(fill="x", pady=(4, 0))
        self.dest_var = tk.StringVar()
        ctk.CTkEntry(dest_row, textvariable=self.dest_var, state="disabled",
                     placeholder_text="Click Browse to choose a folder…",
                     width=480).pack(side="left", padx=(0, 10))
        ctk.CTkButton(dest_row, text="Browse…", width=110,
                      command=self._browse_dest).pack(side="left")

        ctk.CTkLabel(
            self,
            text="⚠️  Tip: choose a cloud-synced folder (OneDrive, Google Drive…) "
                 "for off-site storage.",
            text_color=COL_MUTED, font=ctk.CTkFont(size=11),
            wraplength=660, justify="left",
        ).pack(anchor="w", pady=(8, 0))

    def refresh(self):
        self.app.reload_config()
        self._selected = None
        for w in self.list_frame.winfo_children():
            w.destroy()

        sources = self.app.cfg.get("sources", [])
        if not sources:
            ctk.CTkLabel(
                self.list_frame,
                text="No source folders yet — click Add Source.",
                text_color=COL_MUTED, font=ctk.CTkFont(size=12),
            ).pack(pady=20)
        else:
            for s in sources:
                self._add_row(s["label"], s["path"])

        self.dest_var.set(self.app.cfg.get("destination", ""))

    def _add_row(self, label: str, path: str):
        row = ctk.CTkFrame(self.list_frame, fg_color=COL_ROW_BG,
                           corner_radius=6, height=36)
        row.pack(fill="x", pady=2, padx=1)
        row.pack_propagate(False)
        lbl_w  = ctk.CTkLabel(row, text=label, width=130, anchor="w",
                               font=ctk.CTkFont(size=12, weight="bold"))
        lbl_w.pack(side="left", padx=10)
        path_w = ctk.CTkLabel(row, text=path, anchor="w",
                               font=ctk.CTkFont(size=11), text_color=COL_MUTED)
        path_w.pack(side="left", padx=4)

        def select(_e, lbl=label, r=row):
            self._selected = lbl
            for c in self.list_frame.winfo_children():
                c.configure(fg_color=COL_ROW_BG)
            r.configure(fg_color=(COL_ACCENT, "#1e3a8a"))

        for w in (row, lbl_w, path_w):
            w.bind("<Button-1>", select)

    def _add_source(self):
        dlg = AddSourceDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            try:
                bk.add_source(self.app.cfg, *dlg.result)
                self.refresh()
            except ValueError as e:
                messagebox.showerror("Error", str(e), parent=self)

    def _remove_source(self):
        if not self._selected:
            messagebox.showwarning("No selection",
                                   "Click a row to select it, then Remove.",
                                   parent=self)
            return
        if messagebox.askyesno(
            "Remove",
            f"Remove '{self._selected}' from the list?\n"
            "Already-encrypted files are NOT deleted.",
            parent=self,
        ):
            bk.remove_source(self.app.cfg, self._selected)
            self.refresh()

    def _browse_dest(self):
        # Start the picker at the currently configured destination (if it exists)
        current = self.app.cfg.get("destination", "")
        initial = current if current and Path(current).exists() else None
        path = filedialog.askdirectory(
            title="Select Backup Destination",
            initialdir=initial,
        )
        if path:
            bk.set_destination(self.app.cfg, path)
            self.dest_var.set(path)


# ─── Add Source Dialog ────────────────────────────────────────────────────────

class AddSourceDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Add Source Folder")
        self.geometry("500x230")
        self.resizable(False, False)
        self.result: tuple[str, str] | None = None
        self._build()
        self.grab_set()
        self.focus()

    def _build(self):
        ctk.CTkLabel(self, text="Add Source Folder",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 6))
        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", padx=28, pady=4)
        form.columnconfigure(1, weight=1)

        self.label_var = tk.StringVar()
        self.path_var  = tk.StringVar()

        for row_i, (txt, var, ph) in enumerate([
            ("Label:",  self.label_var, "Short name, e.g. Documents"),
            ("Folder:", self.path_var,  "C:\\Users\\…"),
        ]):
            ctk.CTkLabel(form, text=txt, width=65, anchor="w").grid(
                row=row_i, column=0, sticky="w", pady=6)
            ctk.CTkEntry(form, textvariable=var, placeholder_text=ph).grid(
                row=row_i, column=1, sticky="ew", padx=(8, 8), pady=6)
            if txt == "Folder:":
                ctk.CTkButton(form, text="Browse", width=72,
                              command=self._browse).grid(row=row_i, column=2)

        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=14)
        ctk.CTkButton(btn_row, text="Add", width=110,
                      fg_color=COL_ACCENT, hover_color=COL_ACCENT_H,
                      command=self._ok).pack(side="left", padx=8)
        ctk.CTkButton(btn_row, text="Cancel", width=100,
                      fg_color=("gray65", "gray35"),
                      command=self.destroy).pack(side="left")

    def _browse(self):
        path = filedialog.askdirectory(title="Select Source Folder")
        if path:
            self.path_var.set(path)
            if not self.label_var.get():
                self.label_var.set(Path(path).name)

    def _ok(self):
        label, path = self.label_var.get().strip(), self.path_var.get().strip()
        if not label:
            messagebox.showwarning("Missing label", "Please enter a label.", parent=self)
            return
        if not path or not Path(path).exists():
            messagebox.showwarning("Invalid folder",
                                   "Please select an existing folder.", parent=self)
            return
        self.result = (label, path)
        self.destroy()


# ─── Backup ───────────────────────────────────────────────────────────────────

class BackupFrame(ctk.CTkFrame):
    def __init__(self, parent, app: App):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._running = False
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="Backup",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(self,
                     text="Encrypt and back up your source folders. "
                          "Requires password + key file.",
                     text_color=COL_MUTED,
                     font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(2, 0))

        _section(self, "SOURCE STATUS")

        # Status table header
        cols = [("Label", 115), ("Path", 210), ("Files", 55),
                ("Backed Up", 90), ("Last Backup", 160)]
        hdr = ctk.CTkFrame(self, fg_color=COL_HDR_BG, height=28, corner_radius=6)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        for txt, w in cols:
            ctk.CTkLabel(hdr, text=txt, width=w, anchor="w",
                         font=ctk.CTkFont(size=11, weight="bold")).pack(
                side="left", padx=7)

        self.status_frame = ctk.CTkScrollableFrame(self, height=130)
        self.status_frame.pack(fill="x", pady=(2, 0))

        # Options
        opt = ctk.CTkFrame(self, fg_color="transparent")
        opt.pack(anchor="w", pady=8)
        self.dry_run_var = tk.BooleanVar()
        self.force_var   = tk.BooleanVar()
        ctk.CTkCheckBox(opt, text="Dry run (preview only, nothing written)",
                        variable=self.dry_run_var).pack(side="left", padx=(0, 18))
        ctk.CTkCheckBox(opt, text="Force (re-encrypt unchanged files)",
                        variable=self.force_var).pack(side="left")

        # Source selector
        sel = ctk.CTkFrame(self, fg_color="transparent")
        sel.pack(anchor="w", pady=(0, 10))
        ctk.CTkLabel(sel, text="Back up:").pack(side="left", padx=(0, 8))
        self.source_var  = tk.StringVar(value="All sources")
        self.source_menu = ctk.CTkOptionMenu(sel, variable=self.source_var,
                                              values=["All sources"], width=220)
        self.source_menu.pack(side="left")

        # Run button
        self.run_btn = ctk.CTkButton(
            self, text="▶  Run Backup", height=44,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=COL_ACCENT, hover_color=COL_ACCENT_H,
            command=self._run,
        )
        self.run_btn.pack(fill="x", pady=(0, 6))
        self.progress_bar = ctk.CTkProgressBar(self, mode="indeterminate")

        _section(self, "LOG")
        self.log_box = ctk.CTkTextbox(self, height=160, state="disabled",
                                       font=ctk.CTkFont(family="Consolas", size=11))
        self.log_box.pack(fill="both", expand=True, pady=(2, 0))

    def refresh(self):
        self.app.reload_config()
        for w in self.status_frame.winfo_children():
            w.destroy()

        status = bk.get_status(self.app.cfg, password=self.app._session_pw)
        if not status:
            ctk.CTkLabel(self.status_frame,
                         text="No sources configured. Add them on the Sources tab.",
                         text_color=COL_MUTED).pack(pady=14)
        else:
            for s in status:
                row = ctk.CTkFrame(self.status_frame, fg_color=COL_ROW_BG,
                                   corner_radius=4, height=30)
                row.pack(fill="x", pady=1, padx=1)
                row.pack_propagate(False)
                clr = ("gray20", "white") if s["exists"] else COL_DANGER
                lb  = s["last_backup"]
                lb_str = lb[:19] if lb else ("unlock session to see" if not self.app._session_pw else "—")
                bu_str = str(s["backed_up_count"]) if s["backed_up_count"] is not None \
                         else ("—" if self.app._session_pw else "🔒")
                for txt, w in [
                    (s["label"], 115), (s["path"], 210),
                    (str(s["file_count"]), 55), (bu_str, 90), (lb_str, 160),
                ]:
                    ctk.CTkLabel(row, text=txt, width=w, anchor="w",
                                 font=ctk.CTkFont(size=11),
                                 text_color=clr).pack(side="left", padx=7)

        labels = ["All sources"] + [s["label"] for s in self.app.cfg.get("sources", [])]
        self.source_menu.configure(values=labels)
        if self.source_var.get() not in labels:
            self.source_var.set("All sources")

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _run(self):
        if self._running:
            return
        cfg = self.app.cfg
        if not cfg.get("destination"):
            messagebox.showwarning("No destination",
                                   "Set a backup destination on the Sources tab.",
                                   parent=self)
            return
        if not cfg.get("sources"):
            messagebox.showwarning("No sources",
                                   "Add at least one source on the Sources tab.",
                                   parent=self)
            return
        if not self.app.require_unlock():
            return   # user cancelled password dialog

        selected = self.source_var.get()
        pw = self.app._session_pw

        self._running = True
        self.run_btn.configure(state="disabled", text="Running…")
        self.progress_bar.pack(fill="x", pady=(0, 6))
        self.progress_bar.start()
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

        def worker():
            try:
                bk.backup(
                    cfg,
                    password=pw,
                    source_label=None if selected == "All sources" else selected,
                    dry_run=self.dry_run_var.get(),
                    force=self.force_var.get(),
                    progress=lambda m: self.after(0, self._log, m),
                )
            except Exception as exc:
                self.after(0, self._log, f"[ERROR] {exc}")
            finally:
                self.after(0, self._finish)

        threading.Thread(target=worker, daemon=True).start()

    def _finish(self):
        self._running = False
        self.run_btn.configure(state="normal", text="▶  Run Backup")
        self.progress_bar.stop()
        self.progress_bar.pack_forget()
        self.refresh()


# ─── Restore ──────────────────────────────────────────────────────────────────

class RestoreFrame(ctk.CTkFrame):
    def __init__(self, parent, app: App):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._build()

    def _build(self):
        ctk.CTkLabel(self, text="Restore",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(self,
                     text="Decrypt backed-up files. Requires password + key file.",
                     text_color=COL_MUTED, font=ctk.CTkFont(size=12)).pack(
            anchor="w", pady=(2, 12))

        tab = ctk.CTkTabview(self, height=230)
        tab.pack(fill="x")
        tab.add("  Single File  ")
        tab.add("  Entire Source  ")

        # Single File
        sf = tab.tab("  Single File  ")
        self._enc_var     = self._field_row(sf, "Encrypted file (.enc):")
        self._fout_var    = self._field_row(sf, "Output folder:", is_dir=True)
        ctk.CTkButton(sf, text="🔓  Decrypt File", width=160,
                      fg_color=COL_ACCENT, hover_color=COL_ACCENT_H,
                      command=self._restore_file).pack(anchor="w", pady=(10, 4))

        # Entire Source
        es = tab.tab("  Entire Source  ")
        sel_row = ctk.CTkFrame(es, fg_color="transparent")
        sel_row.pack(anchor="w", pady=8)
        ctk.CTkLabel(sel_row, text="Source label:", width=155, anchor="w").pack(side="left")
        self._src_var = tk.StringVar()
        self.src_menu = ctk.CTkOptionMenu(sel_row, variable=self._src_var,
                                           values=["—"], width=220)
        self.src_menu.pack(side="left", padx=8)
        self._sout_var = self._field_row(es, "Output folder:", is_dir=True)
        ctk.CTkButton(es, text="🔓  Decrypt All Files", width=180,
                      fg_color=COL_ACCENT, hover_color=COL_ACCENT_H,
                      command=self._restore_source).pack(anchor="w", pady=(10, 4))

        _section(self, "LOG")
        self.log_box = ctk.CTkTextbox(self, height=200, state="disabled",
                                       font=ctk.CTkFont(family="Consolas", size=11))
        self.log_box.pack(fill="both", expand=True, pady=(2, 0))

    def _field_row(self, parent, label: str, is_dir: bool = False) -> tk.StringVar:
        var = tk.StringVar()
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(anchor="w", pady=5)
        ctk.CTkLabel(row, text=label, width=185, anchor="w").pack(side="left")
        ctk.CTkEntry(row, textvariable=var, width=320).pack(side="left", padx=(0, 8))
        if is_dir:
            ctk.CTkButton(row, text="Browse", width=80,
                          command=lambda v=var: self._browse_dir(v)).pack(side="left")
        else:
            ctk.CTkButton(row, text="Browse", width=80,
                          command=self._browse_enc).pack(side="left")
        return var

    def refresh(self):
        self.app.reload_config()
        labels = [s["label"] for s in self.app.cfg.get("sources", [])]
        values = labels or ["—"]
        self.src_menu.configure(values=values)
        if labels and self._src_var.get() not in labels:
            self._src_var.set(labels[0])

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _browse_enc(self):
        path = filedialog.askopenfilename(
            title="Select encrypted file",
            filetypes=[("Encrypted files", "*.enc"), ("All files", "*.*")],
        )
        if path:
            self._enc_var.set(path)

    def _browse_dir(self, var: tk.StringVar):
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            var.set(path)

    def _restore_file(self):
        enc = self._enc_var.get().strip()
        out = self._fout_var.get().strip()
        if not enc or not out:
            messagebox.showwarning("Missing fields",
                                   "Select both an encrypted file and an output folder.",
                                   parent=self)
            return
        if not self.app.require_unlock():
            return
        self._clear_log()
        try:
            restored = bk.restore_file(
                Path(enc), Path(out), self.app.cfg, self.app._session_pw)
            self._log(f"[ok]  Restored → {restored}")
            messagebox.showinfo("Restored", f"File restored to:\n{restored}", parent=self)
        except Exception as exc:
            self._log(f"[ERROR]  {exc}")
            messagebox.showerror("Restore failed", str(exc), parent=self)

    def _restore_source(self):
        label = self._src_var.get()
        out   = self._sout_var.get().strip()
        if not label or label == "—":
            messagebox.showwarning("No source", "Select a source label.", parent=self)
            return
        if not out:
            messagebox.showwarning("No output", "Select an output folder.", parent=self)
            return
        if not self.app.require_unlock():
            return
        self._clear_log()
        pw = self.app._session_pw

        def worker():
            try:
                bk.restore_source(
                    self.app.cfg, label, Path(out), password=pw,
                    progress=lambda m: self.after(0, self._log, m),
                )
                self.after(0, lambda: messagebox.showinfo(
                    "Restored", f"'{label}' restored to:\n{out}", parent=self))
            except Exception as exc:
                self.after(0, self._log, f"[ERROR]  {exc}")

        threading.Thread(target=worker, daemon=True).start()


# ─── Settings ────────────────────────────────────────────────────────────────────

class SettingsFrame(ctk.CTkFrame):
    """Settings tab — scrollable; contains Key File, Automated Backups, Security."""

    def __init__(self, parent, app: App):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self._build()

    def _build(self):
        # Fixed title (outside the scroll area)
        ctk.CTkLabel(self, text="Settings",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(self,
                     text="Configure key file, automated backups, and security details.",
                     text_color=COL_MUTED, font=ctk.CTkFont(size=12)).pack(
            anchor="w", pady=(2, 8))

        # Scrollable content
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill="both", expand=True)
        p = scroll   # alias — all content goes into p

        # ── KEY FILE ──────────────────────────────────────────────────────────
        _section(p, "KEY FILE LOCATION")
        ctk.CTkLabel(
            p,
            text="The key file holds a 32-byte random salt. Your password + this salt "
                 "are fed through PBKDF2 to produce the encryption key. Neither alone "
                 "is sufficient. Store it separately from your backups (e.g. USB drive).",
            text_color=COL_MUTED, font=ctk.CTkFont(size=11),
            wraplength=660, justify="left",
        ).pack(anchor="w", pady=(0, 8))

        path_card = ctk.CTkFrame(p, fg_color=("gray87", "gray19"), corner_radius=8)
        path_card.pack(fill="x", pady=(0, 6))
        for lbl_txt, val_attr in [("Default:", str(bk.DEFAULT_KEY_FILE)), ("Current:", None)]:
            r = ctk.CTkFrame(path_card, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=4)
            ctk.CTkLabel(r, text=lbl_txt, width=80, anchor="w",
                         font=ctk.CTkFont(size=11, weight="bold")).pack(side="left")
            if val_attr:
                ctk.CTkLabel(r, text=val_attr, font=ctk.CTkFont(size=11),
                             text_color="gray50", anchor="w").pack(side="left", padx=4)
            else:
                self._key_path_var = tk.StringVar()
                self._key_path_lbl = ctk.CTkLabel(
                    r, textvariable=self._key_path_var,
                    font=ctk.CTkFont(size=11), anchor="w")
                self._key_path_lbl.pack(side="left", padx=4)

        kb = ctk.CTkFrame(p, fg_color="transparent")
        kb.pack(anchor="w", pady=(0, 4))
        ctk.CTkButton(kb, text="📂  Browse / Change", width=180, height=34,
                      fg_color=COL_ACCENT, hover_color=COL_ACCENT_H,
                      command=self._browse_key).pack(side="left", padx=(0, 8))
        ctk.CTkButton(kb, text="🗂  Open Folder", width=150, height=34,
                      fg_color=("gray65", "gray35"),
                      command=self._open_folder).pack(side="left", padx=(0, 8))
        ctk.CTkButton(kb, text="⚠  Regenerate", width=140, height=34,
                      fg_color=("gray65", "gray35"), hover_color=COL_DANGER,
                      command=self._regenerate).pack(side="left")
        ctk.CTkLabel(p,
                     text="⚠️  Changing or regenerating locks your session and makes "
                          "existing backups unreadable without the old file.",
                     text_color="#fbbf24", font=ctk.CTkFont(size=11),
                     wraplength=660, justify="left").pack(anchor="w", pady=(4, 0))

        _divider(p)

        # ── AUTOMATED BACKUPS ─────────────────────────────────────────────────
        _section(p, "AUTOMATED BACKUPS")
        ctk.CTkLabel(
            p,
            text="Windows Task Scheduler runs the backup unattended. The password is "
                 "stored in Windows Credential Manager (encrypted, tied to your user "
                 "account) — similar to how browsers store saved passwords.",
            text_color=COL_MUTED, font=ctk.CTkFont(size=11),
            wraplength=660, justify="left",
        ).pack(anchor="w", pady=(0, 8))

        # Status badge
        status_row = ctk.CTkFrame(p, fg_color="transparent")
        status_row.pack(anchor="w", pady=(0, 8))
        ctk.CTkLabel(status_row, text="Status:", width=70, anchor="w",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(side="left")
        self._sched_status_var = tk.StringVar(value="Checking…")
        self._sched_status_lbl = ctk.CTkLabel(
            status_row, textvariable=self._sched_status_var,
            font=ctk.CTkFont(size=11))
        self._sched_status_lbl.pack(side="left", padx=4)

        # Schedule config card
        sched_card = ctk.CTkFrame(p, fg_color=("gray87", "gray19"), corner_radius=8)
        sched_card.pack(fill="x", pady=(0, 8))
        sched_inner = ctk.CTkFrame(sched_card, fg_color="transparent")
        sched_inner.pack(fill="x", padx=14, pady=10)

        # Row 1: frequency + time
        row1 = ctk.CTkFrame(sched_inner, fg_color="transparent")
        row1.pack(anchor="w", pady=4)
        ctk.CTkLabel(row1, text="Frequency:", width=90, anchor="w",
                     font=ctk.CTkFont(size=11)).pack(side="left")
        self._sched_type_var = tk.StringVar(value="Daily")
        self._sched_type_menu = ctk.CTkOptionMenu(
            row1, variable=self._sched_type_var,
            values=["Daily", "On Login"],
            width=130,
            command=self._on_sched_type_changed,
        )
        self._sched_type_menu.pack(side="left", padx=(0, 16))

        self._time_lbl = ctk.CTkLabel(row1, text="Time (HH:MM):",
                                       font=ctk.CTkFont(size=11))
        self._time_lbl.pack(side="left")
        self._time_var = tk.StringVar(value="02:00")
        self._time_entry = ctk.CTkEntry(row1, textvariable=self._time_var,
                                         width=70, placeholder_text="HH:MM")
        self._time_entry.pack(side="left", padx=(6, 0))

        # Row 2: automation password
        row2 = ctk.CTkFrame(sched_inner, fg_color="transparent")
        row2.pack(anchor="w", pady=4)
        ctk.CTkLabel(row2, text="Password:", width=90, anchor="w",
                     font=ctk.CTkFont(size=11)).pack(side="left")
        self._auto_pw_var = tk.StringVar()
        self._auto_pw_entry = ctk.CTkEntry(
            row2, textvariable=self._auto_pw_var,
            show="●", width=220,
            placeholder_text="Enter backup password for automation")
        self._auto_pw_entry.pack(side="left", padx=(0, 8))
        self._auto_pw_show = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(row2, text="Show", variable=self._auto_pw_show, width=56,
                        command=lambda: self._auto_pw_entry.configure(
                            show="" if self._auto_pw_show.get() else "●")
                        ).pack(side="left")

        ctk.CTkLabel(sched_inner,
                     text="🔒  Stored in Windows Credential Manager — never written to disk in plaintext.",
                     text_color=COL_MUTED, font=ctk.CTkFont(size=10)).pack(
            anchor="w", pady=(2, 0))

        # Action buttons
        ab = ctk.CTkFrame(p, fg_color="transparent")
        ab.pack(anchor="w", pady=(0, 4))
        ctk.CTkButton(ab, text="✓  Set Up Schedule", width=170, height=36,
                      fg_color=COL_GREEN, hover_color="#15803d",
                      command=self._setup_schedule).pack(side="left", padx=(0, 8))
        ctk.CTkButton(ab, text="✕  Remove Schedule", width=160, height=36,
                      fg_color=("gray65", "gray35"), hover_color=COL_DANGER,
                      command=self._remove_schedule).pack(side="left", padx=(0, 8))
        ctk.CTkButton(ab, text="📋  View Log", width=120, height=36,
                      fg_color=("gray65", "gray35"),
                      command=self._view_log).pack(side="left")

        _divider(p)

        # ── SECURITY ──────────────────────────────────────────────────────────
        _section(p, "SECURITY")
        info_card = ctk.CTkFrame(p, fg_color=("gray87", "gray19"), corner_radius=8)
        info_card.pack(fill="x")
        for lbl_txt, val_txt in [
            ("Key derivation:",    f"PBKDF2-HMAC-SHA256  —  {bk.KDF_ITERATIONS:,} iterations"),
            ("Encryption:",        "Fernet  (AES-128-CBC + HMAC-SHA256)"),
            ("Key file content:",  "32-byte random salt  (NOT the encryption key)"),
            ("Authentication:",    "Password  +  key file  —  both always required"),
            ("Auto-backup store:", "Windows Credential Manager  (user-account-encrypted)"),
        ]:
            r = ctk.CTkFrame(info_card, fg_color="transparent")
            r.pack(fill="x", padx=14, pady=5)
            ctk.CTkLabel(r, text=lbl_txt, width=160, anchor="w",
                         font=ctk.CTkFont(size=11, weight="bold")).pack(side="left")
            ctk.CTkLabel(r, text=val_txt, anchor="w",
                         font=ctk.CTkFont(size=11),
                         text_color="gray70").pack(side="left", padx=4)

        _divider(p)
        _section(p, "ABOUT")
        ctk.CTkLabel(p,
                     text=f"Encrypted Backup  v{bk.VERSION}   —   "
                          "Security model inspired by VeraCrypt",
                     text_color=COL_MUTED, font=ctk.CTkFont(size=11)).pack(anchor="w")

    # ── Refresh ───────────────────────────────────────────────────────────────

    def refresh(self):
        self.app.reload_config()

        # Key file path + colour
        key_path = self.app.cfg.get("key_file", str(bk.DEFAULT_KEY_FILE))
        self._key_path_var.set(key_path)
        self._key_path_lbl.configure(
            text_color=COL_GREEN if Path(key_path).exists() else COL_DANGER)

        # Schedule status
        info = bk.get_schedule_info()
        has_pw = bk.keyring_has_password()
        if info:
            self._sched_status_var.set(f"✓  Active — {info}")
            self._sched_status_lbl.configure(text_color=COL_GREEN)
        else:
            self._sched_status_var.set("✕  Not configured")
            self._sched_status_lbl.configure(text_color=COL_MUTED)

        # Pre-fill time and frequency from existing task if possible
        self._on_sched_type_changed(self._sched_type_var.get())

    def _on_sched_type_changed(self, value: str):
        show = value == "Daily"
        state = "normal" if show else "disabled"
        self._time_lbl.configure(text_color="white" if show else "gray50")
        self._time_entry.configure(state=state)

    # ── Key file actions ──────────────────────────────────────────────────────

    def _browse_key(self):
        path = filedialog.askopenfilename(
            title="Select Key File", filetypes=[("All files", "*.*")],
            initialdir=str(Path(self.app.cfg.get(
                "key_file", str(bk.DEFAULT_KEY_FILE))).parent),
        )
        if not path:
            return
        bk.set_key_file(self.app.cfg, path)
        self.app.reload_config()
        self.app._update_key_label()
        self.app._lock_session()
        self.refresh()
        messagebox.showinfo("Key File Updated",
                            f"Key file set to:\n{path}\n\nSession locked.",
                            parent=self)

    def _open_folder(self):
        import subprocess
        key_path = Path(self.app.cfg.get("key_file", str(bk.DEFAULT_KEY_FILE)))
        subprocess.Popen(["explorer", str(key_path.parent if key_path.parent.exists()
                                          else Path.home())])

    def _regenerate(self):
        if not _confirm_regenerate(self):
            return
        key_path = Path(self.app.cfg.get("key_file", str(bk.DEFAULT_KEY_FILE)))
        bk.regenerate_key(key_path)
        self.app._lock_session()
        self.refresh()
        messagebox.showinfo("Key Regenerated",
                            f"New key file written to:\n{key_path}\n\n"
                            "Existing backups are inaccessible with the new key.",
                            parent=self)

    # ── Schedule actions ──────────────────────────────────────────────────────

    def _setup_schedule(self):
        pw = self._auto_pw_var.get().strip()
        if not pw:
            messagebox.showwarning(
                "Password Required",
                "Enter the backup password so it can be stored in "
                "Windows Credential Manager for unattended runs.",
                parent=self)
            return

        # Validate the password against the manifest before storing
        if bk.has_existing_backup(self.app.cfg):
            if not bk.verify_password(self.app.cfg, pw):
                messagebox.showerror(
                    "Wrong Password",
                    "The password does not match your existing backup.\n"
                    "Please enter the correct password.",
                    parent=self)
                return

        # Validate time format
        sched_type = self._sched_type_var.get().lower().replace(" ", "")
        time_str = self._time_var.get().strip()
        if sched_type == "daily":
            import re
            if not re.match(r'^([01]\d|2[0-3]):[0-5]\d$', time_str):
                messagebox.showwarning(
                    "Invalid Time",
                    "Please enter a valid time in HH:MM format (e.g. 02:00).",
                    parent=self)
                return

        # Store password in Credential Manager
        try:
            bk.save_password_to_keyring(pw)
        except Exception as exc:
            messagebox.showerror("Credential Manager Error",
                                 f"Could not store password:\n{exc}",
                                 parent=self)
            return

        # Create the scheduled task
        key = "login" if sched_type == "onlogin" else "daily"
        ok, msg = bk.create_schedule(key, time_str)
        if ok:
            self.refresh()
            messagebox.showinfo(
                "Schedule Created",
                f"Automated backup configured:\n"
                f"  Frequency: {self._sched_type_var.get()}"
                + (f" at {time_str}" if key == "daily" else "") +
                "\n  Password: stored in Windows Credential Manager",
                parent=self)
        else:
            bk.clear_password_from_keyring()   # don't leave password if task failed
            messagebox.showerror("Task Scheduler Error", msg, parent=self)

    def _remove_schedule(self):
        ok, msg = bk.delete_schedule()
        bk.clear_password_from_keyring()
        self.refresh()
        if ok:
            messagebox.showinfo("Removed",
                                "Scheduled task removed and password cleared "
                                "from Credential Manager.",
                                parent=self)
        else:
            messagebox.showinfo("Not found", msg, parent=self)

    def _view_log(self):
        log_path = Path(__file__).parent / "backup_log.txt"
        if not log_path.exists():
            messagebox.showinfo("No log yet",
                                "No backup_log.txt found yet.\n"
                                "It will be created after the first automated run.",
                                parent=self)
            return
        import subprocess
        subprocess.Popen(["notepad", str(log_path)])


# ─── Entry point ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
