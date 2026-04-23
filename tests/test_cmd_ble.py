"""Tests for the BLE command."""

from click.testing import CliRunner

import dawnpy_ble.commands.cmd_ble as cmd_ble_module
from dawnpy_ble.commands.cmd_ble import cmd_ble
from dawnpy_ble.scanner import BleScanResult


def test_cmd_ble_scans_and_chooses_device(monkeypatch):
    runner = CliRunner()
    calls = {}

    monkeypatch.setattr(
        cmd_ble_module,
        "scan_devices",
        lambda timeout=5.0: [
            BleScanResult(name="Thingy", address="AA:BB", rssi=-40),
            BleScanResult(name="Other", address="CC:DD", rssi=-55),
        ],
    )

    def fake_run_console(identifier, descriptor_path, debug):
        calls["run_console"] = (identifier, descriptor_path, debug)

    monkeypatch.setattr(cmd_ble_module, "run_console", fake_run_console)

    result = runner.invoke(
        cmd_ble,
        ["--scan", "-d", ".", "--scan-timeout", "2.5"],
        input="2\n",
    )

    assert result.exit_code == 0
    assert "Scanned BLE devices:" in result.output
    assert calls["run_console"] == ("CC:DD", ".", False)


def test_cmd_ble_requires_identifier_or_scan():
    runner = CliRunner()

    result = runner.invoke(cmd_ble, ["-d", "."])

    assert result.exit_code != 0
    assert "Provide a BLE device identifier or use --scan" in result.output


def test_cmd_ble_requires_descriptor_for_console():
    runner = CliRunner()

    result = runner.invoke(cmd_ble, ["AA:BB"])

    assert result.exit_code != 0
    assert "Provide --descriptor" in result.output


def test_cmd_ble_dumps_services_without_descriptor(monkeypatch):
    runner = CliRunner()
    calls = {}

    def fake_dump_services(identifier, debug=False):
        calls["dump_services"] = (identifier, debug)
        return True

    monkeypatch.setattr(cmd_ble_module, "dump_services_fn", fake_dump_services)

    result = runner.invoke(cmd_ble, ["AA:BB", "--dump-services"])

    assert result.exit_code == 0
    assert calls["dump_services"] == ("AA:BB", False)
