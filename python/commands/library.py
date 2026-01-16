"""Library management for KiCAD footprints.

Handles parsing fp-lib-table files, discovering footprints,
and providing search functionality for component placement.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
from typing import Any

logger = logging.getLogger("kicad_interface")


class LibraryManager:
    """Manages KiCAD footprint libraries.

    Parses fp-lib-table files (both global and project-specific),
    indexes available footprints, and provides search functionality.
    """

    def __init__(self, project_path: Path | None = None) -> None:
        """Initialize library manager.

        Args:
            project_path: Optional path to project directory for project-specific libraries
        """
        self.project_path = project_path
        self.libraries: dict[str, str] = {}  # nickname -> path mapping
        self.footprint_cache: dict[str, list[str]] = {}  # library -> [footprint names]
        self._load_libraries()

    def _load_libraries(self) -> None:
        """Load libraries from fp-lib-table files."""
        # Load global libraries
        global_table = self._get_global_fp_lib_table()
        if global_table and global_table.exists():
            logger.info("Loading global fp-lib-table from: %s", global_table)
            self._parse_fp_lib_table(global_table)
        else:
            logger.warning("Global fp-lib-table not found at: %s", global_table)

        # Load project-specific libraries if project path provided
        if self.project_path:
            project_table = self.project_path / "fp-lib-table"
            if project_table.exists():
                logger.info("Loading project fp-lib-table from: %s", project_table)
                self._parse_fp_lib_table(project_table)

        logger.info("Loaded %d footprint libraries", len(self.libraries))

    def _get_global_fp_lib_table(self) -> Path | None:
        """Get path to global fp-lib-table file."""
        # Try different possible locations
        kicad_config_paths = [
            Path.home() / ".config" / "kicad" / "9.0" / "fp-lib-table",
            Path.home() / ".config" / "kicad" / "8.0" / "fp-lib-table",
            Path.home() / ".config" / "kicad" / "fp-lib-table",
            # Windows paths
            Path.home() / "AppData" / "Roaming" / "kicad" / "9.0" / "fp-lib-table",
            Path.home() / "AppData" / "Roaming" / "kicad" / "8.0" / "fp-lib-table",
            # macOS paths
            Path.home() / "Library" / "Preferences" / "kicad" / "9.0" / "fp-lib-table",
            Path.home() / "Library" / "Preferences" / "kicad" / "8.0" / "fp-lib-table",
        ]

        for path in kicad_config_paths:
            if path.exists():
                return path

        return None

    def _parse_fp_lib_table(self, table_path: Path) -> None:
        """Parse fp-lib-table file.

        Format is S-expression (Lisp-like):
        (fp_lib_table
          (lib (name "Library_Name")(type KiCad)(uri "${KICAD9_FOOTPRINT_DIR}/Library.pretty")
               (options "")(descr "Description"))
        )

        Args:
            table_path: Path to the fp-lib-table file.
        """
        try:
            content = table_path.read_text()

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
                    logger.warning("  Could not resolve URI for library %s: %s", nickname, uri)

        except OSError:
            logger.exception("Error parsing fp-lib-table at %s", table_path)

    def _resolve_uri(self, uri: str) -> str | None:
        """Resolve environment variables and paths in library URI.

        Handles:
        - ${KICAD9_FOOTPRINT_DIR} -> /usr/share/kicad/footprints
        - ${KICAD8_FOOTPRINT_DIR} -> /usr/share/kicad/footprints
        - ${KIPRJMOD} -> project directory
        - Relative paths
        - Absolute paths

        Args:
            uri: The URI string to resolve.

        Returns:
            Resolved path string or None if path doesn't exist.
        """
        # Replace environment variables
        resolved = uri

        # Common KiCAD environment variables
        env_vars = {
            "KICAD9_FOOTPRINT_DIR": self._find_kicad_footprint_dir(),
            "KICAD8_FOOTPRINT_DIR": self._find_kicad_footprint_dir(),
            "KICAD_FOOTPRINT_DIR": self._find_kicad_footprint_dir(),
            "KISYSMOD": self._find_kicad_footprint_dir(),
            "KICAD9_3RD_PARTY": self._find_kicad_3rdparty_dir(),
            "KICAD8_3RD_PARTY": self._find_kicad_3rdparty_dir(),
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
        resolved = os.path.expanduser(resolved)  # noqa: PTH111

        # Convert to absolute path
        path = Path(resolved)

        # Check if path exists
        if path.exists():
            return str(path)
        logger.debug("    Path does not exist: %s", path)
        return None

    def _find_kicad_footprint_dir(self) -> str | None:
        """Find KiCAD footprint directory.

        Returns:
            Path to the footprint directory or None if not found.
        """
        # Try common locations
        possible_paths = [
            Path("/usr/share/kicad/footprints"),
            Path("/usr/local/share/kicad/footprints"),
            Path("C:/Program Files/KiCad/9.0/share/kicad/footprints"),
            Path("C:/Program Files/KiCad/8.0/share/kicad/footprints"),
            Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"),
        ]

        # Also check environment variable
        if "KICAD9_FOOTPRINT_DIR" in os.environ:
            possible_paths.insert(0, Path(os.environ["KICAD9_FOOTPRINT_DIR"]))
        if "KICAD8_FOOTPRINT_DIR" in os.environ:
            possible_paths.insert(0, Path(os.environ["KICAD8_FOOTPRINT_DIR"]))

        for path in possible_paths:
            if path.is_dir():
                return str(path)

        return None

    def _find_kicad_3rdparty_dir(self) -> str | None:
        """Find KiCAD 3rd party libraries directory.

        Resolution order:
        1. Shell environment variable KICAD9_3RD_PARTY
        2. User settings in kicad_common.json
        3. Platform-specific defaults based on detected KiCad version

        Returns:
            Path to the 3rd party directory or None if not found.
        """
        # 1. Check shell environment variable first
        if "KICAD9_3RD_PARTY" in os.environ:
            env_path = Path(os.environ["KICAD9_3RD_PARTY"])
            if env_path.is_dir():
                return str(env_path)

        # 2. Check kicad_common.json for user-defined variables
        kicad_common_paths = [
            Path.home()
            / "Library"
            / "Preferences"
            / "kicad"
            / "9.0"
            / "kicad_common.json",  # macOS
            Path.home() / ".config" / "kicad" / "9.0" / "kicad_common.json",  # Linux
            Path.home()
            / "AppData"
            / "Roaming"
            / "kicad"
            / "9.0"
            / "kicad_common.json",  # Windows
        ]

        version = "9.0"  # Default version
        for config_path in kicad_common_paths:
            if config_path.exists():
                config_version = self._try_load_3rdparty_from_config(config_path)
                if config_version:
                    return config_version

                # Derive version from config path location
                version = config_path.parent.name  # e.g., "9.0"
                break

        # 3. Use platform-specific defaults
        return self._find_3rdparty_default_path(version)

    def _try_load_3rdparty_from_config(self, config_path: Path) -> str | None:
        """Try to load KICAD9_3RD_PARTY from kicad_common.json config.

        Args:
            config_path: Path to the kicad_common.json file.

        Returns:
            Path to the 3rd party directory or None if not found.
        """
        try:
            config = json.loads(config_path.read_text())
            env_vars = config.get("environment", {}).get("vars", {})
            if env_vars and "KICAD9_3RD_PARTY" in env_vars:
                config_3rd_party = Path(env_vars["KICAD9_3RD_PARTY"])
                if config_3rd_party.is_dir():
                    return str(config_3rd_party)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            # Expected: config file may not exist, be invalid, or missing keys
            logger.debug("Could not load KICAD9_3RD_PARTY from config: %s", e)
        return None

    def _find_3rdparty_default_path(self, version: str) -> str | None:
        """Find 3rd party directory using platform-specific defaults.

        Args:
            version: KiCad version string (e.g., "9.0").

        Returns:
            Path to the 3rd party directory or None if not found.
        """
        possible_paths = [
            # macOS - Documents/KiCad/{version}/3rdparty
            Path.home() / "Documents" / "KiCad" / version / "3rdparty",
            # Linux - ~/.local/share/kicad/{version}/3rdparty
            Path.home() / ".local" / "share" / "kicad" / version / "3rdparty",
            # Windows - Documents/KiCad/{version}/3rdparty
            Path.home() / "Documents" / "KiCad" / version / "3rdparty",
        ]

        for path in possible_paths:
            if path.exists():
                logger.info("Found KiCad 3rd party directory: %s", path)
                return str(path)

        logger.warning("Could not find KiCad 3rd party directory")
        return None

    def list_libraries(self) -> list[str]:
        """Get list of available library nicknames.

        Returns:
            List of library nickname strings.
        """
        return list(self.libraries.keys())

    def get_library_path(self, nickname: str) -> str | None:
        """Get filesystem path for a library nickname.

        Args:
            nickname: The library nickname.

        Returns:
            The filesystem path or None if not found.
        """
        return self.libraries.get(nickname)

    def list_footprints(self, library_nickname: str) -> list[str]:
        """List all footprints in a library.

        Args:
            library_nickname: Library name (e.g., "Resistor_SMD")

        Returns:
            List of footprint names (without .kicad_mod extension)
        """
        # Check cache first
        if library_nickname in self.footprint_cache:
            return self.footprint_cache[library_nickname]

        library_path = self.libraries.get(library_nickname)
        if not library_path:
            logger.warning("Library not found: %s", library_nickname)
            return []

        try:
            lib_dir = Path(library_path)

            # List all .kicad_mod files
            footprints = [fp_file.stem for fp_file in lib_dir.glob("*.kicad_mod")]

            # Cache the results
            self.footprint_cache[library_nickname] = footprints
            logger.debug("Found %d footprints in %s", len(footprints), library_nickname)

            return footprints

        except OSError:
            logger.exception("Error listing footprints in %s", library_nickname)
            return []

    def find_footprint(self, footprint_spec: str) -> tuple[str, str] | None:
        """Find a footprint by specification.

        Supports multiple formats:
        - "Library:Footprint" (e.g., "Resistor_SMD:R_0603_1608Metric")
        - "Footprint" (searches all libraries)

        Args:
            footprint_spec: Footprint specification

        Returns:
            Tuple of (library_path, footprint_name) or None if not found
        """
        if ":" in footprint_spec:
            return self._find_footprint_by_library(footprint_spec)
        return self._find_footprint_in_all_libraries(footprint_spec)

    def _find_footprint_by_library(self, footprint_spec: str) -> tuple[str, str] | None:
        """Find a footprint by library:footprint specification.

        Args:
            footprint_spec: Footprint specification in "Library:Footprint" format.

        Returns:
            Tuple of (library_path, footprint_name) or None if not found.
        """
        # Format: Library:Footprint
        library_nickname, footprint_name = footprint_spec.split(":", 1)
        library_path = self.libraries.get(library_nickname)

        if not library_path:
            logger.warning("Library not found: %s", library_nickname)
            return None

        # Check if footprint exists
        fp_file = Path(library_path) / f"{footprint_name}.kicad_mod"
        if fp_file.exists():
            return (library_path, footprint_name)
        logger.warning("Footprint not found: %s", footprint_spec)
        return None

    def _find_footprint_in_all_libraries(self, footprint_name: str) -> tuple[str, str] | None:
        """Find a footprint by searching all libraries.

        Args:
            footprint_name: Footprint name to search for.

        Returns:
            Tuple of (library_path, footprint_name) or None if not found.
        """
        # Search in all libraries
        for library_nickname, library_path in self.libraries.items():
            fp_file = Path(library_path) / f"{footprint_name}.kicad_mod"
            if fp_file.exists():
                logger.info("Found footprint %s in library %s", footprint_name, library_nickname)
                return (library_path, footprint_name)

        logger.warning("Footprint not found in any library: %s", footprint_name)
        return None

    def search_footprints(
        self, pattern: str, limit: int = 20
    ) -> list[dict[str, str]]:
        """Search for footprints matching a pattern.

        Args:
            pattern: Search pattern (supports wildcards *, case-insensitive)
            limit: Maximum number of results to return

        Returns:
            List of dicts with 'library', 'footprint', and 'full_name' keys
        """
        results: list[dict[str, str]] = []
        pattern_lower = pattern.lower()

        # Convert wildcards to regex
        regex_pattern = pattern_lower.replace("*", ".*")
        regex = re.compile(regex_pattern)

        for library_nickname in self.libraries:
            footprints = self.list_footprints(library_nickname)

            for footprint in footprints:
                if regex.search(footprint.lower()):
                    results.append(
                        {
                            "library": library_nickname,
                            "footprint": footprint,
                            "full_name": f"{library_nickname}:{footprint}",
                        }
                    )

                    if len(results) >= limit:
                        return results

        return results

    def get_footprint_info(
        self, library_nickname: str, footprint_name: str
    ) -> dict[str, str] | None:
        """Get information about a specific footprint.

        Args:
            library_nickname: Library name
            footprint_name: Footprint name

        Returns:
            Dict with footprint information or None if not found
        """
        library_path = self.libraries.get(library_nickname)
        if not library_path:
            return None

        fp_file = Path(library_path) / f"{footprint_name}.kicad_mod"
        if not fp_file.exists():
            return None

        return {
            "library": library_nickname,
            "footprint": footprint_name,
            "full_name": f"{library_nickname}:{footprint_name}",
            "path": str(fp_file),
            "library_path": library_path,
        }


class LibraryCommands:
    """Command handlers for library operations."""

    def __init__(self, library_manager: LibraryManager | None = None) -> None:
        """Initialize with optional library manager.

        Args:
            library_manager: Optional LibraryManager instance.
        """
        self.library_manager = library_manager or LibraryManager()

    def list_libraries(self, params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        """List all available footprint libraries.

        Args:
            params: Command parameters (unused).

        Returns:
            Dict with success status and library list.
        """
        try:
            libraries = self.library_manager.list_libraries()
            return {"success": True, "libraries": libraries, "count": len(libraries)}
        except OSError as e:
            logger.exception("Error listing libraries")
            return {
                "success": False,
                "message": "Failed to list libraries",
                "errorDetails": str(e),
            }

    def search_footprints(self, params: dict[str, Any]) -> dict[str, Any]:
        """Search for footprints by pattern.

        Args:
            params: Command parameters with optional 'pattern' and 'limit'.

        Returns:
            Dict with success status and search results.
        """
        try:
            pattern = str(params.get("pattern", "*"))
            limit = int(params.get("limit", 20))

            results = self.library_manager.search_footprints(pattern, limit)

            return {
                "success": True,
                "footprints": results,
                "count": len(results),
                "pattern": pattern,
            }
        except OSError as e:
            logger.exception("Error searching footprints")
            return {
                "success": False,
                "message": "Failed to search footprints",
                "errorDetails": str(e),
            }

    def list_library_footprints(self, params: dict[str, Any]) -> dict[str, Any]:
        """List all footprints in a specific library.

        Args:
            params: Command parameters with required 'library'.

        Returns:
            Dict with success status and footprint list.
        """
        try:
            library = params.get("library")
            if not library:
                return {"success": False, "message": "Missing library parameter"}

            footprints = self.library_manager.list_footprints(str(library))

            return {
                "success": True,
                "library": library,
                "footprints": footprints,
                "count": len(footprints),
            }
        except OSError as e:
            logger.exception("Error listing library footprints")
            return {
                "success": False,
                "message": "Failed to list library footprints",
                "errorDetails": str(e),
            }

    def get_footprint_info(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get information about a specific footprint.

        Args:
            params: Command parameters with required 'footprint'.

        Returns:
            Dict with success status and footprint info.
        """
        try:
            footprint_spec = params.get("footprint")
            if not footprint_spec:
                return {"success": False, "message": "Missing footprint parameter"}

            # Try to find the footprint
            result = self.library_manager.find_footprint(str(footprint_spec))

            if result:
                library_path, footprint_name = result
                # Extract library nickname from path
                library_nickname = self._find_library_nickname(library_path)

                info: dict[str, Any] = {
                    "library": library_nickname,
                    "footprint": footprint_name,
                    "full_name": f"{library_nickname}:{footprint_name}",
                    "library_path": library_path,
                }

                return {"success": True, "footprint_info": info}
            return {"success": False, "message": f"Footprint not found: {footprint_spec}"}

        except OSError as e:
            logger.exception("Error getting footprint info")
            return {
                "success": False,
                "message": "Failed to get footprint info",
                "errorDetails": str(e),
            }

    def _find_library_nickname(self, library_path: str) -> str | None:
        """Find library nickname from its path.

        Args:
            library_path: The filesystem path of the library.

        Returns:
            The library nickname or None if not found.
        """
        for nick, path in self.library_manager.libraries.items():
            if path == library_path:
                return nick
        return None
