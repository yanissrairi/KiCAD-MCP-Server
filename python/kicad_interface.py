"""KiCAD Python Interface Script for Model Context Protocol.

This script handles communication between the MCP TypeScript server
and KiCAD's Python API (pcbnew). It receives commands via stdin as
JSON and returns responses via stdout also as JSON.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import traceback
from typing import TYPE_CHECKING, Any, ClassVar

from resources.resource_definitions import RESOURCE_DEFINITIONS, handle_resource_read

# Import tool schemas and resource definitions
from schemas.tool_schemas import TOOL_SCHEMAS
from utils.kicad_process import KiCADProcessManager, check_and_launch_kicad
from utils.platform_helper import PlatformHelper

if TYPE_CHECKING:
    from commands.jlcsearch import JLCSearchClient as JLCSearchClientType

# Configure logging
log_dir = Path.home() / ".kicad-mcp" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / "kicad_interface.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(str(log_file)), logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("kicad_interface")

# Log Python environment details
logger.info("Python version: %s", sys.version)
logger.info("Python executable: %s", sys.executable)
logger.info("Platform: %s", sys.platform)
logger.info("Working directory: %s", Path.cwd())

# Constants for Windows paths
_PATH_LOG_TRUNCATE_LENGTH = 200


def _log_windows_diagnostics() -> None:
    """Log Windows-specific environment diagnostics."""
    logger.info("=== Windows Environment Diagnostics ===")
    logger.info("PYTHONPATH: %s", os.environ.get("PYTHONPATH", "NOT SET"))
    logger.info(
        "PATH: %s...", os.environ.get("PATH", "NOT SET")[:_PATH_LOG_TRUNCATE_LENGTH]
    )

    # Check for common KiCAD installations
    common_kicad_paths = [
        Path(r"C:\Program Files\KiCad"),
        Path(r"C:\Program Files (x86)\KiCad"),
    ]

    found_kicad = False
    for base_path in common_kicad_paths:
        if base_path.exists():
            logger.info("Found KiCAD installation at: %s", base_path)
            found_kicad = _check_kicad_versions(base_path) or found_kicad

    if not found_kicad:
        logger.warning("No KiCAD installations found in standard locations!")
        logger.warning(
            "Please ensure KiCAD 9.0+ is installed from "
            "https://www.kicad.org/download/windows/"
        )

    logger.info("========================================")


def _check_kicad_versions(base_path: Path) -> bool:
    """Check KiCAD versions in the given base path.

    Args:
        base_path: Path to KiCAD installation directory.

    Returns:
        True if valid Python path was found, False otherwise.
    """
    found_valid = False
    try:
        versions = [d.name for d in base_path.iterdir() if d.is_dir()]
        logger.info("  Versions found: %s", ", ".join(versions))
        for version in versions:
            python_path = base_path / version / "lib" / "python3" / "dist-packages"
            if python_path.exists():
                logger.info("  Python path exists: %s", python_path)
                found_valid = True
            else:
                logger.warning("  Python path missing: %s", python_path)
    except OSError as e:
        logger.warning("  Could not list versions: %s", e)
    return found_valid


# Windows-specific diagnostics
if sys.platform == "win32":
    _log_windows_diagnostics()

# Add utils directory to path for imports
utils_dir = str(Path(__file__).parent)
if utils_dir not in sys.path:
    sys.path.insert(0, utils_dir)

logger.info(
    "Detecting KiCAD Python paths for %s...", PlatformHelper.get_platform_name()
)
paths_added = PlatformHelper.add_kicad_to_python_path()

if paths_added:
    logger.info("Successfully added KiCAD Python paths to sys.path")
else:
    logger.warning(
        "No KiCAD Python paths found - attempting to import pcbnew from system path"
    )

logger.info("Current Python path: %s", sys.path)

# Check if auto-launch is enabled
AUTO_LAUNCH_KICAD = os.environ.get("KICAD_AUTO_LAUNCH", "false").lower() == "true"
if AUTO_LAUNCH_KICAD:
    logger.info("KiCAD auto-launch enabled")

# Check which backend to use
# KICAD_BACKEND can be: 'auto', 'ipc', or 'swig'
KICAD_BACKEND = os.environ.get("KICAD_BACKEND", "auto").lower()
logger.info("KiCAD backend preference: %s", KICAD_BACKEND)

# Try to use IPC backend first if available and preferred
USE_IPC_BACKEND = False
ipc_backend = None


def _try_ipc_backend() -> tuple[bool, Any]:
    """Try to initialize IPC backend.

    Returns:
        Tuple of (success, backend_instance).
    """
    try:
        logger.info("Checking IPC backend availability...")
        from kicad_api.ipc_backend import IPCBackend  # noqa: PLC0415

        # Try to connect to running KiCAD
        backend = IPCBackend()
        if backend.connect():
            logger.info("Using IPC backend - real-time UI sync enabled!")
            logger.info("  KiCAD version: %s", backend.get_version())
            return True, backend
        logger.info("IPC backend available but KiCAD not running with IPC enabled")
        return False, None
    except ImportError:
        logger.info("IPC backend not available (kicad-python not installed)")
        return False, None
    except (OSError, RuntimeError) as e:
        logger.info("IPC backend connection failed: %s", e)
        return False, None


def _get_platform_help_message() -> str:
    """Get platform-specific troubleshooting help message.

    Returns:
        Platform-specific help text.
    """
    if sys.platform == "win32":
        return """
Windows Troubleshooting:
1. Verify KiCAD is installed: C:\\Program Files\\KiCad\\9.0
2. Check PYTHONPATH environment variable points to:
   C:\\Program Files\\KiCad\\9.0\\lib\\python3\\dist-packages
3. Test with: "C:\\Program Files\\KiCad\\9.0\\bin\\python.exe" -c "import pcbnew"
4. Log file location: %USERPROFILE%\\.kicad-mcp\\logs\\kicad_interface.log
5. Run setup-windows.ps1 for automatic configuration
"""
    if sys.platform == "darwin":
        return """
macOS Troubleshooting:
1. Verify KiCAD is installed: /Applications/KiCad/KiCad.app
2. Check PYTHONPATH points to KiCAD's Python packages
3. Run: python3 -c "import pcbnew" to test
"""
    # Linux
    return """
Linux Troubleshooting:
1. Verify KiCAD is installed: apt list --installed | grep kicad
2. Check: /usr/lib/kicad/lib/python3/dist-packages exists
3. Test: python3 -c "import pcbnew"
"""


def _try_swig_backend() -> bool:
    """Try to initialize SWIG backend.

    Returns:
        True if successful, exits on failure.
    """
    try:
        logger.info("Attempting to import pcbnew module (SWIG backend)...")
        import pcbnew  # type: ignore[import-not-found]  # noqa: PLC0415

        logger.info("Successfully imported pcbnew module from: %s", pcbnew.__file__)
        logger.info("pcbnew version: %s", pcbnew.GetBuildVersion())
        logger.warning("Using SWIG backend - changes require manual reload in KiCAD UI")
    except ImportError as e:
        logger.exception("Failed to import pcbnew module")
        logger.exception("Current sys.path: %s", sys.path)

        help_message = _get_platform_help_message()
        logger.exception("%s", help_message)

        # Create error response for debugging (not sent, just for logging)
        _error_response = {
            "success": False,
            "message": "Failed to import pcbnew module - KiCAD Python API not found",
            "errorDetails": (
                f"Error: {e!s}\n\n{help_message}\n\n"
                f"Python sys.path:\n{chr(10).join(sys.path)}"
            ),
        }
        sys.exit(1)
    except (OSError, RuntimeError) as e:
        logger.exception("Unexpected error importing pcbnew")
        logger.exception("%s", traceback.format_exc())
        _error_response = {
            "success": False,
            "message": "Error importing pcbnew module",
            "errorDetails": str(e),
        }
        sys.exit(1)
    else:
        return True


if KICAD_BACKEND in ("auto", "ipc"):
    USE_IPC_BACKEND, ipc_backend = _try_ipc_backend()

# Fall back to SWIG backend if IPC not available
if not USE_IPC_BACKEND and KICAD_BACKEND != "ipc":
    _try_swig_backend()

# If IPC-only mode requested but not available, exit with error
elif KICAD_BACKEND == "ipc" and not USE_IPC_BACKEND:
    _error_response = {
        "success": False,
        "message": "IPC backend requested but not available",
        "errorDetails": (
            "KiCAD must be running with IPC API enabled. "
            "Enable at: Preferences > Plugins > Enable IPC API Server"
        ),
    }
    sys.exit(1)

# Import command handlers
try:
    logger.info("Importing command handlers...")
    from commands.board import BoardCommands
    from commands.component import ComponentCommands
    from commands.component_schematic import ComponentManager
    from commands.connection_schematic import ConnectionManager
    from commands.design_rules import DesignRuleCommands
    from commands.dynamic_symbol_loader import DynamicSymbolLoader
    from commands.export import ExportCommands
    from commands.jlcpcb import JLCPCBClient
    from commands.jlcpcb_parts import JLCPCBPartsManager
    from commands.jlcsearch import JLCSearchClient
    from commands.library import LibraryCommands, LibraryManager as FootprintLibraryManager
    from commands.library_schematic import (
        LibraryManager as SchematicLibraryManager,
    )
    from commands.library_symbol import SymbolLibraryCommands
    from commands.project import ProjectCommands
    from commands.routing import RoutingCommands
    from commands.schematic import SchematicManager
    from commands.schematic_info import get_schematic_info
    from commands.wire_manager import WireManager

    logger.info("Successfully imported all command handlers")
except ImportError as e:
    logger.exception("Failed to import command handlers")
    _error_response = {
        "success": False,
        "message": "Failed to import command handlers",
        "errorDetails": str(e),
    }
    sys.exit(1)


class KiCADInterface:
    """Main interface class to handle KiCAD operations.

    This class provides the bridge between the MCP protocol and KiCAD's
    Python API, routing commands to appropriate handlers.
    """

    # Commands that can be handled via IPC for real-time updates
    IPC_CAPABLE_COMMANDS: ClassVar[dict[str, str]] = {
        # Routing commands
        "route_trace": "_ipc_route_trace",
        "add_via": "_ipc_add_via",
        "add_net": "_ipc_add_net",
        "delete_trace": "_ipc_delete_trace",
        "get_nets_list": "_ipc_get_nets_list",
        # Zone commands
        "add_copper_pour": "_ipc_add_copper_pour",
        "refill_zones": "_ipc_refill_zones",
        # Board commands
        "add_text": "_ipc_add_text",
        "add_board_text": "_ipc_add_text",
        "set_board_size": "_ipc_set_board_size",
        "get_board_info": "_ipc_get_board_info",
        "add_board_outline": "_ipc_add_board_outline",
        "add_mounting_hole": "_ipc_add_mounting_hole",
        "get_layer_list": "_ipc_get_layer_list",
        # Component commands
        "place_component": "_ipc_place_component",
        "move_component": "_ipc_move_component",
        "rotate_component": "_ipc_rotate_component",
        "delete_component": "_ipc_delete_component",
        "get_component_list": "_ipc_get_component_list",
        "get_component_properties": "_ipc_get_component_properties",
        # Save command
        "save_project": "_ipc_save_project",
    }

    def __init__(self) -> None:
        """Initialize the interface and command handlers."""
        self.board: Any = None
        self.project_filename: str | None = None
        self.use_ipc = USE_IPC_BACKEND
        self.ipc_backend = ipc_backend
        self.ipc_board_api: Any = None

        if self.use_ipc:
            logger.info("Initializing with IPC backend (real-time UI sync enabled)")
            try:
                self.ipc_board_api = self.ipc_backend.get_board()
                logger.info("Got IPC board API")
            except (OSError, RuntimeError) as e:
                logger.warning("Could not get IPC board API: %s", e)
        else:
            logger.info("Initializing with SWIG backend")

        logger.info("Initializing command handlers...")

        # Initialize footprint library manager
        self.footprint_library = FootprintLibraryManager()

        # Initialize command handlers
        self.project_commands = ProjectCommands(self.board)
        self.board_commands = BoardCommands(self.board)
        self.component_commands = ComponentCommands(self.board, self.footprint_library)
        self.routing_commands = RoutingCommands(self.board)
        self.design_rule_commands = DesignRuleCommands(self.board)
        self.export_commands = ExportCommands(self.board)
        self.library_commands = LibraryCommands(self.footprint_library)

        # Initialize symbol library manager (for searching local KiCad symbol libraries)
        self.symbol_library_commands = SymbolLibraryCommands()

        # Initialize JLCPCB API integration
        self.jlcpcb_client = JLCPCBClient()  # Official API (requires auth)
        self.jlcsearch_client: JLCSearchClientType = JLCSearchClient()
        self.jlcpcb_parts = JLCPCBPartsManager()

        # Schematic-related classes don't need board reference
        # as they operate directly on schematic files

        # Command routing dictionary
        self.command_routes = self._build_command_routes()

        backend_name = "IPC" if self.use_ipc else "SWIG"
        logger.info("KiCAD interface initialized (backend: %s)", backend_name)

    def _build_command_routes(self) -> dict[str, Any]:
        """Build the command routing dictionary.

        Returns:
            Dictionary mapping command names to handler methods.
        """
        return {
            # Project commands
            "create_project": self.project_commands.create_project,
            "open_project": self.project_commands.open_project,
            "save_project": self.project_commands.save_project,
            "get_project_info": self.project_commands.get_project_info,
            # Board commands
            "set_board_size": self.board_commands.set_board_size,
            "add_layer": self.board_commands.add_layer,
            "set_active_layer": self.board_commands.set_active_layer,
            "get_board_info": self.board_commands.get_board_info,
            "get_layer_list": self.board_commands.get_layer_list,
            "get_board_2d_view": self.board_commands.get_board_2d_view,
            "add_board_outline": self.board_commands.add_board_outline,
            "add_mounting_hole": self.board_commands.add_mounting_hole,
            "add_text": self.board_commands.add_text,
            "add_board_text": self.board_commands.add_text,  # Alias for TypeScript tool
            # Component commands
            "place_component": self.component_commands.place_component,
            "move_component": self.component_commands.move_component,
            "rotate_component": self.component_commands.rotate_component,
            "delete_component": self.component_commands.delete_component,
            "edit_component": self.component_commands.edit_component,
            "get_component_properties": self.component_commands.get_component_properties,
            "get_component_list": self.component_commands.get_component_list,
            "place_component_array": self.component_commands.place_component_array,
            "align_components": self.component_commands.align_components,
            "duplicate_component": self.component_commands.duplicate_component,
            # Routing commands
            "add_net": self.routing_commands.add_net,
            "route_trace": self.routing_commands.route_trace,
            "add_via": self.routing_commands.add_via,
            "delete_trace": self.routing_commands.delete_trace,
            "get_nets_list": self.routing_commands.get_nets_list,
            "create_netclass": self.routing_commands.create_netclass,
            "add_copper_pour": self.routing_commands.add_copper_pour,
            "route_differential_pair": self.routing_commands.route_differential_pair,
            "refill_zones": self._handle_refill_zones,
            # Design rule commands
            "set_design_rules": self.design_rule_commands.set_design_rules,
            "get_design_rules": self.design_rule_commands.get_design_rules,
            "run_drc": self.design_rule_commands.run_drc,
            "get_drc_violations": self.design_rule_commands.get_drc_violations,
            # Export commands
            "export_gerber": self.export_commands.export_gerber,
            "export_pdf": self.export_commands.export_pdf,
            "export_svg": self.export_commands.export_svg,
            "export_3d": self.export_commands.export_3d,
            "export_bom": self.export_commands.export_bom,
            # Library commands (footprint management)
            "list_libraries": self.library_commands.list_libraries,
            "search_footprints": self.library_commands.search_footprints,
            "list_library_footprints": self.library_commands.list_library_footprints,
            "get_footprint_info": self.library_commands.get_footprint_info,
            # Symbol library commands (local KiCad symbol library search)
            "list_symbol_libraries": self.symbol_library_commands.list_symbol_libraries,
            "search_symbols": self.symbol_library_commands.search_symbols,
            "list_library_symbols": self.symbol_library_commands.list_library_symbols,
            "get_symbol_info": self.symbol_library_commands.get_symbol_info,
            # JLCPCB API commands (complete parts catalog via API)
            "download_jlcpcb_database": self._handle_download_jlcpcb_database,
            "search_jlcpcb_parts": self._handle_search_jlcpcb_parts,
            "get_jlcpcb_part": self._handle_get_jlcpcb_part,
            "get_jlcpcb_database_stats": self._handle_get_jlcpcb_database_stats,
            "suggest_jlcpcb_alternatives": self._handle_suggest_jlcpcb_alternatives,
            # Schematic commands
            "create_schematic": self._handle_create_schematic,
            "load_schematic": self._handle_load_schematic,
            "add_schematic_component": self._handle_add_schematic_component,
            "add_schematic_wire": self._handle_add_schematic_wire,
            "add_schematic_connection": self._handle_add_schematic_connection,
            "add_schematic_net_label": self._handle_add_schematic_net_label,
            "connect_to_net": self._handle_connect_to_net,
            "get_net_connections": self._handle_get_net_connections,
            "generate_netlist": self._handle_generate_netlist,
            "get_schematic_info": self._handle_get_schematic_info,
            "list_schematic_libraries": self._handle_list_schematic_libraries,
            "export_schematic_pdf": self._handle_export_schematic_pdf,
            # UI/Process management commands
            "check_kicad_ui": self._handle_check_kicad_ui,
            "launch_kicad_ui": self._handle_launch_kicad_ui,
            # IPC-specific commands (real-time operations)
            "get_backend_info": self._handle_get_backend_info,
            "ipc_add_track": self._handle_ipc_add_track,
            "ipc_add_via": self._handle_ipc_add_via,
            "ipc_add_text": self._handle_ipc_add_text,
            "ipc_list_components": self._handle_ipc_list_components,
            "ipc_get_tracks": self._handle_ipc_get_tracks,
            "ipc_get_vias": self._handle_ipc_get_vias,
            "ipc_save_board": self._handle_ipc_save_board,
        }

    def handle_command(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        """Route command to appropriate handler, preferring IPC when available.

        Args:
            command: The command name to execute.
            params: Parameters for the command.

        Returns:
            Dictionary containing the command result.
        """
        logger.info("Handling command: %s", command)
        logger.debug("Command parameters: %s", params)

        try:
            # Check if we can use IPC for this command (real-time UI sync)
            if self._should_use_ipc(command):
                result = self._execute_ipc_command(command, params)
                if result is not None:
                    return result

            # Fall back to SWIG-based handler
            if self.use_ipc and command in self.IPC_CAPABLE_COMMANDS:
                logger.warning(
                    "IPC handler not available for %s, falling back to SWIG (deprecated)",
                    command,
                )

            return self._execute_swig_command(command, params)

        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as e:
            traceback_str = traceback.format_exc()
            logger.exception("Error handling command %s", command)
            return {
                "success": False,
                "message": f"Error handling command: {command}",
                "errorDetails": f"{e!s}\n{traceback_str}",
            }

    def _should_use_ipc(self, command: str) -> bool:
        """Check if IPC should be used for the given command.

        Args:
            command: The command name.

        Returns:
            True if IPC should be used, False otherwise.
        """
        return (
            self.use_ipc
            and self.ipc_board_api is not None
            and command in self.IPC_CAPABLE_COMMANDS
        )

    def _execute_ipc_command(
        self, command: str, params: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Execute a command via IPC backend.

        Args:
            command: The command name.
            params: Command parameters.

        Returns:
            Command result dict or None if IPC handler not available.
        """
        ipc_handler_name = self.IPC_CAPABLE_COMMANDS[command]
        ipc_handler = getattr(self, ipc_handler_name, None)

        if ipc_handler is None:
            return None

        logger.info("Using IPC backend for %s (real-time sync)", command)
        result = ipc_handler(params)

        # Add indicator that IPC was used
        if isinstance(result, dict):
            result["_backend"] = "ipc"
            result["_realtime"] = True

        logger.debug("IPC command result: %s", result)
        return result

    def _execute_swig_command(
        self, command: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a command via SWIG backend.

        Args:
            command: The command name.
            params: Command parameters.

        Returns:
            Command result dictionary.
        """
        handler = self.command_routes.get(command)

        if handler is None:
            logger.error("Unknown command: %s", command)
            return {
                "success": False,
                "message": f"Unknown command: {command}",
                "errorDetails": "The specified command is not supported",
            }

        # Execute the command
        result = handler(params)
        logger.debug("Command result: %s", result)

        # Add backend indicator
        if isinstance(result, dict):
            result["_backend"] = "swig"
            result["_realtime"] = False

        # Update board reference if command was successful
        if result.get("success", False) and command in {"create_project", "open_project"}:
            logger.info("Updating board reference...")
            self.board = self.project_commands.board
            self._update_command_handlers()

        return result

    def _update_command_handlers(self) -> None:
        """Update board reference in all command handlers."""
        logger.debug("Updating board reference in command handlers")
        self.project_commands.board = self.board
        self.board_commands.board = self.board
        self.component_commands.board = self.board
        self.routing_commands.board = self.board
        self.design_rule_commands.board = self.board
        self.export_commands.board = self.board

    # Schematic command handlers
    def _handle_create_schematic(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a new schematic.

        Args:
            params: Parameters including name, path, and metadata.

        Returns:
            Result dictionary with success status and file path.
        """
        logger.info("Creating schematic")
        try:
            # Support multiple parameter naming conventions for compatibility
            project_name = (
                params.get("projectName")
                or params.get("name")
                or params.get("title")
            )

            # Handle filename parameter - it may contain full path
            filename = params.get("filename")
            if filename:
                filename = filename.removesuffix(".kicad_sch")
                filename_path = Path(filename)
                path = str(filename_path.parent) if filename_path.parent != Path() else "."
                project_name = project_name or filename_path.name
            else:
                path = params.get("path", ".")
            metadata = params.get("metadata", {})

            if not project_name:
                return {
                    "success": False,
                    "message": (
                        "Schematic name is required. "
                        "Provide 'name', 'projectName', or 'filename' parameter."
                    ),
                }

            schematic = SchematicManager.create_schematic(project_name, metadata)
            file_path = f"{path}/{project_name}.kicad_sch"
            success = SchematicManager.save_schematic(schematic, file_path)
        except (OSError, ValueError, KeyError) as e:
            logger.exception("Error creating schematic")
            return {"success": False, "message": str(e)}
        else:
            return {"success": success, "file_path": file_path}

    def _handle_load_schematic(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Load an existing schematic.

        Args:
            params: Parameters including filename.

        Returns:
            Result dictionary with success status and metadata.
        """
        logger.info("Loading schematic")
        try:
            filename = params.get("filename")

            if not filename:
                return {"success": False, "message": "Filename is required"}

            schematic = SchematicManager.load_schematic(filename)
            success = schematic is not None
        except (OSError, ValueError) as e:
            logger.exception("Error loading schematic")
            return {"success": False, "message": str(e)}
        else:
            if success:
                metadata = SchematicManager.get_schematic_metadata(schematic)
                return {"success": success, "metadata": metadata}
            return {"success": False, "message": "Failed to load schematic"}

    def _handle_add_schematic_component(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Add a component to a schematic with dynamic symbol loading support.

        Args:
            params: Parameters including schematicPath and component definition.

        Returns:
            Result dictionary with success status and component info.
        """
        logger.info("Adding component to schematic")
        try:
            schematic_path = params.get("schematicPath")
            component = params.get("component", {})

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}
            if not component:
                return {"success": False, "message": "Component definition is required"}

            schematic_path_obj = Path(schematic_path)
            schematic = SchematicManager.load_schematic(schematic_path)
            if not schematic:
                return {"success": False, "message": "Failed to load schematic"}

            comp_type = component.get("type", "R")
            library = component.get("library", "Device")
            needs_dynamic_loading, template_ref = self._check_dynamic_loading_needed(
                schematic, comp_type, library
            )

            if needs_dynamic_loading:
                template_ref, schematic = self._load_symbol_dynamically(
                    schematic_path_obj, schematic_path, library, comp_type
                )

            component_obj = ComponentManager.add_component(
                schematic, component, schematic_path_obj
            )
            success = component_obj is not None

            if success:
                SchematicManager.save_schematic(schematic, schematic_path)
                return self._build_component_response(
                    component, needs_dynamic_loading, library, comp_type, template_ref
                )
            return {"success": False, "message": "Failed to add component"}
        except (OSError, ValueError, KeyError, AttributeError) as e:
            logger.exception("Error adding component to schematic")
            return {
                "success": False,
                "message": str(e),
                "errorDetails": traceback.format_exc(),
            }

    def _check_dynamic_loading_needed(
        self, schematic: Any, comp_type: str, _library: str  # noqa: ANN401
    ) -> tuple[bool, str | None]:
        """Check if dynamic symbol loading is needed.

        Args:
            schematic: The schematic object.
            comp_type: Component type.
            _library: Library name (unused, kept for API compatibility).

        Returns:
            Tuple of (needs_loading, template_ref).
        """
        template_ref = ComponentManager.TEMPLATE_MAP.get(comp_type)

        if template_ref:
            if not hasattr(schematic.symbol, template_ref):
                logger.info(
                    "Static template %s not found in schematic, will try dynamic loading",
                    template_ref,
                )
                return True, template_ref
            return False, template_ref

        logger.info(
            "Component type %s not in static templates, will use dynamic loading",
            comp_type,
        )
        return True, None

    def _load_symbol_dynamically(
        self,
        schematic_path_obj: Path,
        schematic_path: str,
        library: str,
        comp_type: str,
    ) -> tuple[str | None, Any]:
        """Load a symbol dynamically into the schematic.

        Args:
            schematic_path_obj: Path object to schematic.
            schematic_path: String path to schematic.
            library: Library name.
            comp_type: Component type.

        Returns:
            Tuple of (template_ref, updated_schematic).
        """
        template_ref: str | None = None
        reload_error = None

        try:
            loader = DynamicSymbolLoader()

            # Save current schematic first
            schematic = SchematicManager.load_schematic(schematic_path)
            SchematicManager.save_schematic(schematic, schematic_path)
            logger.info("Saved schematic before dynamic loading")

            logger.info("Dynamically loading symbol: %s:%s", library, comp_type)
            template_ref = loader.load_symbol_dynamically(
                schematic_path_obj, library, comp_type
            )
            logger.info("Dynamic loading successful. Template ref: %s", template_ref)

            # Reload schematic
            schematic = SchematicManager.load_schematic(schematic_path)
            if not schematic:
                # Store error to raise outside try block
                msg = "Failed to reload schematic after dynamic loading"
                reload_error = ValueError(msg)
            else:
                logger.info("Reloaded schematic with new symbol definition")
                return template_ref, schematic
        except ImportError:
            logger.warning(
                "Dynamic symbol loader not available, falling back to static templates"
            )
        except (OSError, ValueError, KeyError):
            logger.exception("Dynamic loading failed")
            logger.warning("Falling back to static templates")
        finally:
            # Raise reload error outside try-except block
            if reload_error:
                raise reload_error

        schematic = SchematicManager.load_schematic(schematic_path)
        return template_ref, schematic

    def _build_component_response(
        self,
        component: dict[str, Any],
        needs_dynamic_loading: bool,  # noqa: FBT001
        library: str,
        comp_type: str,
        template_ref: str | None,
    ) -> dict[str, Any]:
        """Build the response for a successful component add.

        Args:
            component: Component definition.
            needs_dynamic_loading: Whether dynamic loading was used.
            library: Library name.
            comp_type: Component type.
            template_ref: Template reference if known.

        Returns:
            Response dictionary.
        """
        response: dict[str, Any] = {
            "success": True,
            "component_reference": component.get("reference", "unknown"),
            "dynamic_loading_used": needs_dynamic_loading,
        }

        if needs_dynamic_loading:
            response["symbol_source"] = f"{library}:{comp_type}"
            response["template_reference"] = template_ref or "unknown"

        return response

    def _handle_add_schematic_wire(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Add a wire to a schematic using WireManager.

        Args:
            params: Parameters including schematicPath, startPoint, endPoint.

        Returns:
            Result dictionary with success status.
        """
        logger.info("Adding wire to schematic")
        try:
            schematic_path = params.get("schematicPath")
            start_point = params.get("startPoint")
            end_point = params.get("endPoint")
            properties = params.get("properties", {})

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}
            if not start_point or not end_point:
                return {"success": False, "message": "Start and end points are required"}

            stroke_width = properties.get("stroke_width", 0)
            stroke_type = properties.get("stroke_type", "default")

            success = WireManager.add_wire(
                Path(schematic_path),
                start_point,
                end_point,
                stroke_width=stroke_width,
                stroke_type=stroke_type,
            )

            if success:
                return {"success": True, "message": "Wire added successfully"}
            return {"success": False, "message": "Failed to add wire"}
        except (OSError, ValueError, KeyError) as e:
            logger.exception("Error adding wire to schematic")
            return {
                "success": False,
                "message": str(e),
                "errorDetails": traceback.format_exc(),
            }

    def _handle_list_schematic_libraries(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """List available symbol libraries.

        Args:
            params: Parameters including optional searchPaths.

        Returns:
            Result dictionary with libraries list.
        """
        logger.info("Listing schematic libraries")
        try:
            search_paths = params.get("searchPaths")
            libraries = SchematicLibraryManager.list_available_libraries(search_paths)
            return {"success": True, "libraries": libraries}
        except (OSError, ValueError) as e:
            logger.exception("Error listing schematic libraries")
            return {"success": False, "message": str(e)}

    def _handle_export_schematic_pdf(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Export schematic to PDF.

        Args:
            params: Parameters including schematicPath and outputPath.

        Returns:
            Result dictionary with success status.
        """
        logger.info("Exporting schematic to PDF")
        try:
            schematic_path = params.get("schematicPath")
            output_path = params.get("outputPath")

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}
            if not output_path:
                return {"success": False, "message": "Output path is required"}

            result = subprocess.run(  # noqa: S603
                [  # noqa: S607
                    "kicad-cli",
                    "sch",
                    "export",
                    "pdf",
                    "--output",
                    output_path,
                    schematic_path,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            success = result.returncode == 0
            message = result.stderr if not success else ""

            return {"success": success, "message": message}
        except (OSError, subprocess.SubprocessError) as e:
            logger.exception("Error exporting schematic to PDF")
            return {"success": False, "message": str(e)}

    def _handle_add_schematic_connection(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Add a pin-to-pin connection in schematic.

        Args:
            params: Parameters for the connection.

        Returns:
            Result dictionary with success status.
        """
        logger.info("Adding pin-to-pin connection in schematic")
        try:
            schematic_path = params.get("schematicPath")
            source_ref = params.get("sourceRef")
            source_pin = params.get("sourcePin")
            target_ref = params.get("targetRef")
            target_pin = params.get("targetPin")
            routing = params.get("routing", "direct")

            if not all([schematic_path, source_ref, source_pin, target_ref, target_pin]):
                return {"success": False, "message": "Missing required parameters"}

            success = ConnectionManager.add_connection(
                Path(schematic_path),
                source_ref,
                source_pin,
                target_ref,
                target_pin,
                routing=routing,
            )

            if success:
                return {
                    "success": True,
                    "message": (
                        f"Connected {source_ref}/{source_pin} to "
                        f"{target_ref}/{target_pin} (routing: {routing})"
                    ),
                }
            return {"success": False, "message": "Failed to add connection"}
        except (OSError, ValueError, KeyError) as e:
            logger.exception("Error adding schematic connection")
            return {
                "success": False,
                "message": str(e),
                "errorDetails": traceback.format_exc(),
            }

    def _handle_add_schematic_net_label(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Add a net label to schematic using WireManager.

        Args:
            params: Parameters including schematicPath, netName, position.

        Returns:
            Result dictionary with success status.
        """
        logger.info("Adding net label to schematic")
        try:
            schematic_path = params.get("schematicPath")
            net_name = params.get("netName")
            position = params.get("position")
            label_type = params.get("labelType", "label")
            orientation = params.get("orientation", 0)

            if not all([schematic_path, net_name, position]):
                return {"success": False, "message": "Missing required parameters"}

            success = WireManager.add_label(
                Path(schematic_path),
                net_name,
                position,
                label_type=label_type,
                orientation=orientation,
            )

            if success:
                return {
                    "success": True,
                    "message": f"Added net label '{net_name}' at {position}",
                }
            return {"success": False, "message": "Failed to add net label"}
        except (OSError, ValueError, KeyError) as e:
            logger.exception("Error adding net label")
            return {
                "success": False,
                "message": str(e),
                "errorDetails": traceback.format_exc(),
            }

    def _handle_connect_to_net(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Connect a component pin to a named net using wire stub and label.

        Args:
            params: Parameters including schematicPath, componentRef, pinName, netName.

        Returns:
            Result dictionary with success status.
        """
        logger.info("Connecting component pin to net")
        try:
            schematic_path = params.get("schematicPath")
            component_ref = params.get("componentRef")
            pin_name = params.get("pinName")
            net_name = params.get("netName")

            if not all([schematic_path, component_ref, pin_name, net_name]):
                return {"success": False, "message": "Missing required parameters"}

            success = ConnectionManager.connect_to_net(
                Path(schematic_path), component_ref, pin_name, net_name
            )

            if success:
                return {
                    "success": True,
                    "message": f"Connected {component_ref}/{pin_name} to net '{net_name}'",
                }
            return {"success": False, "message": "Failed to connect to net"}
        except (OSError, ValueError, KeyError) as e:
            logger.exception("Error connecting to net")
            return {
                "success": False,
                "message": str(e),
                "errorDetails": traceback.format_exc(),
            }

    def _handle_get_net_connections(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Get all connections for a named net.

        Args:
            params: Parameters including schematicPath and netName.

        Returns:
            Result dictionary with connections list.
        """
        logger.info("Getting net connections")
        try:
            schematic_path = params.get("schematicPath")
            net_name = params.get("netName")

            if not all([schematic_path, net_name]):
                return {"success": False, "message": "Missing required parameters"}

            schematic = SchematicManager.load_schematic(schematic_path)
            if not schematic:
                return {"success": False, "message": "Failed to load schematic"}

            connections = ConnectionManager.get_net_connections(schematic, net_name)
            return {"success": True, "connections": connections}
        except (OSError, ValueError, KeyError) as e:
            logger.exception("Error getting net connections")
            return {"success": False, "message": str(e)}

    def _handle_generate_netlist(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Generate netlist from schematic.

        Args:
            params: Parameters including schematicPath.

        Returns:
            Result dictionary with netlist data.
        """
        logger.info("Generating netlist from schematic")
        try:
            schematic_path = params.get("schematicPath")

            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}

            schematic = SchematicManager.load_schematic(schematic_path)
            if not schematic:
                return {"success": False, "message": "Failed to load schematic"}

            netlist = ConnectionManager.generate_netlist(schematic)
            return {"success": True, "netlist": netlist}
        except (OSError, ValueError, KeyError) as e:
            logger.exception("Error generating netlist")
            return {"success": False, "message": str(e)}

    def _handle_get_schematic_info(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Get comprehensive schematic information for AI inspection.

        Args:
            params: Parameters for schematic info retrieval.

        Returns:
            Result dictionary with schematic info.
        """
        logger.info("Getting schematic info")
        try:
            schematic_path = params.get("schematicPath")
            if not schematic_path:
                return {"success": False, "message": "Schematic path is required"}

            return get_schematic_info(
                schematic_path=schematic_path,
                include_components=params.get("includeComponents", True),
                include_nets=params.get("includeNets", True),
                include_pin_details=params.get("includePinDetails", False),
                include_unconnected=params.get("includeUnconnected", False),
                component_filter=params.get("componentFilter"),
                exclude_templates=params.get("excludeTemplates", True),
            )
        except (OSError, ValueError, KeyError) as e:
            logger.exception("Error getting schematic info")
            return {"success": False, "message": str(e)}

    def _handle_check_kicad_ui(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """Check if KiCAD UI is running.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary with running status.
        """
        logger.info("Checking if KiCAD UI is running")
        try:
            manager = KiCADProcessManager()
            is_running = manager.is_running()
            processes = manager.get_process_info() if is_running else []

            return {
                "success": True,
                "running": is_running,
                "processes": processes,
                "message": "KiCAD is running" if is_running else "KiCAD is not running",
            }
        except (OSError, RuntimeError) as e:
            logger.exception("Error checking KiCAD UI status")
            return {"success": False, "message": str(e)}

    def _handle_launch_kicad_ui(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Launch KiCAD UI.

        Args:
            params: Parameters including optional projectPath and autoLaunch.

        Returns:
            Result dictionary with launch status.
        """
        logger.info("Launching KiCAD UI")
        try:
            project_path = params.get("projectPath")
            auto_launch = params.get("autoLaunch", AUTO_LAUNCH_KICAD)

            path_obj = Path(project_path) if project_path else None
            result = check_and_launch_kicad(path_obj, auto_launch=auto_launch)

            return {"success": True, **result}
        except (OSError, RuntimeError) as e:
            logger.exception("Error launching KiCAD UI")
            return {"success": False, "message": str(e)}

    def _handle_refill_zones(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """Refill all copper pour zones on the board.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary with refill status.
        """
        logger.info("Refilling zones")
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            import pcbnew  # noqa: PLC0415

            filler = pcbnew.ZONE_FILLER(self.board)
            zones = self.board.Zones()
            filler.Fill(zones)

            zone_count = zones.size() if hasattr(zones, "size") else len(list(zones))
            return {
                "success": True,
                "message": "Zones refilled successfully",
                "zoneCount": zone_count,
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("Error refilling zones")
            return {"success": False, "message": str(e)}

    # =========================================================================
    # IPC Backend handlers - these provide real-time UI synchronization
    # =========================================================================

    def _ipc_route_trace(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for route_trace - adds track with real-time UI update.

        Args:
            params: Parameters for trace routing.

        Returns:
            Result dictionary with trace info.
        """
        try:
            start = params.get("start", {})
            end = params.get("end", {})
            layer = params.get("layer", "F.Cu")
            width = params.get("width", 0.25)
            net = params.get("net")

            start_x = (
                start.get("x", 0) if isinstance(start, dict) else params.get("startX", 0)
            )
            start_y = (
                start.get("y", 0) if isinstance(start, dict) else params.get("startY", 0)
            )
            end_x = end.get("x", 0) if isinstance(end, dict) else params.get("endX", 0)
            end_y = end.get("y", 0) if isinstance(end, dict) else params.get("endY", 0)

            success = self.ipc_board_api.add_track(
                start_x=start_x,
                start_y=start_y,
                end_x=end_x,
                end_y=end_y,
                width=width,
                layer=layer,
                net_name=net,
            )

            return {
                "success": success,
                "message": (
                    "Added trace (visible in KiCAD UI)"
                    if success
                    else "Failed to add trace"
                ),
                "trace": {
                    "start": {"x": start_x, "y": start_y, "unit": "mm"},
                    "end": {"x": end_x, "y": end_y, "unit": "mm"},
                    "layer": layer,
                    "width": width,
                    "net": net,
                },
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC route_trace error")
            return {"success": False, "message": str(e)}

    def _ipc_add_via(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for add_via - adds via with real-time UI update.

        Args:
            params: Parameters for via placement.

        Returns:
            Result dictionary with via info.
        """
        try:
            position = params.get("position", {})
            x = position.get("x", 0) if isinstance(position, dict) else params.get("x", 0)
            y = position.get("y", 0) if isinstance(position, dict) else params.get("y", 0)

            size = params.get("size", 0.8)
            drill = params.get("drill", 0.4)
            net = params.get("net")
            from_layer = params.get("from_layer", "F.Cu")
            to_layer = params.get("to_layer", "B.Cu")

            success = self.ipc_board_api.add_via(
                x=x,
                y=y,
                diameter=size,
                drill=drill,
                net_name=net,
                via_type="through",
            )

            return {
                "success": success,
                "message": (
                    "Added via (visible in KiCAD UI)"
                    if success
                    else "Failed to add via"
                ),
                "via": {
                    "position": {"x": x, "y": y, "unit": "mm"},
                    "size": size,
                    "drill": drill,
                    "from_layer": from_layer,
                    "to_layer": to_layer,
                    "net": net,
                },
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC add_via error")
            return {"success": False, "message": str(e)}

    def _ipc_add_net(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for add_net.

        Args:
            params: Parameters including net name.

        Returns:
            Result dictionary.
        """
        name = params.get("name")
        logger.info("IPC add_net: %s (nets auto-created with components)", name)
        return {
            "success": True,
            "message": f"Net '{name}' will be created when components are connected",
            "net": {"name": name},
        }

    def _ipc_add_copper_pour(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for add_copper_pour - adds zone with real-time UI update.

        Args:
            params: Parameters for copper pour.

        Returns:
            Result dictionary with pour info.
        """
        try:
            layer = params.get("layer", "F.Cu")
            net = params.get("net")
            clearance = params.get("clearance", 0.5)
            min_width = params.get("minWidth", 0.25)
            points = params.get("points", [])
            priority = params.get("priority", 0)
            fill_type = params.get("fillType", "solid")
            name = params.get("name", "")

            if not points or len(points) < 3:  # noqa: PLR2004
                return {
                    "success": False,
                    "message": "At least 3 points are required for copper pour outline",
                }

            formatted_points = [
                {"x": point.get("x", 0), "y": point.get("y", 0)} for point in points
            ]

            success = self.ipc_board_api.add_zone(
                points=formatted_points,
                layer=layer,
                net_name=net,
                clearance=clearance,
                min_thickness=min_width,
                priority=priority,
                fill_mode=fill_type,
                name=name,
            )

            return {
                "success": success,
                "message": (
                    "Added copper pour (visible in KiCAD UI)"
                    if success
                    else "Failed to add copper pour"
                ),
                "pour": {
                    "layer": layer,
                    "net": net,
                    "clearance": clearance,
                    "minWidth": min_width,
                    "priority": priority,
                    "fillType": fill_type,
                    "pointCount": len(points),
                },
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC add_copper_pour error")
            return {"success": False, "message": str(e)}

    def _ipc_refill_zones(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """IPC handler for refill_zones - refills all zones with real-time UI update.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary.
        """
        try:
            success = self.ipc_board_api.refill_zones()

            return {
                "success": success,
                "message": (
                    "Zones refilled (visible in KiCAD UI)"
                    if success
                    else "Failed to refill zones"
                ),
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC refill_zones error")
            return {"success": False, "message": str(e)}

    def _ipc_add_text(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for add_text/add_board_text - adds text with real-time UI update.

        Args:
            params: Parameters for text placement.

        Returns:
            Result dictionary.
        """
        try:
            text = params.get("text", "")
            position = params.get("position", {})
            x = position.get("x", 0) if isinstance(position, dict) else params.get("x", 0)
            y = position.get("y", 0) if isinstance(position, dict) else params.get("y", 0)
            layer = params.get("layer", "F.SilkS")
            size = params.get("size", 1.0)
            rotation = params.get("rotation", 0)

            success = self.ipc_board_api.add_text(
                text=text, x=x, y=y, layer=layer, size=size, rotation=rotation
            )

            return {
                "success": success,
                "message": (
                    f"Added text '{text}' (visible in KiCAD UI)"
                    if success
                    else "Failed to add text"
                ),
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC add_text error")
            return {"success": False, "message": str(e)}

    def _ipc_set_board_size(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for set_board_size.

        Args:
            params: Parameters including width, height, unit.

        Returns:
            Result dictionary with board size info.
        """
        try:
            width = params.get("width", 100)
            height = params.get("height", 100)
            unit = params.get("unit", "mm")

            success = self.ipc_board_api.set_size(width, height, unit)

            return {
                "success": success,
                "message": (
                    f"Board size set to {width}x{height} {unit} (visible in KiCAD UI)"
                    if success
                    else "Failed to set board size"
                ),
                "boardSize": {"width": width, "height": height, "unit": unit},
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC set_board_size error")
            return {"success": False, "message": str(e)}

    def _ipc_get_board_info(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """IPC handler for get_board_info.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary with board info.
        """
        try:
            size = self.ipc_board_api.get_size()
            components = self.ipc_board_api.list_components()
            tracks = self.ipc_board_api.get_tracks()
            vias = self.ipc_board_api.get_vias()
            nets = self.ipc_board_api.get_nets()

            return {
                "success": True,
                "boardInfo": {
                    "size": size,
                    "componentCount": len(components),
                    "trackCount": len(tracks),
                    "viaCount": len(vias),
                    "netCount": len(nets),
                    "backend": "ipc",
                    "realtime": True,
                },
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC get_board_info error")
            return {"success": False, "message": str(e)}

    def _ipc_place_component(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for place_component - places component with real-time UI update.

        Args:
            params: Parameters for component placement.

        Returns:
            Result dictionary with component info.
        """
        try:
            reference = params.get("reference", params.get("componentId", ""))
            footprint = params.get("footprint", "")
            position = params.get("position", {})
            x = position.get("x", 0) if isinstance(position, dict) else params.get("x", 0)
            y = position.get("y", 0) if isinstance(position, dict) else params.get("y", 0)
            rotation = params.get("rotation", 0)
            layer = params.get("layer", "F.Cu")
            value = params.get("value", "")

            success = self.ipc_board_api.place_component(
                reference=reference,
                footprint=footprint,
                x=x,
                y=y,
                rotation=rotation,
                layer=layer,
                value=value,
            )

            return {
                "success": success,
                "message": (
                    f"Placed component {reference} (visible in KiCAD UI)"
                    if success
                    else "Failed to place component"
                ),
                "component": {
                    "reference": reference,
                    "footprint": footprint,
                    "position": {"x": x, "y": y, "unit": "mm"},
                    "rotation": rotation,
                    "layer": layer,
                },
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC place_component error")
            return {"success": False, "message": str(e)}

    def _ipc_move_component(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for move_component - moves component with real-time UI update.

        Args:
            params: Parameters for component movement.

        Returns:
            Result dictionary.
        """
        try:
            reference = params.get("reference", params.get("componentId", ""))
            position = params.get("position", {})
            x = position.get("x", 0) if isinstance(position, dict) else params.get("x", 0)
            y = position.get("y", 0) if isinstance(position, dict) else params.get("y", 0)
            rotation = params.get("rotation")

            success = self.ipc_board_api.move_component(
                reference=reference, x=x, y=y, rotation=rotation
            )

            return {
                "success": success,
                "message": (
                    f"Moved component {reference} (visible in KiCAD UI)"
                    if success
                    else "Failed to move component"
                ),
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC move_component error")
            return {"success": False, "message": str(e)}

    def _ipc_delete_component(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for delete_component - deletes component with real-time UI update.

        Args:
            params: Parameters including reference.

        Returns:
            Result dictionary.
            else:
                return {
                    "success": success,
        """
        try:
            reference = params.get("reference", params.get("componentId", ""))

            success = self.ipc_board_api.delete_component(reference=reference)

            return {
                "success": success,
                "message": (
                    f"Deleted component {reference} (visible in KiCAD UI)"
                    if success
                    else "Failed to delete component"
                ),
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC delete_component error")
            return {"success": False, "message": str(e)}

    def _ipc_get_component_list(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """IPC handler for get_component_list.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary with components list.
        """
        try:
            components = self.ipc_board_api.list_components()
            return {"success": True, "components": components, "count": len(components)}
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC get_component_list error")
            return {"success": False, "message": str(e)}

    def _ipc_save_project(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """IPC handler for save_project.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary.
        """
        try:
            success = self.ipc_board_api.save()
            return {
                "success": success,
                "message": "Project saved" if success else "Failed to save project",
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC save_project error")
            return {"success": False, "message": str(e)}

    def _ipc_delete_trace(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for delete_trace.

        Note: IPC doesn't support direct trace deletion yet.

        Args:
            params: Parameters for trace deletion.

        Returns:
            Result dictionary from SWIG fallback.
        """
        logger.info(
            "delete_trace: Falling back to SWIG (IPC doesn't support trace deletion)"
        )
        return self.routing_commands.delete_trace(params)

    def _ipc_get_nets_list(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """IPC handler for get_nets_list - gets nets with real-time data.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary with nets list.
        """
        try:
            nets = self.ipc_board_api.get_nets()
            return {"success": True, "nets": nets, "count": len(nets)}
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC get_nets_list error")
            return {"success": False, "message": str(e)}

    def _ipc_add_board_outline(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for add_board_outline - adds board edge with real-time UI update.

        Args:
            params: Parameters including points and width.

        Returns:
            Result dictionary with outline info.
        """
        try:
            from kipy.board_types import BoardSegment  # noqa: PLC0415
            from kipy.geometry import Vector2  # noqa: PLC0415
            from kipy.proto.board.board_types_pb2 import BoardLayer  # noqa: PLC0415
            from kipy.util.units import from_mm  # noqa: PLC0415

            board = self.ipc_board_api._get_board()  # noqa: SLF001

            points = params.get("points", [])
            width = params.get("width", 0.1)

            if len(points) < 2:  # noqa: PLR2004
                return {
                    "success": False,
                    "message": "At least 2 points required for board outline",
                }

            commit = board.begin_commit()
            lines_created = 0

            for i in range(len(points)):
                start = points[i]
                end = points[(i + 1) % len(points)]

                segment = BoardSegment()
                segment.start = Vector2.from_xy(
                    from_mm(start.get("x", 0)), from_mm(start.get("y", 0))
                )
                segment.end = Vector2.from_xy(
                    from_mm(end.get("x", 0)), from_mm(end.get("y", 0))
                )
                segment.layer = BoardLayer.BL_Edge_Cuts
                segment.attributes.stroke.width = from_mm(width)

                board.create_items(segment)
                lines_created += 1

            board.push_commit(commit, "Added board outline")

            return {
                "success": True,
                "message": (
                    f"Added board outline with {lines_created} segments "
                    "(visible in KiCAD UI)"
                ),
                "segments": lines_created,
            }
        except (OSError, RuntimeError, AttributeError, ImportError) as e:
            logger.exception("IPC add_board_outline error")
            return {"success": False, "message": str(e)}

    def _ipc_add_mounting_hole(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for add_mounting_hole - adds mounting hole with real-time UI.

        Args:
            params: Parameters including x, y, diameter.

        Returns:
            Result dictionary with hole info.
        """
        try:
            from kipy.board_types import BoardCircle  # noqa: PLC0415
            from kipy.geometry import Vector2  # noqa: PLC0415
            from kipy.proto.board.board_types_pb2 import BoardLayer  # noqa: PLC0415
            from kipy.util.units import from_mm  # noqa: PLC0415

            board = self.ipc_board_api._get_board()  # noqa: SLF001

            x = params.get("x", 0)
            y = params.get("y", 0)
            diameter = params.get("diameter", 3.2)  # M3 hole default

            commit = board.begin_commit()

            circle = BoardCircle()
            circle.center = Vector2.from_xy(from_mm(x), from_mm(y))
            circle.radius = from_mm(diameter / 2)
            circle.layer = BoardLayer.BL_Edge_Cuts
            circle.attributes.stroke.width = from_mm(0.1)

            board.create_items(circle)
            board.push_commit(commit, f"Added mounting hole at ({x}, {y})")

            return {
                "success": True,
                "message": f"Added mounting hole at ({x}, {y}) mm (visible in KiCAD UI)",
                "hole": {"position": {"x": x, "y": y}, "diameter": diameter},
            }
        except (OSError, RuntimeError, AttributeError, ImportError) as e:
            logger.exception("IPC add_mounting_hole error")
            return {"success": False, "message": str(e)}

    def _ipc_get_layer_list(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """IPC handler for get_layer_list - gets enabled layers.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary with layers list.
        """
        try:
            layers = self.ipc_board_api.get_enabled_layers()
            return {"success": True, "layers": layers, "count": len(layers)}
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC get_layer_list error")
            return {"success": False, "message": str(e)}

    def _ipc_rotate_component(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for rotate_component - rotates component with real-time UI.

        Args:
            params: Parameters including reference and angle.

        Returns:
            Result dictionary with new rotation.
        """
        try:
            reference = params.get("reference", params.get("componentId", ""))
            angle = params.get("angle", params.get("rotation", 90))

            components = self.ipc_board_api.list_components()
            target = None
            for comp in components:
                if comp.get("reference") == reference:
                    target = comp
                    break

            if not target:
                return {"success": False, "message": f"Component {reference} not found"}

            current_rotation = target.get("rotation", 0)
            new_rotation = (current_rotation + angle) % 360

            success = self.ipc_board_api.move_component(
                reference=reference,
                x=target.get("position", {}).get("x", 0),
                y=target.get("position", {}).get("y", 0),
                rotation=new_rotation,
            )

            return {
                "success": success,
                "message": (
                    f"Rotated component {reference} by {angle} deg (visible in KiCAD UI)"
                    if success
                    else "Failed to rotate component"
                ),
                "newRotation": new_rotation,
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC rotate_component error")
            return {"success": False, "message": str(e)}

    def _ipc_get_component_properties(self, params: dict[str, Any]) -> dict[str, Any]:
        """IPC handler for get_component_properties - gets detailed component info.

        Args:
            params: Parameters including reference.

        Returns:
            Result dictionary with component properties.
        """
        try:
            reference = params.get("reference", params.get("componentId", ""))

            components = self.ipc_board_api.list_components()
            target = None
            for comp in components:
                if comp.get("reference") == reference:
                    target = comp
                    break

            if not target:
                return {"success": False, "message": f"Component {reference} not found"}

            return {"success": True, "component": target}
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("IPC get_component_properties error")
            return {"success": False, "message": str(e)}

    # =========================================================================
    # Legacy IPC command handlers (explicit ipc_* commands)
    # =========================================================================

    def _handle_get_backend_info(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """Get information about the current backend.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary with backend info.
        """
        return {
            "success": True,
            "backend": "ipc" if self.use_ipc else "swig",
            "realtime_sync": self.use_ipc,
            "ipc_connected": (
                self.ipc_backend.is_connected() if self.ipc_backend else False
            ),
            "version": self.ipc_backend.get_version() if self.ipc_backend else "N/A",
            "message": (
                "Using IPC backend with real-time UI sync"
                if self.use_ipc
                else "Using SWIG backend (requires manual reload)"
            ),
        }

    def _handle_ipc_add_track(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add a track using IPC backend (real-time).

        Args:
            params: Parameters for track placement.

        Returns:
            Result dictionary.
        """
        if not self.use_ipc or not self.ipc_board_api:
            return {"success": False, "message": "IPC backend not available"}

        try:
            success = self.ipc_board_api.add_track(
                start_x=params.get("startX", 0),
                start_y=params.get("startY", 0),
                end_x=params.get("endX", 0),
                end_y=params.get("endY", 0),
                width=params.get("width", 0.25),
                layer=params.get("layer", "F.Cu"),
                net_name=params.get("net"),
            )
            return {
                "success": success,
                "message": (
                    "Track added (visible in KiCAD UI)"
                    if success
                    else "Failed to add track"
                ),
                "realtime": True,
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("Error adding track via IPC")
            return {"success": False, "message": str(e)}

    def _handle_ipc_add_via(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add a via using IPC backend (real-time).

        Args:
            params: Parameters for via placement.

        Returns:
            Result dictionary.
        """
        if not self.use_ipc or not self.ipc_board_api:
            return {"success": False, "message": "IPC backend not available"}

        try:
            success = self.ipc_board_api.add_via(
                x=params.get("x", 0),
                y=params.get("y", 0),
                diameter=params.get("diameter", 0.8),
                drill=params.get("drill", 0.4),
                net_name=params.get("net"),
                via_type=params.get("type", "through"),
            )
            return {
                "success": success,
                "message": (
                    "Via added (visible in KiCAD UI)"
                    if success
                    else "Failed to add via"
                ),
                "realtime": True,
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("Error adding via via IPC")
            return {"success": False, "message": str(e)}

    def _handle_ipc_add_text(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add text using IPC backend (real-time).

        Args:
            params: Parameters for text placement.

        Returns:
            Result dictionary.
        """
        if not self.use_ipc or not self.ipc_board_api:
            return {"success": False, "message": "IPC backend not available"}

        try:
            success = self.ipc_board_api.add_text(
                text=params.get("text", ""),
                x=params.get("x", 0),
                y=params.get("y", 0),
                layer=params.get("layer", "F.SilkS"),
                size=params.get("size", 1.0),
                rotation=params.get("rotation", 0),
            )
            return {
                "success": success,
                "message": (
                    "Text added (visible in KiCAD UI)"
                    if success
                    else "Failed to add text"
                ),
                "realtime": True,
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("Error adding text via IPC")
            return {"success": False, "message": str(e)}

    def _handle_ipc_list_components(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """List components using IPC backend.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary with components list.
        """
        if not self.use_ipc or not self.ipc_board_api:
            return {"success": False, "message": "IPC backend not available"}

        try:
            components = self.ipc_board_api.list_components()
            return {"success": True, "components": components, "count": len(components)}
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("Error listing components via IPC")
            return {"success": False, "message": str(e)}

    def _handle_ipc_get_tracks(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """Get tracks using IPC backend.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary with tracks list.
        """
        if not self.use_ipc or not self.ipc_board_api:
            return {"success": False, "message": "IPC backend not available"}

        try:
            tracks = self.ipc_board_api.get_tracks()
            return {"success": True, "tracks": tracks, "count": len(tracks)}
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("Error getting tracks via IPC")
            return {"success": False, "message": str(e)}

    def _handle_ipc_get_vias(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """Get vias using IPC backend.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary with vias list.
        """
        if not self.use_ipc or not self.ipc_board_api:
            return {"success": False, "message": "IPC backend not available"}

        try:
            vias = self.ipc_board_api.get_vias()
            return {"success": True, "vias": vias, "count": len(vias)}
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("Error getting vias via IPC")
            return {"success": False, "message": str(e)}

    def _handle_ipc_save_board(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """Save board using IPC backend.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary.
        """
        if not self.use_ipc or not self.ipc_board_api:
            return {"success": False, "message": "IPC backend not available"}

        try:
            success = self.ipc_board_api.save()
            return {
                "success": success,
                "message": "Board saved" if success else "Failed to save board",
            }
        except (OSError, RuntimeError, AttributeError) as e:
            logger.exception("Error saving board via IPC")
            return {"success": False, "message": str(e)}

    # JLCPCB API handlers

    def _handle_download_jlcpcb_database(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Download JLCPCB parts database from JLCSearch API.

        Args:
            params: Parameters including optional force flag.

        Returns:
            Result dictionary with download stats.
        """
        try:
            force = params.get("force", False)

            stats = self.jlcpcb_parts.get_database_stats()
            if stats["total_parts"] > 0 and not force:
                return {
                    "success": False,
                    "message": "Database already exists. Use force=true to re-download.",
                    "stats": stats,
                }

            logger.info("Downloading JLCPCB parts database from JLCSearch...")

            parts = self.jlcsearch_client.download_all_components(
                callback=lambda _total, msg: logger.info("%s", msg)
            )

            logger.info("Importing %d parts into database...", len(parts))
            self.jlcpcb_parts.import_jlcsearch_parts(
                parts, progress_callback=lambda _curr, _total, msg: logger.info("%s", msg)
            )

            stats = self.jlcpcb_parts.get_database_stats()
            db_size_mb = Path(self.jlcpcb_parts.db_path).stat().st_size / (1024 * 1024)

            return {
                "success": True,
                "total_parts": stats["total_parts"],
                "basic_parts": stats["basic_parts"],
                "extended_parts": stats["extended_parts"],
                "db_size_mb": round(db_size_mb, 2),
                "db_path": stats["db_path"],
            }

        except (OSError, RuntimeError, ValueError) as e:
            logger.exception("Error downloading JLCPCB database")
            return {"success": False, "message": f"Failed to download database: {e!s}"}

    def _handle_search_jlcpcb_parts(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Search JLCPCB parts database.

        Args:
            params: Search parameters.

        Returns:
            Result dictionary with parts list.
        """
        try:
            query = params.get("query")
            category = params.get("category")
            package = params.get("package")
            library_type = params.get("library_type", "All")
            manufacturer = params.get("manufacturer")
            in_stock = params.get("in_stock", True)
            limit = params.get("limit", 20)

            if library_type == "All":
                library_type = None

            parts = self.jlcpcb_parts.search_parts(
                query=query,
                category=category,
                package=package,
                library_type=library_type,
                manufacturer=manufacturer,
                in_stock=in_stock,
                limit=limit,
            )

            for part in parts:
                if part.get("price_json"):
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        part["price_breaks"] = json.loads(part["price_json"])

            return {"success": True, "parts": parts, "count": len(parts)}

        except (OSError, RuntimeError, ValueError) as e:
            logger.exception("Error searching JLCPCB parts")
            return {"success": False, "message": f"Search failed: {e!s}"}

    def _handle_get_jlcpcb_part(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get detailed information for a specific JLCPCB part.

        Args:
            params: Parameters including lcsc_number.

        Returns:
            Result dictionary with part info.
        """
        try:
            lcsc_number = params.get("lcsc_number")
            if not lcsc_number:
                return {"success": False, "message": "Missing lcsc_number parameter"}

            part = self.jlcpcb_parts.get_part_info(lcsc_number)
            if not part:
                return {"success": False, "message": f"Part not found: {lcsc_number}"}

            footprints = self.jlcpcb_parts.map_package_to_footprint(
                part.get("package", "")
            )
            return {"success": True, "part": part, "footprints": footprints}

        except (OSError, RuntimeError, ValueError) as e:
            logger.exception("Error getting JLCPCB part")
            return {"success": False, "message": f"Failed to get part info: {e!s}"}

    def _handle_get_jlcpcb_database_stats(
        self, params: dict[str, Any]  # noqa: ARG002
    ) -> dict[str, Any]:
        """Get statistics about JLCPCB database.

        Args:
            params: Unused parameters.

        Returns:
            Result dictionary with database stats.
        """
        try:
            stats = self.jlcpcb_parts.get_database_stats()
            return {"success": True, "stats": stats}
        except (OSError, RuntimeError, ValueError) as e:
            logger.exception("Error getting database stats")
            return {"success": False, "message": f"Failed to get stats: {e!s}"}

    def _handle_suggest_jlcpcb_alternatives(
        self, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Suggest alternative JLCPCB parts.

        Args:
            params: Parameters including lcsc_number and limit.

        Returns:
            Result dictionary with alternatives list.
        """
        try:
            lcsc_number = params.get("lcsc_number")
            limit = params.get("limit", 5)

            if not lcsc_number:
                return {"success": False, "message": "Missing lcsc_number parameter"}

            original_part = self.jlcpcb_parts.get_part_info(lcsc_number)
            reference_price = None
            if original_part and original_part.get("price_breaks"):
                with contextlib.suppress(ValueError, TypeError, IndexError):
                    reference_price = float(
                        original_part["price_breaks"][0].get("price", 0)
                    )

            alternatives = self.jlcpcb_parts.suggest_alternatives(lcsc_number, limit)

            for part in alternatives:
                if part.get("price_json"):
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        part["price_breaks"] = json.loads(part["price_json"])

            return {
                "success": True,
                "alternatives": alternatives,
                "reference_price": reference_price,
            }

        except (OSError, RuntimeError, ValueError) as e:
            logger.exception("Error suggesting alternatives")
            return {
                "success": False,
                "message": f"Failed to suggest alternatives: {e!s}",
            }


def _handle_json_rpc_initialize(request_id: str | int | None) -> dict[str, Any]:
    """Handle MCP initialize method.

    Args:
        request_id: The JSON-RPC request ID.

    Returns:
        JSON-RPC response dictionary.
    """
    logger.info("Handling MCP initialize")
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {
                "tools": {"listChanged": True},
                "resources": {"subscribe": False, "listChanged": True},
            },
            "serverInfo": {
                "name": "kicad-mcp-server",
                "title": "KiCAD PCB Design Assistant",
                "version": "2.1.0-alpha",
            },
            "instructions": (
                "AI-assisted PCB design with KiCAD. Use tools to create projects, "
                "design boards, place components, route traces, and export "
                "manufacturing files."
            ),
        },
    }


def _handle_json_rpc_tools_list(
    interface: KiCADInterface, request_id: str | int | None
) -> dict[str, Any]:
    """Handle MCP tools/list method.

    Args:
        interface: The KiCAD interface instance.
        request_id: The JSON-RPC request ID.

    Returns:
        JSON-RPC response dictionary.
    """
    logger.info("Handling MCP tools/list")
    tools = []
    for cmd_name in interface.command_routes:
        if cmd_name in TOOL_SCHEMAS:
            tool_def = TOOL_SCHEMAS[cmd_name].copy()
            tools.append(tool_def)
        else:
            logger.warning("No schema defined for tool: %s", cmd_name)
            tools.append(
                {
                    "name": cmd_name,
                    "description": f"KiCAD command: {cmd_name}",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            )

    logger.info("Returning %d tools", len(tools))
    return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}


def _handle_json_rpc_tools_call(
    interface: KiCADInterface,
    params: dict[str, Any],
    request_id: str | int | None,
) -> dict[str, Any]:
    """Handle MCP tools/call method.

    Args:
        interface: The KiCAD interface instance.
        params: The call parameters.
        request_id: The JSON-RPC request ID.

    Returns:
        JSON-RPC response dictionary.
    """
    logger.info("Handling MCP tools/call")
    tool_name = params.get("name")
    tool_params = params.get("arguments", {})

    result = interface.handle_command(tool_name, tool_params)

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": json.dumps(result)}]},
    }


def _handle_json_rpc_resources_list(request_id: str | int | None) -> dict[str, Any]:
    """Handle MCP resources/list method.

    Args:
        request_id: The JSON-RPC request ID.

    Returns:
        JSON-RPC response dictionary.
    """
    logger.info("Handling MCP resources/list")
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"resources": RESOURCE_DEFINITIONS},
    }


def _handle_json_rpc_resources_read(
    interface: KiCADInterface,
    params: dict[str, Any],
    request_id: str | int | None,
) -> dict[str, Any]:
    """Handle MCP resources/read method.

    Args:
        interface: The KiCAD interface instance.
        params: The read parameters.
        request_id: The JSON-RPC request ID.

    Returns:
        JSON-RPC response dictionary.
    """
    logger.info("Handling MCP resources/read")
    resource_uri = params.get("uri")

    if not resource_uri:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32602,
                "message": "Missing required parameter: uri",
            },
        }

    resource_data = handle_resource_read(resource_uri, interface)
    return {"jsonrpc": "2.0", "id": request_id, "result": resource_data}


def _process_json_rpc_message(
    interface: KiCADInterface, command_data: dict[str, Any]
) -> dict[str, Any]:
    """Process a JSON-RPC 2.0 message.

    Args:
        interface: The KiCAD interface instance.
        command_data: The parsed JSON-RPC message.

    Returns:
        JSON-RPC response dictionary.
    """
    method = command_data.get("method")
    params = command_data.get("params", {})
    request_id = command_data.get("id")

    if method == "initialize":
        return _handle_json_rpc_initialize(request_id)
    if method == "tools/list":
        return _handle_json_rpc_tools_list(interface, request_id)
    if method == "tools/call":
        return _handle_json_rpc_tools_call(interface, params, request_id)
    if method == "resources/list":
        return _handle_json_rpc_resources_list(request_id)
    if method == "resources/read":
        return _handle_json_rpc_resources_read(interface, params, request_id)

    logger.error("Unknown JSON-RPC method: %s", method)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _process_legacy_message(
    interface: KiCADInterface, command_data: dict[str, Any]
) -> dict[str, Any]:
    """Process a legacy custom format message.

    Args:
        interface: The KiCAD interface instance.
        command_data: The parsed message.

    Returns:
        Response dictionary.
    """
    logger.info("Detected custom format message")
    command = command_data.get("command")
    params = command_data.get("params", {})

    if not command:
        logger.error("Missing command field")
        return {
            "success": False,
            "message": "Missing command",
            "errorDetails": "The command field is required",
        }

    return interface.handle_command(command, params)


def main() -> None:
    """Main entry point for the KiCAD interface."""
    logger.info("Starting KiCAD interface...")
    interface = KiCADInterface()

    try:
        logger.info("Processing commands from stdin...")
        for line in sys.stdin:
            try:
                logger.debug("Received input: %s", line.strip())
                command_data = json.loads(line)

                # Check if this is JSON-RPC 2.0 format
                if "jsonrpc" in command_data and command_data["jsonrpc"] == "2.0":
                    logger.info("Detected JSON-RPC 2.0 format message")
                    response = _process_json_rpc_message(interface, command_data)
                else:
                    response = _process_legacy_message(interface, command_data)

                logger.debug("Sending response: %s", response)
                sys.stdout.flush()

            except json.JSONDecodeError as e:
                logger.exception("Invalid JSON input")
                _response = {
                    "success": False,
                    "message": "Invalid JSON input",
                    "errorDetails": str(e),
                }
                sys.stdout.flush()

    except KeyboardInterrupt:
        logger.info("KiCAD interface stopped")
        sys.exit(0)

    except (OSError, RuntimeError):
        logger.exception("Unexpected error\n%s", traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
