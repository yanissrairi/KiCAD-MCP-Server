"""Connection Manager for KiCad Schematics.

Manages connections between components in schematics using wire and pin management.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from pathlib import Path

    from skip import Schematic, Symbol

    from commands.pin_locator import PinLocator as PinLocatorType

logger = logging.getLogger(__name__)

# Import new wire and pin managers
try:
    from commands.pin_locator import PinLocator
    from commands.wire_manager import WireManager

    WIRE_MANAGER_AVAILABLE = True
except ImportError:
    logger.warning("WireManager/PinLocator not available")
    WIRE_MANAGER_AVAILABLE = False
    PinLocator = None  # type: ignore[misc, assignment]
    WireManager = None  # type: ignore[misc, assignment]


class ConnectionManager:
    """Manage connections between components in schematics."""

    # Initialize pin locator (class variable, shared across instances)
    _pin_locator: ClassVar[PinLocatorType | None] = None

    @classmethod
    def get_pin_locator(cls) -> PinLocatorType | None:
        """Get or create pin locator instance.

        Returns:
            PinLocator instance if available, None otherwise.
        """
        if cls._pin_locator is None and WIRE_MANAGER_AVAILABLE:
            cls._pin_locator = PinLocator()
        return cls._pin_locator

    @staticmethod
    def add_wire(
        schematic_path: Path,
        start_point: list[float],
        end_point: list[float],
        properties: dict[str, Any] | None = None,
    ) -> bool:
        """Add a wire between two points using WireManager.

        Args:
            schematic_path: Path to .kicad_sch file.
            start_point: [x, y] coordinates for wire start.
            end_point: [x, y] coordinates for wire end.
            properties: Optional wire properties (stroke_width, stroke_type).

        Returns:
            True if successful, False otherwise.
        """
        try:
            if not WIRE_MANAGER_AVAILABLE:
                logger.error("WireManager not available")
                return False

            stroke_width = properties.get("stroke_width", 0) if properties else 0
            stroke_type = properties.get("stroke_type", "default") if properties else "default"

            return WireManager.add_wire(
                schematic_path,
                start_point,
                end_point,
                stroke_width=stroke_width,
                stroke_type=stroke_type,
            )
        except (OSError, ValueError, KeyError):
            logger.exception("Error adding wire")
            return False

    @staticmethod
    def get_pin_location(symbol: Symbol, pin_name: str) -> list[float] | None:
        """Get the absolute location of a pin on a symbol.

        Args:
            symbol: Symbol object.
            pin_name: Name or number of the pin (e.g., "1", "GND", "VCC").

        Returns:
            [x, y] coordinates or None if pin not found.
        """
        try:
            if not hasattr(symbol, "pin"):
                logger.warning(
                    "Symbol %s has no pins", symbol.property.Reference.value
                )
                return None

            # Find the pin by name
            target_pin = None
            for pin in symbol.pin:
                if pin.name == pin_name:
                    target_pin = pin
                    break

            if not target_pin:
                logger.warning(
                    "Pin '%s' not found on %s", pin_name, symbol.property.Reference.value
                )
                return None

            # Get pin location relative to symbol
            pin_loc = target_pin.location
            # Get symbol location
            symbol_at = symbol.at.value

            # Calculate absolute position
            # pin_loc is relative to symbol origin, need to add symbol position
            abs_x = symbol_at[0] + pin_loc[0]
            abs_y = symbol_at[1] + pin_loc[1]

            return [abs_x, abs_y]
        except (AttributeError, IndexError, TypeError):
            logger.exception("Error getting pin location")
            return None

    @staticmethod
    def add_connection(  # noqa: PLR0911
        schematic_path: Path,
        source_ref: str,
        source_pin: str,
        target_ref: str,
        target_pin: str,
        routing: str = "direct",
    ) -> bool:
        """Add a wire connection between two component pins.

        Args:
            schematic_path: Path to .kicad_sch file.
            source_ref: Reference designator of source component (e.g., "R1", "R1_").
            source_pin: Pin name/number on source component.
            target_ref: Reference designator of target component (e.g., "C1", "C1_").
            target_pin: Pin name/number on target component.
            routing: Routing style ('direct', 'orthogonal_h', 'orthogonal_v').

        Returns:
            True if connection was successful, False otherwise.
        """
        try:
            if not WIRE_MANAGER_AVAILABLE:
                logger.error("WireManager/PinLocator not available")
                return False

            locator = ConnectionManager.get_pin_locator()
            if not locator:
                logger.error("Pin locator unavailable")
                return False

            # Get pin locations
            source_loc = locator.get_pin_location(schematic_path, source_ref, source_pin)
            target_loc = locator.get_pin_location(schematic_path, target_ref, target_pin)

            if not source_loc or not target_loc:
                logger.error("Could not determine pin locations")
                return False

            # Create wire based on routing style
            if routing == "direct":
                # Simple direct wire
                success = WireManager.add_wire(schematic_path, source_loc, target_loc)
            elif routing == "orthogonal_h":
                # Orthogonal routing (horizontal first)
                path = WireManager.create_orthogonal_path(
                    source_loc, target_loc, prefer_horizontal_first=True
                )
                success = WireManager.add_polyline_wire(schematic_path, path)
            elif routing == "orthogonal_v":
                # Orthogonal routing (vertical first)
                path = WireManager.create_orthogonal_path(
                    source_loc, target_loc, prefer_horizontal_first=False
                )
                success = WireManager.add_polyline_wire(schematic_path, path)
            else:
                logger.error("Unknown routing style: %s", routing)
                return False

            if success:
                logger.info(
                    "Connected %s/%s to %s/%s (routing: %s)",
                    source_ref,
                    source_pin,
                    target_ref,
                    target_pin,
                    routing,
                )
                return True
            return False

        except (OSError, ValueError, AttributeError):
            logger.exception("Error adding connection")
            return False

    @staticmethod
    def add_net_label(
        schematic: Schematic,
        net_name: str,
        position: list[float],
    ) -> object | None:
        """Add a net label to the schematic.

        Args:
            schematic: Schematic object.
            net_name: Name of the net (e.g., "VCC", "GND", "SIGNAL_1").
            position: [x, y] coordinates for the label.

        Returns:
            Label object or None on error.
        """
        try:
            if not hasattr(schematic, "label"):
                logger.error("Schematic does not have label collection")
                return None

            label = schematic.label.append(
                text=net_name, at={"x": position[0], "y": position[1]}
            )
            logger.info("Added net label '%s' at %s", net_name, position)
            return label
        except (AttributeError, IndexError, TypeError):
            logger.exception("Error adding net label")
            return None

    @staticmethod
    def connect_to_net(  # noqa: PLR0911
        schematic_path: Path,
        component_ref: str,
        pin_name: str,
        net_name: str,
    ) -> bool:
        """Connect a component pin to a named net using a wire stub and label.

        Args:
            schematic_path: Path to .kicad_sch file.
            component_ref: Reference designator (e.g., "U1", "U1_").
            pin_name: Pin name/number.
            net_name: Name of the net to connect to (e.g., "VCC", "GND", "SIGNAL_1").

        Returns:
            True if successful, False otherwise.
        """
        try:
            if not WIRE_MANAGER_AVAILABLE:
                logger.error("WireManager/PinLocator not available")
                return False

            locator = ConnectionManager.get_pin_locator()
            if not locator:
                logger.error("Pin locator unavailable")
                return False

            # Get pin location using PinLocator
            pin_loc = locator.get_pin_location(schematic_path, component_ref, pin_name)
            if not pin_loc:
                logger.error("Could not locate pin %s/%s", component_ref, pin_name)
                return False

            # Add a small wire stub from the pin (2.54mm = 0.1 inch, standard grid spacing)
            stub_end = [pin_loc[0] + 2.54, pin_loc[1]]

            # Create wire stub using WireManager
            wire_success = WireManager.add_wire(schematic_path, pin_loc, stub_end)
            if not wire_success:
                logger.error("Failed to create wire stub for net connection")
                return False

            # Add label at the end of the stub using WireManager
            label_success = WireManager.add_label(
                schematic_path, net_name, stub_end, label_type="label"
            )
            if not label_success:
                logger.error("Failed to add net label '%s'", net_name)
                return False

            logger.info("Connected %s/%s to net '%s'", component_ref, pin_name, net_name)
            return True

        except (OSError, ValueError, AttributeError):
            logger.exception("Error connecting to net")
            return False

    @staticmethod
    def get_net_connections(  # noqa: PLR0911, PLR0912, PLR0915, C901
        schematic: Schematic,
        net_name: str,
        schematic_path: Path | None = None,
    ) -> list[dict[str, str]]:
        """Get all connections for a named net using wire graph analysis.

        Args:
            schematic: Schematic object.
            net_name: Name of the net to query.
            schematic_path: Optional path to schematic file (enables accurate pin matching).

        Returns:
            List of connections: [{"component": ref, "pin": pin_name}, ...].
        """
        try:
            from commands.pin_locator import PinLocator as LocalPinLocator  # noqa: PLC0415

            connections: list[dict[str, str]] = []
            tolerance = 0.5  # 0.5mm tolerance for point coincidence

            def points_coincide(
                p1: list[float] | None,
                p2: list[float] | tuple[float, float] | None,
            ) -> bool:
                """Check if two points are the same (within tolerance)."""
                if not p1 or not p2:
                    return False
                dx = abs(p1[0] - p2[0])
                dy = abs(p1[1] - p2[1])
                return dx < tolerance and dy < tolerance

            # 1. Find all labels with this net name
            if not hasattr(schematic, "label"):
                logger.warning("Schematic has no labels")
                return connections

            net_label_positions: list[list[float]] = []
            for label in schematic.label:
                if (
                    hasattr(label, "value")
                    and label.value == net_name
                    and hasattr(label, "at")
                    and hasattr(label.at, "value")
                ):
                    pos = label.at.value
                    net_label_positions.append([float(pos[0]), float(pos[1])])

            if not net_label_positions:
                logger.info("No labels found for net '%s'", net_name)
                return connections

            logger.debug("Found %d labels for net '%s'", len(net_label_positions), net_name)

            # 2. Find all wires connected to these label positions
            if not hasattr(schematic, "wire"):
                logger.warning("Schematic has no wires")
                return connections

            connected_wire_points: set[tuple[float, float]] = set()
            for wire in schematic.wire:
                if hasattr(wire, "pts") and hasattr(wire.pts, "xy"):
                    # Get all points in this wire (polyline) - use list comprehension
                    wire_points: list[list[float]] = [
                        [float(point.value[0]), float(point.value[1])]
                        for point in wire.pts.xy
                        if hasattr(point, "value")
                    ]

                    # Check if any wire point touches a label
                    wire_connected = False
                    for wire_pt in wire_points:
                        for label_pt in net_label_positions:
                            if points_coincide(wire_pt, label_pt):
                                wire_connected = True
                                break
                        if wire_connected:
                            break

                    # If this wire is connected to the net, add all its points
                    if wire_connected:
                        for pt in wire_points:
                            connected_wire_points.add((pt[0], pt[1]))

            if not connected_wire_points:
                logger.debug("No wires connected to net '%s' labels", net_name)
                return connections

            logger.debug(
                "Found %d wire connection points for net '%s'",
                len(connected_wire_points),
                net_name,
            )

            # 3. Find component pins at wire endpoints
            if not hasattr(schematic, "symbol"):
                logger.warning("Schematic has no symbols")
                return connections

            # Create pin locator for accurate pin matching (if schematic_path available)
            locator: LocalPinLocator | None = None
            if schematic_path and WIRE_MANAGER_AVAILABLE:
                locator = LocalPinLocator()

            for symbol in schematic.symbol:
                # Skip template symbols
                if not hasattr(symbol.property, "Reference"):
                    continue

                ref = symbol.property.Reference.value
                if ref.startswith("_TEMPLATE"):
                    continue

                # Get lib_id for pin location lookup
                lib_id = symbol.lib_id.value if hasattr(symbol, "lib_id") else None
                if not lib_id:
                    continue

                # If we have PinLocator and schematic_path, do accurate pin matching
                if locator and schematic_path:
                    try:
                        # Get all pins for this symbol
                        pins = locator.get_symbol_pins(schematic_path, lib_id)
                        if not pins:
                            continue

                        # Check each pin
                        for pin_num in pins:
                            # Get pin location
                            pin_loc = locator.get_pin_location(
                                schematic_path, ref, pin_num
                            )
                            if not pin_loc:
                                continue

                            # Check if pin coincides with any wire point
                            for wire_pt in connected_wire_points:
                                if points_coincide(pin_loc, wire_pt):
                                    connections.append({"component": ref, "pin": pin_num})
                                    break  # Pin found, no need to check more wire points

                    except (AttributeError, KeyError, TypeError):
                        logger.warning("Error matching pins for %s", ref)
                        # Fall back to proximity matching

                # Fallback: proximity-based matching if no PinLocator
                if not locator or not schematic_path:
                    symbol_pos = symbol.at.value if hasattr(symbol, "at") else None
                    if not symbol_pos:
                        continue

                    symbol_x = float(symbol_pos[0])
                    symbol_y = float(symbol_pos[1])

                    # Check if symbol is near any wire point (within 10mm)
                    for wire_pt in connected_wire_points:
                        dist = (
                            (symbol_x - wire_pt[0]) ** 2 + (symbol_y - wire_pt[1]) ** 2
                        ) ** 0.5
                        if dist < 10.0:  # noqa: PLR2004
                            connections.append({"component": ref, "pin": "unknown"})
                            break  # Only add once per component

            logger.info("Found %d connections for net '%s'", len(connections), net_name)
            return connections

        except (AttributeError, TypeError, ValueError):
            logger.exception("Error getting net connections")
            return []

    @staticmethod
    def generate_netlist(schematic: Schematic) -> dict[str, list[Any]]:
        """Generate a netlist from the schematic.

        Args:
            schematic: Schematic object.

        Returns:
            Dictionary with net information:
            {
                "nets": [
                    {
                        "name": "VCC",
                        "connections": [
                            {"component": "R1", "pin": "1"},
                            {"component": "C1", "pin": "1"}
                        ]
                    },
                    ...
                ],
                "components": [
                    {"reference": "R1", "value": "10k", "footprint": "..."},
                    ...
                ]
            }
        """
        try:
            netlist: dict[str, list[Any]] = {"nets": [], "components": []}

            # Gather all components
            if hasattr(schematic, "symbol"):
                for symbol in schematic.symbol:
                    component_info = {
                        "reference": symbol.property.Reference.value,
                        "value": (
                            symbol.property.Value.value
                            if hasattr(symbol.property, "Value")
                            else ""
                        ),
                        "footprint": (
                            symbol.property.Footprint.value
                            if hasattr(symbol.property, "Footprint")
                            else ""
                        ),
                    }
                    netlist["components"].append(component_info)

            # Gather all nets from labels
            if hasattr(schematic, "label"):
                net_names: set[str] = set()
                for label in schematic.label:
                    if hasattr(label, "value"):
                        net_names.add(label.value)

                # For each net, get connections
                for net_name in net_names:
                    connections = ConnectionManager.get_net_connections(schematic, net_name)
                    if connections:
                        netlist["nets"].append({"name": net_name, "connections": connections})

            logger.info(
                "Generated netlist with %d nets and %d components",
                len(netlist["nets"]),
                len(netlist["components"]),
            )
            return netlist

        except (AttributeError, TypeError):
            logger.exception("Error generating netlist")
            return {"nets": [], "components": []}


if __name__ == "__main__":
    # Example Usage (for testing)
    from commands.schematic import SchematicManager

    # Create a new schematic
    test_sch = SchematicManager.create_schematic("ConnectionTestSchematic")

    # Add some wires
    ConnectionManager.add_wire(test_sch, [100, 100], [200, 100])
    ConnectionManager.add_wire(test_sch, [200, 100], [200, 200])
