"""Module containing BLE command."""

import click
from dawnpy.cli.environment import Environment, pass_environment
from dawnpy.cli.options import configure_cli_logging

from dawnpy_ble.client import dump_services, run_console
from dawnpy_ble.scanner import BleScanResult, scan_devices


@click.command(name="ble")
@click.argument("identifier", type=str, required=False)
@click.option(
    "--descriptor",
    "-d",
    "descriptor_path",
    type=click.Path(exists=True, dir_okay=True, path_type=str),
    required=False,
    help="Descriptor file or directory with descriptor.yaml",
)
@click.option(
    "--debug/--no-debug",
    default=False,
    is_flag=True,
    envvar="DAWNPY_DEBUG",
)
@click.option(
    "--scan",
    is_flag=True,
    default=False,
    help="Scan nearby BLE devices and choose one interactively",
)
@click.option(
    "--scan-timeout",
    type=float,
    default=5.0,
    show_default=True,
    help="BLE scan duration in seconds",
)
@click.option(
    "--dump-services",
    "dump_services_requested",
    is_flag=True,
    default=False,
    help="Connect and dump all GATT services instead of opening the console",
)
@pass_environment
def cmd_ble(
    ctx: Environment,
    identifier: str | None,
    descriptor_path: str | None,
    debug: bool,
    scan: bool,
    scan_timeout: float,
    dump_services_requested: bool,
) -> bool:
    """Run BLE console for descriptor-aware NimBLE device access."""
    ctx.debug = debug
    configure_cli_logging(debug)

    resolved_identifier = identifier
    if scan:
        resolved_identifier = _choose_scanned_device(scan_timeout)

    if not resolved_identifier:
        raise click.ClickException(
            "Provide a BLE device identifier or use --scan"
        )

    if dump_services_requested:
        if not dump_services_fn(resolved_identifier, debug=ctx.debug):
            raise click.ClickException("Failed to dump BLE services")
        return True

    if descriptor_path is None:
        raise click.ClickException(
            "Provide --descriptor for descriptor-aware BLE console access"
        )

    run_console(
        identifier=resolved_identifier,
        descriptor_path=descriptor_path,
        debug=ctx.debug,
    )
    return True


def dump_services_fn(identifier: str, debug: bool = False) -> bool:
    """Dump services through a wrapper that click tests can monkeypatch."""
    return dump_services(identifier, debug=debug)


def _choose_scanned_device(scan_timeout: float) -> str:
    """Scan and prompt the user to select a BLE device."""
    devices = scan_devices(timeout=scan_timeout)
    if not devices:
        raise click.ClickException("No BLE devices found")

    click.echo("Scanned BLE devices:")
    for index, device in enumerate(devices, start=1):
        click.echo(f"{index}. {device.label}")

    choice = click.prompt(
        "Select device",
        type=click.IntRange(1, len(devices)),
        default=1,
    )
    selected: BleScanResult = devices[choice - 1]
    click.echo(f"Using {selected.label}")
    return selected.address
