# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Isolated execution of the individual checks.

The rule this module exists to enforce: one broken check must never cost the
customer the other twenty. A check that raises, hangs or cannot even be imported
becomes a single grey card with an explanation, and everything else still shows
its result. That is why every check returns a value instead of raising, why each
one runs under its own timeout, and why the runner itself has no failure path
that can propagate outward.

Checks never format text. They return keys and parameters; translation happens
at the edge, so the same result can be rendered in three languages and written
into the support report without a second code path.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field

LOG = logging.getLogger("kassio.runner")

OK, WARN, FAIL, UNKNOWN, UNAVAILABLE = "ok", "warn", "fail", "unknown", "unavailable"

CHECK_TIMEOUT_SECONDS = 45
MAX_PARALLEL_CHECKS = 8


@dataclass
class CheckResult:
    id: str
    group: str
    status: str
    title_key: str
    message_key: str = ""
    params: dict = field(default_factory=dict)
    actual: str = ""
    expected: str = ""
    actions: list = field(default_factory=list)
    details: str = ""
    data: dict = field(default_factory=dict)
    duration_ms: int = 0

    def as_dict(self) -> dict:
        return {
            "id": self.id, "group": self.group, "status": self.status,
            "title_key": self.title_key, "message_key": self.message_key,
            "params": self.params, "actual": self.actual, "expected": self.expected,
            "actions": self.actions, "details": self.details, "data": self.data,
            "duration_ms": self.duration_ms,
        }


_REGISTRY: dict = {}
_REGISTRY_ORDER: list = []


def check(group: str, check_id: str, title_key: str):
    """Register a check. The registry is the only place groups are defined."""
    def decorator(function):
        entry = {"group": group, "id": check_id, "title_key": title_key,
                 "function": function}
        _REGISTRY.setdefault(group, []).append(entry)
        if group not in _REGISTRY_ORDER:
            _REGISTRY_ORDER.append(group)
        function.check_id = check_id
        function.check_group = group
        return function
    return decorator


def groups() -> list:
    return list(_REGISTRY_ORDER)


def registered(group: str = "") -> list:
    if group:
        return list(_REGISTRY.get(group, []))
    return [entry for name in _REGISTRY_ORDER for entry in _REGISTRY[name]]


class Snapshot:
    """Caches helper reads for the duration of one run, at most once each."""

    def __init__(self, privileged):
        self._privileged = privileged
        self._lock = threading.Lock()
        self._values: dict = {}
        self._locks: dict = {}

    def read(self, verb: str, *args):
        key = (verb,) + tuple(str(a) for a in args)
        with self._lock:
            if key in self._values:
                return self._values[key]
            per_key = self._locks.setdefault(key, threading.Lock())
        with per_key:
            with self._lock:
                if key in self._values:
                    return self._values[key]
            outcome = self._privileged.read(verb, *args)
            with self._lock:
                self._values[key] = outcome
            return outcome


class Context:
    """Everything a check may look at, assembled once per run."""

    def __init__(self, privileged, config_document, config_findings,
                 deployment_dir, env, pos_api=None):
        self.privileged = privileged
        self.snapshot = Snapshot(privileged)
        self.config = config_document
        self.config_findings = config_findings or []
        self.deployment_dir = deployment_dir
        self.env = env or {}
        self.pos_api = pos_api

    def read(self, verb: str, *args):
        return self.snapshot.read(verb, *args)


def _failure_result(entry: dict, status: str, message_key: str,
                    details: str, duration_ms: int) -> CheckResult:
    return CheckResult(
        id=entry["id"], group=entry["group"], status=status,
        title_key=entry["title_key"], message_key=message_key,
        details=details, duration_ms=duration_ms,
    )


def _execute(entry: dict, context) -> list:
    started = time.monotonic()
    try:
        produced = entry["function"](context)
    except Exception as exc:  # a check must never take the run down with it
        LOG.exception("check %s raised", entry["id"])
        return [_failure_result(entry, UNKNOWN, "check.crashed",
                                f"{exc!r}\n{traceback.format_exc()[-1500:]}",
                                int((time.monotonic() - started) * 1000))]
    duration = int((time.monotonic() - started) * 1000)
    results = produced if isinstance(produced, list) else [produced]
    cleaned = []
    for result in results:
        if not isinstance(result, CheckResult):
            continue
        if not result.duration_ms:
            result.duration_ms = duration
        cleaned.append(result)
    if not cleaned:
        return [_failure_result(entry, UNKNOWN, "check.no_result", "", duration)]
    return cleaned


def run(context, selected_groups=None) -> list:
    """Run checks in parallel. Returns results; never raises."""
    entries = []
    for group in groups():
        if selected_groups and group not in selected_groups:
            continue
        entries.extend(_REGISTRY.get(group, []))
    if not entries:
        return []

    results = []
    pool = None
    try:
        pool = ThreadPoolExecutor(max_workers=min(MAX_PARALLEL_CHECKS, len(entries)))
        futures = {pool.submit(_execute, entry, context): entry for entry in entries}
        # One shared deadline rather than one timeout per future: waiting on
        # each in turn would let a slow check spend the full timeout and the
        # next one spend it again, so the caller's worst case would grow with
        # the number of checks instead of staying bounded.
        deadline = time.monotonic() + CHECK_TIMEOUT_SECONDS
        for future, entry in futures.items():
            remaining = max(0.0, deadline - time.monotonic())
            try:
                results.extend(future.result(timeout=remaining))
            except FutureTimeout:
                LOG.warning("check %s exceeded the %ss budget", entry["id"],
                            CHECK_TIMEOUT_SECONDS)
                results.append(_failure_result(
                    entry, UNKNOWN, "check.timed_out", "",
                    CHECK_TIMEOUT_SECONDS * 1000))
            except Exception as exc:  # noqa: BLE001 - defensive, see docstring
                LOG.exception("collecting %s failed", entry["id"])
                results.append(_failure_result(entry, UNKNOWN, "check.crashed",
                                               repr(exc), 0))
    except Exception:  # pool creation itself failed — still return something
        LOG.exception("runner pool failed; falling back to sequential execution")
        for entry in entries:
            results.extend(_execute(entry, context))
    finally:
        if pool is not None:
            # wait=False is the point of the timeout: exiting a "with" block
            # would join every worker, so a hung check would still hold the
            # answer back for as long as it liked. Queued work is cancelled;
            # a check already running finishes on its own and is bounded by the
            # command timeouts underneath it.
            pool.shutdown(wait=False, cancel_futures=True)

    order = {entry["id"]: index for index, entry in enumerate(entries)}
    results.sort(key=lambda result: (order.get(result.id, 999), result.id))
    return results


def worst_status(results) -> str:
    ranking = {OK: 0, UNAVAILABLE: 1, UNKNOWN: 2, WARN: 3, FAIL: 4}
    worst = OK
    for result in results:
        if ranking.get(result.status, 0) > ranking.get(worst, 0):
            worst = result.status
    return worst


def summarise(results) -> dict:
    """Per-group roll-up for the overview tab."""
    summary = {}
    for result in results:
        bucket = summary.setdefault(result.group, {"counts": {}, "status": OK})
        bucket["counts"][result.status] = bucket["counts"].get(result.status, 0) + 1
    for group, bucket in summary.items():
        bucket["status"] = worst_status([r for r in results if r.group == group])
    return summary
