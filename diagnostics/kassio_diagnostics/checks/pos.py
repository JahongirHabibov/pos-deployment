# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
POS application checks.

Deliberately unauthenticated: these probe the health endpoints the frontend
proxies, so the customer sees whether the application answers at all without
first signing in. Signing in is only required to read or change a setting.
"""

from __future__ import annotations

import urllib.error
import urllib.request

from .. import runner
from ..runner import CheckResult, check

TIMEOUT_SECONDS = 5


def _pos_port(context) -> str:
    port = str((context.env or {}).get("POS_PUBLIC_PORT", "")).strip()
    return port or "80"


def _probe(url: str):
    request = urllib.request.Request(url, method="GET",
                                     headers={"Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.status, response.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return 0, str(exc)[:200]


@check("pos", "pos.frontend", "check.pos.frontend.title")
def pos_frontend(context) -> CheckResult:
    port = _pos_port(context)
    url = f"http://127.0.0.1:{port}/"
    status, detail = _probe(url)
    if 200 <= status < 400:
        return CheckResult(
            id="pos.frontend", group="pos", status=runner.OK,
            title_key="check.pos.frontend.title",
            message_key="check.pos.frontend.message",
            params={"url": url}, actual=str(status))
    return CheckResult(
        id="pos.frontend", group="pos", status=runner.FAIL,
        title_key="check.pos.frontend.title",
        message_key="check.pos.frontend.unreachable",
        params={"url": url}, actual=str(status), details=detail,
        actions=["container.restart"],
        data={"container": "pos-frontend"})


@check("pos", "pos.backend", "check.pos.backend.title")
def pos_backend(context) -> CheckResult:
    port = _pos_port(context)
    url = f"http://127.0.0.1:{port}/api/v1/health/ready"
    status, detail = _probe(url)
    if 200 <= status < 300:
        return CheckResult(
            id="pos.backend", group="pos", status=runner.OK,
            title_key="check.pos.backend.title",
            message_key="check.pos.backend.message",
            params={"url": url}, actual=str(status))
    if status in (401, 403):
        # Answering at all is what this check is about.
        return CheckResult(
            id="pos.backend", group="pos", status=runner.OK,
            title_key="check.pos.backend.title",
            message_key="check.pos.backend.message",
            params={"url": url}, actual=str(status))
    return CheckResult(
        id="pos.backend", group="pos", status=runner.FAIL,
        title_key="check.pos.backend.title",
        message_key="check.pos.backend.unreachable",
        params={"url": url}, actual=str(status), details=detail,
        actions=["container.restart"],
        data={"container": "pos-backend"})
