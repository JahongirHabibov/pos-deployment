# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Support report redaction.

This is the guard rail for a public repository and a report the customer is
encouraged to paste into a chat window. The report is assembled from material
chosen explicitly, and this pass is the second line of defence over text we do
not control, such as container logs.
"""

import pytest

from kassio_diagnostics import report

SECRETS = [
    "POSTGRES_PASSWORD=sup3rs3cret",
    "postgres_password: sup3rs3cret",
    "GHCR_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz012345",
    "API_KEY=abcdef123456",
    "api-key not matched but APIKEY=zzz is",
    "OTPK=\"one-time-key-value\"",
    "JWT_SECRET='hunter2hunter2'",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
    "proxy-authorization=Basic dXNlcjpwYXNz",
    "connecting to postgres://kassio:hunter2@db:5432/pos",
    "token=github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz012345",
]

LEAKS = ["sup3rs3cret", "ghp_abcdefghijklmnopqrstuvwxyz012345", "one-time-key-value",
         "hunter2hunter2", "hunter2", "dXNlcjpwYXNz", "abcdef123456", "zzz",
         "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz012345"]


@pytest.mark.parametrize("line", SECRETS)
def test_known_secret_shapes_are_removed(line):
    cleaned = report.scrub(line)
    for leak in LEAKS:
        if leak in line:
            assert leak not in cleaned, f"{leak!r} survived in {cleaned!r}"


def test_jwt_is_removed():
    cleaned = report.scrub("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl")
    assert "eyJhbGciOiJIUzI1NiJ9" not in cleaned


def test_scrubbing_keeps_ordinary_text_readable():
    text = "printer 192.168.1.50 unreachable on port 9100"
    assert report.scrub(text) == text


def test_scrub_accepts_non_strings():
    assert report.scrub(None) == "None"
    assert report.scrub(1234) == "1234"


def test_report_never_embeds_env_values():
    text = report.build(
        translate=lambda key, **params: key,
        results=[], config_document=None, config_findings=[], system_info=None,
        containers=[], container_logs={"pos-backend": ["POSTGRES_PASSWORD=leaked"]},
        updater_state={}, upgrade_events=[], backup={},
        env_keys=[{"key": "POSTGRES_PASSWORD", "set": True},
                  {"key": "GHCR_TOKEN", "set": True}],
        deployment_dir="/opt/pos-deployment", tool_version="1.0.0", language="de")
    assert "leaked" not in text
    # The names are useful to support and carry no secret by themselves.
    assert "POSTGRES_PASSWORD" in text
    assert "set" in text


def test_report_scrubs_check_details():
    results = [{
        "id": "x", "group": "system", "status": "fail", "title_key": "t",
        "message_key": "m", "params": {}, "actual": "", "expected": "",
        "details": "command failed: PGPASSWORD=leakysecret psql", "data": {},
    }]
    text = report.build(
        translate=lambda key, **params: key, results=results, config_document=None,
        config_findings=[], system_info=None, containers=[], container_logs={},
        updater_state={}, upgrade_events=[], backup={}, env_keys=[],
        deployment_dir="", tool_version="1.0.0", language="de")
    assert "leakysecret" not in text
