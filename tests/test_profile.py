"""Tests for descriptor-derived BLE profiles."""

from pathlib import Path

import pytest

from dawnpy.descriptor.client import load_client_descriptor
from dawnpy.descriptor.definitions.registry import IOTypeInfo, ProtoTypeInfo

from dawnpy_ble.profile import (
    AIOS_SERVICE_UUID,
    ANALOG_CHAR_UUID,
    BAS_SERVICE_UUID,
    BATTERY_LEVEL_CHAR_UUID,
    DIGITAL_CHAR_UUID,
    ESS_SERVICE_UUID,
    BleTransportProfile,
)


class _ProfileObjectIdResolver:
    """Minimal ObjectId resolver for BLE profile tests."""

    def __init__(self):
        self.decoder = type(
            "Decoder",
            (),
            {
                "dtype_info": {
                    1: {"type": "bool"},
                    3: {"type": "uint8"},
                    10: {"type": "float"},
                }
            },
        )()
        self._objids = {}

    def io_objid(self, io):
        if io.io_id not in self._objids:
            self._objids[io.io_id] = 0x40000000 + len(self._objids) + 1
        return self._objids[io.io_id]


@pytest.fixture(autouse=True)
def descriptor_types(monkeypatch):
    """Provide only the descriptor type registry needed by these tests."""
    import dawnpy_ble.profile as profile_mod
    from dawnpy.descriptor.definitions import registry

    registry.reset_type_registry()
    monkeypatch.setattr(registry, "_REGISTRY_LOADED", True)
    registry._IO_TYPES_DATA.update(
        {
            "dummy": IOTypeInfo(
                cpp_class="CIODummy",
                header="dawn/io/dummy.hxx",
                helper_func="{cpp_class}::objectId",
                params=["dtype", "timestamp", "instance"],
            ),
            "dummy_notify": IOTypeInfo(
                cpp_class="CIODummyNotify",
                header="dawn/io/dummy_notify.hxx",
                helper_func="{cpp_class}::objectId",
                params=["dtype", "timestamp", "instance"],
            ),
            "gpi": IOTypeInfo(
                cpp_class="CIOGpi",
                header="dawn/io/gpi.hxx",
                helper_func="{cpp_class}::objectId",
                params=["notify", "instance"],
            ),
            "gpo": IOTypeInfo(
                cpp_class="CIOGpo",
                header="dawn/io/gpo.hxx",
                helper_func="{cpp_class}::objectId",
                params=["notify", "instance"],
            ),
            "sensor": IOTypeInfo(
                cpp_class="CIOSensor",
                header="dawn/io/sensor.hxx",
                helper_func="{cpp_class}::objectId{subtype}",
                params=["dtype", "timestamp", "instance"],
                subtypes=["baro", "gas", "hum", "press", "temp"],
            ),
        }
    )
    registry._PROTO_TYPES_DATA["nimble"] = ProtoTypeInfo(
        cpp_class="CProtoNimblePrph",
        header="dawn/proto/nimble/prph.hxx",
    )
    monkeypatch.setattr(
        profile_mod, "ObjectIdResolver", _ProfileObjectIdResolver
    )
    yield
    registry.reset_type_registry()


def _example_descriptor(name: str) -> str:
    return str(Path(__file__).parent / "fixtures" / name)


def test_profile_maps_gpio_and_sensor_services():
    descriptor = load_client_descriptor(
        _example_descriptor("nimble_gpio_analog_demo.yaml")
    )

    profile = BleTransportProfile.from_descriptor(descriptor)
    bindings = {binding.io_id: binding for binding in profile.iter_bindings()}

    assert bindings["dummyio1"].service_uuid == BAS_SERVICE_UUID
    assert bindings["dummyio1"].characteristic_uuid == BATTERY_LEVEL_CHAR_UUID

    assert bindings["gpi1"].service_uuid == AIOS_SERVICE_UUID
    assert bindings["gpi1"].characteristic_uuid == DIGITAL_CHAR_UUID
    assert bindings["gpi1"].characteristic_index == 0

    assert bindings["gpi2"].characteristic_index == 1
    assert bindings["gpi3"].characteristic_index == 2
    assert bindings["gpi4"].characteristic_index == 3
    assert bindings["gpo1"].characteristic_index == 0
    assert bindings["gpo1"].writable is True
    assert bindings["gpo4"].characteristic_index == 3

    assert bindings["dummyio2"].characteristic_uuid == ANALOG_CHAR_UUID
    assert bindings["dummyio2"].characteristic_index == 0

    assert bindings["dummyio3"].service_uuid == ESS_SERVICE_UUID
    assert bindings["dummyio3"].encoding == "scaled_float"
    assert bindings["dummyio6"].service_uuid != ESS_SERVICE_UUID
    overview = profile.get_service_overview()
    assert overview["gap_name"] == "bcddfgh"
    assert overview["enabled_services"] == [
        "dis",
        "bas",
        "aios",
        "ess",
        "imds",
    ]
    assert overview["service_details"]["bas"]["battery_level"] == "dummyio1"
    assert (
        overview["service_details"]["ess"]["temperature"]["data"] == "dummyio3"
    )
    assert overview["service_details"]["imds"]["pressure"] == "dummyio8"


def test_profile_maps_aios_metadata_wrapped_binding():
    descriptor = load_client_descriptor(
        _example_descriptor("nimble_aios_wrapped_binding.yaml")
    )

    profile = BleTransportProfile.from_descriptor(descriptor)
    bindings = {binding.io_id: binding for binding in profile.iter_bindings()}

    assert bindings["aios_gpi1"].service_uuid == AIOS_SERVICE_UUID
    assert bindings["aios_gpi1"].characteristic_uuid == DIGITAL_CHAR_UUID


def test_profile_prefers_first_binding_for_duplicate_io_service_exposure():
    descriptor = load_client_descriptor(
        _example_descriptor("nimble_env_sensor_demo.yaml")
    )

    profile = BleTransportProfile.from_descriptor(descriptor)
    bindings = {binding.io_id: binding for binding in profile.iter_bindings()}

    assert bindings["temp1"].service_uuid == ESS_SERVICE_UUID
    assert bindings["humidity1"].service_uuid == ESS_SERVICE_UUID
    humidity = bindings["humidity1"]
    assert len(profile.get_binding_options(humidity.objid)) == 2
