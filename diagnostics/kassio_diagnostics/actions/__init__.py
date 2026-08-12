# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
Repair actions.

Every action declares what it needs and how much it risks, so the interface can
decide on its own what to ask before running it — rather than each handler
inventing its own confirmation. An action that reaches this registry has already
passed the confirmation dialog, the permission check and the audit entry.

Actions marked ``client_only`` have no handler: opening the printer's web
interface or the setup wizard happens in the browser. They are registered here
anyway so that a check can offer them in exactly the same way as a real repair.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field

LOG = logging.getLogger("kassio.actions")

RISK_LOW, RISK_MEDIUM, RISK_HIGH = "low", "medium", "high"

MODULES = ("containers", "network", "printer", "system")
FAILED_IMPORTS: dict = {}


@dataclass
class ActionResult:
    ok: bool
    message_key: str = ""
    params: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    details: str = ""
    recheck_groups: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"ok": self.ok, "message_key": self.message_key, "params": self.params,
                "data": self.data, "details": self.details,
                "recheck_groups": self.recheck_groups}


@dataclass
class Action:
    id: str
    needs_sudo: bool
    needs_pos_login: bool
    risk: str
    confirm_key: str
    label_key: str
    handler: object = None
    client_only: bool = False

    def as_dict(self) -> dict:
        return {"id": self.id, "needs_sudo": self.needs_sudo,
                "needs_pos_login": self.needs_pos_login, "risk": self.risk,
                "confirm_key": self.confirm_key, "label_key": self.label_key,
                "client_only": self.client_only}


_REGISTRY: dict = {}


def action(action_id: str, *, needs_sudo: bool = False, needs_pos_login: bool = False,
           risk: str = RISK_LOW, confirm_key: str = "", label_key: str = "",
           client_only: bool = False):
    def decorator(function):
        _REGISTRY[action_id] = Action(
            id=action_id, needs_sudo=needs_sudo, needs_pos_login=needs_pos_login,
            risk=risk, confirm_key=confirm_key or f"action.{action_id}.confirm",
            label_key=label_key or f"action.{action_id}.label",
            handler=function, client_only=client_only)
        return function
    return decorator


def register_client_action(action_id: str, *, label_key: str = "") -> None:
    _REGISTRY[action_id] = Action(
        id=action_id, needs_sudo=False, needs_pos_login=False, risk=RISK_LOW,
        confirm_key="", label_key=label_key or f"action.{action_id}.label",
        handler=None, client_only=True)


def get(action_id: str):
    return _REGISTRY.get(action_id)


def catalogue() -> list:
    return [entry.as_dict() for entry in sorted(_REGISTRY.values(), key=lambda a: a.id)]


def load_all() -> dict:
    for name in MODULES:
        try:
            importlib.import_module(f"{__name__}.{name}")
        except Exception as exc:  # noqa: BLE001 - a broken module must not stop the service
            LOG.exception("action module %s could not be imported", name)
            FAILED_IMPORTS[name] = repr(exc)
    for client_action in ("printer.open_web_ui", "printer.show_instructions",
                          "setup.open_wizard"):
        register_client_action(client_action)
    return dict(FAILED_IMPORTS)


class ActionContext:
    """Everything an action may use. Assembled per request, never cached."""

    def __init__(self, privileged, password: bytes = b"", pos_api=None,
                 pos_token: str = "", config=None, env=None, deployment_dir: str = "",
                 scan_limiter=None):
        self.privileged = privileged
        self.password = password or b""
        self.pos_api = pos_api
        self.pos_token = pos_token
        self.config = config
        self.env = env or {}
        self.deployment_dir = deployment_dir
        self.scan_limiter = scan_limiter


def outcome_to_result(outcome, success_key: str, params: dict,
                      recheck_groups: list) -> ActionResult:
    """Translate a privileged Outcome into an ActionResult."""
    if outcome.ok:
        return ActionResult(True, success_key, params,
                            data=outcome.data if isinstance(outcome.data, dict) else {},
                            recheck_groups=recheck_groups)
    return ActionResult(False, outcome.error_key or "error.command_failed",
                        dict(params, **(outcome.params or {})),
                        details=outcome.detail)
