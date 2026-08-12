# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
The HTTP service.

Access control has three independent layers, and none of them is decoration:

  * The listening socket is bound to the loopback address, so nothing on the LAN
    can reach it.
  * Every request — including the ones that need no login — is checked against
    the UID that owns the peer socket. A loopback port is reachable by every
    local account, so without this a secondary user could read container logs and
    the support report.
  * State-changing requests additionally require a custom header, which forces a
    CORS preflight and therefore cannot be sent silently by some other page the
    browser happens to have open, plus an origin allowlist.

The sudo session token travels in a header, never in a cookie. That is what makes
cross-site request forgery structurally impossible rather than merely unlikely.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import VERSION, actions, checks, config as config_module, deployment, i18n, runner
from .auth import LockedOut, SessionStore
from .netscan import ScanLimiter
from .peercred import peer_uid
from .posapi import PosApi, PosError, PosSessionStore
from .privileged import Privileged, default_helper_path
from . import report as report_module

LOG = logging.getLogger("kassio.server")
AUDIT = logging.getLogger("kassio.audit")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9120

REQUIRED_HEADER = "X-Kassio-Diag"
SESSION_HEADER = "X-Kassio-Diag-Session"
POS_HEADER = "X-Kassio-Diag-Pos"

CHECK_CACHE_SECONDS = 5
ALLOWED_LOG_LINES = (50, 200, 1000)
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}

RE_CONTAINER = re.compile(r"\Apos-[a-z0-9][a-z0-9-]{0,31}\Z")
RE_ACTION_ID = re.compile(r"\A[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\Z")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


class Application:
    """Shared state. One instance per process."""

    def __init__(self, *, web_dir: str, locale_dir: str, helper_path: str = "",
                 config_path: str = config_module.CONFIG_PATH,
                 deployment_dir: str = "", host: str = DEFAULT_HOST,
                 port: int = DEFAULT_PORT):
        self.web_dir = os.path.abspath(web_dir)
        self.locale_dir = os.path.abspath(locale_dir)
        self.config_path = config_path
        self.host = host
        self.port = port
        i18n.set_locale_dir(self.locale_dir)

        self.privileged = Privileged(helper_path or default_helper_path())
        self.sessions = SessionStore(self.privileged)
        self.pos_sessions = PosSessionStore()
        self.scan_limiter = ScanLimiter()

        self.deployment_dir = deployment.find_deployment_dir(deployment_dir)
        self.env = deployment.read_env(self.deployment_dir)

        self.failed_check_modules = checks.load_all()
        self.failed_action_modules = actions.load_all()

        self._config_lock = threading.Lock()
        self._config_cache = (None, [], -1.0)
        self._checks_lock = threading.Lock()
        self._checks_cache = ([], 0.0)

        self.allowed_uids = {os.getuid(), 0}
        self.allowed_origins = {
            f"http://127.0.0.1:{port}", f"http://localhost:{port}",
            f"http://[::1]:{port}",
        }

    # -- configuration ----------------------------------------------------
    def config(self):
        with self._config_lock:
            try:
                mtime = os.path.getmtime(self.config_path)
            except OSError:
                mtime = 0.0
            document, findings, cached_mtime = self._config_cache
            if cached_mtime == mtime and (document is not None or findings):
                return document, findings
            document, findings = config_module.load(self.config_path)
            self._config_cache = (document, findings, mtime)
            return document, findings

    def invalidate(self) -> None:
        with self._config_lock:
            self._config_cache = (None, [], -1.0)
        with self._checks_lock:
            self._checks_cache = ([], 0.0)

    def pos_api(self) -> PosApi:
        port = str(self.env.get("POS_PUBLIC_PORT", "") or "80").strip()
        return PosApi(f"http://127.0.0.1:{port}")

    # -- checks -----------------------------------------------------------
    def context(self):
        document, findings = self.config()
        return runner.Context(self.privileged, document, findings,
                              self.deployment_dir, self.env, self.pos_api())

    def run_checks(self, groups=None, force: bool = False) -> list:
        if not groups and not force:
            with self._checks_lock:
                cached, stamp = self._checks_cache
                if cached and time.monotonic() - stamp < CHECK_CACHE_SECONDS:
                    return cached
        results = runner.run(self.context(), groups)
        if not groups:
            with self._checks_lock:
                self._checks_cache = (results, time.monotonic())
        else:
            with self._checks_lock:
                self._checks_cache = ([], 0.0)
        return results


class Handler(BaseHTTPRequestHandler):
    server_version = "KassioDiagnostics"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # -- plumbing ---------------------------------------------------------
    @property
    def app(self) -> Application:
        return self.server.application

    def log_message(self, fmt, *args):  # route access logs through logging
        LOG.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _ok(self, data, status: int = 200) -> None:
        self._json({"ok": True, "data": data}, status)

    def _error(self, key: str, status: int = 400, **params) -> None:
        self._json({"ok": False, "error": {"key": key, "params": params}}, status)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        if length <= 0 or length > 512 * 1024:
            return {}
        try:
            raw = self.rfile.read(length)
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _query(self) -> dict:
        parsed = urllib.parse.urlparse(self.path)
        return {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

    # -- access control ---------------------------------------------------
    def _peer_allowed(self) -> bool:
        try:
            client_host, client_port = self.connection.getpeername()[:2]
            server_host, server_port = self.connection.getsockname()[:2]
        except OSError:
            return False
        uid = peer_uid(client_host, client_port, server_host, server_port)
        if uid is None:
            # Deny by default: an unprovable peer is not a trusted peer.
            LOG.warning("rejected request from unidentifiable local peer")
            return False
        if uid not in self.app.allowed_uids:
            LOG.warning("rejected request from uid %s", uid)
            return False
        return True

    def _mutation_allowed(self) -> bool:
        if self.headers.get(REQUIRED_HEADER) != "1":
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in self.app.allowed_origins:
            LOG.warning("rejected request with origin %s", origin[:120])
            return False
        return True

    # -- verbs ------------------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def do_OPTIONS(self):
        if not self._peer_allowed():
            self._error("error.forbidden", 403)
            return
        self.send_response(204)
        self.send_header("Allow", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()

    def _dispatch(self, method: str) -> None:
        try:
            if not self._peer_allowed():
                self._error("error.forbidden", 403)
                return
            if method in ("POST", "PUT", "DELETE") and not self._mutation_allowed():
                self._error("error.bad_request_headers", 400)
                return
            path = urllib.parse.urlparse(self.path).path
            handler = self._route(method, path)
            if handler is None:
                self._error("error.not_found", 404)
                return
            handler()
        except BrokenPipeError:
            return
        except Exception:  # a bug must not take the whole service down
            LOG.exception("unhandled error while serving %s %s", method, self.path)
            try:
                self._error("error.internal", 500)
            except OSError:
                return

    def _route(self, method: str, path: str):
        if method == "GET":
            if path in STATIC_FILES:
                return lambda: self._serve_static(path)
            if path == "/api/health":
                return self._get_health
            if path == "/api/meta":
                return self._get_meta
            if path.startswith("/api/i18n/"):
                return lambda: self._get_i18n(path[len("/api/i18n/"):])
            if path == "/api/checks":
                return self._get_checks
            if path == "/api/containers":
                return self._get_containers
            if path.startswith("/api/containers/") and path.endswith("/logs"):
                name = path[len("/api/containers/"):-len("/logs")]
                return lambda: self._get_container_logs(urllib.parse.unquote(name))
            if path == "/api/config":
                return self._get_config
            if path == "/api/session":
                return self._get_session
            if path == "/api/report":
                return self._get_report
            return None
        if method == "POST":
            if path == "/api/session":
                return self._post_session
            if path == "/api/pos/session":
                return self._post_pos_session
            if path.startswith("/api/actions/"):
                return lambda: self._post_action(path[len("/api/actions/"):])
            return None
        if method == "PUT":
            if path == "/api/config":
                return self._put_config
            return None
        if method == "DELETE":
            if path == "/api/session":
                return self._delete_session
            if path == "/api/pos/session":
                return self._delete_pos_session
            return None
        return None

    # -- static -----------------------------------------------------------
    def _serve_static(self, path: str) -> None:
        filename, content_type = STATIC_FILES[path]
        # Names come from a fixed table, so traversal is impossible by
        # construction; the realpath check is a belt-and-braces second look.
        full = os.path.realpath(os.path.join(self.app.web_dir, filename))
        if not full.startswith(self.app.web_dir + os.sep):
            self._error("error.not_found", 404)
            return
        try:
            with open(full, "rb") as handle:
                body = handle.read()
        except OSError:
            self._error("error.not_found", 404)
            return
        self._send(200, body, content_type)

    # -- endpoints --------------------------------------------------------
    def _get_health(self) -> None:
        self._ok({"version": VERSION, "port": self.app.port})

    def _get_meta(self) -> None:
        document, findings = self.app.config()
        self._ok({
            "version": VERSION,
            "languages": list(i18n.LANGUAGES),
            "default_language": i18n.normalise_language(
                (document or {}).get("site", {}).get("language") if document else None),
            "groups": runner.groups(),
            "actions": actions.catalogue(),
            "config_present": document is not None,
            "config_findings": [f.as_dict() for f in findings],
            "deployment_dir": self.app.deployment_dir,
            "log_line_options": list(ALLOWED_LOG_LINES),
            "failed_modules": dict(self.app.failed_check_modules,
                                   **self.app.failed_action_modules),
        })

    def _get_i18n(self, language: str) -> None:
        self._ok({"language": i18n.normalise_language(language),
                  "strings": i18n.load(language)})

    def _get_checks(self) -> None:
        query = self._query()
        raw_groups = query.get("groups", "")
        groups = [g for g in raw_groups.split(",") if g in runner.groups()]
        force = query.get("force") == "1"
        results = self.app.run_checks(groups or None, force=force)
        self._ok({"results": [r.as_dict() for r in results],
                  "summary": runner.summarise(results),
                  "worst": runner.worst_status(results),
                  "generated_at": time.time()})

    def _get_containers(self) -> None:
        outcome = self.app.privileged.read("containers")
        if not outcome.ok:
            self._ok({"available": False, "error_key": outcome.error_key,
                      "details": outcome.detail, "containers": []})
            return
        self._ok(outcome.data or {})

    def _get_container_logs(self, name: str) -> None:
        if not RE_CONTAINER.fullmatch(name or ""):
            self._error("error.rejected_argument", 400, value=(name or "")[:64])
            return
        try:
            lines = int(self._query().get("lines", "200"))
        except ValueError:
            lines = 200
        if lines not in ALLOWED_LOG_LINES:
            lines = 200
        outcome = self.app.privileged.read("container-logs", name, lines)
        if not outcome.ok:
            self._ok({"name": name, "available": False,
                      "error_key": outcome.error_key, "details": outcome.detail,
                      "lines": []})
            return
        self._ok(outcome.data or {})

    def _get_config(self) -> None:
        document, findings = self.app.config()
        self._ok({
            "config": document,
            "template": config_module.empty_config(),
            "findings": [f.as_dict() for f in findings],
            "roles": list(config_module.ROLES),
            "backups": config_module.list_backups(
                os.path.dirname(self.app.config_path)),
            "path": self.app.config_path,
        })

    def _put_config(self) -> None:
        token = self.headers.get(SESSION_HEADER, "")
        password = self.app.sessions.secret_for(token)
        if password is None:
            self._error("error.session_required", 401)
            return
        payload = self._body()
        document = payload.get("config")
        if not isinstance(document, dict):
            self._error("error.rejected_argument", 400, value="config")
            return
        findings = config_module.validate(document)
        if config_module.has_errors(findings):
            self._json({"ok": False,
                        "error": {"key": "config.rejected", "params": {}},
                        "findings": [f.as_dict() for f in findings]}, 400)
            return
        serialised = json.dumps(document, ensure_ascii=False).encode("utf-8")
        outcome = self.app.privileged.act("write-config", password=password,
                                          stdin_data=serialised)
        AUDIT.info("write-config ok=%s", outcome.ok)
        if not outcome.ok:
            self._error(outcome.error_key or "error.command_failed", 500,
                        detail=outcome.detail[:200])
            return
        self.app.invalidate()
        self._ok({"written": True, "findings": [f.as_dict() for f in findings],
                  "backup": (outcome.data or {}).get("backup")})

    # -- sessions ---------------------------------------------------------
    def _get_session(self) -> None:
        token = self.headers.get(SESSION_HEADER, "")
        status = self.app.sessions.status(token)
        pos_token = self.headers.get(POS_HEADER, "")
        status["pos_active"] = bool(self.app.pos_sessions.get(pos_token))
        status["pos_username"] = self.app.pos_sessions.username(pos_token)
        self._ok(status)

    def _post_session(self) -> None:
        payload = self._body()
        password = payload.get("password")
        if not isinstance(password, str) or not password:
            self._error("error.password_required", 400)
            return
        try:
            token, outcome = self.app.sessions.create(password)
        except LockedOut as locked:
            AUDIT.warning("sudo session attempt during lockout")
            self._error("error.locked_out", 429, seconds=locked.seconds_left)
            return
        if token is None:
            AUDIT.warning("sudo session denied: %s", outcome.error_key)
            self._error(outcome.error_key or "error.bad_password", 401)
            return
        AUDIT.info("sudo session opened")
        self._ok({"token": token, **self.app.sessions.status(token)})

    def _delete_session(self) -> None:
        self.app.sessions.revoke(self.headers.get(SESSION_HEADER, ""))
        self._ok({"active": False})

    def _post_pos_session(self) -> None:
        payload = self._body()
        username = payload.get("username")
        password = payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str) \
                or not username or not password:
            self._error("pos.credentials_required", 400)
            return
        try:
            pos_token = self.app.pos_api().login(username, password)
        except PosError as exc:
            AUDIT.warning("POS login failed: %s", exc.error_key)
            self._error(exc.error_key, 401 if exc.status in (401, 403) else 502,
                        detail=exc.detail[:200])
            return
        import secrets
        handle = secrets.token_urlsafe(24)
        self.app.pos_sessions.store(handle, pos_token, username)
        AUDIT.info("POS session opened for %s", username[:40])
        self._ok({"token": handle, "username": username})

    def _delete_pos_session(self) -> None:
        self.app.pos_sessions.revoke(self.headers.get(POS_HEADER, ""))
        self._ok({"active": False})

    # -- actions ----------------------------------------------------------
    def _post_action(self, action_id: str) -> None:
        action_id = urllib.parse.unquote(action_id)
        if not RE_ACTION_ID.fullmatch(action_id or ""):
            self._error("error.not_found", 404)
            return
        definition = actions.get(action_id)
        if definition is None or definition.client_only:
            self._error("error.not_found", 404)
            return

        password = b""
        if definition.needs_sudo:
            token = self.headers.get(SESSION_HEADER, "")
            secret = self.app.sessions.secret_for(token)
            if secret is None:
                self._error("error.session_required", 401)
                return
            password = secret

        pos_token = ""
        if definition.needs_pos_login:
            pos_token = self.app.pos_sessions.get(self.headers.get(POS_HEADER, ""))
            if not pos_token:
                self._error("pos.login_required", 401)
                return

        params = self._body().get("params")
        params = params if isinstance(params, dict) else {}
        document, _ = self.app.config()
        context = actions.ActionContext(
            privileged=self.app.privileged, password=password,
            pos_api=self.app.pos_api(), pos_token=pos_token, config=document,
            env=self.app.env, deployment_dir=self.app.deployment_dir,
            scan_limiter=self.app.scan_limiter)

        started = time.monotonic()
        try:
            result = definition.handler(context, params)
        except Exception as exc:  # noqa: BLE001 - reported, never propagated
            LOG.exception("action %s raised", action_id)
            AUDIT.warning("action %s crashed: %r", action_id, exc)
            self._error("action.crashed", 500, action=action_id)
            return
        AUDIT.info("action %s ok=%s params=%s duration=%.1fs", action_id,
                   getattr(result, "ok", False), sorted(params),
                   time.monotonic() - started)

        payload = result.as_dict()
        if result.ok and result.recheck_groups:
            self.app.invalidate()
            rechecked = self.app.run_checks(result.recheck_groups, force=True)
            payload["results"] = [r.as_dict() for r in rechecked]
        self._json({"ok": result.ok, "data": payload},
                   200 if result.ok else 400)

    # -- report -----------------------------------------------------------
    def _get_report(self) -> None:
        language = i18n.normalise_language(self._query().get("lang", "de"))

        def translate(key, **params):
            return i18n.translate(language, key, **params)

        results = [r.as_dict() for r in self.app.run_checks(force=True)]
        document, findings = self.app.config()
        system_outcome = self.app.privileged.read("system")
        containers_outcome = self.app.privileged.read("containers")
        containers = (containers_outcome.data or {}).get("containers", []) \
            if containers_outcome.ok else []

        logs = {}
        for name in config_module.expected_containers(document):
            outcome = self.app.privileged.read("container-logs", name, 200)
            if outcome.ok and isinstance(outcome.data, dict):
                logs[name] = outcome.data.get("lines", [])

        text = report_module.build(
            translate=translate, results=results, config_document=document,
            config_findings=[f.as_dict() for f in findings],
            system_info=system_outcome.data if system_outcome.ok else None,
            containers=containers, container_logs=logs,
            updater_state=deployment.read_updater_state(self.app.deployment_dir),
            upgrade_events=deployment.read_upgrade_events(self.app.deployment_dir),
            backup=deployment.newest_backup(self.app.deployment_dir),
            env_keys=deployment.env_key_presence(self.app.deployment_dir),
            deployment_dir=self.app.deployment_dir, tool_version=VERSION,
            language=language)

        body = text.encode("utf-8")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="kassio-diagnose-{stamp}.txt"')
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


class DiagnosticsServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, address, application: Application):
        self.application = application
        super().__init__(address, Handler)

    def handle_error(self, request, client_address):
        LOG.exception("error while handling a request from %s", client_address)


def serve(application: Application) -> DiagnosticsServer:
    server = DiagnosticsServer((application.host, application.port), application)
    LOG.info("listening on http://%s:%s", application.host, application.port)
    return server
