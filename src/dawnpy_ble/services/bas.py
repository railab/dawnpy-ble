"""Battery Service handler."""

SERVICE_UUID = "0000180f-0000-1000-8000-00805f9b34fb"

CHARACTERISTICS = {
    "bas.battery_level": (
        SERVICE_UUID,
        "00002a19-0000-1000-8000-00805f9b34fb",
        "u8_percent",
    ),
}
