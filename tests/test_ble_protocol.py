"""Tests for the BLE protocol adapter."""

import struct

import pytest

from dawnpy_ble.ble import DawnBleProtocol
from dawnpy_ble.profile import (
    AIOS_SERVICE_UUID,
    ANALOG_CHAR_UUID,
    BAS_SERVICE_UUID,
    BATTERY_LEVEL_CHAR_UUID,
    DIGITAL_CHAR_UUID,
    ESS_SERVICE_UUID,
    BleCharacteristicBinding,
    BleTransportProfile,
)


class FakeCharacteristic:
    def __init__(self, uuid: str, name: str, properties=None):
        self.uuid = uuid
        self.name = name
        self.properties = properties or ["read"]
        self.handle = name


class FakeService:
    def __init__(self, uuid: str, characteristics):
        self.uuid = uuid
        self.characteristics = characteristics


class FakeBleakClient:
    def __init__(self, identifier: str, timeout: float = 10.0):
        self.identifier = identifier
        self.timeout = timeout
        self.connected = False
        self.services = [
            FakeService(
                "00001800-0000-1000-8000-00805f9b34fb",
                [
                    FakeCharacteristic(
                        "00002a00-0000-1000-8000-00805f9b34fb",
                        "device_name",
                        ["read"],
                    ),
                    FakeCharacteristic(
                        "00002a01-0000-1000-8000-00805f9b34fb",
                        "appearance",
                        ["read"],
                    ),
                ],
            ),
            FakeService(
                "0000180a-0000-1000-8000-00805f9b34fb",
                [
                    FakeCharacteristic(
                        "00002a29-0000-1000-8000-00805f9b34fb",
                        "manufacturer_name",
                        ["read"],
                    ),
                    FakeCharacteristic(
                        "00002a24-0000-1000-8000-00805f9b34fb",
                        "model_number",
                        ["read"],
                    ),
                ],
            ),
            FakeService(
                BAS_SERVICE_UUID,
                [
                    FakeCharacteristic(
                        BATTERY_LEVEL_CHAR_UUID,
                        "battery",
                        ["read", "notify"],
                    )
                ],
            ),
            FakeService(
                AIOS_SERVICE_UUID,
                [
                    FakeCharacteristic(
                        DIGITAL_CHAR_UUID, "digital0", ["read", "notify"]
                    ),
                    FakeCharacteristic(
                        DIGITAL_CHAR_UUID,
                        "digital1",
                        ["read", "write", "notify"],
                    ),
                    FakeCharacteristic(
                        ANALOG_CHAR_UUID, "analog0", ["read", "notify"]
                    ),
                ],
            ),
            FakeService(
                ESS_SERVICE_UUID,
                [
                    FakeCharacteristic(
                        "00002a6e-0000-1000-8000-00805f9b34fb",
                        "temp",
                        ["read", "notify"],
                    )
                ],
            ),
        ]
        self.reads = {
            "device_name": b"Dawn Demo",
            "appearance": struct.pack("<H", 1234),
            "manufacturer_name": b"Railab",
            "model_number": b"Thingy",
            "battery": b"\x4d",
            "digital1": b"\x01",
            "temp": struct.pack("<h", 2534),
        }
        self.writes = []
        self.started_notifications = []
        self.stopped_notifications = []
        self._notify_handlers = {}

    async def connect(self):
        self.connected = True
        return True

    async def disconnect(self):
        self.connected = False

    async def get_services(self):
        return self.services

    async def read_gatt_char(self, characteristic):
        return self.reads[characteristic.name]

    async def write_gatt_char(self, characteristic, data):
        self.writes.append((characteristic.name, bytes(data)))

    async def start_notify(self, characteristic, callback):
        self.started_notifications.append(characteristic.name)
        self._notify_handlers[characteristic.name] = callback

    async def stop_notify(self, characteristic):
        self.stopped_notifications.append(characteristic.name)
        self._notify_handlers.pop(characteristic.name, None)

    def emit_notification(self, characteristic_name, payload):
        self._notify_handlers[characteristic_name](None, payload)


def test_protocol_resolves_duplicate_characteristics_and_reads_scaled_values():
    profile = BleTransportProfile(
        bindings={
            0x1: BleCharacteristicBinding(
                objid=0x1,
                io_id="battery",
                service_uuid=BAS_SERVICE_UUID,
                characteristic_uuid=BATTERY_LEVEL_CHAR_UUID,
                characteristic_index=0,
                dtype="uint8",
                dtype_id=2,
                io_type=0x01,
                io_type_str="Read-Only",
                writable=False,
            ),
            0x2: BleCharacteristicBinding(
                objid=0x2,
                io_id="do1",
                service_uuid=AIOS_SERVICE_UUID,
                characteristic_uuid=DIGITAL_CHAR_UUID,
                characteristic_index=1,
                dtype="bool",
                dtype_id=1,
                io_type=0x03,
                io_type_str="Read-Write",
                writable=True,
            ),
            0x3: BleCharacteristicBinding(
                objid=0x3,
                io_id="temp1",
                service_uuid=ESS_SERVICE_UUID,
                characteristic_uuid="00002a6e-0000-1000-8000-00805f9b34fb",
                characteristic_index=0,
                dtype="float",
                dtype_id=9,
                io_type=0x01,
                io_type_str="Read-Only",
                writable=False,
                encoding="scaled_float",
                struct_format="<h",
                scale=100.0,
            ),
        }
    )
    protocol = DawnBleProtocol(
        "demo-device",
        profile,
        client_factory=FakeBleakClient,
    )

    assert protocol.connect() is True
    assert protocol.ping() is True
    assert protocol.read_io(0x1) == b"\x4d"
    assert protocol.read_io(0x2) == b"\x01"

    temperature = protocol.read_io(0x3)
    assert temperature is not None
    assert struct.unpack("<f", temperature)[0] == pytest.approx(25.34)


def test_protocol_writes_to_the_resolved_characteristic():
    profile = BleTransportProfile(
        bindings={
            0x2: BleCharacteristicBinding(
                objid=0x2,
                io_id="do1",
                service_uuid=AIOS_SERVICE_UUID,
                characteristic_uuid=DIGITAL_CHAR_UUID,
                characteristic_index=1,
                dtype="bool",
                dtype_id=1,
                io_type=0x03,
                io_type_str="Read-Write",
                writable=True,
            ),
        }
    )
    protocol = DawnBleProtocol(
        "demo-device",
        profile,
        client_factory=FakeBleakClient,
    )

    assert protocol.connect() is True
    assert protocol.write_io(0x2, b"\x00") is True
    assert protocol._client.writes == [("digital1", b"\x00")]


def test_protocol_resolves_write_without_response_duplicate_characteristics():
    class DuplicateDigitalClient(FakeBleakClient):
        def __init__(self, identifier: str, timeout: float = 10.0):
            super().__init__(identifier, timeout)
            self.services = [
                FakeService(
                    AIOS_SERVICE_UUID,
                    [
                        FakeCharacteristic(
                            DIGITAL_CHAR_UUID, "di0", ["read", "notify"]
                        ),
                        FakeCharacteristic(
                            DIGITAL_CHAR_UUID, "di1", ["read", "notify"]
                        ),
                        FakeCharacteristic(
                            DIGITAL_CHAR_UUID, "di2", ["read", "notify"]
                        ),
                        FakeCharacteristic(
                            DIGITAL_CHAR_UUID, "di3", ["read", "notify"]
                        ),
                        FakeCharacteristic(
                            DIGITAL_CHAR_UUID,
                            "do0",
                            ["write-without-response"],
                        ),
                        FakeCharacteristic(
                            DIGITAL_CHAR_UUID,
                            "do1",
                            ["write-without-response"],
                        ),
                        FakeCharacteristic(
                            DIGITAL_CHAR_UUID,
                            "do2",
                            ["write-without-response"],
                        ),
                        FakeCharacteristic(
                            DIGITAL_CHAR_UUID,
                            "do3",
                            ["write-without-response"],
                        ),
                        FakeCharacteristic(
                            DIGITAL_CHAR_UUID,
                            "rw",
                            ["read", "write-without-response"],
                        ),
                    ],
                )
            ]
            self.reads = {"rw": b"\x01"}

    profile = BleTransportProfile(
        bindings={
            0x2: BleCharacteristicBinding(
                objid=0x2,
                io_id="rw",
                service_uuid=AIOS_SERVICE_UUID,
                characteristic_uuid=DIGITAL_CHAR_UUID,
                characteristic_index=4,
                dtype="bool",
                dtype_id=1,
                io_type=0x03,
                io_type_str="Read-Write",
                writable=True,
            ),
        }
    )
    protocol = DawnBleProtocol(
        "demo-device",
        profile,
        client_factory=DuplicateDigitalClient,
    )

    assert protocol.connect() is True
    assert protocol.read_io(0x2) == b"\x01"
    assert protocol.write_io(0x2, b"\x00") is True
    assert protocol._client.writes == [("rw", b"\x00")]


def test_protocol_prefers_read_only_characteristic_for_read_only_binding():
    class DuplicateAnalogClient(FakeBleakClient):
        def __init__(self, identifier: str, timeout: float = 10.0):
            super().__init__(identifier, timeout)
            self.services = [
                FakeService(
                    AIOS_SERVICE_UUID,
                    [
                        FakeCharacteristic(
                            ANALOG_CHAR_UUID,
                            "analog_rw",
                            ["read", "write-without-response"],
                        ),
                        FakeCharacteristic(
                            ANALOG_CHAR_UUID,
                            "analog_in",
                            ["read", "notify"],
                        ),
                    ],
                )
            ]
            self.reads = {
                "analog_rw": struct.pack("<f", 0.0),
                "analog_in": struct.pack("<f", 1.5),
            }

    profile = BleTransportProfile(
        bindings={
            0x1: BleCharacteristicBinding(
                objid=0x1,
                io_id="analog_in",
                service_uuid=AIOS_SERVICE_UUID,
                characteristic_uuid=ANALOG_CHAR_UUID,
                characteristic_index=0,
                dtype="float",
                dtype_id=9,
                io_type=0x01,
                io_type_str="Read-Only",
                writable=False,
            ),
            0x2: BleCharacteristicBinding(
                objid=0x2,
                io_id="analog_rw",
                service_uuid=AIOS_SERVICE_UUID,
                characteristic_uuid=ANALOG_CHAR_UUID,
                characteristic_index=0,
                dtype="float",
                dtype_id=9,
                io_type=0x03,
                io_type_str="Read-Write",
                writable=True,
            ),
        }
    )
    protocol = DawnBleProtocol(
        "demo-device",
        profile,
        client_factory=DuplicateAnalogClient,
    )

    assert protocol.connect() is True
    assert struct.unpack("<f", protocol.read_io(0x1))[0] == pytest.approx(1.5)
    assert struct.unpack("<f", protocol.read_io(0x2))[0] == pytest.approx(0.0)


def test_protocol_subscribes_and_decodes_notifications():
    profile = BleTransportProfile(
        bindings={
            0x3: BleCharacteristicBinding(
                objid=0x3,
                io_id="temp1",
                service_uuid=ESS_SERVICE_UUID,
                characteristic_uuid="00002a6e-0000-1000-8000-00805f9b34fb",
                characteristic_index=0,
                dtype="float",
                dtype_id=9,
                io_type=0x01,
                io_type_str="Read-Only",
                writable=False,
                encoding="scaled_float",
                struct_format="<h",
                scale=100.0,
            ),
        }
    )
    protocol = DawnBleProtocol(
        "demo-device",
        profile,
        client_factory=FakeBleakClient,
    )
    notifications = []

    assert protocol.connect() is True
    assert protocol.subscribe_io(
        0x3, lambda objid, data: notifications.append((objid, data))
    )

    protocol._client.emit_notification("temp", struct.pack("<h", 2311))

    assert protocol._client.started_notifications == ["temp"]
    assert notifications
    assert notifications[0][0] == 0x3
    assert struct.unpack("<f", notifications[0][1])[0] == pytest.approx(23.11)
    assert protocol.unsubscribe_io(0x3) is True
    assert protocol._client.stopped_notifications == ["temp"]


def test_protocol_reads_standard_services():
    profile = BleTransportProfile(bindings={})
    protocol = DawnBleProtocol(
        "demo-device",
        profile,
        client_factory=FakeBleakClient,
    )

    assert protocol.connect() is True
    values = protocol.read_standard_services()

    assert values["gap.device_name"] == "Dawn Demo"
    assert values["gap.appearance"] == 1234
    assert values["dis.manufacturer_name"] == "Railab"
    assert values["dis.model_number"] == "Thingy"
    assert values["bas.battery_level"] == 77


def test_protocol_dumps_all_services_and_readable_values():
    profile = BleTransportProfile(bindings={})
    protocol = DawnBleProtocol(
        "demo-device",
        profile,
        client_factory=FakeBleakClient,
    )

    assert protocol.connect() is True
    services = protocol.dump_all_services()

    gap_service = services[0]
    assert gap_service.uuid == "00001800-0000-1000-8000-00805f9b34fb"
    assert gap_service.characteristics[0].handle == "device_name"
    assert gap_service.characteristics[0].value == b"Dawn Demo"
    assert gap_service.characteristics[0].properties == ("read",)
