"""Shared helpers for BLE service handlers."""

from collections.abc import Iterable
from typing import Any, cast

ServiceMap = dict[tuple[str, str], list[Any]]


def normalize_uuid(value: str) -> str:
    """Return a canonical UUID string for comparisons."""
    return str(value).lower()


def iter_services(services: Any) -> Iterable[Any]:
    """Iterate over service collections exposed by Bleak."""
    if services is None:
        return []
    if isinstance(services, dict):
        return services.values()
    registry = getattr(services, "services", None)
    if isinstance(registry, dict):
        return registry.values()
    return cast(Iterable[Any], services)


def build_service_map(services: Any) -> ServiceMap:
    """Index characteristics by service UUID and characteristic UUID."""
    service_map: ServiceMap = {}
    for service in iter_services(services):
        service_uuid = normalize_uuid(getattr(service, "uuid", ""))
        for characteristic in getattr(service, "characteristics", []):
            char_uuid = normalize_uuid(getattr(characteristic, "uuid", ""))
            service_map.setdefault((service_uuid, char_uuid), []).append(
                characteristic
            )
    return service_map
