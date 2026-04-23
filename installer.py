#!/usr/bin/env python3
"""
POS System — Graphical Installation Wizard
==========================================
Leads a distributor through 3 guided steps:
  1. Provisioning  : Consume OTPK → generate .env → patch IMAGE_* tags
  2. Docker Login  : Authenticate with GHCR
  3. Deployment    : Review summary → docker compose up -d with live log

No Docker or Linux knowledge required from the distributor.
"""

import argparse
import base64
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

# ── Timezone list (IANA) ─────────────────────────────────────────────────────
try:
    from zoneinfo import available_timezones as _tz_available
    _TIMEZONES: list[str] = sorted(_tz_available())
except ImportError:  # Python < 3.9 or tzdata not installed
    _TIMEZONES = [
        "Africa/Cairo", "Africa/Johannesburg",
        "America/Chicago", "America/Denver", "America/Los_Angeles",
        "America/New_York", "America/Sao_Paulo", "America/Toronto",
        "Asia/Dubai", "Asia/Hong_Kong", "Asia/Kolkata", "Asia/Seoul",
        "Asia/Shanghai", "Asia/Singapore", "Asia/Tokyo",
        "Australia/Sydney",
        "Europe/Amsterdam", "Europe/Athens", "Europe/Berlin",
        "Europe/Brussels", "Europe/Budapest", "Europe/Copenhagen",
        "Europe/Dublin", "Europe/Helsinki", "Europe/Istanbul",
        "Europe/Kiev", "Europe/Lisbon", "Europe/London",
        "Europe/Madrid", "Europe/Moscow", "Europe/Oslo",
        "Europe/Paris", "Europe/Prague", "Europe/Rome",
        "Europe/Sofia", "Europe/Stockholm", "Europe/Vienna",
        "Europe/Warsaw", "Europe/Zurich",
        "Pacific/Auckland", "Pacific/Honolulu",
        "UTC",
    ]

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_DIR        = Path(__file__).parent.resolve()
ENV_EXAMPLE     = REPO_DIR / ".env.example"
ENV_FILE        = REPO_DIR / ".env"
PROVISION_PY    = REPO_DIR / "provision.py"
COMPOSE_FILE    = REPO_DIR / "docker-compose.prod.yml"
LOCALES_DIR     = REPO_DIR / "locales"
POS_AUTH_FILE   = Path.home() / ".docker" / "pos-auth.json"

# ── Colour palette ────────────────────────────────────────────────────────────
C_BRAND     = "#1a1a2e"
C_ACCENT    = "#4a6cf7"
C_SUCCESS   = "#28a745"
C_DANGER    = "#dc3545"
C_INFO      = "#0288d1"

# ── i18n ──────────────────────────────────────────────────────────────────────
# Translations are loaded from locales/<lang>.json at startup.
# Each JSON file is plain UTF-8 — edit without Python knowledge.
# Mirrors React i18next:
#   TRANSLATIONS  ≈ per-locale JSON files       (one dict per language)
#   _LANG         ≈ i18n.language               (currently active locale)
#   t(key)        ≈ the t() hook                (lookup with optional {param})
#   set_lang(lc)  ≈ i18n.changeLanguage()       (switch + UI rebuild)

_LANG: str = "de"


def _load_translations() -> dict[str, dict[str, str]]:
    """Read locales/*.json and return a merged TRANSLATIONS dict.

    Missing files are silently skipped; t() returns the key as fallback.
    """
    result: dict[str, dict[str, str]] = {}
    for lang in ("de", "en", "ru"):
        p = LOCALES_DIR / f"{lang}.json"
        if p.is_file():
            try:
                result[lang] = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                result[lang] = {}
        else:
            result[lang] = {}
    return result


TRANSLATIONS: dict[str, dict[str, str]] = _load_translations()


def t(key: str, **kwargs: str) -> str:
    """Look up *key* in the active locale, falling back to the key itself.
    Supports {param} placeholders via keyword arguments — same as React i18next
    interpolation: t("s3_log_url", port="8080")
    Unknown kwargs are silently ignored (no KeyError / IndexError).
    """
    text = TRANSLATIONS.get(_LANG, TRANSLATIONS.get("de", {})).get(key, key)
    if not kwargs:
        return text
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError):
        # Partial format: substitute only known placeholders
        import string
        known = {
            field_name
            for _, field_name, _, _ in string.Formatter().parse(text)
            if field_name
        }
        return text.format(**{k: v for k, v in kwargs.items() if k in known})


def set_lang(code: str) -> None:
    global _LANG
    _LANG = code
# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_pos_auth_json(user: str, token: str) -> None:
    """Write ~/.docker/pos-auth.json with base64 auth for ghcr.io.

    This credential-bridge file is mounted into the updater container
    as /root/.docker/config.json so it can pull images from GHCR
    without needing access to docker-credential-desktop.exe.
    """
    POS_AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    auth_b64 = base64.b64encode(f"{user}:{token}".encode()).decode()
    data = {"auths": {"ghcr.io": {"auth": auth_b64}}}
    POS_AUTH_FILE.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )
    POS_AUTH_FILE.chmod(0o600)


def _fetch_recent_tags(repo: str, n: int = 4) -> list[str]:
    """Return the n most recent release/tag names for a public GitHub repo.

    Tries the Releases API first (sorted by published date), then falls back
    to the Tags API. Returns an empty list on any error.
    """
    for endpoint in (
        f"https://api.github.com/repos/{repo}/releases?per_page={n}",
        f"https://api.github.com/repos/{repo}/tags?per_page={n}",
    ):
        try:
            req = urllib.request.Request(
                endpoint,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            names = [r.get("tag_name") or r.get("name", "") for r in data[:n]]
            names = [name for name in names if name]
            if names:
                return names
        except Exception:  # noqa: BLE001
            continue
    return []


def _has_ghcr_credentials() -> tuple[bool, str]:
    """Check whether GHCR credentials are already stored in a Docker config file.

    Inspects ~/.docker/pos-auth.json first, then ~/.docker/config.json.
    Returns (found, human-readable source path).
    Only plain ``auths`` entries are considered; credential-helper entries
    are not decoded (no plain-text token available in that case).
    """
    for path in (POS_AUTH_FILE, Path.home() / ".docker" / "config.json"):
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if "ghcr.io" in data.get("auths", {}):
                    return True, str(path)
            except (json.JSONDecodeError, OSError):
                pass
    return False, ""


def _read_env_keys(keys: list[str]) -> dict[str, str]:
    """Parse .env file and return a dict of requested key → value."""
    result: dict[str, str] = {}
    if not ENV_FILE.is_file():
        return result
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            k = k.strip()
            if k in keys:
                # Strip inline comments (e.g. "value # note")
                v = re.sub(r"\s+#.*$", "", v).strip()
                result[k] = v
    return result


def _patch_env_keys(mapping: dict[str, str]) -> None:
    """Replace or append KEY=value entries in .env."""
    content = ENV_FILE.read_text(encoding="utf-8")
    for key, value in mapping.items():
        new_content, n = re.subn(
            rf"^{re.escape(key)}=.*$",
            lambda _m, k=key, v=value: f"{k}={v}",
            content,
            flags=re.MULTILINE,
        )
        if n > 0:
            content = new_content
        else:
            content += f"\n{key}={value}"
    ENV_FILE.write_text(content, encoding="utf-8")


def _export_env_to_os_environ(env: dict) -> None:
    """Inject .env variables into *env* dict (os.environ copy)."""
    if not ENV_FILE.is_file():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            v = re.sub(r"\s+#.*$", "", v).strip()
            env.setdefault(k.strip(), v)


# ─────────────────────────────────────────────────────────────────────────────
# Main Application
# ─────────────────────────────────────────────────────────────────────────────

class InstallerApp:
    _STEP_KEYS = ["step1_tab", "step2_tab", "step3_tab"]

    def __init__(self, root: tk.Tk, *, skip_setup: bool = False) -> None:
        self.root = root
        self.root.title(t("title"))
        self.root.resizable(True, True)
        self.root.geometry("989x1075")
        self.root.minsize(966, 875)
        self.root.configure(bg="#ffffff")

        # UI Scale: "S" (small), "M" (medium), "L" (large)
        self._ui_scale: str = "M"
        
        # Shared state collected across steps
        self._data: dict[str, str] = {}
        self._load_env_into_data()  # Idee 2: pre-fill from existing .env
        self._current_step = 0
        self._skip_setup = skip_setup
        self._deploy_log_file = None
        self._deploy_proc = None

        self._build_chrome()
        self._show_step(2 if skip_setup else 0)

    # ── UI Scaling Helpers ────────────────────────────────────────────────────

    def _get_font_size(self, base_size: int) -> int:
        """Return scaled font size based on _ui_scale."""
        scale_factors = {"S": 0.9, "M": 1.0, "L": 1.25}
        factor = scale_factors.get(self._ui_scale, 1.0)
        return max(7, int(base_size * factor))

    def _get_entry_width(self, base_width: int) -> int:
        """Return scaled entry field width."""
        scale_factors = {"S": 0.85, "M": 1.0, "L": 1.15}
        factor = scale_factors.get(self._ui_scale, 1.0)
        return max(20, int(base_width * factor))

    def _get_wraplength(self, base_length: int) -> int:
        """Return scaled text wraplength."""
        scale_factors = {"S": 0.9, "M": 1.0, "L": 1.2}
        factor = scale_factors.get(self._ui_scale, 1.0)
        return int(base_length * factor)

    def _get_padding(self, base_padding: int) -> int:
        """Return scaled padding value."""
        scale_factors = {"S": 0.8, "M": 1.0, "L": 1.2}
        factor = scale_factors.get(self._ui_scale, 1.0)
        return int(base_padding * factor)

    def _set_ui_scale(self, scale: str) -> None:
        """Change UI scale and rebuild current step."""
        if scale != self._ui_scale:
            self._ui_scale = scale
            self._save_step_state()
            self._show_step(self._current_step)

    # ── Idee 2: Pre-fill from existing .env ────────────────────────────────────

    def _load_env_into_data(self) -> None:
        """Read values from an existing .env and store them in self._data.

        Only fills keys that are not already set; never touches secrets that
        are not persisted in .env (OTPK, sudo password, GHCR credentials).
        """
        if not ENV_FILE.is_file():
            return
        env_vals = _read_env_keys([
            "IMAGE_BACKEND", "IMAGE_FRONTEND", "IMAGE_IMAGE_SERVICE",
            "IMAGE_UPDATER", "IMAGE_BACKUP", "DEPLOYMENT_REPO",
            "HOST_COMPOSE_PROJECT_DIR",
            "BACKUP_UI_USER", "BACKUP_UI_PASSWORD", "TZ", "PROVISION_DONE",
        ])
        mapping = {
            "image_backend":      "IMAGE_BACKEND",
            "image_frontend":     "IMAGE_FRONTEND",
            "image_service":      "IMAGE_IMAGE_SERVICE",
            "image_updater":      "IMAGE_UPDATER",
            "image_backup":       "IMAGE_BACKUP",
            "deployment_repo":    "DEPLOYMENT_REPO",
            "host_compose_dir":   "HOST_COMPOSE_PROJECT_DIR",
            "backup_ui_user":     "BACKUP_UI_USER",
            "backup_ui_password": "BACKUP_UI_PASSWORD",
            "tz":                 "TZ",
        }
        for data_key, env_key in mapping.items():
            value = env_vals.get(env_key, "")
            if value:
                self._data[data_key] = value
        # Auto-check "skip provisioning" if PROVISION_DONE=true
        if env_vals.get("PROVISION_DONE", "").lower() == "true":
            self._data["_already_prov"] = "1"

    def _reload_provisioned_data(self) -> None:
        """Re-read provisioned secrets from .env into self._data after step 1.

        Called after provision.py has written BACKUP_UI_PASSWORD (and other
        secrets) to .env so that step 2 shows the pre-filled password and
        the admin does not need to re-enter it.
        """
        if not ENV_FILE.is_file():
            return
        vals = _read_env_keys(["BACKUP_UI_PASSWORD", "BACKUP_UI_USER", "TZ", "PROVISION_DONE"])
        for env_key, data_key in (
            ("BACKUP_UI_PASSWORD", "backup_ui_password"),
            ("BACKUP_UI_USER",     "backup_ui_user"),
            ("TZ",                 "tz"),
        ):
            v = vals.get(env_key, "")
            if v:
                self._data[data_key] = v
        # Auto-check "skip provisioning" if PROVISION_DONE=true
        if vals.get("PROVISION_DONE", "").lower() == "true":
            self._data["_already_prov"] = "1"

    # ── Chrome (header + step indicator + nav bar) ────────────────────────────

    def _build_chrome(self) -> None:
        # Header
        hdr = tk.Frame(self.root, bg=C_BRAND)
        hdr.pack(fill=tk.X)
        self._hdr_lbl = tk.Label(
            hdr,
            text=t("title"),
            bg=C_BRAND, fg="white",
            font=("Segoe UI", 15, "bold"),
            pady=16,
        )
        self._hdr_lbl.pack(side=tk.LEFT, padx=24)

        # Language selector (DE / EN / RU) — right side of header
        self._lang_btns: dict[str, tk.Button] = {}
        for code in ("de", "en", "ru"):
            btn = tk.Button(
                hdr,
                text=code.upper(),
                width=4,
                bg=C_ACCENT if code == _LANG else "#3a3a5c",
                fg="white",
                activebackground=C_ACCENT,
                activeforeground="white",
                relief=tk.FLAT,
                font=("Segoe UI", 9, "bold"),
                command=lambda c=code: self._switch_lang(c),
            )
            btn.pack(side=tk.RIGHT, padx=(0, 6), pady=10)
            self._lang_btns[code] = btn

        # UI Scale selector (S / M / L) — right side of header, before language
        tk.Label(hdr, text="Zoom:", bg=C_BRAND, fg="white",
                 font=("Segoe UI", 9)).pack(side=tk.RIGHT, padx=(16, 6), pady=10)
        self._scale_btns: dict[str, tk.Button] = {}
        for scale in ("S", "M", "L"):
            btn = tk.Button(
                hdr,
                text=scale,
                width=2,
                bg=C_ACCENT if scale == self._ui_scale else "#3a3a5c",
                fg="white",
                activebackground=C_ACCENT,
                activeforeground="white",
                relief=tk.FLAT,
                font=("Segoe UI", 9, "bold"),
                command=lambda s=scale: self._set_ui_scale_and_update_buttons(s),
            )
            btn.pack(side=tk.RIGHT, padx=(0, 4), pady=10)
            self._scale_btns[scale] = btn

        # Step indicator bar
        self._step_bar = tk.Frame(self.root, bg="#e8eaf6", pady=0)
        self._step_bar.pack(fill=tk.X)
        self._step_lbls: list[tk.Label] = []
        for key in self._STEP_KEYS:
            lbl = tk.Label(
                self._step_bar,
                text=t(key),
                bg="#e8eaf6", fg="#9e9e9e",
                font=("Segoe UI", 10),
                pady=7,
            )
            lbl.pack(side=tk.LEFT)
            self._step_lbls.append(lbl)

        # Navigation bar — packed BEFORE content so it never gets clipped
        nav = tk.Frame(self.root, bg="white", pady=10)
        nav.pack(side=tk.BOTTOM, fill=tk.X)

        # Content area — scrollable canvas wrapper with frame inside
        self._canvas = tk.Canvas(self.root, bg="white", highlightthickness=0)
        self._canvas.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        
        # Vertical scrollbar
        self._scrollbar = ttk.Scrollbar(self.root, orient=tk.VERTICAL, command=self._canvas.yview)
        self._scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        
        # Inner frame that holds all content widgets
        self._content = tk.Frame(self._canvas, bg="white", padx=28, pady=20)
        self._canvas_window = self._canvas.create_window(0, 0, window=self._content, anchor="nw")
        
        # Update scroll region when content frame changes size
        def _on_frame_configure(event):
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        
        self._content.bind("<Configure>", _on_frame_configure)
        
        # Mouse wheel scrolling support — only bind to canvas
        def _on_mousewheel(event):
            self._canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        
        def _on_mousewheel_linux(event):
            if event.num == 4:
                self._canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self._canvas.yview_scroll(1, "units")
        
        self._canvas.bind("<MouseWheel>", _on_mousewheel)  # Windows/macOS
        self._canvas.bind("<Button-4>", _on_mousewheel_linux)  # Linux
        self._canvas.bind("<Button-5>", _on_mousewheel_linux)  # Linux
        self._btn_back = tk.Button(
            nav, text=t("btn_back"), width=12,
            bg="#f0f0f0", relief=tk.FLAT,
            font=("Segoe UI", 10),
            command=self._back,
        )
        self._btn_back.pack(side=tk.LEFT, padx=20)

        self._nav_right = tk.Frame(nav, bg="white")
        self._nav_right.pack(side=tk.RIGHT, padx=20)

        self._btn_next = tk.Button(
            self._nav_right, text=t("btn_next"), width=18,
            bg=C_ACCENT, fg="white",
            activebackground="#3558e8",
            relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"),
            command=self._next,
        )
        self._btn_next.pack(side=tk.RIGHT)

        self._btn_cancel = tk.Button(
            self._nav_right, text=t("btn_cancel"), width=14,
            bg=C_DANGER, fg="white",
            activebackground="#c0392b",
            relief=tk.FLAT,
            font=("Segoe UI", 10, "bold"),
            command=self._cancel_deployment,
        )
        # Not packed initially — shown only while deployment is running

    def _switch_lang(self, code: str) -> None:
        """Change the active language and rebuild the UI — mirrors i18n.changeLanguage()."""
        # Persist any typed-but-not-submitted field values before destroying widgets.
        self._save_step_state()
        set_lang(code)
        # Update static chrome labels
        self._hdr_lbl.configure(text=t("title"))
        self.root.title(t("title"))
        for i, key in enumerate(self._STEP_KEYS):
            self._step_lbls[i].configure(text=t(key))
        # Highlight the active language button
        for c, btn in self._lang_btns.items():
            btn.configure(bg=C_ACCENT if c == code else "#3a3a5c")
        self._btn_cancel.configure(text=t("btn_cancel"))
        # Rebuild current step content + nav button labels
        self._show_step(self._current_step)

    def _set_ui_scale_and_update_buttons(self, scale: str) -> None:
        """Change the UI scale and update button highlighting."""
        self._set_ui_scale(scale)
        # Highlight the active scale button
        for s, btn in self._scale_btns.items():
            btn.configure(bg=C_ACCENT if s == scale else "#3a3a5c")

    def _save_step_state(self) -> None:
        """Snapshot currently displayed field values into self._data.

        Called before any UI rebuild (language switch, back navigation) so that
        typed-but-not-submitted values survive widget destruction.
        """
        if self._current_step == 0:
            self._save_step1_state()
        elif self._current_step == 1:
            self._save_step2_state()
        elif self._current_step == 2:
            # Persist step-3 sudo field (only shown in --skip-setup mode)
            if hasattr(self, "_s3_sudo_var") and self._s3_sudo_var is not None:
                value = self._s3_sudo_var.get()
                if value:
                    self._data["sudo_password"] = value

    def _save_step1_state(self) -> None:
        if not hasattr(self, "_s1_vars"):
            return
        for key, var in self._s1_vars.items():
            value = var.get()
            if value:  # Only overwrite with non-empty so defaults survive
                self._data[key] = value
        if hasattr(self, "_s1_tz_var") and self._s1_tz_var is not None:
            tz_val = self._s1_tz_var.get().strip()
            if tz_val:
                self._data["tz"] = tz_val
        if hasattr(self, "_s1_already_prov"):
            self._data["_already_prov"] = "1" if self._s1_already_prov.get() else ""

    def _save_step2_state(self) -> None:
        for attr, data_key in (
            ("_s2_user",        "ghcr_user"),
            ("_s2_token",       "ghcr_token"),
            ("_s2_sudo",        "sudo_password"),
            ("_s2_backup_user", "backup_ui_user"),
            ("_s2_backup_pass", "backup_ui_password"),
        ):
            if hasattr(self, attr):
                value = getattr(self, attr).get()
                if value:
                    self._data[data_key] = value
        if hasattr(self, "_s2_already_logged_in"):
            self._data["_already_logged_in"] = (
                "1" if self._s2_already_logged_in.get() else ""
            )


    def _update_step_indicator(self) -> None:
        for i, lbl in enumerate(self._step_lbls):
            if self._skip_setup and i < 2:
                lbl.configure(
                    text=f"{t(self._STEP_KEYS[i])} ({t('skip_step_label')})",
                    bg="#fff3cd", fg="#856404",
                    font=("Segoe UI", 10, "italic"),
                )
            elif i < self._current_step:
                lbl.configure(bg="#c8e6c9", fg="#2e7d32",
                               font=("Segoe UI", 10, "bold"))
            elif i == self._current_step:
                lbl.configure(bg=C_ACCENT, fg="white",
                               font=("Segoe UI", 10, "bold"))
            else:
                lbl.configure(bg="#e8eaf6", fg="#9e9e9e",
                               font=("Segoe UI", 10))

    def _clear_content(self) -> None:
        for w in self._content.winfo_children():
            w.destroy()

    def _show_step(self, step: int) -> None:
        self._current_step = step
        self._update_step_indicator()
        self._clear_content()

        builders = [self._build_step1, self._build_step2, self._build_step3]
        builders[step]()

        # Language buttons only active on step 1
        for btn in self._lang_btns.values():
            btn.configure(state=tk.NORMAL if step == 0 else tk.DISABLED)

        # Cancel button managed by _run_step3; hide on any step transition
        self._btn_cancel.pack_forget()

        # In skip mode, disable back button on step 3
        min_step = 2 if self._skip_setup else 0
        self._btn_back.configure(
            text=t("btn_back"),
            state=tk.NORMAL if step > min_step else tk.DISABLED,
        )
        self._btn_next.configure(
            text=t("btn_install") if step == 2 else t("btn_next"),
            bg=C_ACCENT if step < 2 else C_SUCCESS,
            state=tk.NORMAL,
        )

    def _back(self) -> None:
        if self._current_step > 0:
            self._save_step_state()
            self._show_step(self._current_step - 1)

    def _next(self) -> None:
        handlers = [self._run_step1, self._run_step2, self._run_step3]
        handlers[self._current_step]()

    # ── Shared logging helper ─────────────────────────────────────────────────

    def _log(
        self,
        widget: scrolledtext.ScrolledText,
        text: str,
        fg: str | None = None,
    ) -> None:
        """Append a line to a ScrolledText log widget (thread-safe via root.after)."""
        def _append():
            widget.configure(state=tk.NORMAL)
            if fg:
                tag = f"_col_{fg.replace('#', '')}"
                widget.tag_configure(tag, foreground=fg)
                widget.insert(tk.END, text + "\n", tag)
            else:
                widget.insert(tk.END, text + "\n")
            widget.see(tk.END)
            widget.configure(state=tk.DISABLED)

        self.root.after(0, _append)
        if self._deploy_log_file is not None:
            try:
                self._deploy_log_file.write(text + "\n")
                self._deploy_log_file.flush()
            except OSError:
                pass

    def _set_nav(self, *, back: bool, next_: bool) -> None:
        """Enable/disable navigation buttons (thread-safe)."""
        def _do():
            self._btn_back.configure(state=tk.NORMAL if back else tk.DISABLED)
            self._btn_next.configure(state=tk.NORMAL if next_ else tk.DISABLED)
        self.root.after(0, _do)

    def _cancel_deployment(self) -> None:
        proc = self._deploy_proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            self._log(self._s3_log, t("s3_log_cancelled"), C_DANGER)
            self._set_nav(back=True, next_=True)

    # ── STEP 1 — Provisioning ─────────────────────────────────────────────────

    def _build_step1(self) -> None:
        c = self._content
        tk.Label(c, text=t("s1_title"),
                 font=("Segoe UI", self._get_font_size(13), "bold"), bg="white").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        tk.Label(
            c,
            text=t("s1_desc"),
            bg="white", fg="#555", font=("Segoe UI", self._get_font_size(9)),
            justify=tk.LEFT,
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        # ── Idee 2: info banner when .env already exists ──────────────────
        if ENV_FILE.is_file():
            tk.Label(
                c,
                text=t("s1_env_prefilled"),
                bg="#e8f5e9", fg="#2e7d32",
                font=("Segoe UI", self._get_font_size(9), "italic"),
                anchor="w", padx=6, pady=3,
                relief=tk.GROOVE, bd=1,
            ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        # ── Idee 1: "already provisioned" checkbox ────────────────────────
        self._s1_already_prov = tk.BooleanVar(
            value=bool(self._data.get("_already_prov"))
        )
        tk.Checkbutton(
            c,
            text=t("s1_chk_already_provisioned"),
            variable=self._s1_already_prov,
            command=self._toggle_provision_mode,
            bg="white", font=("Segoe UI", self._get_font_size(9)),
            anchor="w",
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(0, 8))

        fields = [
            ("otpk",             t("s1_lbl_otpk"),              False, "s1_hint_otpk"),
            ("api_url",          t("s1_lbl_url"),               False, "s1_hint_url"),
            ("image_backend",    "IMAGE_BACKEND:",               False, "s1_hint_image_backend"),
            ("image_frontend",   "IMAGE_FRONTEND:",              False, "s1_hint_image_frontend"),
            ("image_service",    "IMAGE_IMAGE_SERVICE:",         False, "s1_hint_image_service"),
            ("image_updater",    "IMAGE_UPDATER:",               False, "s1_hint_image_updater"),
            ("image_backup",     "IMAGE_BACKUP:",                False, "s1_hint_image_backup"),
            ("deployment_repo",  "DEPLOYMENT_REPO:",             False, "s1_hint_deployment_repo"),
            ("host_compose_dir", t("s1_lbl_host_compose_dir"),  False, "s1_hint_host_compose_dir"),
        ]
        self._s1_vars: dict[str, tk.StringVar] = {}
        self._s1_entry_otpk: tk.Entry | None = None
        self._s1_entry_api_url: tk.Entry | None = None
        
        current_row = 4
        for key, label, secret, hint_key in fields:
            tk.Label(c, text=label, bg="white", anchor="w",
                     font=("Segoe UI", self._get_font_size(10), "bold"), width=30).grid(
                row=current_row, column=0, sticky="w", pady=(4, 2))
            var = tk.StringVar(value=self._data.get(key, ""))
            entry = tk.Entry(c, textvariable=var, width=self._get_entry_width(53),
                             show="*" if secret else "",
                             font=("Segoe UI", self._get_font_size(10)),
                             relief=tk.SOLID, bd=1)
            entry.grid(row=current_row, column=1, sticky="ew", padx=(0, 0), pady=(4, 2))
            
            tk.Label(
                c, text=t(hint_key), bg="white", fg="#000",
                font=("Segoe UI", self._get_font_size(9)),
                wraplength=self._get_wraplength(550), anchor="nw", justify=tk.LEFT,
            ).grid(row=current_row+1, column=1, sticky="ew", padx=(0, 0), pady=(0, 6))
            
            self._s1_vars[key] = var
            if key == "otpk":
                self._s1_entry_otpk = entry
            elif key == "api_url":
                self._s1_entry_api_url = entry
            
            current_row += 2

        self._s1_tags_hint = tk.Label(
            c, text="", bg="white", fg="#0288d1",
            font=("Segoe UI", self._get_font_size(9)), anchor="nw",
        )
        self._s1_tags_hint.grid(row=current_row, column=1, sticky="ew", pady=(6, 0))
        self._s1_tags_fetch_after_id: str | None = None
        current_row += 1

        # ── Timezone (TZ) ─────────────────────────────────────────────────
        tk.Label(c, text=t("s1_lbl_tz"), bg="white", anchor="w",
                 font=("Segoe UI", self._get_font_size(10), "bold"), width=30).grid(
            row=current_row, column=0, sticky="w", pady=(4, 2))
        self._s1_tz_var = tk.StringVar(
            value=self._data.get("tz", "Europe/Berlin")
        )
        tz_combo = ttk.Combobox(
            c, textvariable=self._s1_tz_var,
            values=_TIMEZONES, width=self._get_entry_width(51), state="readonly",
            font=("Segoe UI", self._get_font_size(10)),
        )
        tz_combo.grid(row=current_row, column=1, sticky="ew", padx=(0, 0), pady=(4, 2))
        # Ensure the current value is visible in the list
        if self._s1_tz_var.get() in _TIMEZONES:
            tz_combo.current(_TIMEZONES.index(self._s1_tz_var.get()))
        
        tk.Label(
            c, text=t("s1_hint_tz"), bg="white", fg="#000",
            font=("Segoe UI", self._get_font_size(9)),
            wraplength=self._get_wraplength(550), anchor="nw",
        ).grid(row=current_row+1, column=1, sticky="ew", padx=(0, 0), pady=(0, 6))
        
        current_row += 2

        def _on_repo_change(*_: object) -> None:
            if self._s1_tags_fetch_after_id is not None:
                self.root.after_cancel(self._s1_tags_fetch_after_id)
            repo = self._s1_vars["deployment_repo"].get().strip()
            if repo and "/" in repo:
                self._s1_tags_hint.configure(
                    text=t("s1_hint_fetching"), fg="#0288d1")
                self._s1_tags_fetch_after_id = self.root.after(
                    800,
                    lambda r=repo: threading.Thread(
                        target=self._fetch_and_show_tags, args=(r,),
                        daemon=True,
                    ).start(),
                )
            else:
                self._s1_tags_hint.configure(text="")

        self._s1_vars["deployment_repo"].trace_add("write", _on_repo_change)
        # Trigger immediately if a value is already present
        _on_repo_change()

        tk.Label(c, text=t("s1_lbl_output"), bg="white",
                 font=("Segoe UI", self._get_font_size(10), "bold")).grid(
            row=current_row, column=0, columnspan=2,
            sticky="w", pady=(14, 2))

        self._s1_log = scrolledtext.ScrolledText(
            c, height=11, width=82, font=("Courier", self._get_font_size(9)),
            state=tk.DISABLED, bg="#fafafa", relief=tk.SOLID, bd=1)
        self._s1_log.grid(row=current_row+1, column=0, columnspan=2, sticky="ew")
        c.columnconfigure(1, weight=1)
        c.rowconfigure(current_row+1, weight=1)

        # Apply initial toggle state (e.g. restored after language switch)
        if self._s1_already_prov.get():
            self._toggle_provision_mode()

    def _toggle_provision_mode(self) -> None:
        """Disable OTPK / api_url fields when 'already provisioned' is checked."""
        already = self._s1_already_prov.get()
        state = tk.DISABLED if already else tk.NORMAL
        if self._s1_entry_otpk is not None:
            self._s1_entry_otpk.configure(state=state)
        if self._s1_entry_api_url is not None:
            self._s1_entry_api_url.configure(state=state)

    def _fetch_and_show_tags(self, repo: str) -> None:
        """Background worker: fetch recent tags and update the hint label."""
        tags = _fetch_recent_tags(repo, 4)

        def _update() -> None:
            if not hasattr(self, "_s1_tags_hint"):
                return
            try:
                self._s1_tags_hint.winfo_exists()
            except tk.TclError:
                return
            if tags:
                hint = t("s1_recent_tags_label") + "  " + "  ·  ".join(tags)
                self._s1_tags_hint.configure(text=hint, fg="#1565c0")
            else:
                self._s1_tags_hint.configure(
                    text=t("s1_hint_fetch_err"), fg="#bbb")

        self.root.after(0, _update)

    def _run_step1(self) -> None:
        vals = {k: v.get().strip() for k, v in self._s1_vars.items()}
        tz_value = (
            self._s1_tz_var.get().strip()
            if hasattr(self, "_s1_tz_var") and self._s1_tz_var
            else ""
        ) or "Europe/Berlin"
        self._data["tz"] = tz_value

        # ── Idee 1: skip provisioning when checkbox is set ────────────────
        if self._s1_already_prov.get():
            if not ENV_FILE.is_file():
                messagebox.showerror(t("err_title_missing"),
                                     t("s1_err_no_env_for_skip"))
                return

            # Only patch IMAGE_* / DEPLOYMENT_REPO / HOST_COMPOSE_PROJECT_DIR fields that were filled in
            env_key_map = {
                "image_backend":    "IMAGE_BACKEND",
                "image_frontend":   "IMAGE_FRONTEND",
                "image_service":    "IMAGE_IMAGE_SERVICE",
                "image_updater":    "IMAGE_UPDATER",
                "image_backup":     "IMAGE_BACKUP",
                "deployment_repo":  "DEPLOYMENT_REPO",
                "host_compose_dir": "HOST_COMPOSE_PROJECT_DIR",
            }
            patch = {
                env_key: vals[field_key]
                for field_key, env_key in env_key_map.items()
                if vals.get(field_key)
            }
            self._data.update({k: v for k, v in vals.items() if v})
            self._btn_next.configure(state=tk.DISABLED)
            self._btn_back.configure(state=tk.DISABLED)

            def task_skip() -> None:
                self._log(self._s1_log, t("s1_log_skip_provision"), C_INFO)
                patch_full = {**patch, "TZ": tz_value}
                try:
                    _patch_env_keys(patch_full)
                    self._log(self._s1_log, t("s1_log_tags_ok"), C_SUCCESS)
                except Exception as exc:  # noqa: BLE001
                    self._log(self._s1_log,
                              t("s1_log_tags_err", exc=str(exc)), C_DANGER)
                    self._set_nav(back=False, next_=True)
                    return
                self._reload_provisioned_data()
                self._log(self._s1_log, t("s1_log_done"), C_SUCCESS)
                self.root.after(600, lambda: self._show_step(1))

            threading.Thread(target=task_skip, daemon=True).start()
            return

        # ── Normal provisioning path ──────────────────────────────────────
        missing = [k for k, v in vals.items() if not v]
        if missing:
            messagebox.showerror(t("err_title_missing"), t("s1_err_missing"))
            return

        self._data.update(vals)
        self._btn_next.configure(state=tk.DISABLED)
        self._btn_back.configure(state=tk.DISABLED)

        def task() -> None:
            self._log(self._s1_log,
                      t("s1_log_connecting", url=vals["api_url"]), C_INFO)
            cmd = [
                sys.executable, str(PROVISION_PY),
                "--token",       vals["otpk"],
                "--api-url",     vals["api_url"],
                "--env-example", str(ENV_EXAMPLE),
                "--env-output",  str(ENV_FILE),
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, cwd=str(REPO_DIR)
            )

            for line in result.stdout.strip().splitlines():
                self._log(self._s1_log, line)

            if result.returncode != 0:
                err = result.stderr.strip() or t("s1_log_fail")
                for line in err.splitlines():
                    self._log(self._s1_log, line, C_DANGER)
                self._log(self._s1_log, t("s1_log_fail"), C_DANGER)
                self._set_nav(back=False, next_=True)
                return

            # Patch IMAGE_* and TZ into .env
            self._log(self._s1_log, t("s1_log_writing"))
            try:
                _patch_env_keys({
                    "IMAGE_BACKEND":            vals["image_backend"],
                    "IMAGE_FRONTEND":           vals["image_frontend"],
                    "IMAGE_IMAGE_SERVICE":       vals["image_service"],
                    "IMAGE_UPDATER":             vals["image_updater"],
                    "IMAGE_BACKUP":              vals["image_backup"],
                    "DEPLOYMENT_REPO":           vals["deployment_repo"],
                    "HOST_COMPOSE_PROJECT_DIR":  vals["host_compose_dir"],
                    "TZ":                        tz_value,
                    "PROVISION_DONE":            "true",
                })
                self._log(self._s1_log, t("s1_log_tags_ok"), C_SUCCESS)
            except Exception as exc:  # noqa: BLE001
                self._log(self._s1_log,
                          t("s1_log_tags_err", exc=str(exc)), C_DANGER)
                self._set_nav(back=False, next_=True)
                return

            self._reload_provisioned_data()
            self._log(self._s1_log, t("s1_log_done"), C_SUCCESS)
            self.root.after(600, lambda: self._show_step(1))

        threading.Thread(target=task, daemon=True).start()

    # ── STEP 2 — Docker Login ─────────────────────────────────────────────────

    def _build_step2(self) -> None:
        c = self._content
        tk.Label(c, text=t("s2_title"),
                 font=("Segoe UI", self._get_font_size(13), "bold"), bg="white").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        tk.Label(
            c,
            text=t("s2_desc"),
            bg="white", fg="#555", font=("Segoe UI", self._get_font_size(9)),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        # ── auto-detect existing GHCR credentials ─────────────────────────
        creds_found, creds_source = _has_ghcr_credentials()
        current_row = 2
        if creds_found:
            tk.Label(
                c,
                text=t("s2_creds_found", source=creds_source),
                bg="#e8f5e9", fg="#2e7d32",
                font=("Segoe UI", self._get_font_size(9), "italic"),
                anchor="w", padx=6, pady=3,
                relief=tk.GROOVE, bd=1,
            ).grid(row=current_row, column=0, columnspan=2, sticky="ew", pady=(0, 4))
            current_row += 1

        # ── "already logged in" checkbox ───────────────────────────────────
        if "_already_logged_in" in self._data:
            initial_skip = bool(self._data["_already_logged_in"])
        else:
            initial_skip = creds_found
        self._s2_already_logged_in = tk.BooleanVar(value=initial_skip)
        tk.Checkbutton(
            c,
            text=t("s2_chk_already_logged_in"),
            variable=self._s2_already_logged_in,
            command=self._toggle_login_mode,
            bg="white", font=("Segoe UI", self._get_font_size(9)),
            anchor="w",
        ).grid(row=current_row, column=0, columnspan=2, sticky="w", pady=(0, 8))
        current_row += 1

        # ── GHCR User ─────────────────────────────────────────────────────
        tk.Label(c, text=t("s2_lbl_user"), bg="white",
                 font=("Segoe UI", self._get_font_size(10), "bold"), width=26, anchor="w").grid(
            row=current_row, column=0, sticky="w", pady=(4, 2))
        self._s2_user = tk.StringVar(value=self._data.get("ghcr_user", ""))
        self._s2_user_entry = tk.Entry(
            c, textvariable=self._s2_user, width=self._get_entry_width(53),
            font=("Segoe UI", self._get_font_size(10)), relief=tk.SOLID, bd=1)
        self._s2_user_entry.grid(row=current_row, column=1, sticky="ew", padx=(0, 0))
        tk.Label(
            c, text=t("s2_hint_user"), bg="white", fg="#000",
            font=("Segoe UI", self._get_font_size(9)), wraplength=self._get_wraplength(550), anchor="nw",
        ).grid(row=current_row+1, column=1, sticky="ew", pady=(0, 6))
        current_row += 2

        # ── GHCR Token ─────────────────────────────────────────────────────
        tk.Label(c, text=t("s2_lbl_token"), bg="white",
                 font=("Segoe UI", self._get_font_size(10), "bold"), width=26, anchor="w").grid(
            row=current_row, column=0, sticky="w", pady=(4, 2))
        self._s2_token = tk.StringVar(value=self._data.get("ghcr_token", ""))
        self._s2_token_entry = tk.Entry(
            c, textvariable=self._s2_token, width=self._get_entry_width(53), show="*",
            font=("Segoe UI", self._get_font_size(10)), relief=tk.SOLID, bd=1)
        self._s2_token_entry.grid(row=current_row, column=1, sticky="ew", padx=(0, 0))
        tk.Label(
            c, text=t("s2_hint_token"), bg="white", fg="#000",
            font=("Segoe UI", self._get_font_size(9)), wraplength=self._get_wraplength(550), anchor="nw",
        ).grid(row=current_row+1, column=1, sticky="ew", pady=(0, 2))
        current_row += 2

        self._s2_show_token = tk.BooleanVar(value=False)
        self._s2_show_token_btn = tk.Checkbutton(
            c, text=t("s2_show_token"),
            variable=self._s2_show_token,
            command=self._toggle_token_visibility,
            bg="white", font=("Segoe UI", self._get_font_size(9)),
        )
        self._s2_show_token_btn.grid(row=current_row, column=1, sticky="w",
                                     padx=(0, 0), pady=(0, 6))
        current_row += 1

        # ── Sudo Password ──────────────────────────────────────────────────
        tk.Label(c, text=t("s2_lbl_sudo"), bg="white",
                 font=("Segoe UI", self._get_font_size(10), "bold"), width=26, anchor="w").grid(
            row=current_row, column=0, sticky="w", pady=(4, 2))
        self._s2_sudo = tk.StringVar(value=self._data.get("sudo_password", ""))
        self._s2_sudo_entry = tk.Entry(
            c, textvariable=self._s2_sudo, width=self._get_entry_width(53), show="*",
            font=("Segoe UI", self._get_font_size(10)), relief=tk.SOLID, bd=1)
        self._s2_sudo_entry.grid(row=current_row, column=1, sticky="ew", padx=(0, 0))
        tk.Label(
            c, text=t("s2_hint_sudo"), bg="white", fg="#000",
            font=("Segoe UI", self._get_font_size(9)), wraplength=self._get_wraplength(550), anchor="nw",
        ).grid(row=current_row+1, column=1, sticky="ew", pady=(0, 2))
        current_row += 2

        self._s2_show_sudo = tk.BooleanVar(value=False)
        self._s2_show_sudo_btn = tk.Checkbutton(
            c, text=t("s2_show_sudo"),
            variable=self._s2_show_sudo,
            command=self._toggle_sudo_visibility,
            bg="white", font=("Segoe UI", self._get_font_size(9)),
        )
        self._s2_show_sudo_btn.grid(row=current_row, column=1, sticky="w",
                                    padx=(0, 0), pady=(0, 6))
        current_row += 1

        # ── Backup Section ────────────────────────────────────────────────
        tk.Label(c, text=t("s2_backup_section"),
                 bg="white", fg="#888", font=("Segoe UI", self._get_font_size(9), "italic"),
                 anchor="w").grid(row=current_row, column=0, columnspan=2,
                                  sticky="w", pady=(16, 4))
        current_row += 1

        # ── Backup UI User ────────────────────────────────────────────────
        tk.Label(c, text=t("s2_lbl_backup_user"), bg="white",
                 font=("Segoe UI", self._get_font_size(10), "bold"), width=26, anchor="w").grid(
            row=current_row, column=0, sticky="w", pady=(4, 2))
        self._s2_backup_user = tk.StringVar(
            value=self._data.get("backup_ui_user", "admin"))
        tk.Entry(c, textvariable=self._s2_backup_user, width=self._get_entry_width(53),
                 font=("Segoe UI", self._get_font_size(10)), relief=tk.SOLID, bd=1).grid(
            row=current_row, column=1, sticky="ew", padx=(0, 0))
        tk.Label(
            c, text=t("s2_hint_backup_user"), bg="white", fg="#000",
            font=("Segoe UI", self._get_font_size(9)), wraplength=self._get_wraplength(550), anchor="nw",
        ).grid(row=current_row+1, column=1, sticky="ew", pady=(0, 6))
        current_row += 2

        # ── Backup UI Password ────────────────────────────────────────────
        tk.Label(c, text=t("s2_lbl_backup_pass"), bg="white",
                 font=("Segoe UI", self._get_font_size(10), "bold"), width=26, anchor="w").grid(
            row=current_row, column=0, sticky="w", pady=(4, 2))
        self._s2_backup_pass = tk.StringVar(
            value=self._data.get("backup_ui_password", ""))
        self._s2_backup_pass_entry = tk.Entry(
            c, textvariable=self._s2_backup_pass, width=self._get_entry_width(53), show="*",
            font=("Segoe UI", self._get_font_size(10)), relief=tk.SOLID, bd=1)
        self._s2_backup_pass_entry.grid(row=current_row, column=1, sticky="ew",
                                        padx=(0, 0))
        tk.Label(
            c, text=t("s2_hint_backup_pass"), bg="white", fg="#000",
            font=("Segoe UI", self._get_font_size(9)), wraplength=self._get_wraplength(550), anchor="nw",
        ).grid(row=current_row+1, column=1, sticky="ew", pady=(0, 2))
        current_row += 2

        self._s2_show_backup_pass = tk.BooleanVar(value=False)
        tk.Checkbutton(
            c, text=t("s2_show_backup_pass"),
            variable=self._s2_show_backup_pass,
            command=self._toggle_backup_pass_visibility,
            bg="white", font=("Segoe UI", self._get_font_size(9)),
        ).grid(row=current_row, column=1, sticky="w", padx=(0, 0), pady=(0, 6))
        current_row += 1

        # Show a note when BACKUP_UI_PASSWORD was delivered by Legisell Provisioning
        if self._data.get("backup_ui_password"):
            tk.Label(
                c,
                text=t("s2_backup_pass_provisioned"),
                bg="#e8f5e9", fg="#2e7d32",
                font=("Segoe UI", self._get_font_size(9), "italic"),
                anchor="w", padx=6, pady=3,
                relief=tk.GROOVE, bd=1,
            ).grid(row=current_row, column=0, columnspan=2, sticky="ew", pady=(6, 0))
            current_row += 1

        self._s2_status = tk.Label(
            c, text="", bg="white", font=("Segoe UI", self._get_font_size(10)),
            wraplength=self._get_wraplength(750), justify=tk.LEFT)
        self._s2_status.grid(row=current_row, column=0, columnspan=2,
                              sticky="ew", pady=(24, 0))
        c.columnconfigure(1, weight=1)

        # Apply initial toggle state
        if self._s2_already_logged_in.get():
            self._toggle_login_mode()

    def _toggle_login_mode(self) -> None:
        """Disable GHCR user/token/sudo fields when 'already logged in' is checked."""
        skip = self._s2_already_logged_in.get()
        state = tk.DISABLED if skip else tk.NORMAL
        for widget in (
            self._s2_user_entry,
            self._s2_token_entry,
            self._s2_sudo_entry,
            self._s2_show_token_btn,
            self._s2_show_sudo_btn,
        ):
            widget.configure(state=state)

    def _toggle_token_visibility(self) -> None:
        self._s2_token_entry.configure(
            show="" if self._s2_show_token.get() else "*"
        )

    def _toggle_sudo_visibility(self) -> None:
        self._s2_sudo_entry.configure(
            show="" if self._s2_show_sudo.get() else "*"
        )

    def _toggle_backup_pass_visibility(self) -> None:
        self._s2_backup_pass_entry.configure(
            show="" if self._s2_show_backup_pass.get() else "*"
        )

    def _toggle_step3_sudo_visibility(self) -> None:
        if self._s3_sudo_entry is not None:
            self._s3_sudo_entry.configure(
                show="" if self._s3_show_sudo_var.get() else "*"
            )

    def _run_step2(self) -> None:
        backup_user = self._s2_backup_user.get().strip() or "admin"
        backup_pass = self._s2_backup_pass.get()

        # ── skip-login path ───────────────────────────────────────────────
        if self._s2_already_logged_in.get():
            found, _ = _has_ghcr_credentials()
            if not found:
                messagebox.showerror(t("err_title_missing"),
                                     t("s2_err_no_creds_for_skip"))
                return
            if not backup_pass:
                messagebox.showerror(t("err_title_missing"),
                                     t("s2_err_backup_pass"))
                return
            self._data["backup_ui_user"]     = backup_user
            self._data["backup_ui_password"] = backup_pass
            self._btn_next.configure(state=tk.DISABLED)
            self._btn_back.configure(state=tk.DISABLED)
            self.root.after(0, lambda: self._s2_status.configure(
                text=t("s2_log_skip_login"), fg=C_INFO))

            def task_skip() -> None:
                try:
                    _patch_env_keys({
                        "BACKUP_UI_USER":     backup_user,
                        "BACKUP_UI_PASSWORD": backup_pass,
                    })
                except OSError as exc:
                    err_msg = f"\u2717 .env schreiben fehlgeschlagen: {exc}"
                    self.root.after(0, lambda m=err_msg: self._s2_status.configure(
                        text=m, fg=C_DANGER))
                    self._set_nav(back=True, next_=True)
                    return
                self.root.after(0, lambda: self._s2_status.configure(
                    text=t("s2_login_ok"), fg=C_SUCCESS))
                self.root.after(600, lambda: self._show_step(2))

            threading.Thread(target=task_skip, daemon=True).start()
            return

        # ── normal login path ─────────────────────────────────────────────
        user          = self._s2_user.get().strip()
        token         = self._s2_token.get().strip()
        sudo_password = self._s2_sudo.get()
        if not user or not token or not sudo_password:
            messagebox.showerror(t("err_title_missing"), t("s2_err_missing"))
            return
        if not backup_pass:
            messagebox.showerror(t("err_title_missing"), t("s2_err_backup_pass"))
            return

        self._data["ghcr_user"]          = user
        self._data["ghcr_token"]         = token
        self._data["sudo_password"]      = sudo_password
        self._data["backup_ui_user"]     = backup_user
        self._data["backup_ui_password"] = backup_pass
        self._btn_next.configure(state=tk.DISABLED)
        self._btn_back.configure(state=tk.DISABLED)
        self.root.after(0, lambda: self._s2_status.configure(
            text=t("s2_connecting"), fg=C_INFO))

        def task() -> None:
            try:
                result = subprocess.run(
                    ["sudo", "-k", "-S",
                     "docker", "login", "ghcr.io",
                     "-u", user, "--password-stdin"],
                    # sudo reads the first line as its password;
                    # docker login reads the remainder as the registry token.
                    input=sudo_password + "\n" + token,
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError:
                self.root.after(0, lambda: self._s2_status.configure(
                    text=t("s2_no_docker"), fg=C_DANGER))
                self._set_nav(back=True, next_=True)
                return

            combined = (result.stdout + result.stderr).strip()
            success  = result.returncode == 0

            if success:
                try:
                    _patch_env_keys({
                        "BACKUP_UI_USER":     backup_user,
                        "BACKUP_UI_PASSWORD": backup_pass,
                    })
                except OSError as exc:
                    err_msg = f"\u2717 .env schreiben fehlgeschlagen: {exc}"
                    self.root.after(0, lambda m=err_msg: self._s2_status.configure(
                        text=m, fg=C_DANGER))
                    self._set_nav(back=True, next_=True)
                    return
                try:
                    _write_pos_auth_json(user, token)
                except OSError as exc:
                    err_msg = t("s2_auth_file_err", exc=str(exc))
                    self.root.after(0, lambda m=err_msg: self._s2_status.configure(
                        text=m, fg=C_DANGER))
                    self._set_nav(back=True, next_=True)
                    return
                self.root.after(0, lambda: self._s2_status.configure(
                    text=t("s2_login_ok"), fg=C_SUCCESS))
                self.root.after(600, lambda: self._show_step(2))
            else:
                msg = combined or t("s2_login_fail")
                self.root.after(0, lambda: self._s2_status.configure(
                    text=t("s2_login_err", msg=msg), fg=C_DANGER))
                self._set_nav(back=True, next_=True)

        threading.Thread(target=task, daemon=True).start()

    # ── STEP 3 — Summary & Deployment ─────────────────────────────────────────

    def _build_step3(self) -> None:
        c = self._content
        tk.Label(c, text=t("s3_title"),
                 font=("Segoe UI", self._get_font_size(13), "bold"), bg="white").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))

        next_row = 1
        if self._skip_setup:
            banner = tk.Label(
                c, text=t("skip_banner"),
                bg="#fff3cd", fg="#856404",
                font=("Segoe UI", self._get_font_size(10), "bold"),
                anchor="w", padx=10, pady=6,
                relief=tk.GROOVE, bd=1,
            )
            banner.grid(row=next_row, column=0, columnspan=2,
                        sticky="ew", pady=(0, 10))
            next_row += 1

        env = _read_env_keys([
            "APP_NAME", "POS_PUBLIC_PORT",
            "POSTGRES_DB", "POSTGRES_SERVER",
            "IMAGE_BACKEND", "IMAGE_FRONTEND", "IMAGE_IMAGE_SERVICE", "IMAGE_BACKUP",
        ])

        summary = [
            (t("s3_sum_api_url"),    self._data.get("api_url", "—")),
            (t("s3_sum_ghcr_user"),  self._data.get("ghcr_user", "—")),
            (t("s3_sum_app_name"),   env.get("APP_NAME", "—")),
            (t("s3_sum_port"),       env.get("POS_PUBLIC_PORT", "80")),
            (t("s3_sum_db"),         f"{env.get('POSTGRES_DB', '—')} @ {env.get('POSTGRES_SERVER', '—')}"),
            ("IMAGE_BACKEND",        env.get("IMAGE_BACKEND", "—")),
            ("IMAGE_FRONTEND",       env.get("IMAGE_FRONTEND", "—")),
            ("IMAGE_IMAGE_SERVICE",  env.get("IMAGE_IMAGE_SERVICE", "—")),
            ("IMAGE_BACKUP",         env.get("IMAGE_BACKUP", "—")),
            (t("s3_sum_secrets"),    t("s3_secrets_set")),
        ]

        box = tk.Frame(c, bg="#f0f4ff", relief=tk.RIDGE, bd=1,
                       padx=self._get_padding(16), pady=self._get_padding(12))
        box.grid(row=next_row, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        for i, (k, v) in enumerate(summary):
            tk.Label(box, text=k + ":", bg="#f0f4ff", anchor="w", width=26,
                     font=("Segoe UI", self._get_font_size(9), "bold")).grid(
                row=i, column=0, sticky="w", pady=2)
            tk.Label(box, text=v, bg="#f0f4ff", anchor="w",
                     font=("Segoe UI", self._get_font_size(9)), wraplength=self._get_wraplength(500),
                     justify=tk.LEFT).grid(
                row=i, column=1, sticky="w", padx=(8, 0))

        tk.Label(
            c,
            text=t("s3_hint"),
            bg="white", fg="#555", font=("Segoe UI", self._get_font_size(9)),
        ).grid(row=next_row+1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        # Sudo-Passwort-Feld — nur anzeigen wenn Schritt 2 übersprungen wurde
        self._s3_sudo_var: tk.StringVar | None = None
        self._s3_sudo_entry: tk.Entry | None = None
        sudo_row_offset = 0
        if not self._data.get("sudo_password"):
            sudo_row_offset = 2
            tk.Label(c, text=t("s3_lbl_sudo"), bg="white",
                     font=("Segoe UI", self._get_font_size(10), "bold"), width=26, anchor="w").grid(
                row=next_row+2, column=0, sticky="w", pady=(4, 2))
            self._s3_sudo_var = tk.StringVar()
            self._s3_sudo_entry = tk.Entry(
                c, textvariable=self._s3_sudo_var, width=self._get_entry_width(53), show="*",
                font=("Segoe UI", self._get_font_size(10)), relief=tk.SOLID, bd=1)
            self._s3_sudo_entry.grid(row=next_row+2, column=1, sticky="ew", padx=(0, 0))
            self._s3_show_sudo_var = tk.BooleanVar(value=False)
            tk.Checkbutton(
                c, text=t("s3_show_sudo"),
                variable=self._s3_show_sudo_var,
                command=self._toggle_step3_sudo_visibility,
                bg="white", font=("Segoe UI", self._get_font_size(9)),
            ).grid(row=next_row+3, column=1, sticky="w", padx=(0, 0), pady=(2, 0))

        tk.Label(c, text=t("s3_lbl_log"), bg="white",
                 font=("Segoe UI", self._get_font_size(10), "bold")).grid(
            row=next_row+2+sudo_row_offset, column=0, columnspan=2, sticky="w", pady=(4, 2))

        self._s3_log = scrolledtext.ScrolledText(
            c, height=15, width=82, font=("Courier", self._get_font_size(9)),
            state=tk.DISABLED,
            bg="#0d1117", fg="#c9d1d9",
            insertbackground="white",
            relief=tk.SOLID, bd=1,
        )
        self._s3_log.grid(row=next_row+3+sudo_row_offset, column=0, columnspan=2, sticky="ew")
        c.columnconfigure(1, weight=1)
        c.rowconfigure(next_row+3+sudo_row_offset, weight=1)

    def _run_step3(self) -> None:
        self._btn_next.configure(state=tk.DISABLED)  # sofortiges Deaktivieren (verhindert Doppelklick)

        if self._s3_sudo_var is not None:
            sudo_password_in = self._s3_sudo_var.get()
            if not sudo_password_in:
                messagebox.showerror(t("err_title_missing"), t("s3_err_no_sudo"))
                self._btn_next.configure(state=tk.NORMAL)
                return
            self._data["sudo_password"] = sudo_password_in

        self._btn_back.configure(state=tk.DISABLED)

        def task() -> None:
            log_dir = REPO_DIR / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            self._deploy_log_file = (log_dir / f"deploy-{ts}.log").open(
                "w", encoding="utf-8"
            )
            self.root.after(0, lambda: self._btn_cancel.pack(side=tk.LEFT, padx=(0, 8)))
            try:
                env = os.environ.copy()
                _export_env_to_os_environ(env)

                # Ensure required host directories exist for bind mounts
                subprocess.run(["mkdir", "-p", str(REPO_DIR / "updater-state")], check=False)
                subprocess.run(["mkdir", "-p", str(REPO_DIR / "backups")], check=False)

                sudo_password = self._data.get("sudo_password", "")

                def _run_compose(subcmd: list[str]) -> "subprocess.Popen[str] | None":
                    """Run `sudo docker compose -f <file> *subcmd` with live log output.

                    Returns the finished Popen object, or None if docker was not found.
                    Streams stdout/stderr to the log widget and suppresses the sudo
                    password prompt line.
                    """
                    cmd = ["sudo", "-k", "-S", "docker", "compose", "-f", str(COMPOSE_FILE)] + subcmd
                    try:
                        p = subprocess.Popen(
                            cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            cwd=str(REPO_DIR),
                            env=env,
                        )
                    except FileNotFoundError:
                        self._log(self._s3_log, t("s3_no_docker"), C_DANGER)
                        self._set_nav(back=True, next_=False)
                        return None
                    self._deploy_proc = p
                    assert p.stdin is not None
                    p.stdin.write(sudo_password + "\n")
                    p.stdin.flush()
                    p.stdin.close()
                    assert p.stdout is not None
                    for line in p.stdout:
                        clean = line.rstrip()
                        if clean.startswith("[sudo]"):
                            continue  # suppress sudo's password prompt
                        self._log(self._s3_log, clean)
                    p.wait()
                    return p

                def _run_compose_with_progress(
                    subcmd: list[str],
                    operation_label: str
                ) -> "subprocess.Popen[str] | None":
                    """Run `sudo docker compose *subcmd` with progress spinner.
                    
                    Instead of logging every line, buffers output and displays
                    a progress line with spinner animation every 5 seconds.
                    Only logs a summary (success/failure) to the log file.
                    
                    Returns the finished Popen object, or None if docker was not found.
                    """
                    import threading
                    import time
                    
                    cmd = ["sudo", "-k", "-S", "docker", "compose", "-f", str(COMPOSE_FILE)] + subcmd
                    try:
                        p = subprocess.Popen(
                            cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            cwd=str(REPO_DIR),
                            env=env,
                            bufsize=1,
                        )
                    except FileNotFoundError:
                        self._log(self._s3_log, t("s3_no_docker"), C_DANGER)
                        self._set_nav(back=True, next_=False)
                        return None
                    
                    self._deploy_proc = p
                    assert p.stdin is not None
                    p.stdin.write(sudo_password + "\n")
                    p.stdin.flush()
                    p.stdin.close()
                    
                    # Circular buffer: keep last 500 lines for error reporting
                    output_buffer: list[str] = []
                    buffer_max_size = 500
                    lock = threading.Lock()
                    stop_progress = threading.Event()
                    
                    # Thread 1: Read output and buffer it
                    def _read_output() -> None:
                        assert p.stdout is not None
                        for line in p.stdout:
                            clean = line.rstrip()
                            if clean.startswith("[sudo]"):
                                continue
                            with lock:
                                output_buffer.append(clean)
                                if len(output_buffer) > buffer_max_size:
                                    output_buffer.pop(0)
                    
                    # Thread 2: Update progress line every 5 seconds
                    def _show_progress() -> None:
                        spinners = ["|", "/", "-", "\\"]
                        counter = 0
                        progress_line_id = None
                        
                        while not stop_progress.is_set():
                            spinner = spinners[counter % 4]
                            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
                            msg = f"  {timestamp} {spinner} {operation_label}..."
                            
                            # First time: use _log to add line
                            # Subsequent: replace the last line
                            if progress_line_id is None:
                                self._log(self._s3_log, msg, "#aaaaaa")
                                progress_line_id = "___progress___"
                            else:
                                # Replace last line by removing and re-adding
                                def _replace():
                                    try:
                                        self._s3_log.configure(state=tk.NORMAL)
                                        # Delete last line
                                        line_start = self._s3_log.index("end-1c linestart")
                                        line_end = self._s3_log.index("end-1c")
                                        self._s3_log.delete(line_start, line_end)
                                        # Insert new progress line
                                        self._s3_log.insert(tk.END, msg + "\n", "")
                                        self._s3_log.see(tk.END)
                                        self._s3_log.configure(state=tk.DISABLED)
                                    except tk.TclError:
                                        pass
                                
                                self.root.after(0, _replace)
                            
                            counter += 1
                            # Update spinner every 1 second for smooth animation
                            if stop_progress.is_set():
                                break
                            time.sleep(1)
                    
                    # Start the threads
                    reader_thread = threading.Thread(target=_read_output, daemon=True)
                    progress_thread = threading.Thread(target=_show_progress, daemon=True)
                    reader_thread.start()
                    progress_thread.start()
                    
                    # Wait for process to complete
                    p.wait()
                    stop_progress.set()
                    reader_thread.join(timeout=2)
                    progress_thread.join(timeout=2)
                    
                    # Log final result
                    if p.returncode == 0:
                        self._log(self._s3_log, f"  ✓ {operation_label} erfolgreich", C_SUCCESS)
                    else:
                        # On error, show last N lines from buffer for debugging
                        error_context_lines = 20
                        self._log(self._s3_log, f"  ✗ {operation_label} fehlgeschlagen", C_DANGER)
                        with lock:
                            if output_buffer:
                                self._log(self._s3_log, "", None)
                                self._log(self._s3_log, "  — Letzte Ausgabezeilen:", "#888888")
                                for line in output_buffer[-error_context_lines:]:
                                    self._log(self._s3_log, f"    {line}", "#888888")
                    
                    return p

                # ── Step 0: Ensure the shared Docker network exists ───────
                # pos-network is declared external: true in the Compose file so
                # Compose never manages its lifecycle.  We create it here once;
                # the call is idempotent — if the network already exists the
                # daemon returns an error that we deliberately ignore.
                self._log(self._s3_log,
                          "▶ sudo docker network create pos-network", "#7ec8e3")
                net_proc = subprocess.run(
                    ["sudo", "-k", "-S", "docker", "network", "create",
                     "--driver", "bridge", "pos-network"],
                    input=sudo_password + "\n",
                    text=True,
                    capture_output=True,
                    cwd=str(REPO_DIR),
                    env=env,
                )
                # Exit code 1 with "already exists" is expected on re-installs.
                if net_proc.returncode == 0:
                    self._log(self._s3_log, "  Network pos-network created.", "#aaaaaa")
                else:
                    err = (net_proc.stderr or "").strip()
                    if "already exists" in err:
                        self._log(self._s3_log, "  Network pos-network already exists — OK.", "#aaaaaa")
                    else:
                        self._log(self._s3_log, f"  Warning: {err}", C_DANGER)

                # ── Step 1: Pull latest images ────────────────────────────
                self._log(self._s3_log, "")
                self._log(self._s3_log,
                          "▶ sudo docker compose -f docker-compose.prod.yml pull", "#7ec8e3")

                pull_proc = _run_compose_with_progress(["pull"], "Docker-Images werden heruntergeladen")
                if pull_proc is None:
                    return
                if pull_proc.returncode != 0:
                    # returncode < 0 means killed by signal (user clicked Cancel) —
                    # _cancel_deployment() already logged the abort message and
                    # re-enabled nav buttons, so we only act on genuine failures.
                    if pull_proc.returncode > 0:
                        self._log(self._s3_log, t("s3_log_pull_fail"), C_DANGER)
                        self._log(self._s3_log, t("s3_log_tip"), "#aaaaaa")
                        self._set_nav(back=True, next_=True)
                    return

                # ── Step 2: Start / recreate services ─────────────────────
                self._log(self._s3_log, "")
                self._log(self._s3_log,
                          "▶ sudo docker compose -f docker-compose.prod.yml up -d", "#7ec8e3")

                up_proc = _run_compose(["up", "-d"])
                if up_proc is None:
                    return

                if up_proc.returncode == 0:
                    port = _read_env_keys(["POS_PUBLIC_PORT"]).get(
                        "POS_PUBLIC_PORT", "80")
                    self._log(self._s3_log, "")
                    self._log(self._s3_log, t("s3_log_success"), C_SUCCESS)
                    self._log(self._s3_log,
                              t("s3_log_url", port=port), "#7ec8e3")

                    def _finish():
                        self._btn_next.configure(
                            text=t("btn_done"),
                            state=tk.NORMAL,
                            bg=C_SUCCESS,
                            command=self.root.destroy,
                        )
                    self.root.after(0, _finish)
                elif up_proc.returncode > 0:
                    # Genuine failure (not user-cancelled)
                    self._log(self._s3_log, t("s3_log_fail"), C_DANGER)
                    self._log(self._s3_log, t("s3_log_tip"), "#aaaaaa")
                    self._set_nav(back=True, next_=True)
            finally:
                self.root.after(0, lambda: self._btn_cancel.pack_forget())
                try:
                    if self._deploy_log_file is not None:
                        self._deploy_log_file.close()
                except OSError:
                    pass
                self._deploy_log_file = None
                self._deploy_proc = None

        threading.Thread(target=task, daemon=True).start()


# ─────────────────────────────────────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────────────────────────────────────

def _check_prerequisites(*, skip_setup: bool = False) -> list[str]:
    """Return a list of human-readable problems, empty if all OK."""
    problems: list[str] = []
    for lang in ("de", "en", "ru"):
        if not (LOCALES_DIR / f"{lang}.json").is_file():
            problems.append(f"Locale file missing: locales/{lang}.json")
    if not skip_setup:
        if not PROVISION_PY.is_file():
            problems.append(t("err_no_provision", dir=str(REPO_DIR)))
        if not ENV_EXAMPLE.is_file():
            problems.append(t("err_no_envexample", dir=str(REPO_DIR)))
    else:
        # In skip mode, .env must already exist (created by a previous run)
        if not ENV_FILE.is_file():
            problems.append(t("skip_no_env"))
    if not COMPOSE_FILE.is_file():
        problems.append(t("err_no_compose", dir=str(REPO_DIR)))
    if not shutil.which("sudo"):
        problems.append(t("err_no_sudo"))
    if not shutil.which("docker"):
        problems.append(t("err_no_docker"))
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="POS System Installation Wizard")
    parser.add_argument(
        "--skip-setup", action="store_true",
        help="Skip provisioning (step 1) and Docker login (step 2), "
             "jump directly to deployment.",
    )
    args = parser.parse_args()

    # Run a short prerequisite check before opening the GUI
    problems = _check_prerequisites(skip_setup=args.skip_setup)

    root = tk.Tk()
    root.withdraw()  # hide until ready

    if problems:
        msg = t("err_prereq_msg", items="\n".join(f"  \u2022 {p}" for p in problems))
        messagebox.showerror(t("err_prereq_title"), msg, parent=root)
        root.destroy()
        sys.exit(1)

    root.deiconify()
    InstallerApp(root, skip_setup=args.skip_setup)
    root.mainloop()


if __name__ == "__main__":
    main()
