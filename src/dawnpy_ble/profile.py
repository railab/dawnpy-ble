"""Descriptor-aware BLE binding helpers for Dawn NimBLE devices."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from dawnpy.descriptor.client import ClientDescriptor, ClientIo
from dawnpy.descriptor.definitions.summary import ObjectIdResolver
from dawnpy.descriptor.support.utils import resolve_flexible_reference

AIOS_SERVICE_UUID = "00001815-0000-1000-8000-00805f9b34fb"
BAS_SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"
ESS_SERVICE_UUID = "0000181a-0000-1000-8000-00805f9b34fb"
IMDS_SERVICE_UUID = "0000185a-0000-1000-8000-00805f9b34fb"

DIGITAL_CHAR_UUID = "00002a56-0000-1000-8000-00805f9b34fb"
ANALOG_CHAR_UUID = "00002a58-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL_CHAR_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

_SENSOR_SPECS = {
    "temperature": ("00002a6e-0000-1000-8000-00805f9b34fb", "<h", 100.0),
    "humidity": ("00002a6f-0000-1000-8000-00805f9b34fb", "<h", 100.0),
    "pressure": ("00002a6d-0000-1000-8000-00805f9b34fb", "<I", 100.0),
    "uv_index": ("00002a76-0000-1000-8000-00805f9b34fb", "<h", 1.0),
    # Non-standard: 0x272A is not a Bluetooth SIG-assigned characteristic.
    "gas_resistance": (
        "0000272a-0000-1000-8000-00805f9b34fb",
        "<h",
        1.0,
    ),
}
_AIOS_GROUP_ORDER = (
    ("digital_inputs", DIGITAL_CHAR_UUID, False),
    ("digital_outputs", DIGITAL_CHAR_UUID, True),
    ("analog_inputs", ANALOG_CHAR_UUID, False),
    ("analog_outputs", ANALOG_CHAR_UUID, True),
)
_SENSOR_ORDER = tuple(_SENSOR_SPECS.keys())


def _aios_binding_ref(ref: Any) -> str | None:
    """Return AIOS IO id from a scalar, IO object, or metadata wrapper."""
    if isinstance(ref, dict) and ("data" in ref or "io" in ref):
        return resolve_flexible_reference(ref.get("data", ref.get("io")))
    return resolve_flexible_reference(ref)


@dataclass(frozen=True)
class BleCharacteristicBinding:
    """Maps one Dawn IO object to a BLE characteristic."""

    objid: int
    io_id: str
    service_uuid: str
    characteristic_uuid: str
    characteristic_index: int
    dtype: str
    dtype_id: int | None
    io_type: int
    io_type_str: str
    writable: bool
    encoding: str = "raw"
    struct_format: str = ""
    scale: float = 1.0
    source: str = "built_in"


@dataclass(frozen=True)
class OtsObjectBinding:
    """Descriptor-declared OTS object metadata."""

    name: str
    ots_type: str
    access: str
    io_id: str


@dataclass(frozen=True)
class BleTransportProfile:
    """Descriptor-derived BLE access model for one NimBLE protocol."""

    bindings: dict[int, BleCharacteristicBinding]
    binding_options: dict[int, list[BleCharacteristicBinding]] = field(
        default_factory=dict
    )
    gap_name: str = ""
    enabled_services: tuple[str, ...] = ()
    service_details: dict[str, Any] = field(default_factory=dict)
    ots_objects: tuple[OtsObjectBinding, ...] = ()

    @classmethod
    def from_descriptor(  # noqa: C901
        cls, descriptor: ClientDescriptor
    ) -> BleTransportProfile:
        """Build a BLE profile from the first NimBLE protocol in descriptor."""
        proto = descriptor.get_protocol("nimble")
        if proto is None:
            raise ValueError("Descriptor does not contain a NimBLE protocol")

        resolver = ObjectIdResolver()
        dtype_ids = {
            info["type"]: dtype_id
            for dtype_id, info in resolver.decoder.dtype_info.items()
        }
        bindings: dict[int, BleCharacteristicBinding] = {}
        binding_options: dict[int, list[BleCharacteristicBinding]] = {}
        occurrence_counts: dict[tuple[str, str, bool], int] = {}
        enabled_services: list[str] = []
        service_details: dict[str, Any] = {}
        services = proto.config.get("services", {})
        if not isinstance(services, dict):
            services = {}

        def add_binding(
            io_id: str,
            io: ClientIo,
            *,
            service_uuid: str,
            characteristic_uuid: str,
            writable: bool,
            encoding: str = "raw",
            struct_format: str = "",
            scale: float = 1.0,
            source: str = "built_in",
        ) -> None:
            objid = resolver.io_objid(io)
            if objid is None:
                return

            key = (service_uuid, characteristic_uuid, writable)
            index = occurrence_counts.get(key, 0)
            occurrence_counts[key] = index + 1
            io_type = 0x03 if writable else 0x01
            binding = BleCharacteristicBinding(
                objid=objid,
                io_id=io_id,
                service_uuid=service_uuid,
                characteristic_uuid=characteristic_uuid,
                characteristic_index=index,
                dtype=io.dtype,
                dtype_id=dtype_ids.get(io.dtype),
                io_type=io_type,
                io_type_str=("Read-Write" if writable else "Read-Only"),
                writable=writable,
                encoding=encoding,
                struct_format=struct_format,
                scale=scale,
                source=source,
            )
            binding_options.setdefault(objid, []).append(binding)
            bindings.setdefault(objid, binding)

        bas = services.get("bas", {})
        if isinstance(bas, dict):
            enabled_services.append("bas")
            io_id = resolve_flexible_reference(bas.get("battery_level"))
            io = descriptor.get_io(io_id) if io_id else None
            if io_id and io:
                service_details["bas"] = {"battery_level": io_id}
                add_binding(
                    io_id,
                    io,
                    service_uuid=BAS_SERVICE_UUID,
                    characteristic_uuid=BATTERY_LEVEL_CHAR_UUID,
                    writable=False,
                    encoding="packed_scalar",
                    struct_format="<B",
                )

        aios = services.get("aios", {})
        if isinstance(aios, dict):
            enabled_services.append("aios")
            groups = aios.get("groups", [])
            aios_details: dict[str, list[str]] = {
                "digital_inputs": [],
                "digital_outputs": [],
                "analog_inputs": [],
                "analog_outputs": [],
            }
            if isinstance(groups, list):
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    for field_name, char_uuid, outputs in _AIOS_GROUP_ORDER:
                        refs = group.get(field_name, [])
                        if not isinstance(refs, list):
                            continue
                        for ref in refs:
                            io_id = _aios_binding_ref(ref)
                            io = descriptor.get_io(io_id) if io_id else None
                            if io_id and io:
                                aios_details[field_name].append(io_id)
                                add_binding(
                                    io_id,
                                    io,
                                    service_uuid=AIOS_SERVICE_UUID,
                                    characteristic_uuid=char_uuid,
                                    writable=bool(io.rw or outputs),
                                    source="built_in",
                                )

            service_details["aios"] = aios_details

        ess = services.get("ess", {})
        if isinstance(ess, dict):
            enabled_services.append("ess")
            sensor_details: dict[str, Any] = {}
            chars = ess.get("characteristics", [])
            if isinstance(chars, list):
                for entry in chars:
                    if not isinstance(entry, dict):
                        continue
                    sensor_name = str(entry.get("type", ""))
                    if sensor_name not in _SENSOR_SPECS:
                        continue
                    io_id = resolve_flexible_reference(entry.get("data"))
                    io = descriptor.get_io(io_id) if io_id else None
                    if not io_id or io is None:
                        continue
                    sensor_details[sensor_name] = {
                        "data": io_id,
                        "metadata": dict(entry.get("metadata", {}) or {}),
                    }
                    char_uuid, struct_format, scale = _SENSOR_SPECS[
                        sensor_name
                    ]
                    add_binding(
                        io_id,
                        io,
                        service_uuid=ESS_SERVICE_UUID,
                        characteristic_uuid=char_uuid,
                        writable=False,
                        encoding="scaled_float",
                        struct_format=struct_format,
                        scale=scale,
                    )
            service_details["ess"] = sensor_details

        service = services.get("imds", {})
        if isinstance(service, dict):
            enabled_services.append("imds")
            imds_details: dict[str, str] = {}
            for sensor_name in _SENSOR_ORDER:
                io_id = resolve_flexible_reference(service.get(sensor_name))
                io = descriptor.get_io(io_id) if io_id else None
                if not io_id or io is None:
                    continue
                imds_details[sensor_name] = io_id
                char_uuid, struct_format, scale = _SENSOR_SPECS[sensor_name]
                add_binding(
                    io_id,
                    io,
                    service_uuid=IMDS_SERVICE_UUID,
                    characteristic_uuid=char_uuid,
                    writable=False,
                    encoding="scaled_float",
                    struct_format=struct_format,
                    scale=scale,
                )
            service_details["imds"] = imds_details

        dis = services.get("dis", None)
        if dis is not None:
            enabled_services.insert(0, "dis")
            service_details["dis"] = {"enabled": True}

        custom = services.get("custom", [])
        if isinstance(custom, list):
            for service in custom:
                if not isinstance(service, dict):
                    continue
                service_uuid = str(service.get("uuid", "")).lower()
                chars = service.get("characteristics", [])
                if not service_uuid or not isinstance(chars, list):
                    continue
                enabled_services.append(service_uuid)
                details: list[dict[str, Any]] = []
                for entry in chars:
                    if not isinstance(entry, dict):
                        continue
                    io_id = resolve_flexible_reference(entry.get("io"))
                    io = descriptor.get_io(io_id) if io_id else None
                    char_uuid = str(entry.get("uuid", "")).lower()
                    flags = entry.get("flags", [])
                    if (
                        not io_id
                        or io is None
                        or not char_uuid
                        or not isinstance(flags, list)
                    ):
                        continue
                    details.append(
                        {
                            "io": io_id,
                            "uuid": char_uuid,
                            "flags": list(flags),
                        }
                    )
                    add_binding(
                        io_id,
                        io,
                        service_uuid=service_uuid,
                        characteristic_uuid=char_uuid,
                        writable="write" in flags,
                        source="custom",
                    )
                if details:
                    service_details[service_uuid] = {
                        "characteristics": details
                    }

        ots = services.get("ots", {})
        ots_objects: list[OtsObjectBinding] = []
        if isinstance(ots, dict):
            objects = ots.get("objects", [])
            if isinstance(objects, list):
                for entry in objects:
                    if not isinstance(entry, dict):
                        continue
                    io_id = resolve_flexible_reference(entry.get("io"))
                    if not io_id:
                        continue
                    ots_objects.append(
                        OtsObjectBinding(
                            name=str(entry.get("name", "")),
                            ots_type=str(entry.get("type", "file")),
                            access=str(entry.get("access", "rw")),
                            io_id=io_id,
                        )
                    )
            if ots_objects:
                enabled_services.append("ots")
                service_details["ots"] = {
                    "objects": [
                        {
                            "name": o.name,
                            "type": o.ots_type,
                            "access": o.access,
                            "io": o.io_id,
                        }
                        for o in ots_objects
                    ]
                }

        gap_name = str(proto.config.get("gap_name", ""))
        return cls(
            bindings=bindings,
            binding_options=binding_options,
            gap_name=gap_name,
            enabled_services=tuple(enabled_services),
            service_details=service_details,
            ots_objects=tuple(ots_objects),
        )

    def get_binding(self, objid: int) -> BleCharacteristicBinding | None:
        """Return the BLE binding for an object ID."""
        return self.bindings.get(objid)

    def get_binding_options(
        self, objid: int
    ) -> list[BleCharacteristicBinding]:
        """Return all BLE binding options for an object ID."""
        return list(self.binding_options.get(objid, []))

    def discover_all_ios(self) -> dict[int, dict[str, Any]]:
        """Return cached IO metadata in the SimpleProtocolBase shape."""
        result: dict[int, dict[str, Any]] = {}
        for binding in self.bindings.values():
            result[binding.objid] = {
                "io_type": binding.io_type,
                "io_type_str": binding.io_type_str,
                "dimension": 1,
                "dtype": binding.dtype_id or 0,
            }
        return result

    def iter_bindings(self) -> Iterable[BleCharacteristicBinding]:
        """Iterate through bindings in object ID order."""
        for objid in sorted(self.bindings):
            yield self.bindings[objid]

    def get_service_overview(self) -> dict[str, Any]:
        """Return descriptor-defined GAP and service metadata."""
        return {
            "gap_name": self.gap_name,
            "enabled_services": list(self.enabled_services),
            "service_details": dict(self.service_details),
        }
