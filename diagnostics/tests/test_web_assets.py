# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Static checks on the interface assets and the installer.

These exist because of two defects that no Python test could have caught. A
class selector setting ``display`` outranks the user agent's
``[hidden] { display: none }``, so the modal backdrop covered the whole page
from the first paint — the interface looked dark and swallowed every click, with
nothing in any log to explain it. And the installer wrote a browser policy that
nobody had asked for.
"""

from __future__ import annotations

import os
import re

import pytest

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
DIAGNOSTICS_DIR = os.path.dirname(WEB_DIR)


def read(name: str) -> str:
    with open(os.path.join(WEB_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def read_root(name: str) -> str:
    with open(os.path.join(DIAGNOSTICS_DIR, name), encoding="utf-8") as handle:
        return handle.read()


def code_only(text: str, style: str) -> str:
    """Strip comments, so these tests judge behaviour and not prose.

    The files explain in their comments exactly what they refuse to do, and a
    naive substring search would read those explanations as the offence.
    """
    if style == "shell":
        return "\n".join(line for line in text.splitlines()
                          if not line.lstrip().startswith("#"))
    stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in stripped.splitlines())


def expand_shell_vars(text: str) -> str:
    """Resolve the simple NAME=value assignments both scripts open with.

    Both scripts build their paths from variables, so a literal search would
    miss every one of them and quietly pass.
    """
    values = {}
    for name, value in re.findall(r"^([A-Z_][A-Z0-9_]*)=(\S+)\s*$", text, re.M):
        values[name] = value.strip('"')
    for _ in range(3):  # a few passes resolve nesting like ${DIR}/${NAME}
        for name, value in values.items():
            for other, replacement in values.items():
                value = value.replace("${%s}" % other, replacement)
            values[name] = value
    expanded = text
    for name, value in values.items():
        expanded = expanded.replace("${%s}" % name, value)
    return expanded


# ------------------------------------------------------------------ styling


def test_the_hidden_attribute_beats_every_display_rule():
    css = read("styles.css")
    assert re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", css), (
        "without this rule a class selector setting display re-shows elements "
        "marked hidden — which is how the modal backdrop ended up covering the "
        "whole interface")


def test_every_element_marked_hidden_is_covered_by_that_rule():
    html = read("index.html")
    hidden_elements = re.findall(r"<(\w+)[^>]*\shidden[^>]*>", html)
    assert hidden_elements, "the fixture expects at least one hidden element"
    css = read("styles.css")
    assert "[hidden] { display: none !important; }" in css


def test_the_dialog_host_starts_hidden():
    html = read("index.html")
    assert re.search(r'id="dialog-host"[^>]*\shidden', html)


def test_dark_tokens_exist_for_both_preference_and_explicit_choice():
    css = read("styles.css")
    assert ':root[data-theme="dark"]' in css, "an explicit dark choice needs its own block"
    assert ':root:not([data-theme="light"])' in css, (
        "the preference block must be guarded, otherwise choosing light by hand "
        "loses against a dark desktop")


def test_the_light_palette_is_defined_on_bare_root():
    css = read("styles.css")
    root = css.split(":root {", 1)[1].split("}", 1)[0]
    for token in ("--bg", "--surface", "--text", "--ok", "--warn", "--fail"):
        assert token in root, f"{token} has no light definition"


def test_no_external_resource_is_referenced():
    html = read("index.html")
    for match in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert not match.startswith(("http://", "https://", "//")), match


# ------------------------------------------------------------------- script


def test_every_element_the_script_looks_up_exists_in_the_page():
    """A typo here is silent: getElementById returns null and the handler dies."""
    html = read("index.html")
    script = read("app.js")
    identifiers = set(re.findall(r'getElementById\("([^"]+)"\)', script))
    assert identifiers, "the fixture expects the script to look up elements"
    for identifier in sorted(identifiers):
        assert f'id="{identifier}"' in html, f"app.js expects #{identifier}"


def test_server_values_never_reach_inner_html():
    script = code_only(read("app.js"), "js")
    assert "innerHTML" not in script, (
        "container logs and scan results are attacker-influenceable and must go "
        "in through textContent")
    assert "insertAdjacentHTML" not in script
    assert "document.write" not in script


def test_the_session_token_is_never_persisted():
    script = read("app.js")
    stored = re.findall(r'localStorage\.setItem\("([^"]+)"', script)
    assert "kassio-diag-language" in stored
    assert all("token" not in key and "session" not in key for key in stored), stored
    assert "document.cookie" not in script


# ---------------------------------------------------------------- installer


def test_the_installer_never_touches_firefox():
    installer = code_only(read_root("install.sh"), "shell")
    assert "firefox" not in installer.lower(), (
        "Firefox keeps one shared policies.json that this tool does not own")
    assert "policies.json" not in installer


def test_a_browser_bookmark_is_opt_in():
    installer = read_root("install.sh")
    assert "WANT_BOOKMARK=0" in installer, "the default must not touch any browser"
    assert "--bookmark" in installer
    body = installer.split("WANT_BOOKMARK=0", 1)[1]
    assert 'if [[ "${WANT_BOOKMARK}" -eq 1 ]]; then' in body


@pytest.mark.parametrize("path", [
    "/etc/systemd/system/kassio-diagnostics.service",
    "/etc/sudoers.d/kassio-diagnostics",
    "/usr/share/applications/kassio-diagnostics.desktop",
    "/opt/kassio-diagnostics",
])
def test_the_uninstaller_removes_everything_the_installer_creates(path):
    """The installer composes these paths from variables; the uninstaller has
    to name them, so anything it forgets is left behind on the customer's
    machine forever."""
    uninstaller = expand_shell_vars(code_only(read_root("uninstall.sh"), "shell"))
    assert path in uninstaller, f"uninstall.sh never removes {path}"


def test_the_installer_declares_the_same_locations():
    installer = expand_shell_vars(code_only(read_root("install.sh"), "shell"))
    for fragment in ("/opt/kassio-diagnostics", "/etc/kassio-diagnostics",
                     "/etc/sudoers.d/kassio-diagnostics",
                     "/usr/share/applications/kassio-diagnostics.desktop",
                     "/etc/systemd/system/"):
        assert fragment in installer, fragment


def test_no_chromium_bookmark_is_written_on_a_default_run():
    installer = code_only(read_root("install.sh"), "shell")
    # Every write of a policy file has to sit inside the opt-in branch.
    guarded = installer.split('if [[ "${WANT_BOOKMARK}" -eq 1 ]]; then', 1)
    assert len(guarded) == 2, "the bookmark block is no longer guarded"
    assert "policies/managed" not in guarded[0], (
        "a policy file is written before the opt-in check")
