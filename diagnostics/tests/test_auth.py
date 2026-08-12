# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Sudo session lifetime, idle timeout and lockout.

A fake clock is used throughout so the tests state the intent — "two minutes
later" — instead of sleeping through it.
"""

import pytest

from kassio_diagnostics import auth
from kassio_diagnostics.privileged import Outcome


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakePrivileged:
    def __init__(self, correct="right"):
        self.correct = correct
        self.calls = 0

    def verify_password(self, password):
        self.calls += 1
        if password.decode("utf-8") == self.correct:
            return Outcome(True, {"verified": True})
        return Outcome(False, None, "error.bad_password", "")


def test_a_correct_password_opens_a_session():
    store = auth.SessionStore(FakePrivileged(), clock=Clock())
    token, outcome = store.create("right")
    assert token and outcome.ok
    assert store.secret_for(token) == b"right"


def test_a_wrong_password_opens_nothing():
    store = auth.SessionStore(FakePrivileged(), clock=Clock())
    token, outcome = store.create("wrong")
    assert token is None
    assert outcome.error_key == "error.bad_password"


def test_the_session_expires_after_its_lifetime():
    clock = Clock()
    store = auth.SessionStore(FakePrivileged(), clock=clock)
    token, _ = store.create("right")
    clock.advance(auth.SESSION_LIFETIME_SECONDS + 1)
    assert store.secret_for(token) is None


def test_the_session_expires_when_left_idle():
    clock = Clock()
    store = auth.SessionStore(FakePrivileged(), clock=clock)
    token, _ = store.create("right")
    clock.advance(auth.SESSION_IDLE_SECONDS + 1)
    assert store.secret_for(token) is None


def test_use_refreshes_the_idle_timer_but_not_the_lifetime():
    clock = Clock()
    store = auth.SessionStore(FakePrivileged(), clock=clock)
    token, _ = store.create("right")
    # Two idle periods pass without the session dying, because each use resets
    # the idle timer. Their sum stays inside the absolute lifetime.
    for _ in range(2):
        clock.advance(auth.SESSION_IDLE_SECONDS - 10)
        assert store.secret_for(token) == b"right"
    # The absolute lifetime is the ceiling and use does not extend it.
    clock.advance(auth.SESSION_LIFETIME_SECONDS)
    assert store.secret_for(token) is None


def test_lockout_after_repeated_failures():
    clock = Clock()
    store = auth.SessionStore(FakePrivileged(), clock=clock)
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        store.create("wrong")
    with pytest.raises(auth.LockedOut) as raised:
        store.create("right")
    assert raised.value.seconds_left > 0


def test_lockout_ends_after_its_period():
    clock = Clock()
    privileged = FakePrivileged()
    store = auth.SessionStore(privileged, clock=clock)
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        store.create("wrong")
    clock.advance(auth.LOCKOUT_SECONDS + 1)
    token, _ = store.create("right")
    assert token


def test_a_correct_password_clears_the_failure_count():
    clock = Clock()
    store = auth.SessionStore(FakePrivileged(), clock=clock)
    for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
        store.create("wrong")
    store.create("right")
    for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
        store.create("wrong")
    token, _ = store.create("right")   # still not locked out
    assert token


def test_revoking_wipes_the_password_from_memory():
    store = auth.SessionStore(FakePrivileged(), clock=Clock())
    token, _ = store.create("right")
    session = store._sessions[token]
    buffer = session.secret
    store.revoke(token)
    assert store.secret_for(token) is None
    assert bytes(buffer) == b""


def test_status_reports_an_inactive_session_for_an_unknown_token():
    store = auth.SessionStore(FakePrivileged(), clock=Clock())
    assert store.status("nope")["active"] is False


def test_no_secret_for_an_empty_token():
    store = auth.SessionStore(FakePrivileged(), clock=Clock())
    store.create("right")
    assert store.secret_for("") is None
