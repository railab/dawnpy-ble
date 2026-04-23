"""Handlers for standard BLE services used by Dawn devices."""

import struct
from collections.abc import Callable
from typing import Any

from dawnpy_ble.services import bas, device_information, gap, tps
from dawnpy_ble.services.common import ServiceMap, normalize_uuid

RunCoroutine = Callable[[Any], Any]
LogMessage = Callable[[str], None]

STANDARD_CHARACTERISTICS = {
    **gap.CHARACTERISTICS,
    **device_information.CHARACTERISTICS,
    **bas.CHARACTERISTICS,
    **tps.CHARACTERISTICS,
}


class StandardServicesHandler:
    """Resolve and read standard GAP/DIS/BAS/TPS characteristics."""

    def __init__(self) -> None:
        """Initialize empty handler state."""
        self.characteristics: dict[str, Any] = {}

    def resolve(self, service_map: ServiceMap) -> None:
        """Resolve known standard characteristics from a GATT service map."""
        characteristics: dict[str, Any] = {}
        for key, (
            service_uuid,
            characteristic_uuid,
            _encoding,
        ) in STANDARD_CHARACTERISTICS.items():
            candidates = service_map.get(
                (
                    normalize_uuid(service_uuid),
                    normalize_uuid(characteristic_uuid),
                ),
                [],
            )
            if candidates:
                characteristics[key] = candidates[0]
        self.characteristics = characteristics

    def clear(self) -> None:
        """Forget resolved standard characteristics."""
        self.characteristics = {}

    def read(
        self,
        client: Any,
        run: RunCoroutine,
        log: LogMessage,
        err: LogMessage,
    ) -> dict[str, Any]:
        """Read all resolved standard characteristics."""
        results: dict[str, Any] = {}
        for key, characteristic in self.characteristics.items():
            _, _, encoding = STANDARD_CHARACTERISTICS[key]
            try:
                raw = bytes(run(client.read_gatt_char(characteristic)))
            except Exception as exc:
                err(f"BLE standard read failed for {key}: {exc}")
                continue
            decoded = decode_standard_value(encoding, raw)
            results[key] = decoded
            log(
                f"BLE standard read {key}: "
                f"{raw.hex() or '<empty>'} -> {decoded}"
            )
        return results


def decode_standard_value(encoding: str, raw: bytes) -> Any:
    """Decode common GATT standard characteristic payloads."""
    if encoding == "utf8":
        return raw.decode("utf-8", errors="replace")
    if encoding == "u16":
        if len(raw) < 2:
            return None
        return struct.unpack("<H", raw[:2])[0]
    if encoding == "u8_percent":
        if len(raw) < 1:
            return None
        return int(raw[0])
    if encoding == "s8_dbm":
        if len(raw) < 1:
            return None
        return struct.unpack("<b", raw[:1])[0]
    return raw.hex()
