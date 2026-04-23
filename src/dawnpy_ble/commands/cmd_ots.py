"""CLI commands for the Bluetooth SIG Object Transfer Service (OTS).

Subcommands map 1:1 onto the OACP / OLCP procedures and the L2CAP CoC
data path implemented by Dawn's `nimble_ots` peripheral service:

* ``scan``  -- discover advertising peripherals.
* ``list``  -- walk the OTS object list and print metadata.
* ``read``  -- fetch one object's content to a file or stdout.
* ``write`` -- stream a local file into one writable OTS object.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Optional

import click
from bleak import BleakScanner

from dawnpy_ble.services.ots import RES_SUCCESS, OtsClient

DEFAULT_NAME_HINT = "dawn-ots"


@click.group(name="ots")
def cmd_ots() -> None:
    """Bluetooth Object Transfer Service (OTS) client commands."""


@cmd_ots.command(name="scan")
@click.option(
    "--timeout",
    type=float,
    default=5.0,
    show_default=True,
    help="BLE scan duration in seconds.",
)
def cmd_ots_scan(timeout: float) -> None:
    """Scan for nearby BLE peripherals and print their addresses."""
    asyncio.run(_run_scan(timeout))


@cmd_ots.command(name="list")
@click.option(
    "--name",
    "name",
    required=True,
    metavar="GAP_NAME",
    help=f"Advertised GAP name (e.g. '{DEFAULT_NAME_HINT}').",
)
def cmd_ots_list(name: str) -> None:
    """Walk the OTS object list on the named peripheral."""
    asyncio.run(_run_list(name))


@cmd_ots.command(name="read")
@click.option(
    "--name",
    "name",
    required=True,
    metavar="GAP_NAME",
    help="Advertised GAP name.",
)
@click.option(
    "--object",
    "object_name",
    required=True,
    metavar="OBJ",
    help="OTS Object Name to read (must match server side).",
)
@click.option(
    "--offset",
    type=int,
    default=0,
    show_default=True,
    help="Byte offset to start reading from.",
)
@click.option(
    "--length",
    type=int,
    default=None,
    help="Bytes to read (defaults to current object size).",
)
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=str),
    default=None,
    help="Output file path (omit to write to stdout).",
)
def cmd_ots_read(
    name: str,
    object_name: str,
    offset: int,
    length: Optional[int],
    out_path: Optional[str],
) -> None:
    """Read an OTS object end-to-end (OACP Read + L2CAP RX)."""
    asyncio.run(_run_read(name, object_name, offset, length, out_path))


@cmd_ots.command(name="write")
@click.option(
    "--name",
    "name",
    required=True,
    metavar="GAP_NAME",
    help="Advertised GAP name.",
)
@click.option(
    "--object",
    "object_name",
    required=True,
    metavar="OBJ",
    help="OTS Object Name to write (must be writable).",
)
@click.option(
    "--offset",
    type=int,
    default=0,
    show_default=True,
    help="Server byte offset; usually 0 (truncate-and-replace).",
)
@click.option(
    "--in",
    "in_path",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    required=True,
    help="Local file containing the payload to send.",
)
def cmd_ots_write(
    name: str, object_name: str, offset: int, in_path: str
) -> None:
    """Write a local file into an OTS object (OACP Write + L2CAP TX)."""
    asyncio.run(_run_write(name, object_name, offset, in_path))


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _run_scan(timeout: float) -> None:
    """Discover BLE devices and print address + name pairs."""
    click.echo(f"Scanning for {timeout}s ...")
    devices = await BleakScanner.discover(timeout=timeout)
    if not devices:
        click.echo("  (no devices)")
        return
    for dev in devices:
        click.echo(f"  {dev.address}  {dev.name or '<no-name>'}")


async def _run_list(name: str) -> None:
    """Connect, read OTS Feature, walk the object list, and print rows."""
    ots = await OtsClient.from_name(name)
    try:
        feat_oacp, feat_olcp = await ots.read_feature()
        click.echo(
            f"OTS Feature: OACP=0x{feat_oacp:08x} OLCP=0x{feat_olcp:08x}"
        )
        objs = await ots.list_objects()
        if not objs:
            click.echo("  (no objects)")
            return
        click.echo("Objects:")
        for o in objs:
            click.echo(
                f"  [{o.index}] {o.name}  size={o.size_current}B  "
                f"props=0x{o.props:08x} ({o.access_str()})"
            )
    finally:
        await ots.disconnect()


async def _run_read(
    name: str,
    object_name: str,
    offset: int,
    length: Optional[int],
    out_path: Optional[str],
) -> None:
    """Resolve the object, perform OACP Read + L2CAP RX, save the result."""
    ots = await OtsClient.from_name(name)
    try:
        meta = await ots.select_by_name(object_name)
        if meta is None:
            raise click.ClickException(
                f"OTS object '{object_name}' not found on '{name}'"
            )
        n = meta.size_current if length is None else length
        click.echo(f"Reading '{meta.name}' size={n}B ...")
        await ots.open_l2cap()
        try:
            result = await ots.oacp_read(offset, n)
            if result != RES_SUCCESS:
                raise click.ClickException(
                    f"OACP Read rejected, result=0x{result:02x}"
                )
            data = await ots.transfer_read(n)
        finally:
            await ots.close_l2cap()
        if out_path is not None:
            Path(out_path).write_bytes(data)
            click.echo(f"Wrote {len(data)} bytes to {out_path}")
        else:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    finally:
        await ots.disconnect()


async def _run_write(
    name: str, object_name: str, offset: int, in_path: str
) -> None:
    """Resolve the object, perform OACP Write + L2CAP TX, confirm size."""
    payload = Path(in_path).read_bytes()
    ots = await OtsClient.from_name(name)
    try:
        new_size = await ots.write_object(
            object_name, payload, offset=offset, mode=0x02
        )
        click.echo(
            f"Done. Server reports object size = {new_size}B "
            f"(wrote {len(payload)}B at offset {offset})."
        )
    finally:
        await ots.disconnect()
