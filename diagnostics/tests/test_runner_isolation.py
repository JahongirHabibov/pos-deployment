# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Check isolation.

The promise is that one broken check costs one grey card and nothing else. These
tests break checks on purpose — raising, hanging, returning nothing, returning
the wrong type — and assert that the surrounding results survive intact.
"""

import time

import pytest

from kassio_diagnostics import runner
from kassio_diagnostics.runner import CheckResult


@pytest.fixture
def isolated_registry(monkeypatch):
    monkeypatch.setattr(runner, "_REGISTRY", {}, raising=False)
    monkeypatch.setattr(runner, "_REGISTRY_ORDER", [], raising=False)
    return runner


def register(group, check_id, function):
    runner._REGISTRY.setdefault(group, []).append(
        {"group": group, "id": check_id, "title_key": f"title.{check_id}",
         "function": function})
    if group not in runner._REGISTRY_ORDER:
        runner._REGISTRY_ORDER.append(group)


def healthy(check_id):
    def function(context):
        return CheckResult(id=check_id, group="demo", status=runner.OK,
                           title_key="t", message_key="m")
    return function


def test_a_raising_check_does_not_stop_the_others(isolated_registry):
    def explode(context):
        raise RuntimeError("boom")

    register("demo", "good.one", healthy("good.one"))
    register("demo", "bad", explode)
    register("demo", "good.two", healthy("good.two"))

    results = runner.run(context=None)
    by_id = {result.id: result for result in results}
    assert by_id["good.one"].status == runner.OK
    assert by_id["good.two"].status == runner.OK
    assert by_id["bad"].status == runner.UNKNOWN
    assert by_id["bad"].message_key == "check.crashed"
    assert "boom" in by_id["bad"].details


def test_a_check_returning_nothing_becomes_a_result(isolated_registry):
    register("demo", "empty", lambda context: None)
    results = runner.run(context=None)
    assert results[0].status == runner.UNKNOWN
    assert results[0].message_key == "check.no_result"


def test_a_check_returning_junk_becomes_a_result(isolated_registry):
    register("demo", "junk", lambda context: {"not": "a CheckResult"})
    results = runner.run(context=None)
    assert results[0].message_key == "check.no_result"


def test_a_slow_check_is_cut_off_and_the_rest_survive(isolated_registry, monkeypatch):
    monkeypatch.setattr(runner, "CHECK_TIMEOUT_SECONDS", 1)

    def slow(context):
        time.sleep(5)
        return CheckResult(id="slow", group="demo", status=runner.OK, title_key="t")

    register("demo", "fast", healthy("fast"))
    register("demo", "slow", slow)
    results = runner.run(context=None)
    by_id = {result.id: result for result in results}
    assert by_id["fast"].status == runner.OK
    assert by_id["slow"].message_key == "check.timed_out"


def test_a_check_may_return_a_list(isolated_registry):
    register("demo", "many", lambda context: [
        CheckResult(id="many:1", group="demo", status=runner.OK, title_key="t"),
        CheckResult(id="many:2", group="demo", status=runner.WARN, title_key="t"),
    ])
    results = runner.run(context=None)
    assert {result.id for result in results} == {"many:1", "many:2"}


def test_group_selection_runs_only_that_group(isolated_registry):
    register("a", "a.one", healthy("a.one"))
    register("b", "b.one", healthy("b.one"))
    results = runner.run(context=None, selected_groups=["b"])
    assert [result.id for result in results] == ["b.one"]


def test_worst_status_ranking():
    def result(status):
        return CheckResult(id=status, group="g", status=status, title_key="t")
    assert runner.worst_status([result("ok"), result("warn")]) == "warn"
    assert runner.worst_status([result("warn"), result("fail")]) == "fail"
    assert runner.worst_status([result("ok"), result("unavailable")]) == "unavailable"
    assert runner.worst_status([]) == "ok"


def test_snapshot_reads_each_verb_only_once():
    calls = []

    class Recorder:
        def read(self, verb, *args):
            calls.append((verb, args))
            return "value"

    snapshot = runner.Snapshot(Recorder())
    assert snapshot.read("system") == "value"
    assert snapshot.read("system") == "value"
    assert snapshot.read("network") == "value"
    assert calls == [("system", ()), ("network", ())]


def test_a_hung_check_does_not_hold_back_the_answer(isolated_registry, monkeypatch):
    """The timeout has to bound the wait, not just relabel the result.

    Joining every worker on the way out would mean a stuck check still decides
    when the customer sees the page, which is precisely the case the timeout
    exists for.
    """
    monkeypatch.setattr(runner, "CHECK_TIMEOUT_SECONDS", 1)

    def hung(context):
        time.sleep(10)
        return CheckResult(id="hung", group="demo", status=runner.OK, title_key="t")

    register("demo", "fast", healthy("fast"))
    register("demo", "hung", hung)

    started = time.monotonic()
    results = runner.run(context=None)
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"run() waited {elapsed:.1f}s for a hung check"
    by_id = {result.id: result for result in results}
    assert by_id["fast"].status == runner.OK
    assert by_id["hung"].message_key == "check.timed_out"


def test_the_total_wait_does_not_grow_with_the_number_of_slow_checks(isolated_registry,
                                                                     monkeypatch):
    monkeypatch.setattr(runner, "CHECK_TIMEOUT_SECONDS", 2)

    def slow(context):
        time.sleep(10)
        return CheckResult(id="slow", group="demo", status=runner.OK, title_key="t")

    for index in range(4):
        register("demo", f"slow.{index}", slow)

    started = time.monotonic()
    results = runner.run(context=None)
    elapsed = time.monotonic() - started

    # One shared budget, not one per check.
    assert elapsed < 6, f"run() waited {elapsed:.1f}s for four slow checks"
    assert all(result.message_key == "check.timed_out" for result in results)
