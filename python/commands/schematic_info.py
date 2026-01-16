"""Schematic Info - Inspect and analyze KiCAD schematic contents.

Provides comprehensive inspection capabilities for AI-assisted schematic design:
- List all components with positions, values, and pin details
- List all nets and their connections
- Find unconnected pins (ERC-like analysis)
- Filter and search components
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from skip import Schematic

if TYPE_CHECKING:
    from commands.pin_locator import PinLocator

logger = logging.getLogger("kicad_interface")

# Import pin locator for detailed pin information
try:
    from commands.pin_locator import PinLocator as _PinLocator

    PIN_LOCATOR_AVAILABLE = True
except ImportError:
    _PinLocator = None  # type: ignore[assignment]
    logger.warning("PinLocator not available - pin details will be limited")
    PIN_LOCATOR_AVAILABLE = False


class SchematicInspector:
    """Inspect and analyze KiCAD schematic contents."""

    _pin_locator: PinLocator | None

    def __init__(self) -> None:
        """Initialize inspector with optional pin locator."""
        self._pin_locator = _PinLocator() if PIN_LOCATOR_AVAILABLE and _PinLocator else None

    def get_schematic_info(
        self,
        schematic_path: str,
        include_components: bool = True,
        include_nets: bool = True,
        include_pin_details: bool = False,
        include_unconnected: bool = False,
        component_filter: str | None = None,
        exclude_templates: bool = True,
    ) -> dict[str, Any]:
        """Get comprehensive information about a schematic.

        Args:
            schematic_path: Path to .kicad_sch file
            include_components: Include component list (default: True)
            include_nets: Include net/label information (default: True)
            include_pin_details: Include detailed pin info per component (default: False)
            include_unconnected: Find unconnected pins (default: False)
            component_filter: Regex pattern to filter components by reference (e.g., "R.*")
            exclude_templates: Exclude _TEMPLATE_ symbols (default: True)

        Returns:
            Dictionary with schematic information
        """
        try:
            path = Path(schematic_path)
            if not path.exists():
                return {"success": False, "error": f"Schematic not found: {schematic_path}"}

            schematic = Schematic(str(path))

            result = {
                "success": True,
                "schematic": {
                    "path": str(path),
                    "summary": self._get_summary(schematic, exclude_templates),
                },
            }

            if include_components:
                result["schematic"]["components"] = self._get_components(
                    schematic, path, component_filter, exclude_templates, include_pin_details
                )

            if include_nets:
                result["schematic"]["nets"] = self._get_nets(schematic, path)

            if include_unconnected:
                result["schematic"]["unconnectedPins"] = self._find_unconnected_pins(
                    schematic, path, exclude_templates
                )

            return result

        except Exception:
            logger.exception("Error getting schematic info")
            return {"success": False, "error": "Failed to get schematic info"}

    def _get_summary(self, schematic: Schematic, exclude_templates: bool) -> dict[str, Any]:
        """Get summary statistics for schematic.

        Args:
            schematic: The schematic object to analyze.
            exclude_templates: Whether to exclude template symbols from counts.

        Returns:
            Dictionary containing summary statistics.
        """
        summary: dict[str, Any] = {
            "totalComponents": self._count_components(schematic, exclude_templates),
            "totalWires": self._count_wires(schematic),
            "totalNets": self._count_nets(schematic),
            "boundingBox": self._calculate_bounding_box(schematic, exclude_templates),
        }

        return summary

    def _count_components(self, schematic: Schematic, exclude_templates: bool) -> int:
        """Count components in schematic.

        Args:
            schematic: The schematic object to analyze.
            exclude_templates: Whether to exclude template symbols.

        Returns:
            Number of components.
        """
        if not hasattr(schematic, "symbol"):
            return 0

        count = 0
        for symbol in schematic.symbol:
            if exclude_templates:
                ref = self._get_reference(symbol)
                if ref and ref.startswith("_TEMPLATE"):
                    continue
            count += 1
        return count

    def _count_wires(self, schematic: Schematic) -> int:
        """Count wires in schematic.

        Args:
            schematic: The schematic object to analyze.

        Returns:
            Number of wires.
        """
        return len(list(schematic.wire)) if hasattr(schematic, "wire") else 0

    def _count_nets(self, schematic: Schematic) -> int:
        """Count unique nets from labels.

        Args:
            schematic: The schematic object to analyze.

        Returns:
            Number of unique nets.
        """
        if not hasattr(schematic, "label"):
            return 0

        net_names = set()
        for label in schematic.label:
            if hasattr(label, "value") and label.value:
                net_names.add(label.value)
        return len(net_names)

    def _calculate_bounding_box(
        self, schematic: Schematic, exclude_templates: bool
    ) -> dict[str, float]:
        """Calculate bounding box from component positions.

        Args:
            schematic: The schematic object to analyze.
            exclude_templates: Whether to exclude template symbols.

        Returns:
            Bounding box dictionary with minX, minY, maxX, maxY.
        """
        default_box = {"minX": 0, "minY": 0, "maxX": 0, "maxY": 0}

        if not hasattr(schematic, "symbol"):
            return default_box

        min_x, min_y = float("inf"), float("inf")
        max_x, max_y = float("-inf"), float("-inf")
        has_positions = False

        for symbol in schematic.symbol:
            if exclude_templates:
                ref = self._get_reference(symbol)
                if ref and ref.startswith("_TEMPLATE"):
                    continue

            if hasattr(symbol, "at") and hasattr(symbol.at, "value"):
                pos = symbol.at.value
                x, y = float(pos[0]), float(pos[1])
                min_x, min_y = min(min_x, x), min(min_y, y)
                max_x, max_y = max(max_x, x), max(max_y, y)
                has_positions = True

        if not has_positions:
            return default_box

        return {
            "minX": round(min_x, 2),
            "minY": round(min_y, 2),
            "maxX": round(max_x, 2),
            "maxY": round(max_y, 2),
        }

    def _get_components(
        self,
        schematic: Schematic,
        schematic_path: Path,
        component_filter: str | None,
        exclude_templates: bool,
        include_pin_details: bool,
    ) -> list[dict[str, Any]]:
        """Get list of all components with their properties.

        Args:
            schematic: The schematic object to analyze.
            schematic_path: Path to the schematic file.
            component_filter: Optional regex pattern to filter components by reference.
            exclude_templates: Whether to exclude template symbols.
            include_pin_details: Whether to include detailed pin information.

        Returns:
            List of component information dictionaries.
        """
        components: list[dict[str, Any]] = []

        if not hasattr(schematic, "symbol"):
            return components

        # Compile filter regex if provided
        filter_pattern = re.compile(component_filter, re.IGNORECASE) if component_filter else None

        for symbol in schematic.symbol:
            ref = self._get_reference(symbol)

            # Skip templates if requested
            if exclude_templates and ref and ref.startswith("_TEMPLATE"):
                continue

            # Apply filter if provided
            if filter_pattern and ref and not filter_pattern.match(ref):
                continue

            component = {
                "reference": ref or "unknown",
                "value": self._get_property(symbol, "Value", ""),
                "footprint": self._get_property(symbol, "Footprint", ""),
                "libId": symbol.lib_id.value if hasattr(symbol, "lib_id") else "",
                "position": self._get_position(symbol),
            }

            # Add pin details if requested
            if include_pin_details and self._pin_locator and ref:
                component["pins"] = self._get_component_pins(schematic, schematic_path, symbol, ref)

            components.append(component)

        # Sort by reference for consistent output
        components.sort(key=lambda c: self._sort_reference(c["reference"]))

        return components

    def _get_component_pins(
        self,
        schematic: Schematic,  # noqa: ARG002
        schematic_path: Path,
        symbol: object,
        reference: str,
    ) -> list[dict[str, Any]]:
        """Get detailed pin information for a component.

        Args:
            schematic: The schematic object (currently unused but kept for API consistency).
            schematic_path: Path to the schematic file.
            symbol: The symbol object to get pins for.
            reference: The reference designator of the component.

        Returns:
            List of pin information dictionaries.
        """
        pins: list[dict[str, Any]] = []

        lib_id = symbol.lib_id.value if hasattr(symbol, "lib_id") else None  # type: ignore[attr-defined]
        if not lib_id or not self._pin_locator:
            return pins

        # Get pin definitions from lib_symbols
        pin_defs = self._pin_locator.get_symbol_pins(schematic_path, lib_id)

        for pin_num, pin_data in pin_defs.items():
            # Get absolute pin position
            pin_loc = self._pin_locator.get_pin_location(schematic_path, reference, pin_num)

            pin_info: dict[str, Any] = {
                "number": pin_num,
                "name": pin_data.get("name", ""),
                "type": pin_data.get("type", "passive"),
                "position": {
                    "x": round(pin_loc[0], 2) if pin_loc else 0,
                    "y": round(pin_loc[1], 2) if pin_loc else 0,
                },
            }
            pins.append(pin_info)

        # Sort pins by number
        pins.sort(key=lambda p: self._sort_pin_number(p["number"]))

        return pins

    def _get_nets(self, schematic: Schematic, schematic_path: Path) -> list[dict[str, Any]]:
        """Get all nets with their connections.

        Args:
            schematic: The schematic object to analyze.
            schematic_path: Path to the schematic file.

        Returns:
            List of net information dictionaries with name and connections.
        """
        nets: list[dict[str, Any]] = []

        if not hasattr(schematic, "label"):
            return nets

        # Collect unique net names
        net_names: set[str] = set()
        for label in schematic.label:
            if hasattr(label, "value") and label.value:
                net_names.add(label.value)

        # For each net, find connections
        from commands.connection_schematic import ConnectionManager

        for net_name in sorted(net_names):
            connections = ConnectionManager.get_net_connections(schematic, net_name, schematic_path)

            net_info: dict[str, Any] = {"name": net_name, "connections": connections}
            nets.append(net_info)

        return nets

    def _build_wire_points_set(self, schematic: Schematic) -> set[tuple[float, float]]:
        """Build a set of all wire endpoints for connection detection.

        Args:
            schematic: The schematic object to analyze.

        Returns:
            Set of (x, y) coordinates representing wire endpoints.
        """
        wire_points: set[tuple[float, float]] = set()

        if not hasattr(schematic, "wire"):
            return wire_points

        for wire in schematic.wire:
            if hasattr(wire, "pts") and hasattr(wire.pts, "xy"):
                for point in wire.pts.xy:
                    if hasattr(point, "value"):
                        # Round to avoid floating point issues
                        x = round(float(point.value[0]), 1)
                        y = round(float(point.value[1]), 1)
                        wire_points.add((x, y))

        return wire_points

    def _is_pin_connected(
        self, pin_x: float, pin_y: float, wire_points: set[tuple[float, float]]
    ) -> bool:
        """Check if a pin position is connected to any wire.

        Args:
            pin_x: X coordinate of the pin.
            pin_y: Y coordinate of the pin.
            wire_points: Set of wire endpoint coordinates.

        Returns:
            True if the pin is connected to a wire, False otherwise.
        """
        tolerance = 0.5  # mm tolerance for connection detection

        for wx, wy in wire_points:
            if abs(pin_x - wx) < tolerance and abs(pin_y - wy) < tolerance:
                return True
        return False

    def _should_skip_symbol(self, symbol: object, exclude_templates: bool) -> bool:
        """Determine if a symbol should be skipped during analysis.

        Args:
            symbol: The symbol object to check.
            exclude_templates: Whether to exclude template symbols.

        Returns:
            True if the symbol should be skipped, False otherwise.
        """
        if not exclude_templates:
            return False

        ref = self._get_reference(symbol)
        return ref is not None and ref.startswith("_TEMPLATE")

    def _check_symbol_pins(
        self,
        schematic_path: Path,
        ref: str,
        lib_id: str,
        wire_points: set[tuple[float, float]],
    ) -> list[dict[str, Any]]:
        """Check all pins of a symbol for connections.

        Args:
            schematic_path: Path to the schematic file.
            ref: Reference designator of the symbol.
            lib_id: Library ID of the symbol.
            wire_points: Set of wire endpoint coordinates.

        Returns:
            List of unconnected pin information dictionaries for this symbol.
        """
        unconnected: list[dict[str, Any]] = []

        # Get all pins for this symbol
        pin_defs = self._pin_locator.get_symbol_pins(schematic_path, lib_id)

        for pin_num, pin_data in pin_defs.items():
            pin_loc = self._pin_locator.get_pin_location(schematic_path, ref, pin_num)

            if pin_loc:
                pin_x = round(pin_loc[0], 1)
                pin_y = round(pin_loc[1], 1)

                if not self._is_pin_connected(pin_x, pin_y, wire_points):
                    unconnected.append(
                        {
                            "component": ref,
                            "pin": pin_num,
                            "pinName": pin_data.get("name", ""),
                            "pinType": pin_data.get("type", "passive"),
                            "position": {"x": round(pin_loc[0], 2), "y": round(pin_loc[1], 2)},
                        }
                    )

        return unconnected

    def _find_unconnected_pins(
        self, schematic: Schematic, schematic_path: Path, exclude_templates: bool
    ) -> list[dict[str, Any]]:
        """Find pins that are not connected to any wire or net.

        Args:
            schematic: The schematic object to analyze.
            schematic_path: Path to the schematic file.
            exclude_templates: Whether to exclude template symbols.

        Returns:
            List of unconnected pin information dictionaries.
        """
        unconnected: list[dict[str, Any]] = []

        if not hasattr(schematic, "symbol") or not self._pin_locator:
            return unconnected

        wire_points = self._build_wire_points_set(schematic)

        # Check each component's pins
        for symbol in schematic.symbol:
            if self._should_skip_symbol(symbol, exclude_templates):
                continue

            ref = self._get_reference(symbol)
            lib_id = symbol.lib_id.value if hasattr(symbol, "lib_id") else None

            if not lib_id or not ref:
                continue

            symbol_unconnected = self._check_symbol_pins(
                schematic_path, ref, lib_id, wire_points
            )
            unconnected.extend(symbol_unconnected)

        return unconnected

    # Helper methods

    def _get_reference(self, symbol: object) -> str | None:
        """Safely get reference designator from symbol.

        Args:
            symbol: A KiCAD symbol object.

        Returns:
            The reference designator string, or None if not found.
        """
        try:
            if hasattr(symbol, "property") and hasattr(symbol.property, "Reference"):
                return symbol.property.Reference.value  # type: ignore[attr-defined]
            if hasattr(symbol, "reference"):
                return symbol.reference  # type: ignore[attr-defined]
        except (AttributeError, TypeError) as e:
            # Expected: symbol may not have reference property
            logger.debug("Could not get reference from symbol: %s", e)
        return None

    def _get_property(self, symbol: object, prop_name: str, default: str = "") -> str:
        """Safely get a property value from symbol.

        Args:
            symbol: A KiCAD symbol object.
            prop_name: The name of the property to retrieve.
            default: Default value if property not found.

        Returns:
            The property value as a string, or default if not found.
        """
        try:
            if hasattr(symbol, "property") and hasattr(symbol.property, prop_name):
                prop = getattr(symbol.property, prop_name)
                return prop.value if hasattr(prop, "value") else str(prop)  # type: ignore[attr-defined]
        except (AttributeError, TypeError) as e:
            # Expected: property may not exist on symbol
            logger.debug("Could not get property '%s' from symbol: %s", prop_name, e)
        return default

    def _get_position(self, symbol: object) -> dict[str, float]:
        """Get position and rotation from symbol.

        Args:
            symbol: A KiCAD symbol object.

        Returns:
            Dictionary with x, y coordinates and rotation angle.
        """
        try:
            if hasattr(symbol, "at") and hasattr(symbol.at, "value"):
                pos = symbol.at.value  # type: ignore[attr-defined]
                return {
                    "x": round(float(pos[0]), 2),
                    "y": round(float(pos[1]), 2),
                    "rotation": round(float(pos[2]), 1) if len(pos) > 2 else 0,  # noqa: PLR2004
                }
        except (AttributeError, TypeError, IndexError, ValueError) as e:
            # Expected: position data may be missing or malformed
            logger.debug("Could not parse position from symbol: %s", e)
        return {"x": 0, "y": 0, "rotation": 0}

    def _sort_reference(self, ref: str) -> tuple[str, int]:
        """Sort references naturally (R1, R2, R10 not R1, R10, R2).

        Args:
            ref: Reference designator string.

        Returns:
            Tuple of (prefix, number) for natural sorting.
        """
        match = re.match(r"([A-Za-z]+)(\d*)", ref or "")
        if match:
            prefix, num = match.groups()
            return (prefix, int(num) if num else 0)
        return (ref or "", 0)

    def _sort_pin_number(self, pin: str) -> tuple[int, int | str]:
        """Sort pin numbers naturally.

        Args:
            pin: Pin number/identifier string.

        Returns:
            Tuple for sorting: (0, number) for numeric pins, (1, string) for others.
        """
        try:
            return (0, int(pin))
        except ValueError:
            return (1, pin)


# Singleton instance
_inspector = SchematicInspector()


def get_schematic_info(
    schematic_path: str,
    include_components: bool = True,
    include_nets: bool = True,
    include_pin_details: bool = False,
    include_unconnected: bool = False,
    component_filter: str | None = None,
    exclude_templates: bool = True,
) -> dict[str, Any]:
    """Get comprehensive information about a KiCAD schematic.

    This is the main entry point for the MCP tool.

    Args:
        schematic_path: Path to .kicad_sch file
        include_components: Include component list (default: True)
        include_nets: Include net/label information (default: True)
        include_pin_details: Include detailed pin info per component (default: False)
        include_unconnected: Find unconnected pins - ERC-like (default: False)
        component_filter: Regex pattern to filter components (e.g., "R.*" for resistors)
        exclude_templates: Exclude _TEMPLATE_ symbols (default: True)

    Returns:
        Dictionary containing:
        - success: bool
        - schematic: {
            path: str,
            summary: {totalComponents, totalWires, totalNets, boundingBox},
            components: [{reference, value, footprint, libId, position, pins?}],
            nets: [{name, connections: [{component, pin}]}],
            unconnectedPins: [{component, pin, pinName, pinType, position}]
          }
    """
    return _inspector.get_schematic_info(
        schematic_path=schematic_path,
        include_components=include_components,
        include_nets=include_nets,
        include_pin_details=include_pin_details,
        include_unconnected=include_unconnected,
        component_filter=component_filter,
        exclude_templates=exclude_templates,
    )
