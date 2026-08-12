# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
The "changes nothing" invariant, enforced mechanically.

The design promises that the diagnostics tool writes only to its own paths and
never to the POS deployment. That promise is easy to break by accident during a
later change, so it is asserted here against the source itself rather than left
to review: the service package must contain no write at all, and the helper must
write only below /etc/kassio-diagnostics.
"""

import ast
import os

import pytest

DIAGNOSTICS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGE_DIR = os.path.join(DIAGNOSTICS_DIR, "kassio_diagnostics")
HELPER_PATH = os.path.join(DIAGNOSTICS_DIR, "bin", "diag-helper")

WRITE_MODES = ("w", "a", "x", "+")
MUTATING_CALLS = {
    ("os", "remove"), ("os", "unlink"), ("os", "rmdir"), ("os", "removedirs"),
    ("os", "rename"), ("os", "replace"), ("os", "truncate"), ("os", "chmod"),
    ("os", "chown"), ("os", "makedirs"), ("os", "mkdir"), ("os", "symlink"),
    ("shutil", "rmtree"), ("shutil", "copy"), ("shutil", "copy2"),
    ("shutil", "move"), ("shutil", "copytree"),
}


def package_files():
    paths = []
    for root, _, files in os.walk(PACKAGE_DIR):
        for name in files:
            if name.endswith(".py"):
                paths.append(os.path.join(root, name))
    return sorted(paths)


def parse(path):
    with open(path, encoding="utf-8") as handle:
        return ast.parse(handle.read(), filename=path)


def opens_for_writing(node):
    if not isinstance(node, ast.Call):
        return False
    name = node.func.id if isinstance(node.func, ast.Name) else \
        node.func.attr if isinstance(node.func, ast.Attribute) else ""
    if name != "open":
        return False
    mode = ""
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        mode = str(node.args[1].value)
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            mode = str(keyword.value.value)
    return any(flag in mode for flag in WRITE_MODES)


def mutating_call(node):
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    value = node.func.value
    if isinstance(value, ast.Name) and (value.id, node.func.attr) in MUTATING_CALLS:
        return f"{value.id}.{node.func.attr}"
    return None


@pytest.mark.parametrize("path", package_files(), ids=os.path.basename)
def test_the_service_package_never_opens_a_file_for_writing(path):
    for node in ast.walk(parse(path)):
        assert not opens_for_writing(node), (
            f"{os.path.relpath(path, DIAGNOSTICS_DIR)}:{getattr(node, 'lineno', '?')} "
            "opens a file for writing; all writes must go through the helper")


@pytest.mark.parametrize("path", package_files(), ids=os.path.basename)
def test_the_service_package_never_deletes_or_moves_files(path):
    for node in ast.walk(parse(path)):
        found = mutating_call(node)
        assert found is None, (
            f"{os.path.relpath(path, DIAGNOSTICS_DIR)}:{getattr(node, 'lineno', '?')} "
            f"calls {found}; the service must not modify the file system")


def test_the_service_package_never_uses_a_shell():
    for path in package_files():
        source = open(path, encoding="utf-8").read()
        assert "shell=True" not in source, path
        assert "os.system" not in source, path
        assert "os.popen" not in source, path


def test_the_helper_writes_only_below_its_own_configuration_directory():
    tree = parse(HELPER_PATH)
    module = {node.targets[0].id: node.value
              for node in tree.body
              if isinstance(node, ast.Assign) and len(node.targets) == 1
              and isinstance(node.targets[0], ast.Name)}
    assert isinstance(module["CONFIG_DIR"], ast.Constant)
    assert module["CONFIG_DIR"].value == "/etc/kassio-diagnostics"

    writers = [node for node in ast.walk(tree) if opens_for_writing(node)]
    # The only writer is the atomic replace in write-config, which uses
    # NamedTemporaryFile inside CONFIG_DIR rather than open().
    assert writers == [], "the helper gained a direct file write"

    source = open(HELPER_PATH, encoding="utf-8").read()
    for forbidden in ("shutil.rmtree", "os.system", "os.popen", "shell=True"):
        assert forbidden not in source, forbidden


def test_the_helper_never_touches_the_pos_deployment():
    source = open(HELPER_PATH, encoding="utf-8").read()
    for forbidden in ("docker-compose", ".env", "manifest.json", "updater-state"):
        assert forbidden not in source, (
            f"the helper references {forbidden}; POS files are read by the "
            "service, read-only, and never by the privileged helper")


def test_the_deployment_reader_uses_a_key_allowlist():
    from kassio_diagnostics import deployment
    assert deployment.ENV_ALLOWLIST == ("POS_PUBLIC_PORT", "TZ",
                                        "COMPOSE_PROJECT_NAME",
                                        "HOST_COMPOSE_PROJECT_DIR")


def test_the_deployment_reader_returns_only_allowlisted_keys(tmp_path):
    from kassio_diagnostics import deployment
    (tmp_path / "docker-compose.prod.yml").write_text("services: {}", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "POS_PUBLIC_PORT=8080\nPOSTGRES_PASSWORD=secret\nGHCR_TOKEN=ghp_x\nTZ=Europe/Berlin\n",
        encoding="utf-8")
    values = deployment.read_env(str(tmp_path))
    assert values == {"POS_PUBLIC_PORT": "8080", "TZ": "Europe/Berlin"}
    assert "secret" not in repr(values)
