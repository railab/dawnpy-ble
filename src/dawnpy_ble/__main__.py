"""Standalone CLI entry point for dawnpy-ble."""

import click

from dawnpy_ble.commands.cmd_ble import cmd_ble
from dawnpy_ble.commands.cmd_ots import cmd_ots


@click.group()
def cli() -> None:
    """Dawn BLE tools (descriptor-aware console + OTS client)."""


cli.add_command(cmd_ble)
cli.add_command(cmd_ots)


def main() -> None:
    """Run the BLE CLI."""
    cli(prog_name="dawnpy-ble")


if __name__ == "__main__":
    main()
