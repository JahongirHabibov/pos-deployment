# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
The service's side of the privilege boundary.

Read verbs are attempted unprivileged first and only escalate when the helper
reports that root is required (exit code 3). That ordering is what keeps the
tool useful on a machine where the sudoers drop-in is missing or the admin user
was changed: everything that does not truly need root keeps working.

Write verbs always go through ``sudo -S -k``. The ``-k`` matters: it stops sudo
from populating its own timestamp cache, so the five-minute session lives only
inside this process and never widens sudo for other processes of the same user.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field

LOG = logging.getLogger("kassio.privileged")

EXIT_OK, EXIT_USAGE, EXIT_NEEDS_ROOT, EXIT_NO_TOOL, EXIT_FAILED = 0, 2, 3, 4, 5

DEFAULT_HELPER = "/opt/kassio-diagnostics/bin/diag-helper"


@dataclass
class Outcome:
    ok: bool
    data: object = None
    error_key: str = ""
    detail: str = ""
    params: dict = field(default_factory=dict)


def _parse(stdout: str) -> dict:
    import json
    try:
        payload = json.loads(stdout or "{}")
    except (ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


class Privileged:
    def __init__(self, helper_path: str = DEFAULT_HELPER, sudo_path: str = "sudo"):
        self.helper_path = helper_path
        self.sudo_path = sudo_path

    # -- plumbing ---------------------------------------------------------
    def _run(self, argv, timeout, stdin_bytes=None):
        try:
            proc = subprocess.run(
                argv, input=stdin_bytes, capture_output=True,
                timeout=timeout, check=False,
            )
        except FileNotFoundError:
            return None, "", "command not found"
        except subprocess.TimeoutExpired:
            return None, "", "timeout"
        except OSError as exc:
            return None, "", str(exc)
        return (proc.returncode,
                proc.stdout.decode("utf-8", "replace"),
                proc.stderr.decode("utf-8", "replace"))

    def _outcome_from(self, code, stdout, stderr) -> Outcome:
        payload = _parse(stdout)
        if code == EXIT_OK and payload.get("ok"):
            return Outcome(True, payload.get("data"))
        if code == EXIT_NO_TOOL:
            return Outcome(False, None, "error.tool_missing",
                           str(payload.get("detail", ""))[:500],
                           {"tool": str(payload.get("detail", ""))[:60]})
        if code == EXIT_USAGE:
            return Outcome(False, None, "error.rejected_argument",
                           str(payload.get("error", ""))[:500])
        if code == EXIT_NEEDS_ROOT:
            return Outcome(False, None, "error.needs_password", "")
        detail = str(payload.get("detail") or payload.get("error") or stderr)[:2000]
        return Outcome(False, None, "error.command_failed", detail)

    # -- read -------------------------------------------------------------
    def read(self, verb: str, *args, timeout: int = 40) -> Outcome:
        argv = [self.helper_path, "read", verb] + [str(a) for a in args]
        code, out, err = self._run(argv, timeout)
        if code is None:
            LOG.warning("helper unavailable for read %s: %s", verb, err)
            return Outcome(False, None, "error.helper_unavailable", err[:500])
        if code != EXIT_NEEDS_ROOT:
            return self._outcome_from(code, out, err)

        # Needs root: retry through the NOPASSWD sudoers entry.
        if shutil.which(self.sudo_path) is None:
            return Outcome(False, None, "error.sudo_missing", "")
        code, out, err = self._run(
            [self.sudo_path, "-n", "--", self.helper_path, "read", verb]
            + [str(a) for a in args], timeout)
        if code is None:
            return Outcome(False, None, "error.helper_unavailable", err[:500])
        if code == 1 and "password is required" in err.lower():
            return Outcome(False, None, "error.sudoers_rule_missing", err[:500])
        return self._outcome_from(code, out, err)

    # -- act --------------------------------------------------------------
    def act(self, verb: str, *args, password: bytes, stdin_data: bytes = b"",
            timeout: int = 150) -> Outcome:
        """Run a mutating verb. The password is consumed by sudo, not the verb."""
        if shutil.which(self.sudo_path) is None:
            return Outcome(False, None, "error.sudo_missing", "")
        argv = [self.sudo_path, "-S", "-k", "-p", "", "--",
                self.helper_path, "act", verb] + [str(a) for a in args]
        payload = bytes(password) + b"\n" + (stdin_data or b"")
        started = time.monotonic()
        code, out, err = self._run(argv, timeout, stdin_bytes=payload)
        LOG.info("act %s finished rc=%s in %.1fs", verb, code, time.monotonic() - started)
        if code is None:
            return Outcome(False, None, "error.helper_unavailable", err[:500])
        if code == 1 and _looks_like_bad_password(err):
            return Outcome(False, None, "error.bad_password", "")
        return self._outcome_from(code, out, err)

    def verify_password(self, password: bytes) -> Outcome:
        """Confirm a password without any side effect on the system."""
        return self.act("noop", password=password, timeout=30)


def _looks_like_bad_password(stderr: str) -> bool:
    lowered = (stderr or "").lower()
    return ("incorrect password" in lowered
            or "sorry, try again" in lowered
            or "authentication failure" in lowered
            or "no password was provided" in lowered)


def default_helper_path() -> str:
    """Prefer the installed helper, fall back to the one next to the source."""
    if os.path.exists(DEFAULT_HELPER):
        return DEFAULT_HELPER
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local = os.path.join(here, "bin", "diag-helper")
    return local if os.path.exists(local) else DEFAULT_HELPER
