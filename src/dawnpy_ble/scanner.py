"""BLE device discovery helpers."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

BleDiscoverFactory = Callable[..., Any]


@dataclass(frozen=True)
class BleScanResult:
    """One BLE scan result."""

    name: str
    address: str
    rssi: int | None = None

    @property
    def label(self) -> str:
        """Return a human-readable scan label."""
        if self.rssi is None:
            return f"{self.name} ({self.address})"
        return f"{self.name} ({self.address}, RSSI {self.rssi})"


async def _async_scan(
    timeout: float,
    discover_factory: BleDiscoverFactory | None = None,
) -> list[BleScanResult]:
    if discover_factory is None:
        from bleak import BleakScanner  # pragma: no cover

        discover_factory = BleakScanner.discover

    devices = await discover_factory(timeout=timeout)
    results: list[BleScanResult] = []
    for device in devices:
        name = str(getattr(device, "name", "") or "Unnamed device")
        address = str(
            getattr(device, "address", "")
            or getattr(device, "identifier", "")
            or ""
        )
        rssi = getattr(device, "rssi", None)
        results.append(
            BleScanResult(
                name=name,
                address=address,
                rssi=int(rssi) if isinstance(rssi, int) else None,
            )
        )
    results.sort(key=lambda item: (item.name.lower(), item.address))
    return results


def scan_devices(
    timeout: float = 5.0,
    discover_factory: BleDiscoverFactory | None = None,
) -> list[BleScanResult]:
    """Scan nearby BLE devices."""
    return asyncio.run(_async_scan(timeout, discover_factory))
