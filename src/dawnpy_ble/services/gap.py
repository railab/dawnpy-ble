"""Generic Access Profile service handler."""

SERVICE_UUID = "00001800-0000-1000-8000-00805f9b34fb"

CHARACTERISTICS = {
    "gap.device_name": (
        SERVICE_UUID,
        "00002a00-0000-1000-8000-00805f9b34fb",
        "utf8",
    ),
    "gap.appearance": (
        SERVICE_UUID,
        "00002a01-0000-1000-8000-00805f9b34fb",
        "u16",
    ),
}
