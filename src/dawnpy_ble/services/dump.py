"""Generic GATT service dump helpers."""

from dataclasses import dataclass
from typing import Any

from dawnpy_ble.services.common import iter_services


@dataclass(frozen=True)
class DumpedCharacteristic:
    """Single characteristic captured from a service dump."""

    uuid: str
    handle: Any
    properties: tuple[str, ...]
    value: bytes | None = None
    error: str | None = None


@dataclass(frozen=True)
class DumpedService:
    """Single BLE service captured from a service dump."""

    uuid: str
    characteristics: tuple[DumpedCharacteristic, ...]


async def dump_services(client: Any, services: Any) -> list[DumpedService]:
    """Read and describe every discovered GATT service."""
    dumped: list[DumpedService] = []
    for service in iter_services(services):
        characteristics = []
        for characteristic in getattr(service, "characteristics", []):
            props = tuple(getattr(characteristic, "properties", []) or [])
            value = None
            error = None
            if "read" in props:
                try:
                    value = bytes(await client.read_gatt_char(characteristic))
                except Exception as exc:
                    error = str(exc)
            characteristics.append(
                DumpedCharacteristic(
                    uuid=str(getattr(characteristic, "uuid", "")),
                    handle=getattr(characteristic, "handle", None),
                    properties=props,
                    value=value,
                    error=error,
                )
            )
        dumped.append(
            DumpedService(
                uuid=str(getattr(service, "uuid", "")),
                characteristics=tuple(characteristics),
            )
        )
    return dumped
