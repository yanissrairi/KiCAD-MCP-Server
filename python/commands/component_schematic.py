"""Component Schematic Manager - Add and manage components in KiCAD schematics.

Provides component management capabilities for AI-assisted schematic design:
- Add components by cloning from templates (static or dynamic)
- Remove, update, and search for components
- Support for dynamic symbol loading from KiCAD libraries
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar
import uuid

if TYPE_CHECKING:
    from pathlib import Path

    from skip import Schematic

    from commands.dynamic_symbol_loader import DynamicSymbolLoader

logger = logging.getLogger(__name__)

# Import dynamic symbol loader
try:
    from commands.dynamic_symbol_loader import DynamicSymbolLoader as _DynamicSymbolLoader

    DYNAMIC_LOADING_AVAILABLE = True
except ImportError:
    logger.warning("Dynamic symbol loader not available - falling back to template-only mode")
    DYNAMIC_LOADING_AVAILABLE = False
    _DynamicSymbolLoader = None  # type: ignore[assignment, misc]


class ComponentManager:
    """Manage components in a schematic."""

    # Initialize dynamic loader (class variable, shared across instances)
    _dynamic_loader: ClassVar[DynamicSymbolLoader | None] = None

    # Template symbol references mapping component type to template reference
    TEMPLATE_MAP: ClassVar[dict[str, str]] = {
        # Passives
        "R": "_TEMPLATE_R",
        "C": "_TEMPLATE_C",
        "L": "_TEMPLATE_L",
        "Y": "_TEMPLATE_Y",
        "Crystal": "_TEMPLATE_Y",
        # Semiconductors
        "D": "_TEMPLATE_D",
        "LED": "_TEMPLATE_LED",
        "Q": "_TEMPLATE_Q_NPN",
        "Q_NPN": "_TEMPLATE_Q_NPN",
        "Q_NMOS": "_TEMPLATE_Q_NMOS",
        "MOSFET": "_TEMPLATE_Q_NMOS",
        # ICs
        "U": "_TEMPLATE_U_OPAMP",
        "OpAmp": "_TEMPLATE_U_OPAMP",
        "IC": "_TEMPLATE_U_OPAMP",
        "U_REG": "_TEMPLATE_U_REG",
        "Regulator": "_TEMPLATE_U_REG",
        # Connectors
        "J": "_TEMPLATE_J2",
        "J2": "_TEMPLATE_J2",
        "J4": "_TEMPLATE_J4",
        "Conn_2": "_TEMPLATE_J2",
        "Conn_4": "_TEMPLATE_J4",
        # Misc
        "SW": "_TEMPLATE_SW",
        "Button": "_TEMPLATE_SW",
        "Switch": "_TEMPLATE_SW",
    }

    @classmethod
    def get_dynamic_loader(cls) -> DynamicSymbolLoader | None:
        """Get or create dynamic symbol loader instance.

        Returns:
            The dynamic symbol loader instance, or None if not available.
        """
        if cls._dynamic_loader is None and DYNAMIC_LOADING_AVAILABLE:
            cls._dynamic_loader = _DynamicSymbolLoader()
        return cls._dynamic_loader

    @classmethod
    def get_or_create_template(
        cls,
        schematic: Schematic,
        comp_type: str,
        library: str | None = None,
        schematic_path: Path | None = None,
    ) -> tuple[str, bool]:
        """Get template reference for a component type, creating it dynamically if needed.

        Args:
            schematic: Schematic object
            comp_type: Component type (e.g., 'R', 'LED', 'STM32F103C8Tx')
            library: Optional library name (defaults to 'Device' for common types)
            schematic_path: Optional path to schematic file (required for dynamic loading)

        Returns:
            Tuple of (template_ref, needs_reload) where needs_reload indicates
            if schematic must be reloaded
        """

        def template_exists(sch: Schematic, template_ref: str) -> bool:
            """Check if template exists by iterating symbols (handles special characters)."""
            for symbol in sch.symbol:
                if (
                    hasattr(symbol.property, "Reference")
                    and symbol.property.Reference.value == template_ref
                ):
                    return True
            return False

        # 1. Check static template map first
        if comp_type in cls.TEMPLATE_MAP:
            template_ref = cls.TEMPLATE_MAP[comp_type]
            # Verify template exists in schematic
            if template_exists(schematic, template_ref):
                logger.debug("Using static template: %s", template_ref)
                return (template_ref, False)

        # 2. Check if dynamically loaded template already exists
        # Build potential template reference names
        potential_refs: list[str] = []
        if library:
            potential_refs.append(f"_TEMPLATE_{library}_{comp_type}")
        potential_refs.append(f"_TEMPLATE_{comp_type}")
        if comp_type in cls.TEMPLATE_MAP:
            potential_refs.append(cls.TEMPLATE_MAP[comp_type])

        # Check each potential reference
        for template_ref in potential_refs:
            if template_exists(schematic, template_ref):
                logger.debug("Found existing template: %s", template_ref)
                return (template_ref, False)

        # 3. Try dynamic loading
        if not DYNAMIC_LOADING_AVAILABLE:
            logger.warning(
                "Component type '%s' not in static templates and dynamic loading unavailable",
                comp_type,
            )
            # Fall back to basic resistor template
            return ("_TEMPLATE_R", False)

        loader = cls.get_dynamic_loader()
        if not loader:
            logger.warning("Dynamic loader unavailable, using fallback template")
            return ("_TEMPLATE_R", False)

        # Check if schematic path is available
        if schematic_path is None:
            logger.warning(
                "Dynamic loading requires schematic file path but none was provided"
            )
            fallback = cls.TEMPLATE_MAP.get(comp_type, "_TEMPLATE_R")
            return (fallback, False)

        # Determine library name
        effective_library = library if library is not None else "Device"

        try:
            logger.info(
                "Attempting dynamic load: %s:%s from %s",
                effective_library,
                comp_type,
                schematic_path,
            )

            # Use dynamic symbol loader to inject symbol and create template
            template_ref = loader.load_symbol_dynamically(
                schematic_path, effective_library, comp_type
            )

            logger.info("Successfully loaded symbol dynamically. Template ref: %s", template_ref)
            # Signal that schematic needs reload to see new template
            return (template_ref, True)

        except Exception:
            logger.exception("Dynamic loading failed for %s:%s", effective_library, comp_type)
            # Fall back to static template if available
            fallback = cls.TEMPLATE_MAP.get(comp_type, "_TEMPLATE_R")
            return (fallback, False)

    @staticmethod
    def add_component(
        schematic: Schematic,
        component_def: dict[str, Any],
        schematic_path: Path | None = None,
    ) -> Any:
        """Add a component to the schematic by cloning from template.

        Args:
            schematic: Schematic object to add component to
            component_def: Component definition dictionary
            schematic_path: Optional path to schematic file (enables dynamic symbol loading)

        Returns:
            The newly created symbol

        Raises:
            ValueError: If the template symbol is not found in the schematic
        """
        # Import here to avoid circular imports
        from commands.schematic import SchematicManager

        logger.info(
            "Adding component: type=%s, ref=%s",
            component_def.get("type"),
            component_def.get("reference"),
        )
        logger.debug("Full component_def: %s", component_def)

        # Get component type and determine template
        comp_type = component_def.get("type", "R")
        library = component_def.get("library")  # Optional library specification

        # Get template reference (static or dynamic)
        template_ref, needs_reload = ComponentManager.get_or_create_template(
            schematic, comp_type, library, schematic_path
        )

        # If dynamic loading occurred, reload schematic to see new template
        if needs_reload and schematic_path:
            logger.info("Reloading schematic after dynamic loading: %s", schematic_path)
            schematic = SchematicManager.load_schematic(str(schematic_path))

        # Find template symbol by reference (handles special characters like +)
        template_symbol = None
        for symbol in schematic.symbol:
            if (
                hasattr(symbol.property, "Reference")
                and symbol.property.Reference.value == template_ref
            ):
                template_symbol = symbol
                break

        if not template_symbol:
            available_refs = [
                str(s.property.Reference.value)
                for s in schematic.symbol
                if hasattr(s.property, "Reference")
            ]
            logger.error(
                "Template symbol %s not found in schematic. Available symbols: %s",
                template_ref,
                available_refs,
            )
            msg = (
                f"Template symbol {template_ref} not found. "
                "The schematic must be created from template_with_symbols.kicad_sch"
            )
            raise ValueError(msg)

        # Clone the template symbol
        new_symbol = template_symbol.clone()
        logger.debug("Cloned template symbol %s", template_ref)

        # Set reference
        reference = component_def.get("reference", "R?")
        new_symbol.property.Reference.value = reference
        logger.debug("Set reference to %s", reference)

        # Set value
        if "value" in component_def:
            new_symbol.property.Value.value = component_def["value"]
            logger.debug("Set value to %s", component_def["value"])

        # Set footprint
        if "footprint" in component_def:
            new_symbol.property.Footprint.value = component_def["footprint"]
            logger.debug("Set footprint to %s", component_def["footprint"])

        # Set datasheet
        if "datasheet" in component_def:
            new_symbol.property.Datasheet.value = component_def["datasheet"]

        # Set position
        x = component_def.get("x", 0)
        y = component_def.get("y", 0)
        rotation = component_def.get("rotation", 0)
        new_symbol.at.value = [x, y, rotation]
        logger.debug("Set position to (%s, %s, %s)", x, y, rotation)

        # Set BOM and board flags
        new_symbol.in_bom.value = component_def.get("in_bom", True)
        new_symbol.on_board.value = component_def.get("on_board", True)
        new_symbol.dnp.value = component_def.get("dnp", False)

        # Generate new UUID
        new_symbol.uuid.value = str(uuid.uuid4())

        # NOTE: Do NOT call schematic.symbol.append(new_symbol) here!
        # The clone() method in kicad-skip already appends the cloned symbol
        # to the parent collection. Calling append() again causes a name collision
        # in NamedElementCollection._named dict, which adds an underscore suffix
        # to the reference (e.g., "R1" becomes "R1_"). This breaks connection lookup.

        logger.info("Successfully added component %s to schematic", reference)

        return new_symbol

    @staticmethod
    def remove_component(schematic: Schematic, component_ref: str) -> bool:
        """Remove a component from the schematic by reference designator.

        Args:
            schematic: The schematic to modify
            component_ref: The component reference designator (e.g., 'R1')

        Returns:
            True if the component was removed, False if not found
        """
        # kicad-skip doesn't have a direct remove_symbol method by reference.
        # We need to find the symbol and then remove it from the symbols list.
        symbol_to_remove = None
        for symbol in schematic.symbol:
            if symbol.reference == component_ref:
                symbol_to_remove = symbol
                break

        if symbol_to_remove:
            schematic.symbol.remove(symbol_to_remove)
            return True
        return False

    @staticmethod
    def update_component(
        schematic: Schematic,
        component_ref: str,
        new_properties: dict[str, Any],
    ) -> bool:
        """Update component properties by reference designator.

        Args:
            schematic: The schematic containing the component
            component_ref: The component reference designator (e.g., 'R1')
            new_properties: Dictionary of property names to new values

        Returns:
            True if the component was updated, False if not found
        """
        symbol_to_update = None
        for symbol in schematic.symbol:
            if symbol.reference == component_ref:
                symbol_to_update = symbol
                break

        if symbol_to_update:
            for key, value in new_properties.items():
                if key in symbol_to_update.property:
                    symbol_to_update.property[key].value = value
                else:
                    # Add as a new property if it doesn't exist
                    symbol_to_update.property.append(key, value)
            return True
        return False

    @staticmethod
    def get_component(schematic: Schematic, component_ref: str) -> Any | None:
        """Get a component by reference designator.

        Args:
            schematic: The schematic to search
            component_ref: The component reference designator (e.g., 'R1')

        Returns:
            The symbol object if found, None otherwise
        """
        for symbol in schematic.symbol:
            if symbol.reference == component_ref:
                return symbol
        return None

    @staticmethod
    def search_components(schematic: Schematic, query: str) -> list[Any]:
        """Search for components matching criteria (basic implementation).

        Args:
            schematic: The schematic to search
            query: Search query string (matches reference, name, or value)

        Returns:
            List of matching symbol objects
        """
        # This is a basic search, could be expanded to use regex or more complex logic
        matching_components = []
        query_lower = query.lower()
        # Use list comprehension for better performance
        return [
            symbol
            for symbol in schematic.symbol
            if (
                query_lower in symbol.reference.lower()
                or query_lower in symbol.name.lower()
                or (
                    hasattr(symbol.property, "Value")
                    and query_lower in symbol.property.Value.value.lower()
                )
            )
        ]

    @staticmethod
    def get_all_components(schematic: Schematic) -> list[Any]:
        """Get all components in schematic.

        Args:
            schematic: The schematic to query

        Returns:
            List of all symbol objects in the schematic
        """
        return list(schematic.symbol)


if __name__ == "__main__":
    # Example Usage (for testing)
    from commands.schematic import SchematicManager

    def _run_example() -> None:
        """Run example component operations for testing."""
        # Create a new schematic
        test_sch = SchematicManager.create_schematic("ComponentTestSchematic")

        # Add components
        comp1_def: dict[str, Any] = {
            "type": "R",
            "reference": "R1",
            "value": "10k",
            "x": 100,
            "y": 100,
        }
        comp2_def: dict[str, Any] = {
            "type": "C",
            "reference": "C1",
            "value": "0.1uF",
            "x": 200,
            "y": 100,
            "library": "Device",
        }
        comp3_def: dict[str, Any] = {
            "type": "LED",
            "reference": "D1",
            "x": 300,
            "y": 100,
            "library": "Device",
            "properties": {"Color": "Red"},
        }

        ComponentManager.add_component(test_sch, comp1_def)
        ComponentManager.add_component(test_sch, comp2_def)
        ComponentManager.add_component(test_sch, comp3_def)

        # Get a component
        _ = ComponentManager.get_component(test_sch, "C1")

        # Update a component
        ComponentManager.update_component(test_sch, "R1", {"value": "20k", "Tolerance": "5%"})

        # Search components
        _ = ComponentManager.search_components(test_sch, "100")

        # Get all components
        _ = ComponentManager.get_all_components(test_sch)

        # Remove a component
        ComponentManager.remove_component(test_sch, "D1")
        _ = ComponentManager.get_all_components(test_sch)

    _run_example()
