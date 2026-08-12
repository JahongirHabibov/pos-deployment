# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""Expected-configuration schema and plausibility checks."""

import json

from kassio_diagnostics import config as config_module


def base():
    return {
        "schema_version": 1,
        "site": {"name": "Filiale", "technician": "t", "configured_at": "",
                 "language": "de"},
        "network": {"interface": "enp3s0", "subnet": "192.168.1.0/24",
                    "gateway": "192.168.1.1", "addressing": "static"},
        "identity": {"machine_id_hash": "sha256:abc"},
        "devices": [{"id": "receipt-1", "name": "Bondrucker", "role": "receipt_printer",
                     "ip": "192.168.1.50", "mac": "00:26:ab:12:34:56", "port": 9100}],
        "containers": ["pos-backend"],
    }


def keys(findings):
    return [finding.key for finding in findings]


def test_a_complete_configuration_has_no_findings():
    assert config_module.validate(base()) == []


def test_wrong_schema_version_is_an_error():
    document = base()
    document["schema_version"] = 99
    findings = config_module.validate(document)
    assert "config.schema_version" in keys(findings)
    assert config_module.has_errors(findings)


def test_device_outside_the_subnet_is_an_error():
    document = base()
    document["devices"][0]["ip"] = "10.0.0.5"
    findings = config_module.validate(document)
    assert "config.device_ip_outside_subnet" in keys(findings)
    assert config_module.has_errors(findings)


def test_duplicate_addresses_are_rejected():
    document = base()
    document["devices"].append(dict(document["devices"][0], id="receipt-2",
                                    mac="00:26:ab:99:99:99"))
    assert "config.device_ip_duplicate" in keys(config_module.validate(document))


def test_duplicate_identifiers_are_rejected():
    document = base()
    document["devices"].append(dict(document["devices"][0], ip="192.168.1.51",
                                    mac="00:26:ab:99:99:99"))
    assert "config.device_id_duplicate" in keys(config_module.validate(document))


def test_duplicate_hardware_identifiers_are_rejected():
    document = base()
    document["devices"].append(dict(document["devices"][0], id="receipt-2",
                                    ip="192.168.1.51"))
    assert "config.device_mac_duplicate" in keys(config_module.validate(document))


def test_missing_mac_is_a_warning_not_an_error():
    document = base()
    del document["devices"][0]["mac"]
    findings = config_module.validate(document)
    assert "config.device_mac_missing" in keys(findings)
    assert not config_module.has_errors(findings)


def test_invalid_mac_is_an_error():
    document = base()
    document["devices"][0]["mac"] = "00-26-ab-12-34"
    assert config_module.has_errors(config_module.validate(document))


def test_invalid_port_is_an_error():
    for port in (0, 70000, "9100", True, None):
        document = base()
        document["devices"][0]["port"] = port
        assert "config.device_port_invalid" in keys(config_module.validate(document))


def test_container_names_must_look_like_pos_containers():
    document = base()
    document["containers"] = ["pos-backend", "database", "pos-x\n"]
    findings = config_module.validate(document)
    assert keys(findings).count("config.container_name_invalid") == 2


def test_interface_with_leading_dash_is_rejected():
    document = base()
    document["network"]["interface"] = "-x"
    assert "config.interface_invalid" in keys(config_module.validate(document))


def test_gateway_outside_subnet_is_a_warning():
    document = base()
    document["network"]["gateway"] = "10.0.0.1"
    findings = config_module.validate(document)
    assert "config.gateway_outside_subnet" in keys(findings)
    assert not config_module.has_errors(findings)


def test_missing_file_is_reported_without_raising(tmp_path):
    document, findings = config_module.load(str(tmp_path / "absent.json"))
    assert document is None
    assert keys(findings) == ["config.missing"]


def test_broken_file_is_reported_without_raising(tmp_path):
    path = tmp_path / "expected-config.json"
    path.write_text("{ not json", encoding="utf-8")
    document, findings = config_module.load(str(path))
    assert document is None
    assert keys(findings) == ["config.unreadable"]


def test_valid_file_round_trips(tmp_path):
    path = tmp_path / "expected-config.json"
    path.write_text(json.dumps(base()), encoding="utf-8")
    document, findings = config_module.load(str(path))
    assert document["site"]["name"] == "Filiale"
    assert findings == []


def test_expected_containers_falls_back_to_the_default_set():
    assert config_module.expected_containers(None) == config_module.DEFAULT_CONTAINERS
    assert config_module.expected_containers({"containers": []}) == \
           config_module.DEFAULT_CONTAINERS
    assert config_module.expected_containers({"containers": ["pos-backend"]}) == \
           ["pos-backend"]
