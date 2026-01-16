"""Dynamic Symbol Loader for KiCad Schematics.

Loads symbols from .kicad_sym library files and injects them into schematics
on-the-fly, eliminating the need for static templates.

This enables access to all ~10,000+ KiCad symbols dynamically.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import sexpdata
from sexpdata import Symbol

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger("kicad_interface")


class DynamicSymbolLoader:
    """Dynamically loads symbols from KiCad library files and injects them into schematics.

    Workflow:
    1. Parse .kicad_sym library file to extract symbol definition
    2. Inject symbol definition into schematic's lib_symbols section
    3. Create an offscreen template instance that can be cloned
    4. Clone the template to create actual component instances
    """

    def __init__(self) -> None:
        """Initialize the dynamic symbol loader."""
        self.library_cache: dict[str, list[Any]] = {}  # Cache: path -> parsed data
        self.symbol_cache: dict[str, list[Any]] = {}  # Cache: "lib:symbol" -> symbol_def

    def find_kicad_symbol_libraries(self) -> list[Path]:
        """Find all KiCad symbol library directories.

        Returns:
            List of paths to symbol library directories.
        """
        possible_paths = [
            # Linux
            Path("/usr/share/kicad/symbols"),
            Path("/usr/local/share/kicad/symbols"),
            # Windows
            Path("C:/Program Files/KiCad/9.0/share/kicad/symbols"),
            Path("C:/Program Files/KiCad/8.0/share/kicad/symbols"),
            # macOS
            Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"),
            # User libraries
            Path.home() / ".local" / "share" / "kicad" / "9.0" / "symbols",
            Path.home() / ".local" / "share" / "kicad" / "8.0" / "symbols",
            Path.home() / "Documents" / "KiCad" / "9.0" / "3rdparty" / "symbols",
        ]

        # Check environment variables
        for env_var in ["KICAD9_SYMBOL_DIR", "KICAD8_SYMBOL_DIR", "KICAD_SYMBOL_DIR"]:
            if env_var in os.environ:
                possible_paths.insert(0, Path(os.environ[env_var]))

        found_paths = []
        for path in possible_paths:
            if path.exists() and path.is_dir():
                found_paths.append(path)
                logger.info("Found KiCad symbol library directory: %s", path)

        return found_paths

    def find_library_file(self, library_name: str) -> Path | None:
        """Find the .kicad_sym file for a given library name.

        Args:
            library_name: Library name (e.g., "Device", "Connector_Generic").

        Returns:
            Path to .kicad_sym file or None if not found.
        """
        library_dirs = self.find_kicad_symbol_libraries()

        for lib_dir in library_dirs:
            lib_file = lib_dir / f"{library_name}.kicad_sym"
            if lib_file.exists():
                logger.debug("Found library file: %s", lib_file)
                return lib_file

        logger.warning("Library file not found: %s.kicad_sym", library_name)
        return None

    def parse_library_file(self, library_path: Path) -> list[Any]:
        """Parse a .kicad_sym file into S-expression data structure.

        Args:
            library_path: Path to .kicad_sym file.

        Returns:
            Parsed S-expression data.

        Raises:
            OSError: If the file cannot be read.
            ValueError: If the file cannot be parsed.
        """
        # Check cache first
        cache_key = str(library_path)
        if cache_key in self.library_cache:
            logger.debug("Using cached library data for: %s", library_path.name)
            return self.library_cache[cache_key]

        logger.info("Parsing library file: %s", library_path)

        with library_path.open(encoding="utf-8") as f:
            content = f.read()

        # Parse S-expression
        parsed: list[Any] = sexpdata.loads(content)

        # Cache the result
        self.library_cache[cache_key] = parsed

        logger.debug("Successfully parsed library: %s", library_path.name)
        return parsed

    def extract_symbol_definition(
        self, library_path: Path, symbol_name: str
    ) -> list[Any] | None:
        """Extract a specific symbol definition from a library file.

        Args:
            library_path: Path to .kicad_sym file.
            symbol_name: Name of symbol to extract (e.g., "R", "LED").

        Returns:
            Symbol definition as S-expression list, or None if not found.
        """
        cache_key = f"{library_path.name}:{symbol_name}"
        if cache_key in self.symbol_cache:
            logger.debug("Using cached symbol: %s", cache_key)
            return self.symbol_cache[cache_key]

        parsed_lib = self.parse_library_file(library_path)

        # Library structure: (kicad_symbol_lib (version ...) (symbol ...) ...)
        # We need to find the symbol with matching name

        for item in parsed_lib:
            if not self._is_symbol_element(item):
                continue
            # Symbol structure: (symbol "Name" ...)
            if len(item) > 1 and isinstance(item[1], str):
                # Handle both "Device:R" and "R" formats
                item_name = item[1]
                if ":" in item_name:
                    item_name = item_name.split(":")[1]

                if item_name == symbol_name:
                    logger.info("Found symbol definition: %s", symbol_name)
                    # Cache and return
                    self.symbol_cache[cache_key] = item
                    return item

        logger.warning("Symbol '%s' not found in %s", symbol_name, library_path.name)
        return None

    def inject_symbol_into_schematic(
        self, schematic_path: Path, library_name: str, symbol_name: str
    ) -> bool:
        """Inject a symbol definition from a library into a schematic file.

        Args:
            schematic_path: Path to .kicad_sch file to modify.
            library_name: Source library name (e.g., "Device").
            symbol_name: Symbol to inject (e.g., "R").

        Returns:
            True if successful, False otherwise.

        Raises:
            ValueError: If library or symbol not found, or schematic malformed.
            OSError: If file operations fail.
        """
        # 1. Find and parse the library file
        library_path = self.find_library_file(library_name)
        if not library_path:
            msg = f"Library not found: {library_name}"
            raise ValueError(msg)

        # 2. Extract the symbol definition
        symbol_def = self.extract_symbol_definition(library_path, symbol_name)
        if not symbol_def:
            msg = f"Symbol '{symbol_name}' not found in library '{library_name}'"
            raise ValueError(msg)

        # 3. Read the schematic file
        with schematic_path.open(encoding="utf-8") as f:
            sch_content = f.read()

        sch_data: list[Any] = sexpdata.loads(sch_content)

        # 4. Find the lib_symbols section
        lib_symbols_index = self._find_lib_symbols_index(sch_data)
        if lib_symbols_index is None:
            msg = "No lib_symbols section found in schematic"
            raise ValueError(msg)

        # 5. Check if symbol already exists in lib_symbols
        full_symbol_name = f"{library_name}:{symbol_name}"
        symbol_exists = self._check_symbol_exists(
            sch_data[lib_symbols_index], full_symbol_name, symbol_name
        )

        if symbol_exists:
            logger.info("Symbol %s already exists in schematic", full_symbol_name)
        else:
            # 6. Inject the symbol definition
            # Need to update the symbol name to include library prefix
            modified_symbol_def = list(symbol_def)  # Make a copy
            modified_symbol_def[1] = full_symbol_name  # Update name to "Library:Symbol"

            sch_data[lib_symbols_index].append(modified_symbol_def)
            logger.info("Injected symbol %s into schematic", full_symbol_name)

        # 7. Write the modified schematic back
        with schematic_path.open("w", encoding="utf-8") as f:
            output = sexpdata.dumps(sch_data)
            f.write(output)

        logger.info(
            "Successfully injected symbol %s into %s",
            full_symbol_name,
            schematic_path.name,
        )
        return True

    def create_template_instance(
        self,
        schematic_path: Path,
        library_name: str,
        symbol_name: str,
        template_ref: str | None = None,
    ) -> str:
        """Create an offscreen template instance of a symbol that can be cloned.

        Args:
            schematic_path: Path to .kicad_sch file.
            library_name: Library name (e.g., "Device").
            symbol_name: Symbol name (e.g., "R").
            template_ref: Optional custom reference (defaults to _TEMPLATE_{LIB}_{SYM}).

        Returns:
            Template reference name.

        Raises:
            ValueError: If schematic structure is invalid.
            OSError: If file operations fail.
        """
        if template_ref is None:
            # Clean up library and symbol names for reference
            lib_clean = library_name.replace("-", "_").replace(".", "_")
            sym_clean = symbol_name.replace("-", "_").replace(".", "_")
            template_ref = f"_TEMPLATE_{lib_clean}_{sym_clean}"

        # Read schematic
        with schematic_path.open(encoding="utf-8") as f:
            sch_content = f.read()

        sch_data: list[Any] = sexpdata.loads(sch_content)

        # Check if template already exists
        existing_ref = self._find_existing_template(sch_data, template_ref)
        if existing_ref:
            logger.info("Template instance %s already exists", template_ref)
            return template_ref

        # Find sheet_instances index (we'll insert before this)
        sheet_instances_index = self._find_sheet_instances_index(sch_data)
        if sheet_instances_index is None:
            msg = "No sheet_instances section found in schematic"
            raise ValueError(msg)

        # Create template symbol instance
        full_lib_id = f"{library_name}:{symbol_name}"

        # Calculate y position based on existing templates
        template_count = self._count_existing_templates(sch_data)
        y_offset = -100 - (template_count * 10)

        template_instance = self._build_template_instance(
            full_lib_id, template_ref, symbol_name, y_offset
        )

        # Insert before sheet_instances
        sch_data.insert(sheet_instances_index, template_instance)

        # Write back
        with schematic_path.open("w", encoding="utf-8") as f:
            output = sexpdata.dumps(sch_data)
            f.write(output)

        logger.info("Created template instance: %s at y=%d", template_ref, y_offset)
        return template_ref

    def load_symbol_dynamically(
        self, schematic_path: Path, library_name: str, symbol_name: str
    ) -> str:
        """Complete workflow: inject symbol and create template instance.

        Args:
            schematic_path: Path to .kicad_sch file.
            library_name: Library name (e.g., "Device").
            symbol_name: Symbol name (e.g., "R").

        Returns:
            Template reference that can be used with kicad-skip clone().
        """
        logger.info("Loading symbol dynamically: %s:%s", library_name, symbol_name)

        # Step 1: Inject symbol definition into lib_symbols
        self.inject_symbol_into_schematic(schematic_path, library_name, symbol_name)

        # Step 2: Create template instance
        template_ref = self.create_template_instance(
            schematic_path, library_name, symbol_name
        )

        logger.info("Symbol loaded successfully. Template reference: %s", template_ref)
        return template_ref

    # --- Private helper methods ---

    @staticmethod
    def _is_symbol_element(item: Any) -> bool:
        """Check if an item is a symbol element in parsed data."""
        return (
            isinstance(item, list)
            and len(item) > 0
            and item[0] == Symbol("symbol")
        )

    @staticmethod
    def _find_lib_symbols_index(sch_data: Sequence[Any]) -> int | None:
        """Find the index of lib_symbols section in schematic data."""
        for i, item in enumerate(sch_data):
            if (
                isinstance(item, list)
                and len(item) > 0
                and item[0] == Symbol("lib_symbols")
            ):
                return i
        return None

    @staticmethod
    def _check_symbol_exists(
        lib_symbols_section: list[Any],
        full_symbol_name: str,
        symbol_name: str,
    ) -> bool:
        """Check if a symbol already exists in lib_symbols section."""
        for item in lib_symbols_section[1:]:  # Skip the 'lib_symbols' symbol
            if (
                isinstance(item, list)
                and len(item) > 1
                and item[0] == Symbol("symbol")
            ) and item[1] in (full_symbol_name, symbol_name):
                return True
        return False

    @staticmethod
    def _find_existing_template(sch_data: Sequence[Any], template_ref: str) -> bool:
        """Check if a template with given reference already exists."""
        for item in sch_data:
            if not (isinstance(item, list) and len(item) > 0 and item[0] == Symbol("symbol")):
                continue
            # Find Reference property
            for prop in item:
                if (
                    isinstance(prop, list)
                    and len(prop) > 2
                    and prop[0] == Symbol("property")
                    and prop[1] == "Reference"
                    and prop[2] == template_ref
                ):
                    return True
        return False

    @staticmethod
    def _find_sheet_instances_index(sch_data: Sequence[Any]) -> int | None:
        """Find the index of sheet_instances section in schematic data."""
        for i, item in enumerate(sch_data):
            if (
                isinstance(item, list)
                and len(item) > 0
                and item[0] == Symbol("sheet_instances")
            ):
                return i
        return None

    @staticmethod
    def _count_existing_templates(sch_data: Sequence[Any]) -> int:
        """Count existing template instances in schematic."""
        count = 0
        for item in sch_data:
            if not (isinstance(item, list) and len(item) > 0 and item[0] == Symbol("symbol")):
                continue
            for p in item:
                if (
                    isinstance(p, list)
                    and len(p) > 2
                    and p[0] == Symbol("property")
                    and p[1] == "Reference"
                    and str(p[2]).startswith("_TEMPLATE")
                ):
                    count += 1
                    break
        return count

    @staticmethod
    def _build_template_instance(
        lib_id: str,
        template_ref: str,
        symbol_name: str,
        y_offset: int,
    ) -> list[Any]:
        """Build a template symbol instance S-expression."""
        new_uuid = str(uuid.uuid4())

        return [
            Symbol("symbol"),
            [Symbol("lib_id"), lib_id],
            [Symbol("at"), -100, y_offset, 0],
            [Symbol("unit"), 1],
            [Symbol("in_bom"), Symbol("no")],
            [Symbol("on_board"), Symbol("no")],
            [Symbol("dnp"), Symbol("yes")],
            [Symbol("uuid"), new_uuid],
            [
                Symbol("property"),
                "Reference",
                template_ref,
                [Symbol("at"), -100, y_offset - 2.54, 0],
                [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]]],
            ],
            [
                Symbol("property"),
                "Value",
                symbol_name,
                [Symbol("at"), -100, y_offset + 2.54, 0],
                [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]]],
            ],
            [
                Symbol("property"),
                "Footprint",
                "",
                [Symbol("at"), -100, y_offset, 0],
                [
                    Symbol("effects"),
                    [Symbol("font"), [Symbol("size"), 1.27, 1.27]],
                    Symbol("hide"),
                ],
            ],
            [
                Symbol("property"),
                "Datasheet",
                "~",
                [Symbol("at"), -100, y_offset, 0],
                [
                    Symbol("effects"),
                    [Symbol("font"), [Symbol("size"), 1.27, 1.27]],
                    Symbol("hide"),
                ],
            ],
        ]


def _run_tests() -> None:
    """Run basic functionality tests for the dynamic symbol loader."""
    import sys

    logging.basicConfig(level=logging.INFO)

    loader = DynamicSymbolLoader()

    # Test 1: Find library directories
    lib_dirs = loader.find_kicad_symbol_libraries()
    logger.info("Found %d library directories", len(lib_dirs))

    # Test 2: Find Device library
    device_lib = loader.find_library_file("Device")
    if not device_lib:
        logger.error("Device library not found")
        sys.exit(1)

    logger.info("Device library found at: %s", device_lib)

    # Test 3: Parse library file
    parsed = loader.parse_library_file(device_lib)
    logger.info("Parsed library with %d top-level elements", len(parsed))

    # Test 4: Extract specific symbols
    for symbol in ["R", "C", "LED"]:
        symbol_def = loader.extract_symbol_definition(device_lib, symbol)
        if symbol_def:
            logger.info("Found symbol: %s", symbol)
        else:
            logger.warning("Symbol not found: %s", symbol)


if __name__ == "__main__":
    _run_tests()
