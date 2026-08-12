# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Sudo session handling.

The customer types the sudo password once and can then run a short series of
repairs without retyping it on a touchscreen. That convenience is bounded on
three sides: an absolute lifetime, a much shorter idle timeout, and a visible
lock button. In practice the password is held for seconds, not minutes.

The password never reaches disk, never reaches a log line, never reaches the
browser and never reaches the support report. It lives in a bytearray so it can
be overwritten on expiry rather than left to the garbage collector, and the
systemd unit sets LimitCORE=0 so a crash cannot spill it into a core file.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass

LOG = logging.getLogger("kassio.auth")

SESSION_LIFETIME_SECONDS = 300
SESSION_IDLE_SECONDS = 120
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 900


@dataclass
class Session:
    token: str
    secret: bytearray
    created_at: float
    last_used_at: float

    def expired(self, now: float) -> bool:
        return (now - self.created_at > SESSION_LIFETIME_SECONDS
                or now - self.last_used_at > SESSION_IDLE_SECONDS)


class LockedOut(Exception):
    def __init__(self, seconds_left: int):
        super().__init__("locked out")
        self.seconds_left = seconds_left


class SessionStore:
    """Holds at most a handful of sudo sessions for a single local operator."""

    def __init__(self, privileged, clock=time.monotonic):
        self._privileged = privileged
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict = {}
        self._failed_attempts = 0
        self._locked_until = 0.0

    # -- lockout ----------------------------------------------------------
    def _lockout_remaining(self, now: float) -> int:
        return max(0, int(self._locked_until - now))

    def lockout_remaining(self) -> int:
        with self._lock:
            return self._lockout_remaining(self._clock())

    # -- lifecycle --------------------------------------------------------
    def _purge(self, now: float) -> None:
        for token in [t for t, s in self._sessions.items() if s.expired(now)]:
            self._destroy(token)

    def _destroy(self, token: str) -> None:
        session = self._sessions.pop(token, None)
        if session is not None:
            _wipe(session.secret)

    def create(self, password: str):
        """Verify a password and open a session. Raises LockedOut when barred."""
        now = self._clock()
        with self._lock:
            self._purge(now)
            remaining = self._lockout_remaining(now)
            if remaining > 0:
                raise LockedOut(remaining)

        secret = bytearray((password or "").encode("utf-8"))
        outcome = self._privileged.verify_password(bytes(secret))

        with self._lock:
            if not outcome.ok:
                _wipe(secret)
                if outcome.error_key == "error.bad_password":
                    self._failed_attempts += 1
                    LOG.warning("sudo verification failed (%d/%d)",
                                self._failed_attempts, MAX_FAILED_ATTEMPTS)
                    if self._failed_attempts >= MAX_FAILED_ATTEMPTS:
                        self._locked_until = self._clock() + LOCKOUT_SECONDS
                        self._failed_attempts = 0
                        LOG.warning("sudo verification locked out for %d seconds",
                                    LOCKOUT_SECONDS)
                return None, outcome
            self._failed_attempts = 0
            token = secrets.token_urlsafe(32)
            stamp = self._clock()
            self._sessions[token] = Session(token, secret, stamp, stamp)
            LOG.info("sudo session opened")
            return token, outcome

    def secret_for(self, token: str):
        """Return the password for a live session and refresh its idle timer."""
        if not token:
            return None
        now = self._clock()
        with self._lock:
            self._purge(now)
            session = self._sessions.get(token)
            if session is None:
                return None
            session.last_used_at = now
            return bytes(session.secret)

    def status(self, token: str) -> dict:
        now = self._clock()
        with self._lock:
            self._purge(now)
            session = self._sessions.get(token) if token else None
            return {
                "active": session is not None,
                "expires_in": int(max(0, SESSION_LIFETIME_SECONDS
                                      - (now - session.created_at))) if session else 0,
                "idle_timeout": SESSION_IDLE_SECONDS,
                "locked_for": self._lockout_remaining(now),
            }

    def revoke(self, token: str) -> None:
        with self._lock:
            self._destroy(token)
            LOG.info("sudo session locked by operator")

    def revoke_all(self) -> None:
        with self._lock:
            for token in list(self._sessions):
                self._destroy(token)


def _wipe(buffer: bytearray) -> None:
    for index in range(len(buffer)):
        buffer[index] = 0
    del buffer[:]
