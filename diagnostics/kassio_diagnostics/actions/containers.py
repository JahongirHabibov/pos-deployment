# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""Container repairs — restarting exactly one pos-* container, and nothing more."""

from __future__ import annotations

import re

from . import RISK_MEDIUM, ActionResult, action, outcome_to_result

RE_CONTAINER = re.compile(r"\Apos-[a-z0-9][a-z0-9-]{0,31}\Z")


@action("container.restart", needs_sudo=True, risk=RISK_MEDIUM)
def restart_container(context, params: dict) -> ActionResult:
    name = str((params or {}).get("container", ""))
    # Validated here as well as in the helper: the helper is the security
    # boundary, this is the layer that can give the customer a readable answer.
    if not RE_CONTAINER.fullmatch(name):
        return ActionResult(False, "error.rejected_argument", {"value": name[:64]})
    outcome = context.privileged.act("restart-container", name,
                                     password=context.password)
    return outcome_to_result(outcome, "action.container.restart.done",
                             {"container": name}, ["docker", "pos"])
