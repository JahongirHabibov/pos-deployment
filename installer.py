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
import urllib.error
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

# ── Paths ─────────────────────────────────────────────────────────────────────
REPO_DIR        = Path(__file__).parent.resolve()
ENV_EXAMPLE     = REPO_DIR / ".env.example"
ENV_FILE        = REPO_DIR / ".env"
PROVISION_PY    = REPO_DIR / "provision.py"
COMPOSE_FILE    = REPO_DIR / "docker-compose.prod.yml"
LOCALES_DIR     = REPO_DIR / "locales"
POS_AUTH_FILE   = Path.home() / ".docker" / "pos-auth.json"
KIOSK_AGENT_DIR = REPO_DIR / "kiosk-agent"
KIOSK_AGENT_INSTALL = KIOSK_AGENT_DIR / "install.sh"

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

class AuthFileError(Exception):
    """User-facing validation error for Docker auth bridge files."""

    def __init__(self, message_key: str, path: Path, detail: str = "") -> None:
        super().__init__(message_key)
        self.message_key = message_key
        self.path = path
        self.detail = detail

    def translated(self) -> str:
        return t(self.message_key, path=str(self.path), detail=self.detail)


def _ghcr_auth_entry_present(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    auths = data.get("auths", {})
    if not isinstance(auths, dict):
        return False
    ghcr = auths.get("ghcr.io")
    if not isinstance(ghcr, dict):
        return False
    return bool(ghcr.get("auth") or ghcr.get("identitytoken"))


def _validate_ghcr_auth_file(path: Path) -> None:
    """Validate that *path* is a Docker config JSON with GHCR credentials."""
    if not path.exists():
        raise AuthFileError("s2_auth_file_missing", path)
    if path.is_dir():
        raise AuthFileError("s2_auth_file_is_directory", path)
    if not path.is_file():
        raise AuthFileError("s2_auth_file_not_file", path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthFileError("s2_auth_file_invalid_json", path, str(exc)) from exc
    except OSError as exc:
        raise AuthFileError("s2_auth_file_unreadable", path, str(exc)) from exc
    if not _ghcr_auth_entry_present(data):
        raise AuthFileError("s2_auth_file_missing_ghcr", path)


def _find_ghcr_auth_file() -> tuple[Path | None, AuthFileError | None]:
    """Return a usable GHCR auth file, preferring the updater bridge file."""
    first_error: AuthFileError | None = None
    for path in (POS_AUTH_FILE, Path.home() / ".docker" / "config.json"):
        try:
            _validate_ghcr_auth_file(path)
            return path, None
        except AuthFileError as exc:
            if path == POS_AUTH_FILE and path.exists():
                return None, exc
            if first_error is None and path.exists():
                first_error = exc
    return None, first_error


def _patch_auth_file_env(path: Path) -> None:
    _patch_env_keys({"POS_DOCKER_AUTH_FILE": str(path.expanduser().resolve())})


def _ensure_deployment_auth_file(env: dict) -> Path:
    """Ensure compose receives an absolute, valid GHCR auth JSON path."""
    configured = str(env.get("POS_DOCKER_AUTH_FILE", "")).strip()
    if configured:
        auth_path = Path(configured).expanduser()
        if not auth_path.is_absolute():
            raise AuthFileError("s3_auth_file_not_absolute", auth_path)
    else:
        found, error = _find_ghcr_auth_file()
        if found is None:
            raise error or AuthFileError("s2_auth_file_missing", POS_AUTH_FILE)
        auth_path = found

    _validate_ghcr_auth_file(auth_path)
    auth_path = auth_path.resolve()
    _patch_auth_file_env(auth_path)
    env["POS_DOCKER_AUTH_FILE"] = str(auth_path)
    return auth_path


def _prepare_pos_auth_file_for_write() -> None:
    POS_AUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not POS_AUTH_FILE.exists():
        return
    if POS_AUTH_FILE.is_dir():
        try:
            POS_AUTH_FILE.rmdir()
        except OSError as exc:
            raise AuthFileError(
                "s2_auth_file_directory_not_empty",
                POS_AUTH_FILE,
                str(exc),
            ) from exc
        return
    if not POS_AUTH_FILE.is_file():
        raise AuthFileError("s2_auth_file_not_file", POS_AUTH_FILE)

def _write_pos_auth_json(user: str, token: str) -> None:
    """Write ~/.docker/pos-auth.json with base64 auth for ghcr.io.

    This credential-bridge file is mounted into the updater container
    as /root/.docker/config.json so it can pull images from GHCR
    without needing access to docker-credential-desktop.exe.
    """
    _prepare_pos_auth_file_for_write()
    auth_b64 = base64.b64encode(f"{user}:{token}".encode()).decode()
    data = {"auths": {"ghcr.io": {"auth": auth_b64}}}
    POS_AUTH_FILE.write_text(
        json.dumps(data, indent=2) + "\n", encoding="utf-8"
    )
    POS_AUTH_FILE.chmod(0o600)
    _validate_ghcr_auth_file(POS_AUTH_FILE)


# Manifest service name → the installer field it prefills.
_MANIFEST_FIELDS = {
    "backend": "image_backend",
    "frontend": "image_frontend",
    "image-service": "image_service",
    "updater": "image_updater",
    "backup": "image_backup",
}

# Public deployment repo that hosts the rolling manifest.json. Baked in so a
# FIRST install — where no .env exists yet — still prefills the IMAGE_* fields;
# a concrete DEPLOYMENT_REPO in .env always wins over this default.
DEFAULT_DEPLOYMENT_REPO = "JahongirHabibov/pos-deployment"

# A GitHub "<owner>/<name>" pair — deliberately strict so template placeholders
# such as "<org>/pos-deployment" are rejected instead of fetched (and 404'd).
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _is_usable_repo(repo: str) -> bool:
    """True when *repo* is a concrete <owner>/<name>, not a template placeholder."""
    return bool(_REPO_RE.match(str(repo).strip()))


def _resolve_deployment_repo(value: str) -> str:
    """Return the repo to read manifest.json from: operator value, else default."""
    value = str(value).strip()
    return value if _is_usable_repo(value) else DEFAULT_DEPLOYMENT_REPO


def _manifest_url(repo: str, branch: str) -> str:
    """Raw manifest URL with a cache-buster.

    raw.githubusercontent.com answers with ``cache-control: max-age=300``, so a
    plain URL can serve a manifest up to 5 minutes stale — long enough for an
    installer run started right after a release to miss the newest tags. The
    query string is ignored by the origin but is part of the CDN cache key.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S%f")
    return (f"https://raw.githubusercontent.com/{repo}/{branch}"
            f"/manifest.json?_={stamp}")


def _fetch_manifest(repo: str) -> tuple[dict, str]:
    """Fetch the rolling manifest.json from the deployment repo's default branch.

    manifest.json is the single machine-readable release source: CI writes one
    entry per service, and the updater sidecar reads the same file. Per-service
    release tags (backend-v1.4.2) made the old Releases/Tags API path useless —
    it returned a flat list that could not be mapped back onto IMAGE_* fields.

    Returns (manifest, "") on success and ({}, reason) on failure. The reason is
    surfaced in the GUI so a deployer sees WHY no tags showed up (wrong repo,
    offline, proxy) instead of a bare "could not retrieve versions".
    """
    if not _is_usable_repo(repo):
        return {}, f"invalid repo '{repo}'"
    reason = ""
    for branch in ("main", "master"):
        try:
            req = urllib.request.Request(
                _manifest_url(repo, branch),
                headers={
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            if isinstance(data, dict) and isinstance(data.get("services"), dict):
                return data, ""
            detail = "manifest.json has no 'services' object"
        except urllib.error.HTTPError as exc:
            detail = f"HTTP {exc.code}"
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)
        # Keep the FIRST failure: "main" is the real default branch, so its
        # reason is the diagnostic one — the "master" retry always 404s.
        reason = reason or f"{branch}: {detail}"
    return {}, reason


def _image_tag(image_ref: str) -> str:
    """Return the tag portion of a full image ref (part after the last ':').

    Falls back to the whole ref when it carries no tag. A registry port
    (host:5000/repo:tag) is unaffected — only the last ':' is split.
    """
    ref = str(image_ref).strip()
    if ":" in ref:
        return ref.rsplit(":", 1)[1]
    return ref


def _manifest_service_rows(manifest: dict) -> list[dict[str, str]]:
    """Map the manifest onto ordered rows for the IMAGE_* fields.

    Returns one dict per service that exists in BOTH the manifest and
    _MANIFEST_FIELDS, in _MANIFEST_FIELDS order, each with:
      service — manifest service name (e.g. "image-service")
      field   — installer field key (e.g. "image_service")
      image   — full image ref incl. tag
      tag     — tag portion only (for display)
    Services missing from the manifest are skipped (their field is left as-is).
    """
    rows: list[dict[str, str]] = []
    services = manifest.get("services", {})
    if not isinstance(services, dict):
        return rows
    for service, field in _MANIFEST_FIELDS.items():
        entry = services.get(service)
        if not isinstance(entry, dict):
            continue
        image = str(entry.get("image", "")).strip()
        if not image:
            continue
        rows.append({
            "service": service,
            "field": field,
            "image": image,
            "tag": _image_tag(image),
        })
    return rows


def _has_ghcr_credentials() -> tuple[bool, str]:
    """Check whether GHCR credentials are already stored in a Docker config file.

    Inspects ~/.docker/pos-auth.json first, then ~/.docker/config.json.
    Returns (found, human-readable source path).
    Only plain ``auths`` entries are considered; credential-helper entries
    are not decoded (no plain-text token available in that case).
    """
    path, _ = _find_ghcr_auth_file()
    return (True, str(path)) if path else (False, "")


# ── Kiosk power agent ────────────────────────────────────────────────────────
# The agent powers the host off on request, which no container can do, so it is
# installed on the machine itself instead of shipped as an image. Only terminals
# that run this repo are covered here; thin clients get it from their kiosk
# image (see kiosk-agent/README.md).
KIOSK_AGENT_ENV_KEY = "POS_KIOSK_AGENT"


def _step3_row_offsets(show_sudo: bool, show_kiosk: bool) -> tuple[int, int, int]:
    """Grid-row offsets for the optional widgets on step 3.

    Each optional block takes two rows (input plus its hint/toggle). The log
    widget has to start below whatever is actually shown — get this wrong and
    the log overlaps the sudo field or the kiosk checkbox.

    Returns (sudo_offset, kiosk_offset, total).
    """
    sudo = 2 if show_sudo else 0
    kiosk = 2 if show_kiosk else 0
    return sudo, kiosk, sudo + kiosk


def _kiosk_agent_origin(port: str) -> str:
    """Browser origin of the POS on this host — the only origin the agent accepts.

    The kiosk session opens the POS on the machine itself, so the origin is
    always localhost; only a non-default public port has to be spelled out.
    """
    port = (port or "80").strip()
    if port in ("", "80"):
        return "http://localhost"
    return f"http://localhost:{port}"


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
    ENV_FILE.chmod(0o600)  # .env holds secrets — keep it owner-only


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

        DEPLOYMENT_REPO is special: it is always resolved, even without a .env,
        so a first install still knows where to read manifest.json from.
        """
        if not ENV_FILE.is_file():
            self._data["deployment_repo"] = DEFAULT_DEPLOYMENT_REPO
            return
        env_vals = _read_env_keys([
            "IMAGE_BACKEND", "IMAGE_FRONTEND", "IMAGE_IMAGE_SERVICE",
            "IMAGE_UPDATER", "IMAGE_BACKUP", "DEPLOYMENT_REPO",
            "HOST_COMPOSE_PROJECT_DIR",
            "PROVISION_DONE", "POS_WSL2",
        ])
        mapping = {
            "image_backend":      "IMAGE_BACKEND",
            "image_frontend":     "IMAGE_FRONTEND",
            "image_service":      "IMAGE_IMAGE_SERVICE",
            "image_updater":      "IMAGE_UPDATER",
            "image_backup":       "IMAGE_BACKUP",
            "deployment_repo":    "DEPLOYMENT_REPO",
            "host_compose_dir":   "HOST_COMPOSE_PROJECT_DIR",
        }
        for data_key, env_key in mapping.items():
            value = env_vals.get(env_key, "")
            if value:
                self._data[data_key] = value
        # A missing key, or the "<org>/pos-deployment" placeholder copied from
        # .env.example, must not silently disable the manifest prefill.
        self._data["deployment_repo"] = _resolve_deployment_repo(
            self._data.get("deployment_repo", "")
        )
        # Auto-check "skip provisioning" if PROVISION_DONE=true
        if env_vals.get("PROVISION_DONE", "").lower() == "true":
            self._data["_already_prov"] = "1"
        if env_vals.get("POS_WSL2", "").lower() == "true":
            self._data["wsl2"] = "1"

    def _reload_provisioned_data(self) -> None:
        """Re-read selected values from .env into self._data after step 1."""
        if not ENV_FILE.is_file():
            return
        vals = _read_env_keys(["PROVISION_DONE", "POS_WSL2"])
        # Auto-check "skip provisioning" if PROVISION_DONE=true
        if vals.get("PROVISION_DONE", "").lower() == "true":
            self._data["_already_prov"] = "1"
        if vals.get("POS_WSL2", "").lower() == "true":
            self._data["wsl2"] = "1"

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
        if hasattr(self, "_s1_already_prov"):
            self._data["_already_prov"] = "1" if self._s1_already_prov.get() else ""
        if hasattr(self, "_s1_wsl2"):
            self._data["wsl2"] = "1" if self._s1_wsl2.get() else ""

    def _save_step2_state(self) -> None:
        for attr, data_key in (
            ("_s2_user",        "ghcr_user"),
            ("_s2_token",       "ghcr_token"),
            ("_s2_sudo",        "sudo_password"),
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

        # ── Release versions box (top) ────────────────────────────────────
        self._s1_tags_frame = tk.Frame(c, bg="#e8f5e9", relief=tk.GROOVE, bd=1)
        self._s1_tags_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._s1_tags_frame.grid_remove()
        self._s1_tags_hint = tk.Label(
            self._s1_tags_frame, text="", bg="#e8f5e9", fg="#1b5e20",
            font=("Segoe UI", self._get_font_size(9), "bold"),
            anchor="w", padx=8, pady=4,
        )
        self._s1_tags_hint.pack(fill=tk.X)
        self._s1_tags_fetch_after_id: str | None = None

        # ── Idee 2: info banner when .env already exists ──────────────────
        if ENV_FILE.is_file():
            tk.Label(
                c,
                text=t("s1_env_prefilled"),
                bg="#e8f5e9", fg="#2e7d32",
                font=("Segoe UI", self._get_font_size(9), "italic"),
                anchor="w", padx=6, pady=3,
                relief=tk.GROOVE, bd=1,
            ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 6))

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
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 4))

        # ── WSL 2 / Docker without Sudo checkbox ─────────────────────────
        self._s1_wsl2 = tk.BooleanVar(
            value=bool(self._data.get("wsl2"))
        )
        self._s1_wsl2_btn = tk.Checkbutton(
            c,
            text=t("s1_chk_wsl2"),
            variable=self._s1_wsl2,
            bg="white", font=("Segoe UI", self._get_font_size(9)),
            anchor="w",
        )
        self._s1_wsl2_btn.grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 8))

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
        # IMAGE_* entry widgets, so a manifest refresh can highlight changed rows.
        self._s1_image_entries: dict[str, tk.Entry] = {}

        current_row = 6
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
            elif key in _MANIFEST_FIELDS.values():
                self._s1_image_entries[key] = entry

            current_row += 2

        # Timezone is configured in-app via the first-run Setup wizard
        # (stored in the database), not written to .env here.

        def _on_repo_change(*_: object) -> None:
            if self._s1_tags_fetch_after_id is not None:
                self.root.after_cancel(self._s1_tags_fetch_after_id)
            repo = self._s1_vars["deployment_repo"].get().strip()
            if _is_usable_repo(repo):
                self._s1_tags_hint.configure(
                    text=t("s1_hint_fetching"), fg="#555555")
                self._s1_tags_frame.grid()
                self._s1_tags_fetch_after_id = self.root.after(
                    800,
                    lambda r=repo: threading.Thread(
                        target=self._fetch_and_show_tags, args=(r,),
                        daemon=True,
                    ).start(),
                )
            else:
                self._s1_tags_hint.configure(text="")
                self._s1_tags_frame.grid_remove()

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
        """Background worker: set the IMAGE_* fields from the rolling manifest.

        The manifest's full image ref (URL + tag) always replaces each field, so
        an empty (new deployment), wrong, or outdated value self-heals to the
        current release. Change detection only drives the per-service notice and
        the highlight — the operator is asked to verify and can still edit a row.
        """
        manifest, error = _fetch_manifest(repo)
        rows = _manifest_service_rows(manifest)
        updated_at = str(manifest.get("updated_at", ""))[:10]

        def _update() -> None:
            if not hasattr(self, "_s1_tags_hint"):
                return
            try:
                self._s1_tags_hint.winfo_exists()
            except tk.TclError:
                return

            if not rows:
                # Name the failure — a silent grey line left deployers guessing
                # whether the repo was wrong, the host offline or CI behind.
                text = t("s1_hint_fetch_err")
                if error:
                    text = f"{text}  ({repo} → {error})"
                self._s1_tags_hint.configure(text=text, fg="#c62828")
                return

            # Reset every IMAGE_* row to the neutral background before re-marking.
            for entry in self._s1_image_entries.values():
                try:
                    entry.configure(bg="white")
                except tk.TclError:
                    pass

            label_width = max(len(row["service"]) for row in rows)
            lines = [t("s1_manifest_header").format(date=updated_at or "—")]
            for row in rows:
                var = self._s1_vars.get(row["field"])
                if var is None:
                    continue
                changed = var.get().strip() != row["image"]
                var.set(row["image"])
                if changed:
                    entry = self._s1_image_entries.get(row["field"])
                    if entry is not None:
                        try:
                            entry.configure(bg="#fffde7")
                        except tk.TclError:
                            pass
                line_key = "s1_manifest_row_changed" if changed else "s1_manifest_row_same"
                lines.append(t(line_key).format(
                    service=row["service"].ljust(label_width),
                    tag=row["tag"],
                ))

            self._s1_tags_hint.configure(text="\n".join(lines), fg="#1b5e20")
            if hasattr(self, "_s1_tags_frame") and self._s1_tags_frame.winfo_exists():
                self._s1_tags_frame.grid()

        self.root.after(0, _update)

    def _run_step1(self) -> None:
        vals = {k: v.get().strip() for k, v in self._s1_vars.items()}

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
                patch_full = {
                    **patch,
                    "POS_WSL2": "true" if self._s1_wsl2.get() else "false"
                }
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

            # Patch IMAGE_* deployment keys into .env
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
                    "PROVISION_DONE":            "true",
                    "POS_WSL2":                  "true" if self._s1_wsl2.get() else "false",
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
        self._s2_sudo_label = tk.Label(c, text=t("s2_lbl_sudo"), bg="white",
                 font=("Segoe UI", self._get_font_size(10), "bold"), width=26, anchor="w")
        self._s2_sudo_label.grid(row=current_row, column=0, sticky="w", pady=(4, 2))
        self._s2_sudo = tk.StringVar(value=self._data.get("sudo_password", ""))
        self._s2_sudo_entry = tk.Entry(
            c, textvariable=self._s2_sudo, width=self._get_entry_width(53), show="*",
            font=("Segoe UI", self._get_font_size(10)), relief=tk.SOLID, bd=1)
        self._s2_sudo_entry.grid(row=current_row, column=1, sticky="ew", padx=(0, 0))
        self._s2_sudo_hint = tk.Label(
            c, text=t("s2_hint_sudo"), bg="white", fg="#000",
            font=("Segoe UI", self._get_font_size(9)), wraplength=self._get_wraplength(550), anchor="nw",
        )
        self._s2_sudo_hint.grid(row=current_row+1, column=1, sticky="ew", pady=(0, 2))
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

        self._s2_status = tk.Label(
            c, text="", bg="white", font=("Segoe UI", self._get_font_size(10)),
            wraplength=self._get_wraplength(750), justify=tk.LEFT)
        self._s2_status.grid(row=current_row, column=0, columnspan=2,
                              sticky="ew", pady=(24, 0))
        c.columnconfigure(1, weight=1)

        # Apply initial toggle state
        if self._s2_already_logged_in.get():
            self._toggle_login_mode()

        # Hide sudo password widgets if WSL 2 mode is active
        if bool(self._data.get("wsl2")):
            self._s2_sudo_label.grid_remove()
            self._s2_sudo_entry.grid_remove()
            self._s2_sudo_hint.grid_remove()
            self._s2_show_sudo_btn.grid_remove()

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

    def _toggle_step3_sudo_visibility(self) -> None:
        if self._s3_sudo_entry is not None:
            self._s3_sudo_entry.configure(
                show="" if self._s3_show_sudo_var.get() else "*"
            )

    def _run_step2(self) -> None:
        # ── skip-login path ───────────────────────────────────────────────
        if self._s2_already_logged_in.get():
            auth_path, auth_error = _find_ghcr_auth_file()
            if auth_path is None:
                messagebox.showerror(
                    t("err_title_missing"),
                    auth_error.translated()
                    if auth_error
                    else t("s2_err_no_creds_for_skip"),
                )
                return
            self._btn_next.configure(state=tk.DISABLED)
            self._btn_back.configure(state=tk.DISABLED)
            self.root.after(0, lambda: self._s2_status.configure(
                text=t("s2_log_skip_login"), fg=C_INFO))

            def task_skip() -> None:
                try:
                    _patch_auth_file_env(auth_path)
                except OSError as exc:
                    err_msg = t("s2_auth_file_env_err", exc=str(exc))
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
        is_wsl2       = bool(self._data.get("wsl2"))
        sudo_password = self._s2_sudo.get() if not is_wsl2 else ""

        if is_wsl2:
            if not user or not token:
                messagebox.showerror(t("err_title_missing"), t("s2_err_missing_wsl2"))
                return
        else:
            if not user or not token or not sudo_password:
                messagebox.showerror(t("err_title_missing"), t("s2_err_missing"))
                return

        self._data["ghcr_user"]          = user
        self._data["ghcr_token"]         = token
        if not is_wsl2:
            self._data["sudo_password"]  = sudo_password
        self._btn_next.configure(state=tk.DISABLED)
        self._btn_back.configure(state=tk.DISABLED)
        self.root.after(0, lambda: self._s2_status.configure(
            text=t("s2_connecting"), fg=C_INFO))

        def task() -> None:
            try:
                if is_wsl2:
                    result = subprocess.run(
                        ["docker", "login", "ghcr.io",
                         "-u", user, "--password-stdin"],
                        input=token,
                        capture_output=True,
                        text=True,
                    )
                else:
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
                    _write_pos_auth_json(user, token)
                except AuthFileError as exc:
                    err_msg = exc.translated()
                    self.root.after(0, lambda m=err_msg: self._s2_status.configure(
                        text=m, fg=C_DANGER))
                    self._set_nav(back=True, next_=True)
                    return
                except OSError as exc:
                    err_msg = t("s2_auth_file_err", exc=str(exc))
                    self.root.after(0, lambda m=err_msg: self._s2_status.configure(
                        text=m, fg=C_DANGER))
                    self._set_nav(back=True, next_=True)
                    return
                try:
                    _patch_auth_file_env(POS_AUTH_FILE)
                except OSError as exc:
                    err_msg = t("s2_auth_file_env_err", exc=str(exc))
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

        # Sudo-Passwort-Feld — nur anzeigen wenn Schritt 2 übersprungen wurde und nicht WSL 2 Modus
        show_sudo = not self._data.get("sudo_password") and not self._data.get("wsl2")
        # Kiosk-Power-Agent — nur wo es einen systemd-Host zum Ausschalten gibt.
        show_kiosk = KIOSK_AGENT_INSTALL.is_file() and not self._data.get("wsl2")
        sudo_row_offset, _kiosk_row_offset, extra_rows = _step3_row_offsets(
            show_sudo, show_kiosk)

        self._s3_sudo_var: tk.StringVar | None = None
        self._s3_sudo_entry: tk.Entry | None = None
        if show_sudo:
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

        # ── Kiosk power agent — optional host-level install ────────────────
        # Hidden under WSL 2: there is no systemd host to power off there.
        self._s3_kiosk_var: tk.BooleanVar | None = None
        if show_kiosk:
            enabled_before = _read_env_keys([KIOSK_AGENT_ENV_KEY]).get(
                KIOSK_AGENT_ENV_KEY, "").strip().lower() == "true"
            self._s3_kiosk_var = tk.BooleanVar(value=enabled_before)
            tk.Checkbutton(
                c, text=t("s3_kiosk_agent"),
                variable=self._s3_kiosk_var,
                bg="white", font=("Segoe UI", self._get_font_size(10)),
                anchor="w", justify=tk.LEFT,
            ).grid(row=next_row+2+sudo_row_offset, column=0, columnspan=2,
                   sticky="w", pady=(6, 0))
            tk.Label(
                c, text=t("s3_kiosk_agent_hint"), bg="white", fg="#555",
                font=("Segoe UI", self._get_font_size(9)),
                wraplength=self._get_wraplength(560), anchor="w", justify=tk.LEFT,
            ).grid(row=next_row+3+sudo_row_offset, column=0, columnspan=2,
                   sticky="w", pady=(0, 4))

        tk.Label(c, text=t("s3_lbl_log"), bg="white",
                 font=("Segoe UI", self._get_font_size(10), "bold")).grid(
            row=next_row+2+extra_rows, column=0, columnspan=2, sticky="w", pady=(4, 2))

        self._s3_log = scrolledtext.ScrolledText(
            c, height=15, width=82, font=("Courier", self._get_font_size(9)),
            state=tk.DISABLED,
            bg="#0d1117", fg="#c9d1d9",
            insertbackground="white",
            relief=tk.SOLID, bd=1,
        )
        self._s3_log.grid(row=next_row+3+extra_rows, column=0, columnspan=2, sticky="ew")
        c.columnconfigure(1, weight=1)
        c.rowconfigure(next_row+3+extra_rows, weight=1)

    def _run_step3(self) -> None:
        self._btn_next.configure(state=tk.DISABLED)  # sofortiges Deaktivieren (verhindert Doppelklick)

        if self._s3_sudo_var is not None:
            sudo_password_in = self._s3_sudo_var.get()
            if not sudo_password_in:
                messagebox.showerror(t("err_title_missing"), t("s3_err_no_sudo"))
                self._btn_next.configure(state=tk.NORMAL)
                return
            self._data["sudo_password"] = sudo_password_in

        # Tk variables must be read on the main thread — hand the value to the
        # worker as plain data.
        if self._s3_kiosk_var is not None:
            self._data["install_kiosk_agent"] = bool(self._s3_kiosk_var.get())

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

                try:
                    auth_path = _ensure_deployment_auth_file(env)
                    self._log(
                        self._s3_log,
                        t("s3_auth_file_ok", path=str(auth_path)),
                        C_SUCCESS,
                    )
                except AuthFileError as exc:
                    self._log(self._s3_log, exc.translated(), C_DANGER)
                    self._set_nav(back=True, next_=True)
                    return
                except OSError as exc:
                    self._log(
                        self._s3_log,
                        t("s2_auth_file_env_err", exc=str(exc)),
                        C_DANGER,
                    )
                    self._set_nav(back=True, next_=True)
                    return

                sudo_password = self._data.get("sudo_password", "")

                def _run_compose(subcmd: list[str]) -> "subprocess.Popen[str] | None":
                    """Run `sudo docker compose -f <file> *subcmd` with live log output.

                    Returns the finished Popen object, or None if docker was not found.
                    Streams stdout/stderr to the log widget and suppresses the sudo
                    password prompt line.
                    """
                    is_wsl2 = bool(self._data.get("wsl2"))
                    if is_wsl2:
                        cmd = ["docker", "compose", "-f", str(COMPOSE_FILE)] + subcmd
                    else:
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
                    if not is_wsl2:
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
                    
                    is_wsl2 = bool(self._data.get("wsl2"))
                    if is_wsl2:
                        cmd = ["docker", "compose", "-f", str(COMPOSE_FILE)] + subcmd
                    else:
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
                    if not is_wsl2:
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

                def _install_kiosk_agent(port: str) -> None:
                    """Install/refresh the local power agent after a successful deploy.

                    Runs last on purpose: a failure here must not read as a failed
                    deployment. Without the agent the POS simply hides its power
                    button, everything else keeps working.
                    """
                    origin = _kiosk_agent_origin(port)
                    self._log(self._s3_log, "")
                    self._log(
                        self._s3_log,
                        f"▶ sudo kiosk-agent/install.sh --origins {origin}",
                        "#7ec8e3",
                    )
                    cmd = ["sudo", "-k", "-S", "bash", str(KIOSK_AGENT_INSTALL),
                           "--origins", origin]
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
                    except OSError as exc:
                        self._log(self._s3_log,
                                  t("s3_kiosk_agent_fail", exc=str(exc)), C_DANGER)
                        return
                    assert p.stdin is not None
                    p.stdin.write(sudo_password + "\n")
                    p.stdin.flush()
                    p.stdin.close()
                    assert p.stdout is not None
                    for line in p.stdout:
                        clean = line.rstrip()
                        if clean.startswith("[sudo]"):
                            continue
                        self._log(self._s3_log, clean)
                    p.wait()
                    if p.returncode == 0:
                        self._log(self._s3_log, t("s3_kiosk_agent_ok"), C_SUCCESS)
                    else:
                        self._log(self._s3_log,
                                  t("s3_kiosk_agent_fail", exc=f"exit {p.returncode}"),
                                  C_DANGER)

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

                    # Remember the choice so a re-run pre-ticks the box.
                    if self._s3_kiosk_var is not None:
                        wanted = bool(self._data.get("install_kiosk_agent"))
                        _patch_env_keys({KIOSK_AGENT_ENV_KEY: "true" if wanted else "false"})
                        if wanted:
                            _install_kiosk_agent(port)

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
    # Check if WSL 2 mode is already enabled in the existing .env
    wsl_mode = False
    if ENV_FILE.is_file():
        try:
            # Simple custom parser to read POS_WSL2 without full parsing code
            for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("POS_WSL2="):
                    wsl_mode = line.partition("=")[2].strip().lower() == "true"
                    break
        except Exception:
            pass

    if not wsl_mode and not shutil.which("sudo"):
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
