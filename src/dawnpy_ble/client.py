"""Interactive BLE client utilities for Dawn NimBLE devices."""

import time
from collections.abc import Callable

from dawnpy.cli.simple_device_client import SimpleDeviceClient
from dawnpy.cli.simple_device_console import SimpleDeviceConsole
from dawnpy.descriptor.client import (
    find_descriptor_path,
    load_client_descriptor,
)
from dawnpy.device.decode import decode_value

from dawnpy_ble.ble import DawnBleProtocol
from dawnpy_ble.profile import BleTransportProfile
from dawnpy_ble.services.dump import DumpedService


class BleClient(SimpleDeviceClient):  # pragma: no cover
    """BLE communication client for Dawn NimBLE devices."""

    connect_error = "ERROR: Failed to connect to BLE device"
    ping_error = "ERROR: Failed to resolve NimBLE descriptor bindings"

    def __init__(
        self,
        identifier: str,
        descriptor_path: str,
        debug: bool = False,
    ) -> None:
        """Initialize a descriptor-aware BLE client."""
        super().__init__()
        resolved_path = find_descriptor_path(descriptor_path)
        descriptor = load_client_descriptor(resolved_path)
        profile = BleTransportProfile.from_descriptor(descriptor)

        self.identifier = identifier
        self.descriptor_path = resolved_path
        self.debug = debug
        self.client = DawnBleProtocol(
            identifier,
            profile,
            verbose=debug,
        )
        self.profile = profile

    def discovery(self) -> None:
        """Discover IOs defined by the descriptor and print a summary."""
        if not self.connected:
            print("ERROR: Not connected to device")
            return

        print("=" * 60)
        print("BLE Device Discovery: Descriptor-bound IO Information")
        print("=" * 60)

        io_data = self.client.discover_all_ios()
        self.discovered_ios = io_data

        if not io_data:
            print("No BLE-bound IO objects found")
            return

        for objid, info in io_data.items():
            decoded = self.client.decode_object_id(objid)
            print(f"\nObject ID: 0x{objid:08X} ({decoded})")
            print(f"  Type: {info['io_type_str']}")
            print(f"  Dimension: {info['dimension']}")
            print(f"  Data Type: {info['dtype']}")

    def perform_discovery(self) -> bool:
        """Connect and allow descriptors that expose only standard services."""
        if not self.connect():
            return False

        self.discovered_ios = self.client.discover_all_ios()
        if self.discovered_ios:
            return True
        return bool(self.profile.enabled_services)

    def list_discovered_features(self) -> None:
        """Print details for cached discovery results."""
        if not self.discovered_ios:
            print("No discovered IOs. Run discovery (d) first.")
            return

        print("\nDetailed BLE IO Information:")
        for objid in sorted(self.discovered_ios):
            info = self.discovered_ios[objid]
            decoded = self.client.decode_object_id(objid)
            print(f"\nObject ID: 0x{objid:08X} ({decoded})")
            print(f"  Type: {info.get('io_type_str', 'unknown')}")
            print(f"  Data Type: {info.get('dtype', 'unknown')}")

    def show_ble_metadata(self) -> None:
        """Print descriptor-defined BLE GAP and service metadata."""
        overview = self.profile.get_service_overview()
        print("\nBLE Descriptor Metadata:")
        print(f"  GAP name: {overview.get('gap_name') or '<unset>'}")

        services = overview.get("enabled_services", [])
        if not services:
            print("  Enabled services: none")
            return

        print(f"  Enabled services: {', '.join(services)}")
        details = overview.get("service_details", {})
        for service_name in services:
            service_detail = details.get(service_name, {})
            print(f"\n  {service_name.upper()}:")
            if not service_detail:
                print("    <no descriptor details>")
                continue
            for key, value in service_detail.items():
                if isinstance(value, list):
                    rendered = ", ".join(value) if value else "<none>"
                else:
                    rendered = str(value)
                print(f"    {key}: {rendered}")

    def read_standard_services(self) -> None:
        """Read standard BLE services directly from the connected device."""
        if not self.connected:
            print("ERROR: Not connected to device")
            return

        values = self.client.read_standard_services()
        if not values:
            print("No readable GAP/DIS/BAS characteristics found")
            return

        print("\nStandard BLE Service Values:")
        for key in sorted(values):
            print(f"  {key}: {values[key]}")

    def dump_all_services(self) -> None:
        """Print all discovered GATT services and characteristics."""
        if not self.connected:
            print("ERROR: Not connected to device")
            return
        print_service_dump(self.client.dump_all_services())

    def monitoring(
        self,
        poll_interval: float = 1.0,
        duration: float = 10.0,
        objids: list[int] | None = None,
    ) -> None:
        """Continuously poll and print values for selected IOs."""
        if not self.connected:
            print("ERROR: Not connected to device")
            return

        io_list = objids if objids else self.client.get_io_list()
        if not io_list:
            print("No BLE-bound IO objects found")
            return

        print("\n" + "=" * 60)
        print("Continuous BLE IO Monitoring")
        print("=" * 60)
        print(f"\nMonitoring {len(io_list)} IO objects for {duration}s...\n")

        start_time = time.time()
        poll_count = 0

        while time.time() - start_time < duration:
            print(f"--- Poll #{poll_count} ---")
            for objid in io_list:
                data = self.client.read_io(objid)
                info = self.client.get_io_info(objid)
                if data is None or not info:
                    print(f"  0x{objid:08X}: ERROR")
                    continue
                lines = decode_value(
                    data,
                    info["dtype"],
                    self.client.objid_decoder,
                    objid=objid,
                )
                for line in lines:
                    print(f"  {line}")
            poll_count += 1
            time.sleep(poll_interval)

    def subscribe_notifications(self, objids: list[int]) -> None:
        """Subscribe to notifications for selected IOs."""
        if not self.connected:
            print("ERROR: Not connected to device")
            return
        if not objids:
            print("ERROR: No object IDs provided")
            return

        for objid in objids:
            info = self.client.get_io_info(objid)
            if not info:
                print(f"ERROR: Failed to get IO info for 0x{objid:08X}")
                continue
            if self.client.subscribe_io(objid, self._on_notification):
                print(f"Subscribed to 0x{objid:08X}")
            else:
                print(f"ERROR: Failed to subscribe to 0x{objid:08X}")

    def unsubscribe_notifications(self, objids: list[int]) -> None:
        """Stop notifications for selected IOs."""
        if not self.connected:
            print("ERROR: Not connected to device")
            return
        if not objids:
            print("ERROR: No object IDs provided")
            return

        for objid in objids:
            if self.client.unsubscribe_io(objid):
                print(f"Unsubscribed from 0x{objid:08X}")
            else:
                print(f"ERROR: Failed to unsubscribe from 0x{objid:08X}")

    def _on_notification(self, objid: int, data: bytes) -> None:
        """Render one incoming BLE notification."""
        info = self.client.get_io_info(objid)
        if not info:
            print(f"NOTIFY 0x{objid:08X}: <unknown dtype> {data.hex()}")
            return
        self.format_value(objid, data, info["dtype"])


class BleConsole(SimpleDeviceConsole):  # pragma: no cover
    """Interactive BLE console for descriptor-aware NimBLE access."""

    def __init__(
        self,
        identifier: str,
        descriptor_path: str,
        debug: bool = False,
    ) -> None:
        """Initialize console state and BLE client."""
        super().__init__(
            prompt="\nEnter BLE command (h for help): ",
            history_file=".dawnpy_ble_history",
        )
        self.client = BleClient(identifier, descriptor_path, debug=debug)

    def _console_header(self) -> str:
        """Return the BLE startup banner."""
        return (
            f"\nBLE Console - Device: {self.client.identifier}"
            f"\nDescriptor: {self.client.descriptor_path}"
        )

    def show_menu(self) -> None:
        """Display available console commands."""
        self.print_menu(
            "BLE Console - Commands",
            [
                "d: Device discovery",
                "g: Read GAP/DIS/BAS services",
                "i: Show BLE descriptor metadata",
                "l: List discovered features",
                "m [objids]: Continuous monitoring",
                "n <objids>: Subscribe to notifications",
                "r <objids>: Read object ID(s)",
                "s: Dump all GATT services",
                "u <objids>: Unsubscribe from notifications",
                "w <objid> <value>: Write value",
                "h: Show help",
                "q: Quit",
            ],
        )

    def commands_with_args(self) -> dict[str, Callable[[str], None]]:
        """Return BLE console commands with arguments."""
        commands = dict(super().commands_with_args())
        commands["n"] = self.cmd_subscribe
        commands["u"] = self.cmd_unsubscribe
        return commands

    def commands_no_args(self) -> dict[str, Callable[[], None]]:
        """Return BLE console commands without arguments."""
        commands = dict(super().commands_no_args())
        commands["g"] = self.client.read_standard_services
        commands["s"] = self.client.dump_all_services
        return commands

    def cmd_info(self, args: str) -> None:
        """Show BLE metadata or object details."""
        if args:
            super().cmd_info(args)
            return
        self.client.show_ble_metadata()

    def cmd_subscribe(self, args: str) -> None:
        """Subscribe to one or more object IDs."""
        if not args:
            print("ERROR: Usage: n <objid> [,<objid>,...]")
            return
        objids = self.client.parse_object_ids(args)
        if objids:
            self.client.subscribe_notifications(objids)

    def cmd_unsubscribe(self, args: str) -> None:
        """Unsubscribe one or more object IDs."""
        if not args:
            print("ERROR: Usage: u <objid> [,<objid>,...]")
            return
        objids = self.client.parse_object_ids(args)
        if objids:
            self.client.unsubscribe_notifications(objids)


def run_console(  # pragma: no cover
    identifier: str,
    descriptor_path: str,
    debug: bool = False,
) -> None:
    """Run the interactive BLE console."""
    console = BleConsole(identifier, descriptor_path, debug=debug)
    console.run()


def dump_services(identifier: str, debug: bool = False) -> bool:
    """Connect to a BLE device and print its full GATT service tree."""
    protocol = DawnBleProtocol(
        identifier,
        BleTransportProfile(bindings={}),
        verbose=debug,
    )
    if not protocol.connect():
        print("ERROR: Failed to connect to BLE device")
        return False
    try:
        print_service_dump(protocol.dump_all_services())
    finally:
        protocol.disconnect()
    return True


def print_service_dump(services: list[DumpedService]) -> None:
    """Render a GATT service dump in a stable human-readable format."""
    print("\nBLE GATT Service Dump:")
    if not services:
        print("  (no services)")
        return
    for service in services:
        print(f"\nService {service.uuid}")
        if not service.characteristics:
            print("  (no characteristics)")
            continue
        for characteristic in service.characteristics:
            props = ", ".join(characteristic.properties) or "-"
            handle = characteristic.handle
            handle_text = "?" if handle is None else str(handle)
            print(f"  Characteristic {characteristic.uuid}")
            print(f"    handle: {handle_text}")
            print(f"    properties: {props}")
            if characteristic.value is not None:
                print(f"    value: {characteristic.value.hex()}")
            elif characteristic.error is not None:
                print(f"    value: <read failed: {characteristic.error}>")
