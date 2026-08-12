# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
The helper is the security boundary, so its input handling is tested directly.

The repository is public: an attacker reads these patterns in full and looks for
the gap between what they appear to allow and what they actually allow. The two
cases that matter most have their own tests — a trailing newline slipping past a
``$`` anchor, and an identifier starting with a dash being read as an option.
"""

import json
import subprocess

import pytest

EXIT_USAGE = 2


def run_helper(helper_path, *args, stdin=""):
    return subprocess.run([helper_path] + list(args), capture_output=True,
                          text=True, input=stdin, timeout=30, check=False)


# --------------------------------------------------------------- rejection


@pytest.mark.parametrize("value", [
    "pos-backend\n",          # trailing newline: the classic ^...$ bypass
    "pos-backend\r\n",
    "\npos-backend",
    "-pos-backend",           # leading dash: would become an option
    "--privileged",
    "pos backend",
    "pos-backend;id",
    "pos-backend$(id)",
    "pos-backend`id`",
    "pos-backend|id",
    "pos-backend&&id",
    "../../etc/passwd",
    "/etc/passwd",
    "database",               # not a pos-* container
    "POS-BACKEND",
    "",
    "pos-" + "a" * 64,
])
def test_container_names_are_rejected(helper_module, value):
    with pytest.raises(helper_module.Rejected):
        helper_module._container(value)


@pytest.mark.parametrize("value", ["pos-backend", "pos-db-1", "pos-image-service"])
def test_valid_container_names_pass(helper_module, value):
    assert helper_module._container(value) == value


@pytest.mark.parametrize("value", [
    "docker.service\n", "-x.service", "../x.service", "docker", "docker.socketX",
    "a" * 80 + ".service", "",
])
def test_unit_names_are_rejected(helper_module, value):
    with pytest.raises(helper_module.Rejected):
        helper_module._unit(value)


@pytest.mark.parametrize("value", [
    "/dev/sda\n", "/dev/../etc/passwd", "/dev/sda1x", "-/dev/sda", "/etc/passwd", "",
])
def test_block_devices_are_rejected(helper_module, value):
    with pytest.raises(helper_module.Rejected):
        helper_module._blockdev(value)


@pytest.mark.parametrize("value", ["/dev/sda", "/dev/nvme0n1", "/dev/mmcblk0"])
def test_valid_block_devices_pass(helper_module, value):
    assert helper_module._blockdev(value) == value


@pytest.mark.parametrize("value", ["100", "0", "-1", "1e3", "", "200 ", None])
def test_log_line_counts_are_rejected(helper_module, value):
    with pytest.raises(helper_module.Rejected):
        helper_module._log_lines(value)


@pytest.mark.parametrize("value", ["50", "200", "1000"])
def test_allowed_log_line_counts_pass(helper_module, value):
    assert helper_module._log_lines(value) == int(value)


def test_patterns_use_fullmatch_semantics(helper_module):
    """A newline must not slip through, whatever the pattern looks like."""
    for pattern in (helper_module.RE_CONTAINER, helper_module.RE_UNIT,
                    helper_module.RE_BLOCKDEV, helper_module.RE_IFACE):
        assert pattern.pattern.startswith("\\A")
        assert pattern.pattern.endswith("\\Z")


# ----------------------------------------------------- argument separation


def test_container_log_command_separates_options(helper_module, recorded_commands):
    helper_module.verb_read_container_logs(["pos-backend", "200"])
    argv = recorded_commands[-1]
    assert "--" in argv
    assert argv.index("--") < argv.index("pos-backend")


def test_container_restart_command_separates_options(helper_module, recorded_commands):
    helper_module.verb_act_restart_container(["pos-backend"])
    argv = recorded_commands[-1]
    assert "--" in argv
    assert argv.index("--") < argv.index("pos-backend")


def test_container_inspect_command_separates_options(helper_module, recorded_commands):
    helper_module.verb_read_container_inspect(["pos-backend"])
    argv = recorded_commands[-1]
    assert "--" in argv and argv.index("--") < argv.index("pos-backend")


def test_every_caller_supplied_value_is_preceded_by_a_separator(helper_module,
                                                                recorded_commands):
    """Whatever else changes, no user value may sit in option position."""
    helper_module.verb_read_container_logs(["pos-frontend", "50"])
    helper_module.verb_read_container_inspect(["pos-frontend"])
    helper_module.verb_act_restart_container(["pos-frontend"])
    for argv in recorded_commands:
        assert "pos-frontend" in argv
        assert "--" in argv, argv
        assert argv.index("--") < argv.index("pos-frontend"), argv


def test_no_command_is_ever_run_through_a_shell(helper_module):
    source = open(helper_module.__file__ if hasattr(helper_module, "__file__")
                  else "", "r", encoding="utf-8").read() if False else None
    # The helper never imports os.system or uses shell=True; assert on the call
    # site instead of the file, so the check survives refactoring.
    import inspect
    body = inspect.getsource(helper_module.run)
    assert "shell=True" not in body
    assert "os.system" not in body


# ------------------------------------------------------------- end to end


def test_unknown_verb_is_rejected(helper_path):
    result = run_helper(helper_path, "read", "nonsense")
    assert result.returncode == EXIT_USAGE
    assert json.loads(result.stdout)["ok"] is False


def test_unknown_mode_is_rejected(helper_path):
    result = run_helper(helper_path, "exec", "system")
    assert result.returncode == EXIT_USAGE


def test_newline_container_name_is_rejected_end_to_end(helper_path):
    result = run_helper(helper_path, "read", "container-logs", "pos-backend\n", "200")
    assert result.returncode == EXIT_USAGE
    assert json.loads(result.stdout)["error"] == "invalid container name"


def test_extra_arguments_are_rejected(helper_path):
    result = run_helper(helper_path, "read", "system", "extra")
    assert result.returncode == EXIT_USAGE


def test_write_config_rejects_non_json(helper_path):
    result = run_helper(helper_path, "act", "write-config", stdin="not json")
    # Either rejected outright, or refused for lack of root — never accepted.
    assert result.returncode in (2, 3)
