"""Bluetooth SIG Object Transfer Service (OTS) client.

Spec-compliant client for the Dawn `nimble_ots` service. Talks to the
peripheral's GATT control points (OACP/OLCP) over Bleak and to the
companion L2CAP CoC channel (PSM 0x0025) via raw ``AF_BLUETOOTH``
sockets for bulk data transfer.

Linux-only (BlueZ >= 5.50, kernel >= 5.0). Most distros require root
or ``cap_net_raw,cap_net_admin`` on the Python interpreter to open
``AF_BLUETOOTH`` L2CAP sockets.
"""

from __future__ import annotations

import asyncio
import socket
import struct
from typing import Any, Optional

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

# ---------------------------------------------------------------------------
# OTS spec UUIDs / opcodes / constants
# ---------------------------------------------------------------------------

UUID_SERVICE = "00001825-0000-1000-8000-00805f9b34fb"
UUID_FEATURE = "00002abd-0000-1000-8000-00805f9b34fb"
UUID_OBJ_NAME = "00002abe-0000-1000-8000-00805f9b34fb"
UUID_OBJ_TYPE = "00002abf-0000-1000-8000-00805f9b34fb"
UUID_OBJ_SIZE = "00002ac0-0000-1000-8000-00805f9b34fb"
UUID_OBJ_ID = "00002ac3-0000-1000-8000-00805f9b34fb"
UUID_OBJ_PROPS = "00002ac4-0000-1000-8000-00805f9b34fb"
UUID_OACP = "00002ac5-0000-1000-8000-00805f9b34fb"
UUID_OLCP = "00002ac6-0000-1000-8000-00805f9b34fb"

# OACP request opcodes (BT OTS spec table 3.9).

OACP_READ = 0x04
OACP_WRITE = 0x06
OACP_ABORT = 0x07
OACP_RESPONSE = 0x60

# OLCP request opcodes (BT OTS spec table 3.16).

OLCP_FIRST = 0x01
OLCP_LAST = 0x02
OLCP_PREVIOUS = 0x03
OLCP_NEXT = 0x04
OLCP_GOTO = 0x05
OLCP_RESPONSE = 0x70

# OACP / OLCP result codes.

RES_SUCCESS = 0x01
RES_OPCODE_NS = 0x02
RES_INVALID_PARAM = 0x03
RES_OOR = 0x05  # OLCP only

# Object Properties bitmap (BT OTS spec table 3.6).

PROP_READ = 1 << 2
PROP_WRITE = 1 << 3
PROP_TRUNC = 1 << 5

# L2CAP CoC parameters (BT OTS spec § 4.6).

PSM_OTS = 0x0025
BDADDR_LE_PUBLIC = 0x01
BDADDR_LE_RANDOM = 0x02

# AF_BLUETOOTH socket options.

SOL_BLUETOOTH = 274
BT_SECURITY = 4
BT_SECURITY_LOW = 1


class IndicationWaiter:
    """Wait for a single OACP/OLCP indication on a characteristic."""

    def __init__(self, expected_resp_op: int):
        """Set up an awaitable that fires on @c expected_resp_op.

        :param expected_resp_op: Opcode byte that begins the response
            indication payload (``OACP_RESPONSE`` or ``OLCP_RESPONSE``).
        """
        self.expected = expected_resp_op
        self.event = asyncio.Event()
        self.payload: Optional[bytes] = None

    def __call__(self, _sender: Any, data: bytearray) -> None:
        """Bleak notification handler -- captures matching indications."""
        if not data:
            return
        if data[0] == self.expected:
            self.payload = bytes(data)
            self.event.set()

    async def wait(self, timeout: float = 5.0) -> bytes:
        """Block until the indication arrives or @c timeout elapses.

        :param timeout: Seconds to wait before raising
            :class:`asyncio.TimeoutError`.
        :return: Raw indication payload bytes.
        """
        await asyncio.wait_for(self.event.wait(), timeout=timeout)
        assert self.payload is not None
        return self.payload


class OtsObjectMeta:
    """Snapshot of a single OTS object's metadata."""

    __slots__ = ("index", "name", "size_current", "size_alloc", "props")

    def __init__(
        self,
        index: int,
        name: str,
        size_current: int,
        size_alloc: int,
        props: int,
    ) -> None:
        """Bundle the metadata fields read from one OTS object."""
        self.index = index
        self.name = name
        self.size_current = size_current
        self.size_alloc = size_alloc
        self.props = props

    @property
    def readable(self) -> bool:
        """Return ``True`` if Object Properties advertise read access."""
        return bool(self.props & PROP_READ)

    @property
    def writable(self) -> bool:
        """Return ``True`` if Object Properties advertise write access."""
        return bool(self.props & PROP_WRITE)

    def access_str(self) -> str:
        """Return a short ``R``/``W``/``-`` string for display."""
        flags = []
        if self.readable:
            flags.append("R")
        if self.writable:
            flags.append("W")
        return "".join(flags) or "-"


class OtsClient:
    """High-level OTS client driving GATT + L2CAP CoC together."""

    def __init__(self, client: BleakClient, addr_type: int):
        """Bind to an already-connected :class:`BleakClient`.

        :param client: Connected Bleak client for the peripheral.
        :param addr_type: BLE address type (``BDADDR_LE_PUBLIC`` or
            ``BDADDR_LE_RANDOM``) used when opening the L2CAP channel.
        """
        self.client = client
        self.addr_type = addr_type
        self.l2cap: Optional[socket.socket] = None

    @staticmethod
    async def from_name(name: str, timeout: float = 10.0) -> "OtsClient":
        """Scan for ``name`` and return a connected :class:`OtsClient`.

        :param name: Advertised GAP name (e.g. ``dawn-ots``).
        :param timeout: Scan timeout in seconds.
        :raises RuntimeError: If no device with that name is found.
        """
        device = await BleakScanner.find_device_by_name(name, timeout=timeout)
        if device is None:
            raise RuntimeError(f"BLE device '{name}' not found")
        return await OtsClient.from_device(device)

    @staticmethod
    async def from_device(device: BLEDevice) -> "OtsClient":
        """Connect to a discovered :class:`BLEDevice` and return a client.

        :param device: Bleak BLE device to connect to.
        """
        client = BleakClient(device)
        await client.connect()
        addr_type = BDADDR_LE_RANDOM
        details = getattr(device, "details", None) or {}
        if isinstance(details, dict):
            props = details.get("props") or {}
            if str(props.get("AddressType", "")).lower() == "public":
                addr_type = BDADDR_LE_PUBLIC
        return OtsClient(client, addr_type)

    async def disconnect(self) -> None:
        """Close the L2CAP channel (if open) and the GATT link."""
        await self.close_l2cap()
        await self.client.disconnect()

    # ------------------------------------------------------------------
    # OLCP / OACP control points
    # ------------------------------------------------------------------

    async def _olcp(self, request: bytes, timeout: float = 5.0) -> int:
        """Write to OLCP and await the matching indication."""
        waiter = IndicationWaiter(OLCP_RESPONSE)
        await self.client.start_notify(UUID_OLCP, waiter)
        try:
            await self.client.write_gatt_char(
                UUID_OLCP, request, response=True
            )
            resp = await waiter.wait(timeout=timeout)
        finally:
            await self.client.stop_notify(UUID_OLCP)
        return resp[2] if len(resp) >= 3 else 0xFF

    async def olcp_first(self) -> int:
        """Issue OLCP First. Return the OLCP result code."""
        return await self._olcp(bytes([OLCP_FIRST]))

    async def olcp_last(self) -> int:
        """Issue OLCP Last. Return the OLCP result code."""
        return await self._olcp(bytes([OLCP_LAST]))

    async def olcp_next(self) -> int:
        """Issue OLCP Next. Return the OLCP result code."""
        return await self._olcp(bytes([OLCP_NEXT]))

    async def olcp_previous(self) -> int:
        """Issue OLCP Previous. Return the OLCP result code."""
        return await self._olcp(bytes([OLCP_PREVIOUS]))

    async def olcp_goto(self, obj_id: int) -> int:
        """Issue OLCP Go To with a 48-bit object id."""
        return await self._olcp(
            bytes([OLCP_GOTO]) + obj_id.to_bytes(6, "little")
        )

    async def _oacp(self, request: bytes, timeout: float = 5.0) -> int:
        """Write to OACP and await the matching indication."""
        waiter = IndicationWaiter(OACP_RESPONSE)
        await self.client.start_notify(UUID_OACP, waiter)
        try:
            await self.client.write_gatt_char(
                UUID_OACP, request, response=True
            )
            resp = await waiter.wait(timeout=timeout)
        finally:
            await self.client.stop_notify(UUID_OACP)
        return resp[2] if len(resp) >= 3 else 0xFF

    async def oacp_read(self, offset: int, length: int) -> int:
        """Issue OACP Read. Return the OACP result code."""
        req = bytes([OACP_READ]) + struct.pack("<II", offset, length)
        return await self._oacp(req)

    async def oacp_write(
        self, offset: int, length: int, mode: int = 0x02
    ) -> int:
        """Issue OACP Write. ``mode`` bit 1 (0x02) means truncate.

        :param offset: Byte offset on the server.
        :param length: Number of bytes the client will stream.
        :param mode: Mode byte (BT OTS spec table 3.11).
        """
        req = (
            bytes([OACP_WRITE])
            + struct.pack("<II", offset, length)
            + bytes([mode])
        )
        return await self._oacp(req)

    async def oacp_abort(self) -> int:
        """Issue OACP Abort. Return the OACP result code."""
        return await self._oacp(bytes([OACP_ABORT]))

    # ------------------------------------------------------------------
    # Object metadata reads
    # ------------------------------------------------------------------

    async def read_feature(self) -> tuple[int, int]:
        """Read the OTS Feature characteristic.

        :return: ``(oacp_features, olcp_features)`` 32-bit bitmaps.
        """
        data = await self.client.read_gatt_char(UUID_FEATURE)
        if len(data) < 8:
            return 0, 0
        return struct.unpack("<II", bytes(data[:8]))

    async def read_object_name(self) -> str:
        """Read Object Name of the currently selected object."""
        data = await self.client.read_gatt_char(UUID_OBJ_NAME)
        return bytes(data).rstrip(b"\x00").decode("utf-8", errors="replace")

    async def read_object_size(self) -> tuple[int, int]:
        """Read Object Size: ``(current, allocated)`` in bytes."""
        data = await self.client.read_gatt_char(UUID_OBJ_SIZE)
        if len(data) < 8:
            return 0, 0
        return struct.unpack("<II", bytes(data[:8]))

    async def read_object_props(self) -> int:
        """Read Object Properties bitmap."""
        data = await self.client.read_gatt_char(UUID_OBJ_PROPS)
        if len(data) < 4:
            return 0
        return int(struct.unpack("<I", bytes(data[:4]))[0])

    # ------------------------------------------------------------------
    # Object iteration helpers
    # ------------------------------------------------------------------

    async def list_objects(self) -> list[OtsObjectMeta]:
        """Walk the OTS object list via OLCP First/Next.

        :return: A list of :class:`OtsObjectMeta` -- one entry per object.
        """
        objects: list[OtsObjectMeta] = []
        result = await self.olcp_first()
        if result != RES_SUCCESS:
            return objects
        while True:
            name = await self.read_object_name()
            cur, alloc = await self.read_object_size()
            props = await self.read_object_props()
            objects.append(
                OtsObjectMeta(len(objects), name, cur, alloc, props)
            )
            result = await self.olcp_next()
            if result != RES_SUCCESS:
                break
        return objects

    async def select_by_name(self, target: str) -> Optional[OtsObjectMeta]:
        """Walk the object list and select the entry named ``target``.

        :return: Metadata for the selected object, or ``None`` if no
            object with that name is found.
        """
        result = await self.olcp_first()
        idx = 0
        while result == RES_SUCCESS:
            name = await self.read_object_name()
            if name == target:
                cur, alloc = await self.read_object_size()
                props = await self.read_object_props()
                return OtsObjectMeta(idx, name, cur, alloc, props)
            result = await self.olcp_next()
            idx += 1
        return None

    # ------------------------------------------------------------------
    # L2CAP CoC bulk transfer
    # ------------------------------------------------------------------

    def _open_l2cap(self) -> socket.socket:
        """Open a fresh L2CAP CoC socket on PSM 0x0025."""
        sock = socket.socket(
            socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP
        )
        sock.setsockopt(
            SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", BT_SECURITY_LOW, 0)
        )
        sock.bind(("00:00:00:00:00:00", 0, 0, BDADDR_LE_PUBLIC))
        sock.connect((self.client.address, PSM_OTS, 0, self.addr_type))
        return sock

    async def open_l2cap(self) -> None:
        """Open the OTS CoC channel.

        Per OTS spec the channel must be established BEFORE OACP
        Read/Write is issued so the server can stream data immediately.
        """
        if self.l2cap is not None:
            return
        loop = asyncio.get_running_loop()
        self.l2cap = await loop.run_in_executor(None, self._open_l2cap)

    async def close_l2cap(self) -> None:
        """Close the OTS CoC channel if open."""
        if self.l2cap is None:
            return
        sock = self.l2cap
        self.l2cap = None
        await asyncio.get_running_loop().run_in_executor(None, sock.close)

    async def transfer_read(self, length: int, timeout: float = 10.0) -> bytes:
        """Receive ``length`` bytes on the open L2CAP channel.

        :param length: Number of bytes the server is going to send.
        :param timeout: Per-call RX timeout in seconds.
        :raises TimeoutError: If the transfer stalls before completion.
        """
        if self.l2cap is None:
            raise RuntimeError(
                "L2CAP channel not open; call open_l2cap() first"
            )
        loop = asyncio.get_running_loop()
        buf = bytearray()
        deadline = loop.time() + timeout
        while len(buf) < length:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"L2CAP RX timed out after {len(buf)}/{length} bytes"
                )
            self.l2cap.settimeout(remaining)
            try:
                chunk = await loop.run_in_executor(None, self.l2cap.recv, 4096)
            except socket.timeout:
                raise TimeoutError(
                    f"L2CAP RX timed out after {len(buf)}/{length} bytes"
                )
            if not chunk:
                break
            buf.extend(chunk)
        return bytes(buf[:length])

    async def transfer_write(self, payload: bytes, mtu: int = 256) -> None:
        """Send ``payload`` to the server on the open L2CAP channel.

        :param payload: Bytes to send.
        :param mtu: Maximum bytes per send call (per L2CAP MPS).
        """
        if self.l2cap is None:
            raise RuntimeError(
                "L2CAP channel not open; call open_l2cap() first"
            )
        loop = asyncio.get_running_loop()
        offset = 0
        while offset < len(payload):
            chunk = payload[offset : offset + mtu]
            await loop.run_in_executor(None, self.l2cap.send, chunk)
            offset += len(chunk)

    # ------------------------------------------------------------------
    # End-to-end helpers
    # ------------------------------------------------------------------

    async def read_object(
        self, name: str, *, offset: int = 0, length: Optional[int] = None
    ) -> bytes:
        """Select object ``name`` and read its content end-to-end.

        :raises RuntimeError: If the object is unknown or OACP rejects
            the request.
        """
        meta = await self.select_by_name(name)
        if meta is None:
            raise RuntimeError(f"OTS object '{name}' not found")
        if length is None:
            length = meta.size_current
        await self.open_l2cap()
        try:
            result = await self.oacp_read(offset, length)
            if result != RES_SUCCESS:
                raise RuntimeError(
                    f"OACP Read rejected (result=0x{result:02x})"
                )
            return await self.transfer_read(length)
        finally:
            await self.close_l2cap()

    async def write_object(
        self, name: str, payload: bytes, *, offset: int = 0, mode: int = 0x02
    ) -> int:
        """Select ``name`` and stream ``payload`` end-to-end.

        :return: New ``current size`` reported by the server after the
            transfer (read back from Object Size for confirmation).
        :raises RuntimeError: If the object is unknown or OACP rejects.
        """
        meta = await self.select_by_name(name)
        if meta is None:
            raise RuntimeError(f"OTS object '{name}' not found")
        await self.open_l2cap()
        try:
            result = await self.oacp_write(offset, len(payload), mode=mode)
            if result != RES_SUCCESS:
                raise RuntimeError(
                    f"OACP Write rejected (result=0x{result:02x})"
                )
            await self.transfer_write(payload)
        finally:
            await self.close_l2cap()
        new_size, _ = await self.read_object_size()
        return new_size
