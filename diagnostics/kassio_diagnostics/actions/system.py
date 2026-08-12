# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
System-level repairs.

Image pruning is limited to dangling images — untagged and referenced by no
container. The images the updater keeps for a rollback are tagged and therefore
never touched; the confirmation dialog still states what will be removed before
anything happens.
"""

from __future__ import annotations

from . import RISK_HIGH, RISK_LOW, ActionResult, action, outcome_to_result


@action("system.prune_dangling_images", needs_sudo=True, risk=RISK_LOW)
def prune_dangling_images(context, params: dict) -> ActionResult:
    outcome = context.privileged.act("prune-dangling-images", password=context.password)
    return outcome_to_result(outcome, "action.system.prune_dangling_images.done", {},
                             ["system", "docker"])


@action("system.restart_power_agent", needs_sudo=True, risk=RISK_LOW)
def restart_power_agent(context, params: dict) -> ActionResult:
    outcome = context.privileged.act("restart-power-agent", password=context.password)
    return outcome_to_result(outcome, "action.system.restart_power_agent.done", {},
                             ["services"])


@action("system.reboot", needs_sudo=True, risk=RISK_HIGH)
def reboot(context, params: dict) -> ActionResult:
    outcome = context.privileged.act("reboot", password=context.password)
    return outcome_to_result(outcome, "action.system.reboot.done", {}, [])


@action("system.poweroff", needs_sudo=True, risk=RISK_HIGH)
def poweroff(context, params: dict) -> ActionResult:
    outcome = context.privileged.act("poweroff", password=context.password)
    return outcome_to_result(outcome, "action.system.poweroff.done", {}, [])
