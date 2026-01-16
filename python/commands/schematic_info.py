"""
Schematic Info - Inspect and analyze KiCAD schematic contents

Provides comprehensive inspection capabilities for AI-assisted schematic design:
- List all components with positions, values, and pin details
- List all nets and their connections
- Find unconnected pins (ERC-like analysis)
- Filter and search components
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Any, Optional
from skip import Schematic

logger = logging.getLogger('kicad_interface')

# Import pin locator for detailed pin information
try:
    from commands.pin_locator import PinLocator
    PIN_LOCATOR_AVAILABLE = True
except ImportError:
    logger.warning("PinLocator not available - pin details will be limited")
    PIN_LOCATOR_AVAILABLE = False


class SchematicInspector:
    """Inspect and analyze KiCAD schematic contents"""

    def __init__(self):
        """Initialize inspector with optional pin locator"""
        self._pin_locator = PinLocator() if PIN_LOCATOR_AVAILABLE else None

    def get_schematic_info(
        self,
        schematic_path: str,
        include_components: bool = True,
        include_nets: bool = True,
        include_pin_details: bool = False,
        include_unconnected: bool = False,
        component_filter: Optional[str] = None,
        exclude_templates: bool = True
    ) -> Dict[str, Any]:
        """
        Get comprehensive information about a schematic

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
                    "summary": self._get_summary(schematic, exclude_templates)
                }
            }

            if include_components:
                result["schematic"]["components"] = self._get_components(
                    schematic,
                    path,
                    component_filter,
                    exclude_templates,
                    include_pin_details
                )

            if include_nets:
                result["schematic"]["nets"] = self._get_nets(schematic, path)

            if include_unconnected:
                result["schematic"]["unconnectedPins"] = self._find_unconnected_pins(
                    schematic, path, exclude_templates
                )

            return result

        except Exception as e:
            logger.error(f"Error getting schematic info: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}

    def _get_summary(self, schematic: Schematic, exclude_templates: bool) -> Dict[str, Any]:
        """Get summary statistics for schematic"""
        summary = {
            "totalComponents": 0,
            "totalWires": 0,
            "totalNets": 0,
            "boundingBox": {"minX": 0, "minY": 0, "maxX": 0, "maxY": 0}
        }

        # Count components (excluding templates if requested)
        if hasattr(schematic, 'symbol'):
            for symbol in schematic.symbol:
                if exclude_templates:
                    ref = self._get_reference(symbol)
                    if ref and ref.startswith('_TEMPLATE'):
                        continue
                summary["totalComponents"] += 1

        # Count wires
        if hasattr(schematic, 'wire'):
            summary["totalWires"] = len(list(schematic.wire))

        # Count unique nets from labels
        if hasattr(schematic, 'label'):
            net_names = set()
            for label in schematic.label:
                if hasattr(label, 'value') and label.value:
                    net_names.add(label.value)
            summary["totalNets"] = len(net_names)

        # Calculate bounding box from component positions
        min_x, min_y = float('inf'), float('inf')
        max_x, max_y = float('-inf'), float('-inf')
        has_positions = False

        if hasattr(schematic, 'symbol'):
            for symbol in schematic.symbol:
                if exclude_templates:
                    ref = self._get_reference(symbol)
                    if ref and ref.startswith('_TEMPLATE'):
                        continue

                if hasattr(symbol, 'at') and hasattr(symbol.at, 'value'):
                    pos = symbol.at.value
                    x, y = float(pos[0]), float(pos[1])
                    min_x, min_y = min(min_x, x), min(min_y, y)
                    max_x, max_y = max(max_x, x), max(max_y, y)
                    has_positions = True

        if has_positions:
            summary["boundingBox"] = {
                "minX": round(min_x, 2),
                "minY": round(min_y, 2),
                "maxX": round(max_x, 2),
                "maxY": round(max_y, 2)
            }

        return summary

    def _get_components(
        self,
        schematic: Schematic,
        schematic_path: Path,
        component_filter: Optional[str],
        exclude_templates: bool,
        include_pin_details: bool
    ) -> List[Dict[str, Any]]:
        """Get list of all components with their properties"""
        components = []

        if not hasattr(schematic, 'symbol'):
            return components

        # Compile filter regex if provided
        filter_pattern = re.compile(component_filter, re.IGNORECASE) if component_filter else None

        for symbol in schematic.symbol:
            ref = self._get_reference(symbol)

            # Skip templates if requested
            if exclude_templates and ref and ref.startswith('_TEMPLATE'):
                continue

            # Apply filter if provided
            if filter_pattern and ref and not filter_pattern.match(ref):
                continue

            component = {
                "reference": ref or "unknown",
                "value": self._get_property(symbol, 'Value', ''),
                "footprint": self._get_property(symbol, 'Footprint', ''),
                "libId": symbol.lib_id.value if hasattr(symbol, 'lib_id') else '',
                "position": self._get_position(symbol)
            }

            # Add pin details if requested
            if include_pin_details and self._pin_locator and ref:
                component["pins"] = self._get_component_pins(
                    schematic, schematic_path, symbol, ref
                )

            components.append(component)

        # Sort by reference for consistent output
        components.sort(key=lambda c: self._sort_reference(c["reference"]))

        return components

    def _get_component_pins(
        self,
        schematic: Schematic,
        schematic_path: Path,
        symbol,
        reference: str
    ) -> List[Dict[str, Any]]:
        """Get detailed pin information for a component"""
        pins = []

        lib_id = symbol.lib_id.value if hasattr(symbol, 'lib_id') else None
        if not lib_id:
            return pins

        # Get pin definitions from lib_symbols
        pin_defs = self._pin_locator.get_symbol_pins(schematic_path, lib_id)

        for pin_num, pin_data in pin_defs.items():
            # Get absolute pin position
            pin_loc = self._pin_locator.get_pin_location(schematic_path, reference, pin_num)

            pin_info = {
                "number": pin_num,
                "name": pin_data.get('name', ''),
                "type": pin_data.get('type', 'passive'),
                "position": {
                    "x": round(pin_loc[0], 2) if pin_loc else 0,
                    "y": round(pin_loc[1], 2) if pin_loc else 0
                }
            }
            pins.append(pin_info)

        # Sort pins by number
        pins.sort(key=lambda p: self._sort_pin_number(p["number"]))

        return pins

    def _get_nets(self, schematic: Schematic, schematic_path: Path) -> List[Dict[str, Any]]:
        """Get all nets with their connections"""
        nets = []

        if not hasattr(schematic, 'label'):
            return nets

        # Collect unique net names
        net_names = set()
        for label in schematic.label:
            if hasattr(label, 'value') and label.value:
                net_names.add(label.value)

        # For each net, find connections
        from commands.connection_schematic import ConnectionManager

        for net_name in sorted(net_names):
            connections = ConnectionManager.get_net_connections(
                schematic, net_name, schematic_path
            )

            net_info = {
                "name": net_name,
                "connections": connections
            }
            nets.append(net_info)

        return nets

    def _find_unconnected_pins(
        self,
        schematic: Schematic,
        schematic_path: Path,
        exclude_templates: bool
    ) -> List[Dict[str, Any]]:
        """Find pins that are not connected to any wire or net"""
        unconnected = []

        if not hasattr(schematic, 'symbol') or not self._pin_locator:
            return unconnected

        # Build set of all wire endpoints
        wire_points = set()
        if hasattr(schematic, 'wire'):
            for wire in schematic.wire:
                if hasattr(wire, 'pts') and hasattr(wire.pts, 'xy'):
                    for point in wire.pts.xy:
                        if hasattr(point, 'value'):
                            # Round to avoid floating point issues
                            x = round(float(point.value[0]), 1)
                            y = round(float(point.value[1]), 1)
                            wire_points.add((x, y))

        tolerance = 0.5  # mm tolerance for connection detection

        def is_connected(pin_x: float, pin_y: float) -> bool:
            """Check if a pin position is connected to any wire"""
            for wx, wy in wire_points:
                if abs(pin_x - wx) < tolerance and abs(pin_y - wy) < tolerance:
                    return True
            return False

        # Check each component's pins
        for symbol in schematic.symbol:
            ref = self._get_reference(symbol)

            if exclude_templates and ref and ref.startswith('_TEMPLATE'):
                continue

            lib_id = symbol.lib_id.value if hasattr(symbol, 'lib_id') else None
            if not lib_id or not ref:
                continue

            # Get all pins for this symbol
            pin_defs = self._pin_locator.get_symbol_pins(schematic_path, lib_id)

            for pin_num, pin_data in pin_defs.items():
                pin_loc = self._pin_locator.get_pin_location(schematic_path, ref, pin_num)

                if pin_loc and not is_connected(round(pin_loc[0], 1), round(pin_loc[1], 1)):
                    unconnected.append({
                        "component": ref,
                        "pin": pin_num,
                        "pinName": pin_data.get('name', ''),
                        "pinType": pin_data.get('type', 'passive'),
                        "position": {
                            "x": round(pin_loc[0], 2),
                            "y": round(pin_loc[1], 2)
                        }
                    })

        return unconnected

    # Helper methods

    def _get_reference(self, symbol) -> Optional[str]:
        """Safely get reference designator from symbol"""
        try:
            if hasattr(symbol, 'property') and hasattr(symbol.property, 'Reference'):
                return symbol.property.Reference.value
            if hasattr(symbol, 'reference'):
                return symbol.reference
        except:
            pass
        return None

    def _get_property(self, symbol, prop_name: str, default: str = '') -> str:
        """Safely get a property value from symbol"""
        try:
            if hasattr(symbol, 'property') and hasattr(symbol.property, prop_name):
                prop = getattr(symbol.property, prop_name)
                return prop.value if hasattr(prop, 'value') else str(prop)
        except:
            pass
        return default

    def _get_position(self, symbol) -> Dict[str, float]:
        """Get position and rotation from symbol"""
        try:
            if hasattr(symbol, 'at') and hasattr(symbol.at, 'value'):
                pos = symbol.at.value
                return {
                    "x": round(float(pos[0]), 2),
                    "y": round(float(pos[1]), 2),
                    "rotation": round(float(pos[2]), 1) if len(pos) > 2 else 0
                }
        except:
            pass
        return {"x": 0, "y": 0, "rotation": 0}

    def _sort_reference(self, ref: str) -> tuple:
        """Sort references naturally (R1, R2, R10 not R1, R10, R2)"""
        match = re.match(r'([A-Za-z]+)(\d*)', ref or '')
        if match:
            prefix, num = match.groups()
            return (prefix, int(num) if num else 0)
        return (ref or '', 0)

    def _sort_pin_number(self, pin: str) -> tuple:
        """Sort pin numbers naturally"""
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
    component_filter: Optional[str] = None,
    exclude_templates: bool = True
) -> Dict[str, Any]:
    """
    Get comprehensive information about a KiCAD schematic

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
        exclude_templates=exclude_templates
    )
