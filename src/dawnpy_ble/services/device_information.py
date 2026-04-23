"""Device Information Service handler."""

SERVICE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"

CHARACTERISTICS = {
    "dis.manufacturer_name": (
        SERVICE_UUID,
        "00002a29-0000-1000-8000-00805f9b34fb",
        "utf8",
    ),
    "dis.model_number": (
        SERVICE_UUID,
        "00002a24-0000-1000-8000-00805f9b34fb",
        "utf8",
    ),
    "dis.serial_number": (
        SERVICE_UUID,
        "00002a25-0000-1000-8000-00805f9b34fb",
        "utf8",
    ),
    "dis.hardware_revision": (
        SERVICE_UUID,
        "00002a27-0000-1000-8000-00805f9b34fb",
        "utf8",
    ),
    "dis.firmware_revision": (
        SERVICE_UUID,
        "00002a26-0000-1000-8000-00805f9b34fb",
        "utf8",
    ),
    "dis.software_revision": (
        SERVICE_UUID,
        "00002a28-0000-1000-8000-00805f9b34fb",
        "utf8",
    ),
}
