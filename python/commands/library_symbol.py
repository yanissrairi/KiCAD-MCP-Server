"""Library management for KiCAD symbols.

Handles parsing sym-lib-table files, discovering symbols,
and providing search functionality for component selection.
"""

from dataclasses import asdict, dataclass
import logging
import os
from pathlib import Path
import re
from typing import Any

logger = logging.getLogger("kicad_interface")

# Constants for symbol match scoring
_SCORE_EXACT_LCSC = 1000
_SCORE_EXACT_NAME = 500
_SCORE_EXACT_VALUE = 400
_SCORE_PARTIAL_NAME = 100
_SCORE_PARTIAL_VALUE = 80
_SCORE_MPN_MATCH = 70
_SCORE_DESCRIPTION_MATCH = 50
_SCORE_MANUFACTURER_MATCH = 30
_SCORE_CATEGORY_MATCH = 20

# Library testing/demo iteration limit
LIBRARY_TEST_LIMIT = 10


@dataclass
class SymbolInfo:
    """Information about a symbol in a library."""

    name: str  # Symbol name (without library prefix)
    library: str  # Library nickname
    full_ref: str  # "Library:SymbolName"
    value: str = ""  # Value property
    description: str = ""  # Description property
    footprint: str = ""  # Footprint reference if present
    lcsc_id: str = ""  # LCSC property if present
    manufacturer: str = ""  # Manufacturer property
    mpn: str = ""  # Part/MPN property
    category: str = ""  # Category property
    datasheet: str = ""  # Datasheet URL
    stock: str = ""  # Stock (from JLCPCB libs)
    price: str = ""  # Price (from JLCPCB libs)
    lib_class: str = ""  # Basic/Preferred/Extended


class SymbolLibraryManager:
    """Manages KiCAD symbol libraries.

    Parses sym-lib-table files (both global and project-specific),
    indexes available symbols, and provides search functionality.
    """

    def __init__(self, project_path: Path | None = None) -> None:
        """Initialize symbol library manager.

        Args:
            project_path: Optional path to project directory for project-specific libraries
        """
        self.project_path = project_path
        self.libraries: dict[str, str] = {}  # nickname -> path mapping
        self.symbol_cache: dict[str, list[SymbolInfo]] = {}  # library -> [SymbolInfo]
        self._load_libraries()

    def _load_libraries(self) -> None:
        """Load libraries from sym-lib-table files."""
        # Load global libraries
        global_table = self._get_global_sym_lib_table()
        if global_table and global_table.exists():
            logger.info("Loading global sym-lib-table from: %s", global_table)
            self._parse_sym_lib_table(global_table)
        else:
            logger.warning("Global sym-lib-table not found at: %s", global_table)

        # Load project-specific libraries if project path provided
        if self.project_path:
            project_table = self.project_path / "sym-lib-table"
            if project_table.exists():
                logger.info("Loading project sym-lib-table from: %s", project_table)
                self._parse_sym_lib_table(project_table)

        logger.info("Loaded %d symbol libraries", len(self.libraries))

    def _get_global_sym_lib_table(self) -> Path | None:
        """Get path to global sym-lib-table file."""
        # Try different possible locations (same as fp-lib-table but for symbols)
        kicad_config_paths = [
            Path.home() / ".config" / "kicad" / "9.0" / "sym-lib-table",
            Path.home() / ".config" / "kicad" / "8.0" / "sym-lib-table",
            Path.home() / ".config" / "kicad" / "sym-lib-table",
            # Windows paths
            Path.home() / "AppData" / "Roaming" / "kicad" / "9.0" / "sym-lib-table",
            Path.home() / "AppData" / "Roaming" / "kicad" / "8.0" / "sym-lib-table",
            # macOS paths
            Path.home() / "Library" / "Preferences" / "kicad" / "9.0" / "sym-lib-table",
            Path.home() / "Library" / "Preferences" / "kicad" / "8.0" / "sym-lib-table",
        ]

        for path in kicad_config_paths:
            if path.exists():
                return path

        return None

    def _parse_sym_lib_table(self, table_path: Path) -> None:
        """Parse sym-lib-table file.

        Format is S-expression (Lisp-like):
        (sym_lib_table
          (lib (name "Library_Name")(type KiCad)(uri "${KICAD9_SYMBOL_DIR}/Library.kicad_sym")(options "")(descr "Description"))
        )
        """
        try:
            with Path(table_path).open(encoding="utf-8") as f:
                content = f.read()

            # Simple regex-based parser for lib entries
            # Pattern: (lib (name "NAME")(type TYPE)(uri "URI")...)
            lib_pattern = (
                r'\(lib\s+\(name\s+"?([^")\s]+)"?\)\s*\(type\s+[^)]+\)\s*\(uri\s+"?([^")\s]+)"?'
            )

            for match in re.finditer(lib_pattern, content, re.IGNORECASE):
                nickname = match.group(1)
                uri = match.group(2)

                # Resolve environment variables in URI
                resolved_uri = self._resolve_uri(uri)

                if resolved_uri:
                    self.libraries[nickname] = resolved_uri
                    logger.debug("  Found library: %s -> %s", nickname, resolved_uri)
                else:
                    logger.debug("  Could not resolve URI for library %s: %s", nickname, uri)

        except Exception:
            logger.exception("Error parsing sym-lib-table at %s", table_path)

    def _resolve_uri(self, uri: str) -> str | None:
        """Resolve environment variables and paths in library URI.

        Handles:
        - ${KICAD9_SYMBOL_DIR} -> /Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols
        - ${KICAD9_3RD_PARTY} -> ~/Documents/KiCad/9.0/3rdparty
        - ${KIPRJMOD} -> project directory
        - Relative paths
        - Absolute paths
        """
        resolved = uri

        # Common KiCAD environment variables
        env_vars = {
            "KICAD9_SYMBOL_DIR": self._find_kicad_symbol_dir(),
            "KICAD8_SYMBOL_DIR": self._find_kicad_symbol_dir(),
            "KICAD_SYMBOL_DIR": self._find_kicad_symbol_dir(),
            "KICAD9_3RD_PARTY": self._find_3rd_party_dir(),
            "KICAD8_3RD_PARTY": self._find_3rd_party_dir(),
            "KISYSSYM": self._find_kicad_symbol_dir(),
        }

        # Project directory
        if self.project_path:
            env_vars["KIPRJMOD"] = str(self.project_path)

        # Replace environment variables
        for var, value in env_vars.items():
            if value:
                resolved = resolved.replace(f"${{{var}}}", value)
                resolved = resolved.replace(f"${var}", value)

        # Expand ~ to home directory
        resolved = Path(resolved).expanduser()

        # Convert to absolute path
        path = Path(resolved)

        # Check if path exists
        if path.exists():
            return str(path)
        logger.debug("    Path does not exist: %s", path)
        return None

    def _find_kicad_symbol_dir(self) -> str | None:
        """Find KiCAD symbol directory."""
        possible_paths = [
            "/usr/share/kicad/symbols",
            "/usr/local/share/kicad/symbols",
            "C:/Program Files/KiCad/9.0/share/kicad/symbols",
            "C:/Program Files/KiCad/8.0/share/kicad/symbols",
            "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
        ]

        # Check environment variable
        if "KICAD9_SYMBOL_DIR" in os.environ:
            possible_paths.insert(0, os.environ["KICAD9_SYMBOL_DIR"])
        if "KICAD8_SYMBOL_DIR" in os.environ:
            possible_paths.insert(0, os.environ["KICAD8_SYMBOL_DIR"])

        for path in possible_paths:
            if Path(path).is_dir():
                return path

        return None

    def _find_3rd_party_dir(self) -> str | None:
        """Find KiCAD 3rd party library directory (PCM installed libs)."""
        possible_paths = [
            str(Path.home() / "Documents" / "KiCad" / "9.0" / "3rdparty"),
            str(Path.home() / "Documents" / "KiCad" / "8.0" / "3rdparty"),
        ]

        # Check environment variable
        if "KICAD9_3RD_PARTY" in os.environ:
            possible_paths.insert(0, os.environ["KICAD9_3RD_PARTY"])
        if "KICAD8_3RD_PARTY" in os.environ:
            possible_paths.insert(0, os.environ["KICAD8_3RD_PARTY"])

        for path in possible_paths:
            if Path(path).is_dir():
                return path

        return None

    def _parse_kicad_sym_file(self, library_path: str, library_name: str) -> list[SymbolInfo]:
        """Parse a .kicad_sym file to extract symbol metadata.

        Args:
            library_path: Path to the .kicad_sym file
            library_name: Nickname of the library

        Returns:
            List of SymbolInfo objects
        """
        symbols = []

        try:
            with Path(library_path).open(encoding="utf-8") as f:
                content = f.read()

            # Find all top-level symbol definitions
            # Pattern: (symbol "SYMBOL_NAME" ... ) at the top level
            # We need to find symbols that are direct children of kicad_symbol_lib
            # and not sub-symbols (which have names like "PARENT_0_1")

            # Simple approach: find all (symbol "NAME" and filter out sub-symbols
            symbol_pattern = r'\(symbol\s+"([^"]+)"'

            for match in re.finditer(symbol_pattern, content):
                symbol_name = match.group(1)

                # Skip sub-symbols (they contain _0_, _1_, etc. suffixes)
                if re.search(r"_\d+_\d+$", symbol_name):
                    continue

                # Find the start position of this symbol
                start_pos = match.start()

                # Extract properties from this symbol block
                # We need to find the matching closing paren - use a simple heuristic
                # Look for the next 2000 characters for properties
                block_end = min(start_pos + 5000, len(content))
                symbol_block = content[start_pos:block_end]

                # Extract properties
                properties = self._extract_properties(symbol_block)

                symbol_info = SymbolInfo(
                    name=symbol_name,
                    library=library_name,
                    full_ref=f"{library_name}:{symbol_name}",
                    value=properties.get("Value", ""),
                    description=properties.get("Description", ""),
                    footprint=properties.get("Footprint", ""),
                    lcsc_id=properties.get("LCSC", ""),
                    manufacturer=properties.get("Manufacturer", ""),
                    mpn=properties.get("Part", properties.get("MPN", "")),
                    category=properties.get("Category", ""),
                    datasheet=properties.get("Datasheet", ""),
                    stock=properties.get("Stock", ""),
                    price=properties.get("Price", ""),
                    lib_class=properties.get("Class", ""),
                )

                symbols.append(symbol_info)

            logger.debug("Parsed %d symbols from %s", len(symbols), library_name)

        except Exception:
            logger.exception("Error parsing symbol library %s", library_path)

        return symbols

    def _extract_properties(self, symbol_block: str) -> dict[str, str]:
        """Extract properties from a symbol block."""
        properties = {}

        # Pattern for properties: (property "KEY" "VALUE" ...)
        prop_pattern = r'\(property\s+"([^"]+)"\s+"([^"]*)"'

        for match in re.finditer(prop_pattern, symbol_block):
            key = match.group(1)
            value = match.group(2)
            properties[key] = value

        return properties

    def list_libraries(self) -> list[str]:
        """Get list of available library nicknames."""
        return list(self.libraries.keys())

    def get_library_path(self, nickname: str) -> str | None:
        """Get filesystem path for a library nickname."""
        return self.libraries.get(nickname)

    def list_symbols(self, library_nickname: str) -> list[SymbolInfo]:
        """List all symbols in a library.

        Args:
            library_nickname: Library name (e.g., "Device")

        Returns:
            List of SymbolInfo objects
        """
        # Check cache first
        if library_nickname in self.symbol_cache:
            return self.symbol_cache[library_nickname]

        library_path = self.libraries.get(library_nickname)
        if not library_path:
            logger.warning("Library not found: %s", library_nickname)
            return []

        # Parse the library file
        symbols = self._parse_kicad_sym_file(library_path, library_nickname)

        # Cache the results
        self.symbol_cache[library_nickname] = symbols

        return symbols

    def search_symbols(
        self, query: str, limit: int = 20, library_filter: str | None = None
    ) -> list[SymbolInfo]:
        """Search for symbols matching a query.

        Args:
            query: Search query (matches name, LCSC ID, description, category, manufacturer)
            limit: Maximum number of results to return
            library_filter: Optional library name pattern to filter by

        Returns:
            List of SymbolInfo objects sorted by relevance
        """
        results = []
        query_lower = query.lower()

        # Determine which libraries to search
        libraries_to_search = self.libraries.keys()
        if library_filter:
            filter_lower = library_filter.lower()
            libraries_to_search = [
                lib for lib in libraries_to_search if filter_lower in lib.lower()
            ]

        for library_nickname in libraries_to_search:
            symbols = self.list_symbols(library_nickname)

            for symbol in symbols:
                score = self._score_match(query_lower, symbol)
                if score > 0:
                    results.append((score, symbol))

                    if len(results) >= limit * 3:  # Get extra for sorting
                        break

            if len(results) >= limit * 3:
                break

        # Sort by score (descending) and return top results
        results.sort(key=lambda x: x[0], reverse=True)
        return [symbol for _, symbol in results[:limit]]

    def _score_match(self, query: str, symbol: SymbolInfo) -> int:
        """Score how well a symbol matches a query.

        Args:
            query: Search query string (lowercase).
            symbol: SymbolInfo to score against.

        Returns:
            Score (0 = no match, higher = better match).
        """
        score = 0

        # Exact LCSC ID match - highest priority
        if symbol.lcsc_id and symbol.lcsc_id.lower() == query:
            score += _SCORE_EXACT_LCSC

        # Exact name match
        if symbol.name.lower() == query:
            score += _SCORE_EXACT_NAME

        # Exact value match
        if symbol.value.lower() == query:
            score += _SCORE_EXACT_VALUE

        # Partial name match
        if query in symbol.name.lower():
            score += _SCORE_PARTIAL_NAME

        # Partial value match
        if query in symbol.value.lower():
            score += _SCORE_PARTIAL_VALUE

        # Description match
        if query in symbol.description.lower():
            score += _SCORE_DESCRIPTION_MATCH

        # MPN match
        if symbol.mpn and query in symbol.mpn.lower():
            score += _SCORE_MPN_MATCH

        # Manufacturer match
        if symbol.manufacturer and query in symbol.manufacturer.lower():
            score += _SCORE_MANUFACTURER_MATCH

        # Category match
        if symbol.category and query in symbol.category.lower():
            score += _SCORE_CATEGORY_MATCH

        return score

    def get_symbol_info(self, library_nickname: str, symbol_name: str) -> SymbolInfo | None:
        """Get information about a specific symbol.

        Args:
            library_nickname: Library name
            symbol_name: Symbol name

        Returns:
            SymbolInfo or None if not found
        """
        symbols = self.list_symbols(library_nickname)

        for symbol in symbols:
            if symbol.name == symbol_name:
                return symbol

        return None

    def find_symbol(self, symbol_spec: str) -> SymbolInfo | None:
        """Find a symbol by specification.

        Supports multiple formats:
        - "Library:Symbol" (e.g., "Device:R")
        - "Symbol" (searches all libraries)

        Args:
            symbol_spec: Symbol specification

        Returns:
            SymbolInfo or None if not found
        """
        if ":" in symbol_spec:
            # Format: Library:Symbol
            library_nickname, symbol_name = symbol_spec.split(":", 1)
            return self.get_symbol_info(library_nickname, symbol_name)
        # Search all libraries
        for library_nickname in self.libraries:
            result = self.get_symbol_info(library_nickname, symbol_spec)
            if result:
                return result

        return None


class SymbolLibraryCommands:
    """Command handlers for symbol library operations."""

    def __init__(self, library_manager: SymbolLibraryManager | None = None) -> None:
        """Initialize with optional library manager.

        Args:
            library_manager: Optional library manager instance.
        """
        self.library_manager = library_manager or SymbolLibraryManager()

    def list_symbol_libraries(self, params: dict[str, Any]) -> dict[str, Any]:
        """List all available symbol libraries.

        Args:
            params: Command parameters (unused).

        Returns:
            Dictionary with success status and list of libraries.
        """
        _ = params  # Unused but required by command interface
        try:
            libraries = self.library_manager.list_libraries()
            return {"success": True, "libraries": libraries, "count": len(libraries)}
        except Exception as e:
            logger.exception("Error listing symbol libraries")
            return {
                "success": False,
                "message": "Failed to list symbol libraries",
                "errorDetails": str(e),
            }

    def search_symbols(self, params: dict[str, Any]) -> dict[str, Any]:
        """Search for symbols by query.

        Args:
            params: Command parameters with query, limit, and optional library filter.

        Returns:
            Dictionary with success status and matching symbols.
        """
        try:
            query = params.get("query", "")
            if not query:
                return {"success": False, "message": "Missing query parameter"}

            limit = params.get("limit", 20)
            library_filter = params.get("library")

            results = self.library_manager.search_symbols(query, limit, library_filter)

            return {
                "success": True,
                "symbols": [asdict(s) for s in results],
                "count": len(results),
                "query": query,
            }
        except Exception as e:
            logger.exception("Error searching symbols")
            return {
                "success": False,
                "message": "Failed to search symbols",
                "errorDetails": str(e),
            }

    def list_library_symbols(self, params: dict[str, Any]) -> dict[str, Any]:
        """List all symbols in a specific library.

        Args:
            params: Command parameters with library name.

        Returns:
            Dictionary with success status and list of symbols.
        """
        try:
            library = params.get("library")
            if not library:
                return {"success": False, "message": "Missing library parameter"}

            # Check if library exists in sym-lib-table
            if library not in self.library_manager.libraries:
                available_libs = list(self.library_manager.libraries.keys())
                return {
                    "success": False,
                    "message": f"Library '{library}' not found in sym-lib-table",
                    "errorDetails": (
                        f"Library '{library}' is not registered in your KiCad "
                        f"symbol library table. Found {len(available_libs)} libraries. "
                        "Please add this library to your sym-lib-table file, "
                        "or use one of the available libraries."
                    ),
                    "available_libraries_count": len(available_libs),
                    "suggestion": "Use 'list_symbol_libraries' to see all available libraries",
                }

            symbols = self.library_manager.list_symbols(library)

            return {
                "success": True,
                "library": library,
                "symbols": [asdict(s) for s in symbols],
                "count": len(symbols),
            }
        except Exception as e:
            logger.exception("Error listing library symbols")
            return {
                "success": False,
                "message": "Failed to list library symbols",
                "errorDetails": str(e),
            }

    def get_symbol_info(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get information about a specific symbol.

        Args:
            params: Command parameters with symbol specification.

        Returns:
            Dictionary with success status and symbol information.
        """
        try:
            symbol_spec = params.get("symbol")
            if not symbol_spec:
                return {"success": False, "message": "Missing symbol parameter"}

            result = self.library_manager.find_symbol(symbol_spec)

            if result:
                return {"success": True, "symbol_info": asdict(result)}
            return {"success": False, "message": f"Symbol not found: {symbol_spec}"}

        except Exception as e:
            logger.exception("Error getting symbol info")
            return {
                "success": False,
                "message": "Failed to get symbol info",
                "errorDetails": str(e),
            }


if __name__ == "__main__":
    # Test the symbol library manager

    logging.basicConfig(level=logging.INFO)

    manager = SymbolLibraryManager()

    for _name in list(manager.libraries.keys())[:LIBRARY_TEST_LIMIT]:
        pass
    if len(manager.libraries) > LIBRARY_TEST_LIMIT:
        pass

    # Test search
    if manager.libraries:
        results = manager.search_symbols("ESP32", limit=5)
        for symbol in results:
            if symbol.lcsc_id:
                pass
