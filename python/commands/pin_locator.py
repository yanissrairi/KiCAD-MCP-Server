"""Pin Locator for KiCad Schematics.

Discovers pin locations on symbol instances, accounting for position, rotation, and mirroring.
Uses S-expression parsing to extract pin data from symbol definitions.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

import sexpdata
from sexpdata import Symbol
from skip import Schematic

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

logger = logging.getLogger("kicad_interface")


class PinLocator:
    """Locate pins on symbol instances in KiCad schematics."""

    def __init__(self) -> None:
        """Initialize pin locator with empty cache."""
        self.pin_definition_cache: dict[str, dict[str, dict[str, Any]]] = {}

    @staticmethod
    def _is_pin_definition(sexp: Any) -> bool:
        """Check if an S-expression is a pin definition.

        Args:
            sexp: S-expression to check

        Returns:
            True if this is a pin definition, False otherwise
        """
        return isinstance(sexp, list) and len(sexp) > 0 and sexp[0] == Symbol("pin")

    @staticmethod
    def _create_pin_data(sexp: list[Any]) -> dict[str, Any]:
        """Create initial pin data structure with default values.

        Args:
            sexp: Pin S-expression

        Returns:
            Pin data dictionary with defaults
        """
        return {
            "x": 0,
            "y": 0,
            "angle": 0,
            "length": 0,
            "name": "",
            "number": "",
            "type": str(sexp[1]) if len(sexp) > 1 else "passive",
        }

    @staticmethod
    def _extract_pin_attributes(sexp: list[Any], pin_data: dict[str, Any]) -> None:
        """Extract pin attributes from S-expression.

        Args:
            sexp: Pin S-expression
            pin_data: Pin data dictionary to populate (modified in place)
        """
        for item in sexp:
            if not isinstance(item, list) or len(item) == 0:
                continue

            if item[0] == Symbol("at") and len(item) >= 3:  # noqa: PLR2004
                pin_data["x"] = float(item[1])
                pin_data["y"] = float(item[2])
                if len(item) >= 4:  # noqa: PLR2004
                    pin_data["angle"] = float(item[3])

            elif item[0] == Symbol("length") and len(item) >= 2:  # noqa: PLR2004
                pin_data["length"] = float(item[1])

            elif item[0] == Symbol("name") and len(item) >= 2:  # noqa: PLR2004
                pin_data["name"] = str(item[1]).strip('"')

            elif item[0] == Symbol("number") and len(item) >= 2:  # noqa: PLR2004
                pin_data["number"] = str(item[1]).strip('"')

    @staticmethod
    def _extract_pins_recursive(sexp: Any, pins: dict[str, dict[str, Any]]) -> None:
        """Recursively search for pin definitions in S-expression.

        Args:
            sexp: S-expression to search through
            pins: Dictionary to store found pins (modified in place)
        """
        if not isinstance(sexp, list):
            return

        # Check if this is a pin definition
        if PinLocator._is_pin_definition(sexp):
            pin_data = PinLocator._create_pin_data(sexp)
            PinLocator._extract_pin_attributes(sexp, pin_data)

            # Store by pin number
            if pin_data["number"]:
                pins[pin_data["number"]] = pin_data

        # Recurse into sublists
        for item in sexp:
            if isinstance(item, list):
                PinLocator._extract_pins_recursive(item, pins)

    @staticmethod
    def parse_symbol_definition(symbol_def: Sequence[Any]) -> dict[str, dict[str, Any]]:
        """Parse a symbol definition from lib_symbols to extract pin information.

        Args:
            symbol_def: S-expression list representing symbol definition

        Returns:
            Dictionary mapping pin number -> pin data:
            {
                "1": {"x": 0, "y": 3.81, "angle": 270, "length": 1.27, "name": "~", "type": "passive"},
                "2": {"x": 0, "y": -3.81, "angle": 90, "length": 1.27, "name": "~", "type": "passive"}
            }
        """
        pins: dict[str, dict[str, Any]] = {}
        PinLocator._extract_pins_recursive(list(symbol_def), pins)
        return pins

    def get_symbol_pins(self, schematic_path: Path, lib_id: str) -> dict[str, dict[str, Any]]:
        """Get pin definitions for a symbol from the schematic's lib_symbols section.

        Args:
            schematic_path: Path to .kicad_sch file
            lib_id: Library identifier (e.g., "Device:R", "MCU_ST_STM32F1:STM32F103C8Tx")

        Returns:
            Dictionary mapping pin number -> pin data
        """
        # Check cache
        cache_key = f"{schematic_path}:{lib_id}"
        if cache_key in self.pin_definition_cache:
            logger.debug("Using cached pin data for %s", lib_id)
            return self.pin_definition_cache[cache_key]

        try:
            # Read schematic
            with schematic_path.open(encoding="utf-8") as f:
                sch_content = f.read()

            sch_data = sexpdata.loads(sch_content)

            # Find lib_symbols section
            lib_symbols = None
            for item in sch_data:
                if isinstance(item, list) and len(item) > 0 and item[0] == Symbol("lib_symbols"):
                    lib_symbols = item
                    break

            if not lib_symbols:
                logger.error("No lib_symbols section found in schematic")
                return {}

            # Find the specific symbol definition
            for item in lib_symbols[1:]:  # Skip 'lib_symbols' itself
                if isinstance(item, list) and len(item) > 1 and item[0] == Symbol("symbol"):
                    symbol_name = str(item[1]).strip('"')
                    if symbol_name == lib_id:
                        # Found the symbol, parse pins
                        pins = self.parse_symbol_definition(item)
                        self.pin_definition_cache[cache_key] = pins
                        logger.info("Extracted %d pins from %s", len(pins), lib_id)
                        return pins

            logger.warning("Symbol %s not found in lib_symbols", lib_id)

        except (OSError, ValueError, TypeError, KeyError) as e:
            logger.exception("Error getting symbol pins: %s", e)

        return {}

    @staticmethod
    def rotate_point(x: float, y: float, angle_degrees: float) -> tuple[float, float]:
        """Rotate a point around the origin.

        Args:
            x: X coordinate
            y: Y coordinate
            angle_degrees: Rotation angle in degrees (counterclockwise)

        Returns:
            (rotated_x, rotated_y)
        """
        if angle_degrees == 0:
            return (x, y)

        angle_rad = math.radians(angle_degrees)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)

        rotated_x = x * cos_a - y * sin_a
        rotated_y = x * sin_a + y * cos_a

        return (rotated_x, rotated_y)

    def get_pin_location(
        self, schematic_path: Path, symbol_reference: str, pin_number: str
    ) -> list[float] | None:
        """Get the absolute location of a pin on a symbol instance.

        Args:
            schematic_path: Path to .kicad_sch file
            symbol_reference: Symbol reference designator (e.g., "R1", "U1")
            pin_number: Pin number/identifier (e.g., "1", "2", "GND", "VCC")

        Returns:
            [x, y] absolute coordinates of the pin, or None if not found
        """
        try:
            # Load schematic with kicad-skip to get symbol instance
            sch = Schematic(str(schematic_path))

            # Find the symbol instance
            target_symbol = None
            for symbol in sch.symbol:
                ref = symbol.property.Reference.value
                if ref == symbol_reference:
                    target_symbol = symbol
                    break

            if not target_symbol:
                logger.error("Symbol %s not found in schematic", symbol_reference)
                return None

            # Get symbol position and rotation
            symbol_at = target_symbol.at.value
            symbol_x = float(symbol_at[0])
            symbol_y = float(symbol_at[1])
            symbol_rotation = float(symbol_at[2]) if len(symbol_at) > 2 else 0.0  # noqa: PLR2004

            # Get symbol lib_id
            lib_id = target_symbol.lib_id.value if hasattr(target_symbol, "lib_id") else None
            if not lib_id:
                logger.error("Symbol %s has no lib_id", symbol_reference)
                return None

            logger.debug(
                "Symbol %s: pos=(%s, %s), rot=%s, lib_id=%s",
                symbol_reference,
                symbol_x,
                symbol_y,
                symbol_rotation,
                lib_id,
            )

            # Get pin definitions for this symbol
            pins = self.get_symbol_pins(schematic_path, lib_id)
            if not pins:
                logger.error("No pin definitions found for %s", lib_id)
                return None

            # Find the requested pin
            if pin_number not in pins:
                logger.error(
                    "Pin %s not found on %s. Available pins: %s",
                    pin_number,
                    symbol_reference,
                    list(pins.keys()),
                )
                return None

            pin_data = pins[pin_number]

            # Get pin position relative to symbol origin
            pin_rel_x = pin_data["x"]
            pin_rel_y = pin_data["y"]

            logger.debug("Pin %s relative position: (%s, %s)", pin_number, pin_rel_x, pin_rel_y)

            # Apply symbol rotation to pin position
            if symbol_rotation != 0:
                pin_rel_x, pin_rel_y = self.rotate_point(pin_rel_x, pin_rel_y, symbol_rotation)
                logger.debug("After rotation %s deg: (%s, %s)", symbol_rotation, pin_rel_x, pin_rel_y)

            # Calculate absolute position
            abs_x = symbol_x + pin_rel_x
            abs_y = symbol_y + pin_rel_y

            logger.info("Pin %s/%s located at (%s, %s)", symbol_reference, pin_number, abs_x, abs_y)
            return [abs_x, abs_y]

        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.exception("Error getting pin location: %s", e)
            return None

    def get_all_symbol_pins(
        self, schematic_path: Path, symbol_reference: str
    ) -> dict[str, list[float]]:
        """Get locations of all pins on a symbol instance.

        Args:
            schematic_path: Path to .kicad_sch file
            symbol_reference: Symbol reference designator (e.g., "R1", "U1")

        Returns:
            Dictionary mapping pin number -> [x, y] coordinates
        """
        try:
            # Load schematic
            sch = Schematic(str(schematic_path))

            # Find symbol
            target_symbol = None
            for symbol in sch.symbol:
                if symbol.property.Reference.value == symbol_reference:
                    target_symbol = symbol
                    break

            if not target_symbol:
                logger.error("Symbol %s not found", symbol_reference)
                return {}

            # Get lib_id
            lib_id = target_symbol.lib_id.value if hasattr(target_symbol, "lib_id") else None
            if not lib_id:
                logger.error("Symbol %s has no lib_id", symbol_reference)
                return {}

            # Get pin definitions
            pins = self.get_symbol_pins(schematic_path, lib_id)
            if not pins:
                return {}

            # Calculate location for each pin
            result: dict[str, list[float]] = {}
            for pin_num in pins:
                location = self.get_pin_location(schematic_path, symbol_reference, pin_num)
                if location:
                    result[pin_num] = location

            logger.info("Located %d pins on %s", len(result), symbol_reference)
            return result

        except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
            logger.exception("Error getting all symbol pins: %s", e)
            return {}
