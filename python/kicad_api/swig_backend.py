"""SWIG Backend (Legacy - DEPRECATED).

Uses the legacy SWIG-based pcbnew Python bindings.
This backend wraps the existing implementation for backward compatibility.

Warning:
    SWIG bindings are deprecated as of KiCAD 9.0
    and will be removed in KiCAD 10.0.
    Please migrate to IPC backend.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, override

from kicad_api.base import (
    APINotAvailableError,
    BoardAPI,
    KiCADBackend,
    KiCADConnectionError,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import ModuleType

logger = logging.getLogger(__name__)


class SWIGBackend(KiCADBackend):
    """Legacy SWIG-based backend.

    Wraps existing commands/project.py, commands/component.py, etc.
    for compatibility during migration period.
    """

    def __init__(self) -> None:
        """Initialize the SWIG backend."""
        self._connected = False
        self._pcbnew: ModuleType | None = None
        logger.warning(
            "Using DEPRECATED SWIG backend. "
            "This will be removed in KiCAD 10.0. "
            "Please migrate to IPC API."
        )

    def connect(self) -> bool:
        """'Connect' to SWIG API (just validates pcbnew import).

        Returns:
            True if pcbnew module available.

        Raises:
            APINotAvailableError: If pcbnew module is not available.
        """
        try:
            import pcbnew

            self._pcbnew = pcbnew
            version = pcbnew.GetBuildVersion()
            logger.info("Connected to pcbnew (SWIG): %s", version)
            self._connected = True
        except ImportError as e:
            logger.exception("pcbnew module not found")
            msg = (
                "SWIG backend requires pcbnew module. "
                "Ensure KiCAD Python module is in PYTHONPATH."
            )
            raise APINotAvailableError(msg) from e
        else:
            return True

    def disconnect(self) -> None:
        """Disconnect from SWIG API (no-op)."""
        self._connected = False
        self._pcbnew = None
        logger.info("Disconnected from SWIG backend")

    def is_connected(self) -> bool:
        """Check if connected.

        Returns:
            True if connected to the SWIG backend.
        """
        return self._connected

    def get_version(self) -> str:
        """Get KiCAD version.

        Returns:
            The KiCAD build version string.

        Raises:
            KiCADConnectionError: If not connected.
        """
        if not self.is_connected():
            msg = "Not connected"
            raise KiCADConnectionError(msg)

        if self._pcbnew is None:
            msg = "pcbnew module not loaded"
            raise KiCADConnectionError(msg)

        return self._pcbnew.GetBuildVersion()

    # Project Operations
    def create_project(self, path: Path, name: str) -> dict[str, Any]:
        """Create project using existing SWIG implementation.

        Args:
            path: Directory path for the project.
            name: Project name.

        Returns:
            Dictionary with project info.

        Raises:
            KiCADConnectionError: If not connected.
        """
        if not self.is_connected():
            msg = "Not connected"
            raise KiCADConnectionError(msg)

        # Import existing implementation
        from commands.project import ProjectCommands

        return ProjectCommands.create_project(str(path), name)

    def open_project(self, path: Path) -> dict[str, Any]:
        """Open project using existing SWIG implementation.

        Args:
            path: Path to .kicad_pro file.

        Returns:
            Dictionary with project info.

        Raises:
            KiCADConnectionError: If not connected.
        """
        if not self.is_connected():
            msg = "Not connected"
            raise KiCADConnectionError(msg)

        from commands.project import ProjectCommands

        return ProjectCommands.open_project(str(path))

    def save_project(self, path: Path | None = None) -> dict[str, Any]:
        """Save project using existing SWIG implementation.

        Args:
            path: Optional new path to save to.

        Returns:
            Dictionary with save status.

        Raises:
            KiCADConnectionError: If not connected.
        """
        if not self.is_connected():
            msg = "Not connected"
            raise KiCADConnectionError(msg)

        from commands.project import ProjectCommands

        path_str = str(path) if path else None
        return ProjectCommands.save_project(path_str)

    def close_project(self) -> None:
        """Close project (SWIG doesn't have explicit close)."""
        logger.info("Closing project (SWIG backend)")
        # SWIG backend doesn't maintain project state,
        # so this is essentially a no-op

    # Board Operations
    def get_board(self) -> BoardAPI:
        """Get board API.

        Returns:
            SWIGBoardAPI instance for board operations.

        Raises:
            KiCADConnectionError: If not connected.
        """
        if not self.is_connected():
            msg = "Not connected"
            raise KiCADConnectionError(msg)

        return SWIGBoardAPI(self._pcbnew)


class SWIGBoardAPI(BoardAPI):
    """Board API implementation wrapping SWIG/pcbnew."""

    def __init__(self, pcbnew_module: ModuleType | None) -> None:
        """Initialize the SWIG Board API.

        Args:
            pcbnew_module: The pcbnew module instance.
        """
        self.pcbnew = pcbnew_module
        self._board = None

    def set_size(self, width: float, height: float, unit: str = "mm") -> bool:
        """Set board size using existing implementation.

        Args:
            width: Board width.
            height: Board height.
            unit: Unit of measurement ("mm" or "in").

        Returns:
            True if successful.
        """
        from commands.board import BoardCommands

        result = BoardCommands.set_board_size(width, height, unit)
        return result.get("success", False)

    def get_size(self) -> dict[str, float]:
        """Get board size.

        Returns:
            Dictionary with width, height, unit.

        Raises:
            NotImplementedError: This method is not yet wrapped.
        """
        # TODO(developer): Implement using existing SWIG code  # noqa: TD003, FIX002
        msg = "get_size not yet wrapped"
        raise NotImplementedError(msg)

    def add_layer(self, layer_name: str, layer_type: str) -> bool:
        """Add layer using existing implementation.

        Args:
            layer_name: Name of the layer.
            layer_type: Type ("copper", "technical", "user").

        Returns:
            True if successful.
        """
        from commands.board import BoardCommands

        result = BoardCommands.add_layer(layer_name, layer_type)
        return result.get("success", False)

    def list_components(self) -> list[dict[str, Any]]:
        """List components using existing implementation.

        Returns:
            List of component dictionaries.
        """
        from commands.component import ComponentCommands

        result = ComponentCommands.get_component_list()
        if result.get("success"):
            return result.get("components", [])
        return []

    @override
    def place_component(
        self,
        reference: str,
        footprint: str,
        x: float,
        y: float,
        rotation: float = 0,
        layer: str = "F.Cu",
    ) -> bool:
        """Place component using existing implementation.

        Args:
            reference: Component reference (e.g., "R1").
            footprint: Footprint library path.
            x: X position (mm).
            y: Y position (mm).
            rotation: Rotation angle (degrees).
            layer: Layer name.

        Returns:
            True if successful.
        """
        from commands.component import ComponentCommands

        result = ComponentCommands.place_component(
            component_id=footprint,
            position={"x": x, "y": y, "unit": "mm"},
            reference=reference,
            rotation=rotation,
            layer=layer,
        )
        return result.get("success", False)


# This backend serves as a wrapper during the migration period.
# Once IPC backend is fully implemented, this can be deprecated.
