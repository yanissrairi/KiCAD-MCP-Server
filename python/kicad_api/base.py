"""Abstract base class for KiCAD API backends.

Defines the interface that all KiCAD backends must implement.
"""

from abc import ABC, abstractmethod
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class KiCADBackend(ABC):
    """Abstract base class for KiCAD API backends."""

    @abstractmethod
    def connect(self) -> bool:
        """Connect to KiCAD.

        Returns:
            True if connection successful, False otherwise
        """

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect from KiCAD and clean up resources."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if currently connected to KiCAD.

        Returns:
            True if connected, False otherwise
        """

    @abstractmethod
    def get_version(self) -> str:
        """Get KiCAD version.

        Returns:
            Version string (e.g., "9.0.0")
        """

    # Project Operations
    @abstractmethod
    def create_project(self, path: Path, name: str) -> dict[str, Any]:
        """Create a new KiCAD project.

        Args:
            path: Directory path for the project
            name: Project name

        Returns:
            Dictionary with project info
        """

    @abstractmethod
    def open_project(self, path: Path) -> dict[str, Any]:
        """Open an existing KiCAD project.

        Args:
            path: Path to .kicad_pro file

        Returns:
            Dictionary with project info
        """

    @abstractmethod
    def save_project(self, path: Path | None = None) -> dict[str, Any]:
        """Save the current project.

        Args:
            path: Optional new path to save to

        Returns:
            Dictionary with save status
        """

    @abstractmethod
    def close_project(self) -> None:
        """Close the current project."""

    # Board Operations
    @abstractmethod
    def get_board(self) -> "BoardAPI":
        """Get board API for current project.

        Returns:
            BoardAPI instance
        """


class BoardAPI(ABC):
    """Abstract interface for board operations."""

    @abstractmethod
    def set_size(self, width: float, height: float, unit: str = "mm") -> bool:
        """Set board size.

        Args:
            width: Board width
            height: Board height
            unit: Unit of measurement ("mm" or "in")

        Returns:
            True if successful
        """

    @abstractmethod
    def get_size(self) -> dict[str, float]:
        """Get current board size.

        Returns:
            Dictionary with width, height, unit
        """

    @abstractmethod
    def add_layer(self, layer_name: str, layer_type: str) -> bool:
        """Add a layer to the board.

        Args:
            layer_name: Name of the layer
            layer_type: Type ("copper", "technical", "user")

        Returns:
            True if successful
        """

    @abstractmethod
    def list_components(self) -> list[dict[str, Any]]:
        """List all components on the board.

        Returns:
            List of component dictionaries
        """

    @abstractmethod
    def place_component(
        self,
        reference: str,
        footprint: str,
        x: float,
        y: float,
        rotation: float = 0,
        layer: str = "F.Cu",
    ) -> bool:
        """Place a component on the board.

        Args:
            reference: Component reference (e.g., "R1")
            footprint: Footprint library path
            x: X position (mm)
            y: Y position (mm)
            rotation: Rotation angle (degrees)
            layer: Layer name

        Returns:
            True if successful
        """

    # Routing Operations
    def add_track(
        self,
        start_x: float,
        start_y: float,
        end_x: float,
        end_y: float,
        width: float = 0.25,
        layer: str = "F.Cu",
        net_name: str | None = None,
    ) -> bool:
        """Add a track (trace) to the board.

        Args:
            start_x: Start X position (mm)
            start_y: Start Y position (mm)
            end_x: End X position (mm)
            end_y: End Y position (mm)
            width: Track width (mm)
            layer: Layer name
            net_name: Optional net name

        Returns:
            True if successful
        """
        raise NotImplementedError

    def add_via(
        self,
        x: float,
        y: float,
        diameter: float = 0.8,
        drill: float = 0.4,
        net_name: str | None = None,
        via_type: str = "through",
    ) -> bool:
        """Add a via to the board.

        Args:
            x: X position (mm)
            y: Y position (mm)
            diameter: Via diameter (mm)
            drill: Drill diameter (mm)
            net_name: Optional net name
            via_type: Via type ("through", "blind", "micro")

        Returns:
            True if successful
        """
        raise NotImplementedError

    # Transaction support for undo/redo (IPC backend only)
    def supports_transactions(self) -> bool:
        """Check if this backend supports transaction/undo operations.

        Returns:
            True if begin_transaction/commit/rollback are available.
            Default: False (only IPC backend supports transactions).
        """
        return False

    def begin_transaction(self, description: str = "MCP Operation") -> None:
        """Begin a transaction for grouping operations into a single undo step.

        Only available if supports_transactions() returns True.
        Currently only supported by IPC backend.

        Args:
            description: Human-readable description of the transaction.

        Raises:
            NotImplementedError: If backend doesn't support transactions.
        """
        msg = (
            f"{self.__class__.__name__} does not support transactions. "
            "Use IPC backend for transaction support."
        )
        raise NotImplementedError(msg)

    def commit_transaction(self, description: str = "MCP Operation") -> None:
        """Commit the current transaction.

        Only available if supports_transactions() returns True.

        Args:
            description: Human-readable description of the commit.

        Raises:
            NotImplementedError: If backend doesn't support transactions.
        """
        msg = (
            f"{self.__class__.__name__} does not support transactions. "
            "Use IPC backend for transaction support."
        )
        raise NotImplementedError(msg)

    def rollback_transaction(self) -> None:
        """Roll back the current transaction.

        Only available if supports_transactions() returns True.

        Raises:
            NotImplementedError: If backend doesn't support transactions.
        """
        msg = (
            f"{self.__class__.__name__} does not support transactions. "
            "Use IPC backend for transaction support."
        )
        raise NotImplementedError(msg)

    def save(self) -> bool:
        """Save the board."""
        raise NotImplementedError

    # Query operations
    def get_tracks(self) -> list[dict[str, Any]]:
        """Get all tracks on the board."""
        raise NotImplementedError

    def get_vias(self) -> list[dict[str, Any]]:
        """Get all vias on the board."""
        raise NotImplementedError

    def get_nets(self) -> list[dict[str, Any]]:
        """Get all nets on the board."""
        raise NotImplementedError

    def get_selection(self) -> list[dict[str, Any]]:
        """Get currently selected items."""
        raise NotImplementedError


class BackendError(Exception):
    """Base exception for backend errors."""


class KiCADConnectionError(BackendError):
    """Raised when connection to KiCAD fails."""


class APINotAvailableError(BackendError):
    """Raised when required API is not available."""
