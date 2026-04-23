"""BLE protocol adapter for Dawn NimBLE devices."""

import asyncio
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dawnpy.simple_protocol import SimpleProtocolBase

from dawnpy_ble.profile import BleCharacteristicBinding, BleTransportProfile
from dawnpy_ble.services.common import (
    build_service_map,
    iter_services,
    normalize_uuid,
)
from dawnpy_ble.services.dump import DumpedService, dump_services
from dawnpy_ble.services.standard import StandardServicesHandler

BleClientFactory = Callable[..., Any]
NotificationCallback = Callable[[int, bytes], None]


@dataclass(frozen=True)
class ResolvedCharacteristic:
    """Concrete BLE characteristic selected for a Dawn binding."""

    binding: BleCharacteristicBinding
    characteristic: Any


class DawnBleProtocol(SimpleProtocolBase):
    """Descriptor-aware BLE client for Dawn NimBLE peripherals."""

    IO_TYPE_READ_ONLY = 0x01
    IO_TYPE_WRITE_ONLY = 0x02
    IO_TYPE_READ_WRITE = 0x03

    def __init__(
        self,
        identifier: str,
        profile: BleTransportProfile,
        timeout: float = 10.0,
        verbose: bool = False,
        client_factory: BleClientFactory | None = None,
    ) -> None:
        """Initialize BLE transport state."""
        super().__init__(verbose=verbose)
        self.identifier = identifier
        self.profile = profile
        self.timeout = timeout
        self._client_factory = client_factory
        self._client: Any = None
        self._resolved: dict[int, ResolvedCharacteristic] = {}
        self._unresolved_reasons: dict[int, str] = {}
        self._notification_callbacks: dict[int, NotificationCallback] = {}
        self._services: Any = None
        self._standard_services = StandardServicesHandler()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self.io_info = profile.discover_all_ios()

    def _create_client(self) -> Any:
        """Create a Bleak client instance."""
        if self._client_factory is not None:
            return self._client_factory(self.identifier, timeout=self.timeout)

        from bleak import BleakClient  # pragma: no cover

        return BleakClient(self.identifier, timeout=self.timeout)

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Start and return the dedicated BLE event loop."""
        if self._loop is not None:
            return self._loop

        ready = threading.Event()
        loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

        def runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop_holder["loop"] = loop
            ready.set()
            loop.run_forever()
            loop.close()

        thread = threading.Thread(
            target=runner,
            name="dawnpy-ble-loop",
            daemon=True,
        )
        thread.start()
        ready.wait()
        self._loop = loop_holder["loop"]
        self._loop_thread = thread
        return self._loop

    def _stop_loop(self) -> None:
        """Stop the dedicated BLE event loop."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=1.0)
        self._loop = None
        self._loop_thread = None

    def _run(self, coro: Any) -> Any:
        """Run one BLE coroutine on the dedicated event loop."""
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    async def _async_connect(self) -> bool:
        client = self._create_client()
        await client.connect()
        connected = getattr(client, "is_connected", None)
        if callable(connected):
            connected = connected()
        if connected is None:
            connected = True
        if not connected:
            return False

        services = await self._get_services(client)
        self._client = client
        self._services = services
        self._resolve_characteristics(services)
        return True

    async def _get_services(self, client: Any) -> Any:
        """Fetch BLE services from a connected client."""
        get_services = getattr(client, "get_services", None)
        if callable(get_services):
            return await get_services()
        return getattr(client, "services", None)

    def connect(self) -> bool:
        """Connect to the BLE device and resolve descriptor bindings."""
        try:
            connected = bool(self._run(self._async_connect()))
            if connected:
                self._log(
                    f"BLE connected to {self.identifier}; "
                    f"resolved {len(self._resolved)}"
                    f"/{len(self.profile.bindings)} "
                    "descriptor bindings"
                )
            return connected
        except Exception as exc:
            self._err(f"Failed to connect to BLE device: {exc}")
            return False

    async def _async_disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()

    def disconnect(self) -> None:
        """Disconnect from the BLE device."""
        if self._client is None:
            self._stop_loop()
            return
        try:
            for objid in list(self._notification_callbacks):
                try:
                    self._run(self._async_stop_notify(objid))
                except Exception:
                    pass
            self._run(self._async_disconnect())
        finally:
            self._client = None
            self._resolved = {}
            self._notification_callbacks = {}
            self._services = None
            self._standard_services.clear()
            self._stop_loop()

    def ping(self) -> bool:
        """Validate that the BLE transport resolved at least one binding."""
        return self._client is not None and bool(
            self._resolved or self._standard_services.characteristics
        )

    def discover_all_ios(self) -> dict[int, dict[str, Any]]:
        """Return descriptor-derived IO metadata."""
        return dict(self.io_info)

    def get_io_info(self, objid: int) -> dict[str, Any] | None:
        """Return descriptor-derived IO info for one object."""
        return self.io_info.get(objid)

    def get_io_list(self) -> list[int]:
        """Return the sorted list of bound IO object IDs."""
        return sorted(self.profile.bindings)

    def subscribe_io(self, objid: int, callback: NotificationCallback) -> bool:
        """Subscribe to BLE notifications for one IO object."""
        if self._client is None:
            self._err("BLE client is not connected")
            return False
        resolved = self._resolved.get(objid)
        if resolved is None:
            self._err(f"BLE notify binding unresolved for 0x{objid:08X}")
            return False
        if not self._supports_notify(resolved.characteristic):
            self._err(
                "BLE characteristic does not support notifications for "
                f"0x{objid:08X}"
            )
            return False
        try:
            self._run(self._async_start_notify(objid, callback))
        except Exception as exc:
            self._err(f"BLE notify subscribe failed for 0x{objid:08X}: {exc}")
            return False
        return True

    def unsubscribe_io(self, objid: int) -> bool:
        """Stop BLE notifications for one IO object."""
        if self._client is None:
            self._err("BLE client is not connected")
            return False
        if objid not in self._notification_callbacks:
            self._err(f"BLE notifications are not active for 0x{objid:08X}")
            return False
        try:
            self._run(self._async_stop_notify(objid))
        except Exception as exc:
            self._err(
                f"BLE notify unsubscribe failed for 0x{objid:08X}: {exc}"
            )
            return False
        return True

    def is_subscribed(self, objid: int) -> bool:
        """Return True when notifications are active for the IO."""
        return objid in self._notification_callbacks

    def read_standard_services(self) -> dict[str, Any]:
        """Read standard GAP, DIS, and BAS characteristics from the device."""
        if self._client is None:
            self._err("BLE client is not connected")
            return {}

        return self._standard_services.read(
            self._client,
            self._run,
            self._log,
            self._err,
        )

    def dump_all_services(self) -> list[DumpedService]:
        """Read and return the complete discovered GATT service tree."""
        if self._client is None:
            self._err("BLE client is not connected")
            return []
        return list(self._run(dump_services(self._client, self._services)))

    def read_io(self, objid: int) -> bytes | None:
        """Read an IO value from its bound BLE characteristic."""
        resolved = self._resolved.get(objid)
        if self._client is None:
            self._err("BLE client is not connected")
            return None
        if resolved is None:
            options = self.profile.get_binding_options(objid)
            if not options:
                self._err(
                    f"No BLE binding found for 0x{objid:08X}. "
                    "The descriptor may not expose this IO over NimBLE."
                )
            else:
                self._err(
                    self._unresolved_reasons.get(
                        objid,
                        (
                            "BLE binding unresolved for "
                            f"0x{objid:08X} ({options[0].io_id})"
                        ),
                    )
                )
            return None
        self._log(
            "BLE read request "
            f"objid=0x{objid:08X} io={resolved.binding.io_id} "
            f"service={resolved.binding.service_uuid} "
            f"char={resolved.binding.characteristic_uuid} "
            f"index={resolved.binding.characteristic_index} "
            "selected="
            f"{self._describe_characteristic(resolved.characteristic)}"
        )
        try:
            raw = bytes(
                self._run(self._client.read_gatt_char(resolved.characteristic))
            )
        except Exception as exc:
            self._err(f"BLE read failed for 0x{objid:08X}: {exc}")
            return None
        self._log(
            f"BLE read raw 0x{objid:08X}: "
            f"{len(raw)} bytes {raw.hex() or '<empty>'}"
        )
        decoded = self._decode_payload(resolved.binding, raw)
        if decoded is None:
            self._err(
                "BLE decode failed for "
                f"0x{objid:08X} ({resolved.binding.io_id}): "
                f"encoding={resolved.binding.encoding} "
                f"struct={resolved.binding.struct_format or '-'} "
                f"dtype_id={resolved.binding.dtype_id} "
                f"raw_len={len(raw)}"
            )
            return None
        self._log(
            "BLE decoded payload "
            f"0x{objid:08X}: {len(decoded)} bytes {decoded.hex()}"
        )
        return decoded

    def write_io(self, objid: int, data: bytes) -> bool:
        """Write raw bytes to a writable BLE characteristic."""
        resolved = self._resolved.get(objid)
        if (
            resolved is None
            or self._client is None
            or not resolved.binding.writable
        ):
            if resolved is None:
                self._err(f"BLE write binding unresolved for 0x{objid:08X}")
            elif not resolved.binding.writable:
                self._err(f"BLE binding for 0x{objid:08X} is not writable")
            return False
        self._log(
            "BLE write request "
            f"objid=0x{objid:08X} io={resolved.binding.io_id} "
            "selected="
            f"{self._describe_characteristic(resolved.characteristic)} "
            f"payload={bytes(data).hex()}"
        )
        try:
            self._run(
                self._client.write_gatt_char(
                    resolved.characteristic,
                    bytes(data),
                )
            )
        except Exception as exc:
            self._err(f"BLE write failed for 0x{objid:08X}: {exc}")
            return False
        return True

    def read_io_seek(self, objid: int) -> bytes | None:
        """BLE NimBLE mappings do not expose seekable IOs."""
        _ = objid
        return None

    def _decode_payload(
        self,
        binding: BleCharacteristicBinding,
        raw: bytes,
    ) -> bytes | None:
        """Convert BLE-native payloads into dawnpy-compatible raw IO bytes."""
        if binding.encoding == "raw":
            return raw

        if binding.encoding == "packed_scalar":
            if not binding.struct_format or binding.dtype_id is None:
                self._log(
                    "BLE packed_scalar decode missing metadata for "
                    f"{binding.io_id}"
                )
                return None
            size = struct.calcsize(binding.struct_format)
            if len(raw) < size:
                self._log(
                    "BLE packed_scalar decode short payload "
                    f"for {binding.io_id}: need {size}, got {len(raw)}"
                )
                return None
            value = struct.unpack(binding.struct_format, raw[:size])[0]
            return self.pack_data_by_dtype(binding.dtype_id, value)

        if binding.encoding == "scaled_float":
            if not binding.struct_format:
                self._log(
                    "BLE scaled_float decode missing struct for "
                    f"{binding.io_id}"
                )
                return None
            size = struct.calcsize(binding.struct_format)
            if len(raw) < size:
                self._log(
                    "BLE scaled_float decode short payload "
                    f"for {binding.io_id}: need {size}, got {len(raw)}"
                )
                return None
            value = struct.unpack(binding.struct_format, raw[:size])[0]
            scaled = float(value) / binding.scale
            return struct.pack("<f", scaled)

        self._log(
            f"BLE decode encountered unsupported encoding {binding.encoding}"
        )
        return None

    async def _async_start_notify(
        self, objid: int, callback: NotificationCallback
    ) -> None:
        resolved = self._resolved[objid]

        def handler(_: Any, data: bytearray | bytes) -> None:
            raw = bytes(data)
            self._log(
                "BLE notify raw "
                f"0x{objid:08X}: {len(raw)} bytes {raw.hex() or '<empty>'}"
            )
            decoded = self._decode_payload(resolved.binding, raw)
            if decoded is None:
                self._err(
                    "BLE notify decode failed for "
                    f"0x{objid:08X} ({resolved.binding.io_id})"
                )
                return
            callback(objid, decoded)

        await self._client.start_notify(resolved.characteristic, handler)
        self._notification_callbacks[objid] = callback
        self._log(
            "BLE notifications enabled "
            f"objid=0x{objid:08X} io={resolved.binding.io_id} "
            "selected="
            f"{self._describe_characteristic(resolved.characteristic)}"
        )

    async def _async_stop_notify(self, objid: int) -> None:
        resolved = self._resolved.get(objid)
        if resolved is None:
            self._notification_callbacks.pop(objid, None)
            return
        await self._client.stop_notify(resolved.characteristic)
        self._notification_callbacks.pop(objid, None)
        self._log(
            "BLE notifications disabled "
            f"objid=0x{objid:08X} io={resolved.binding.io_id}"
        )

    def _resolve_characteristics(self, services: Any) -> None:  # noqa: C901
        """Map descriptor bindings onto discovered GATT characteristics."""
        service_map = build_service_map(services)
        for service in self._iter_services(services):
            service_uuid = self._normalize_uuid(getattr(service, "uuid", ""))
            self._log(f"BLE service discovered: {service_uuid}")
            for characteristic in getattr(service, "characteristics", []):
                char_uuid = self._normalize_uuid(
                    getattr(characteristic, "uuid", "")
                )
                self._log(
                    f"  characteristic {char_uuid} "
                    f"{self._describe_characteristic(characteristic)}"
                )

        resolved: dict[int, ResolvedCharacteristic] = {}
        unresolved_reasons: dict[int, str] = {}
        for binding in self.profile.iter_bindings():
            if binding.objid in resolved:
                continue

            options = self.profile.get_binding_options(binding.objid)
            if not options:
                options = [binding]
            candidate_counts: list[str] = []
            selected: ResolvedCharacteristic | None = None

            for option in options:
                key = (
                    self._normalize_uuid(option.service_uuid),
                    self._normalize_uuid(option.characteristic_uuid),
                )
                candidates = service_map.get(key, [])
                candidate_counts.append(
                    f"{option.service_uuid}/{option.characteristic_uuid}"
                    f"[{option.characteristic_index}]={len(candidates)}"
                )
                self._log(
                    "Resolving BLE binding "
                    f"objid=0x{option.objid:08X} io={option.io_id} "
                    f"service={option.service_uuid} "
                    f"char={option.characteristic_uuid} "
                    f"index={option.characteristic_index} "
                    f"candidates={len(candidates)}"
                )
                candidates = self._filter_candidates_for_binding(
                    option, candidates
                )
                if option.characteristic_index >= len(candidates):
                    self._log(
                        "  unresolved: candidate index out of range for "
                        f"0x{option.objid:08X}"
                    )
                    continue
                selected = ResolvedCharacteristic(
                    binding=option,
                    characteristic=candidates[option.characteristic_index],
                )
                resolved_char = candidates[option.characteristic_index]
                self._log(
                    "  resolved to "
                    f"{self._describe_characteristic(resolved_char)}"
                )
                break

            if selected is not None:
                resolved[binding.objid] = selected
                continue

            unresolved_reasons[binding.objid] = (
                f"BLE binding unresolved for 0x{binding.objid:08X} "
                f"({binding.io_id}): no matching characteristic exposed by "
                "the device for any descriptor binding. Tried "
                + ", ".join(candidate_counts)
            )
        self._resolved = resolved
        self._unresolved_reasons = unresolved_reasons
        self._standard_services.resolve(service_map)

    @staticmethod
    def _iter_services(services: Any) -> Any:
        """Iterate over service collections exposed by Bleak."""
        return iter_services(services)

    @staticmethod
    def _normalize_uuid(value: str) -> str:
        return normalize_uuid(value)

    @staticmethod
    def _describe_characteristic(characteristic: Any) -> str:
        """Return a compact description of a Bleak characteristic object."""
        uuid = getattr(characteristic, "uuid", "?")
        handle = getattr(characteristic, "handle", "?")
        properties = getattr(characteristic, "properties", None)
        props = ",".join(properties) if isinstance(properties, list) else "?"
        return f"(uuid={uuid}, handle={handle}, props={props})"

    @staticmethod
    def _supports_notify(characteristic: Any) -> bool:
        """Return True when the characteristic supports notify/indicate."""
        properties = getattr(characteristic, "properties", None)
        if not isinstance(properties, list):
            return False
        return "notify" in properties or "indicate" in properties

    @staticmethod
    def _filter_candidates_for_binding(
        binding: BleCharacteristicBinding,
        candidates: list[Any],
    ) -> list[Any]:
        """Filter repeated UUID candidates by the binding access mode."""
        write_props = {"write", "write-without-response"}
        candidate_props = [
            (
                characteristic,
                set(getattr(characteristic, "properties", []) or []),
            )
            for characteristic in candidates
        ]
        if binding.writable:
            filtered = [
                characteristic
                for characteristic, props in candidate_props
                if write_props & props
            ]
        else:
            filtered = [
                characteristic
                for characteristic, props in candidate_props
                if "read" in props and not (write_props & props)
            ]
        if filtered and binding.characteristic_index < len(filtered):
            return filtered
        return candidates
