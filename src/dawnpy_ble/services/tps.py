"""Tx Power Service handler."""

SERVICE_UUID = "00001804-0000-1000-8000-00805f9b34fb"

CHARACTERISTICS = {
    "tps.tx_power_level": (
        SERVICE_UUID,
        "00002a07-0000-1000-8000-00805f9b34fb",
        "s8_dbm",
    ),
}
