# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""
The HTTP layer.

The tests that matter here are the refusals. A loopback port is reachable by
every account on the machine, so "the request came from localhost" proves
nothing on its own — the peer UID check is what carries the access decision, and
it has to deny when it cannot prove ownership.
"""

import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from kassio_diagnostics import server as server_module
from kassio_diagnostics.privileged import Outcome
from kassio_diagnostics.server import Application

DIAGNOSTICS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakePrivileged:
    def read(self, verb, *args, timeout=40):
        return Outcome(False, None, "error.unavailable", "test double")

    def act(self, verb, *args, password=b"", stdin_data=b"", timeout=150):
        return Outcome(False, None, "error.bad_password", "")

    def verify_password(self, password):
        return Outcome(False, None, "error.bad_password", "")


@pytest.fixture
def offline(monkeypatch):
    """The suite must never reach the network, not even through a check."""
    from kassio_diagnostics import netscan
    from kassio_diagnostics.checks import network as network_checks
    from kassio_diagnostics.checks import pos as pos_checks
    monkeypatch.setattr(netscan, "tcp_probe", lambda host, port, timeout=0.3: False)
    monkeypatch.setattr(netscan, "ping", lambda host, timeout=1: False)
    monkeypatch.setattr(netscan, "probe_device", lambda host, port, timeout=1.0: {
        "host": host, "port": port, "tcp": False, "icmp": False, "reachable": False})
    monkeypatch.setattr(pos_checks, "_probe", lambda url: (0, "offline"))
    monkeypatch.setattr(network_checks, "resolve_name", lambda name: False)


@pytest.fixture
def running_server(tmp_path, monkeypatch, offline):
    application = Application(
        web_dir=os.path.join(DIAGNOSTICS_DIR, "web"),
        locale_dir=os.path.join(DIAGNOSTICS_DIR, "locales"),
        helper_path="/nonexistent/diag-helper",
        config_path=str(tmp_path / "expected-config.json"),
        deployment_dir=str(tmp_path), host="127.0.0.1", port=0)
    application.privileged = FakePrivileged()
    application.sessions._privileged = application.privileged

    httpd = server_module.DiagnosticsServer(("127.0.0.1", 0), application)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", application
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def request(base, path, method="GET", body=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    all_headers = {"Accept": "application/json"}
    if data is not None:
        all_headers["Content-Type"] = "application/json"
    all_headers.update(headers or {})
    req = urllib.request.Request(base + path, data=data, headers=all_headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def mutating(extra=None):
    headers = {"X-Kassio-Diag": "1"}
    headers.update(extra or {})
    return headers


# ----------------------------------------------------------- happy paths


def test_health_answers(running_server):
    base, _ = running_server
    status, body, _ = request(base, "/api/health")
    assert status == 200
    assert json.loads(body)["data"]["version"]


def test_the_interface_is_served_with_a_strict_policy(running_server):
    base, _ = running_server
    status, body, headers = request(base, "/")
    assert status == 200
    assert b"<title>" in body
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"


def test_no_response_ever_sets_a_cookie(running_server):
    base, _ = running_server
    for path in ("/", "/api/health", "/api/meta", "/api/i18n/de"):
        _, _, headers = request(base, path)
        assert "Set-Cookie" not in headers


def test_translations_are_served(running_server):
    base, _ = running_server
    status, body, _ = request(base, "/api/i18n/ru")
    payload = json.loads(body)["data"]
    assert status == 200
    assert payload["language"] == "ru"
    assert payload["strings"]["ui.app_title"]


def test_meta_lists_actions_and_groups(running_server):
    base, _ = running_server
    _, body, _ = request(base, "/api/meta")
    data = json.loads(body)["data"]
    assert "devices" in data["groups"]
    identifiers = {action["id"] for action in data["actions"]}
    assert {"container.restart", "printer.adopt_found_ip", "devices.scan"} <= identifiers
    assert data["config_present"] is False


def test_a_missing_configuration_is_reported_not_fatal(running_server):
    base, _ = running_server
    status, body, _ = request(base, "/api/config")
    data = json.loads(body)["data"]
    assert status == 200
    assert data["config"] is None
    assert data["template"]["schema_version"] == 1
    assert [f["key"] for f in data["findings"]] == ["config.missing"]


# -------------------------------------------------------------- refusals


def test_a_foreign_local_user_is_refused(running_server, monkeypatch):
    base, _ = running_server
    monkeypatch.setattr(server_module, "peer_uid",
                        lambda *args, **kwargs: 65534)
    status, body, _ = request(base, "/api/health")
    assert status == 403
    assert json.loads(body)["error"]["key"] == "error.forbidden"


def test_an_unprovable_peer_is_refused(running_server, monkeypatch):
    base, _ = running_server
    monkeypatch.setattr(server_module, "peer_uid", lambda *args, **kwargs: None)
    for path in ("/api/health", "/api/report", "/api/containers/pos-backend/logs"):
        status, _, _ = request(base, path)
        assert status == 403, path


def test_root_is_accepted_as_a_peer(running_server, monkeypatch):
    base, _ = running_server
    monkeypatch.setattr(server_module, "peer_uid", lambda *args, **kwargs: 0)
    status, _, _ = request(base, "/api/health")
    assert status == 200


def test_a_mutation_without_the_custom_header_is_refused(running_server):
    base, _ = running_server
    status, body, _ = request(base, "/api/session", "POST", {"password": "x"})
    assert status == 400
    assert json.loads(body)["error"]["key"] == "error.bad_request_headers"


def test_a_mutation_from_a_foreign_origin_is_refused(running_server):
    base, _ = running_server
    status, _, _ = request(base, "/api/session", "POST", {"password": "x"},
                           mutating({"Origin": "https://evil.example"}))
    assert status == 400


def test_an_invalid_container_name_is_refused(running_server):
    base, _ = running_server
    status, body, _ = request(base, "/api/containers/pos-backend%0A/logs")
    assert status == 400
    assert json.loads(body)["error"]["key"] == "error.rejected_argument"


def test_a_wrong_sudo_password_is_refused(running_server):
    base, _ = running_server
    status, body, _ = request(base, "/api/session", "POST", {"password": "wrong"},
                              mutating())
    assert status == 401
    assert json.loads(body)["error"]["key"] == "error.bad_password"


def test_repeated_failures_lock_the_session_endpoint(running_server):
    base, _ = running_server
    for _ in range(5):
        request(base, "/api/session", "POST", {"password": "wrong"}, mutating())
    status, body, _ = request(base, "/api/session", "POST", {"password": "wrong"},
                              mutating())
    assert status == 429
    assert json.loads(body)["error"]["key"] == "error.locked_out"


def test_an_action_needing_sudo_is_refused_without_a_session(running_server):
    base, _ = running_server
    status, body, _ = request(base, "/api/actions/container.restart", "POST",
                              {"params": {"container": "pos-backend"}}, mutating())
    assert status == 401
    assert json.loads(body)["error"]["key"] == "error.session_required"


def test_an_action_needing_a_pos_login_is_refused_without_one(running_server):
    base, _ = running_server
    status, body, _ = request(base, "/api/actions/printer.test_print", "POST",
                              {"params": {}}, mutating())
    assert status == 401
    assert json.loads(body)["error"]["key"] == "pos.login_required"


def test_a_client_only_action_has_no_endpoint(running_server):
    base, _ = running_server
    status, _, _ = request(base, "/api/actions/printer.open_web_ui", "POST",
                           {"params": {}}, mutating())
    assert status == 404


def test_an_unknown_action_is_not_found(running_server):
    base, _ = running_server
    status, _, _ = request(base, "/api/actions/rm.rf", "POST", {}, mutating())
    assert status == 404


def test_a_malformed_action_name_is_not_found(running_server):
    base, _ = running_server
    status, _, _ = request(base, "/api/actions/..%2F..%2Fetc%2Fpasswd", "POST", {},
                           mutating())
    assert status == 404


def test_writing_the_configuration_needs_a_session(running_server):
    base, _ = running_server
    status, body, _ = request(base, "/api/config", "PUT", {"config": {}}, mutating())
    assert status == 401
    assert json.loads(body)["error"]["key"] == "error.session_required"


def test_an_unknown_path_is_not_found(running_server):
    base, _ = running_server
    status, _, _ = request(base, "/api/does-not-exist")
    assert status == 404


def test_static_paths_outside_the_table_are_not_served(running_server):
    base, _ = running_server
    for path in ("/../server.py", "/etc/passwd", "/kassio_diagnostics/server.py"):
        status, _, _ = request(base, path)
        assert status == 404, path


# ------------------------------------------------- degradation over HTTP


def test_checks_still_answer_when_the_helper_is_gone(running_server):
    base, _ = running_server
    status, body, _ = request(base, "/api/checks?force=1")
    payload = json.loads(body)["data"]
    assert status == 200
    assert payload["results"], "a broken helper must still produce results"
    assert all(result["status"] in ("ok", "warn", "fail", "unknown", "unavailable")
               for result in payload["results"])


def test_the_container_list_degrades_without_crashing(running_server):
    base, _ = running_server
    status, body, _ = request(base, "/api/containers")
    data = json.loads(body)["data"]
    assert status == 200
    assert data["available"] is False
    assert data["containers"] == []


def test_the_report_is_produced_even_with_no_data(running_server):
    base, _ = running_server
    status, body, headers = request(base, "/api/report?lang=de")
    assert status == 200
    assert headers["Content-Type"].startswith("text/plain")
    assert "attachment" in headers["Content-Disposition"]
    assert b"Support-Report" in body


# ------------------------------------------------- connection correctness


def test_a_rejected_request_leaves_the_connection_usable():
    """A rejection must still consume the request body.

    This connection is HTTP/1.1 and stays open. If an early rejection skipped
    the body, those bytes would be parsed as the next request line — and the
    browser reuses connections, so the customer's next click would break for a
    reason that has nothing to do with what they clicked.
    """
    import http.client

    # A dedicated server so the reuse is genuinely on one connection.
    application = Application(
        web_dir=os.path.join(DIAGNOSTICS_DIR, "web"),
        locale_dir=os.path.join(DIAGNOSTICS_DIR, "locales"),
        helper_path="/nonexistent/diag-helper",
        config_path="/nonexistent/expected-config.json",
        host="127.0.0.1", port=0)
    application.privileged = FakePrivileged()
    application.sessions._privileged = application.privileged
    httpd = server_module.DiagnosticsServer(("127.0.0.1", 0), application)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                              daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        payload = json.dumps({"params": {"container": "pos-backend"}})
        connection.request("POST", "/api/actions/container.restart", body=payload,
                           headers={"Content-Type": "application/json",
                                    "X-Kassio-Diag": "1"})
        first = connection.getresponse()
        first.read()
        assert first.status == 401

        # Same connection, straight after the rejection.
        connection.request("GET", "/api/health")
        second = connection.getresponse()
        body = second.read()
        assert second.status == 200
        assert json.loads(body)["ok"] is True
        connection.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_an_oversized_body_is_refused_without_breaking_the_connection(running_server):
    base, _ = running_server
    huge = {"params": {"container": "pos-backend", "padding": "x" * 700000}}
    status, _, _ = request(base, "/api/actions/container.restart", "POST", huge,
                           mutating())
    assert status in (400, 401)
    status, _, _ = request(base, "/api/health")
    assert status == 200


def test_head_sends_headers_without_a_body(running_server):
    import http.client
    base, _ = running_server
    port = int(base.rsplit(":", 1)[1])
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("HEAD", "/api/health")
    response = connection.getresponse()
    body = response.read()
    assert response.status == 200
    assert body == b""
    assert response.getheader("Content-Length") is not None
    connection.close()


def test_saving_the_configuration_records_when_it_happened():
    from kassio_diagnostics import config as config_module
    document = {"site": {"name": "Filiale", "technician": "t"}}
    stamped = config_module.stamp(document)
    assert stamped["site"]["configured_at"]
    assert stamped["site"]["name"] == "Filiale"
    assert stamped["site"]["technician"] == "t"


def test_stamping_tolerates_a_missing_site_block():
    from kassio_diagnostics import config as config_module
    stamped = config_module.stamp({"schema_version": 1})
    assert stamped["site"]["configured_at"]


def test_the_configuration_endpoint_offers_the_current_device_identity(running_server):
    base, _ = running_server
    _, body, _ = request(base, "/api/config")
    # The helper is a stub here, so the value is empty — the field must still
    # exist, because the setup panel keys its "record identity" button off it.
    assert "current_machine_id" in json.loads(body)["data"]
