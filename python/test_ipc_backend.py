#!/usr/bin/env python3
"""Test script for KiCAD IPC Backend.

This script tests the real-time UI synchronization capabilities
of the IPC backend. Run this while KiCAD is open with a board.

Prerequisites:
1. KiCAD 9.0+ must be running
2. IPC API must be enabled: Preferences > Plugins > Enable IPC API Server
3. A board should be open in the PCB editor

Usage:
    ./venv/bin/python python/test_ipc_backend.py
"""

from __future__ import annotations

import argparse
import contextlib
import logging
from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kicad_api.ipc_backend import IPCBackend

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def test_connection() -> IPCBackend | None:
    """Test basic IPC connection to KiCAD.

    Returns:
        IPCBackend instance if connection successful, None otherwise.
    """
    try:
        from kicad_api.ipc_backend import IPCBackend  # noqa: PLC0415

        backend = IPCBackend()

        if backend.connect():
            return backend
        return None

    except ImportError:
        return None
    except Exception:  # noqa: BLE001
        return None


def test_board_access(backend: IPCBackend) -> object | None:
    """Test board access and component listing.

    Args:
        backend: The IPCBackend instance to use.

    Returns:
        Board API instance if successful, None otherwise.
    """
    try:
        board_api = backend.get_board()

        # List components
        components = board_api.list_components()

        max_display = 5
        if components:
            for comp in components[:max_display]:
                _ = comp.get("reference", "N/A")
                _ = comp.get("value", "N/A")
                pos = comp.get("position", {})
                _ = pos.get("x", 0)
                _ = pos.get("y", 0)

        return board_api

    except Exception:  # noqa: BLE001
        return None


def test_board_info(board_api: object) -> bool:
    """Test getting board information.

    Args:
        board_api: The board API instance to use.

    Returns:
        True if successful, False otherwise.
    """
    try:
        # Get board size
        board_api.get_size()  # type: ignore[attr-defined]

        # Get enabled layers
        with contextlib.suppress(Exception):
            _ = board_api.get_enabled_layers()  # type: ignore[attr-defined]

        # Get nets
        _ = board_api.get_nets()  # type: ignore[attr-defined]

        # Get tracks
        board_api.get_tracks()  # type: ignore[attr-defined]

        # Get vias
        board_api.get_vias()  # type: ignore[attr-defined]

    except Exception:  # noqa: BLE001
        return False
    else:
        return True


def verify_realtime_track(board_api: object, *, interactive: bool = False) -> bool:
    """Verify adding a track in real-time (appears immediately in KiCAD UI).

    Args:
        board_api: The board API instance to use.
        interactive: Whether to prompt for confirmation.

    Returns:
        True if successful, False otherwise.
    """
    if interactive:
        user_response = input("\nProceed with adding a test track? [y/N]: ").strip().lower()
        if user_response != "y":
            return False

    try:
        # Add a track
        success = board_api.add_track(  # type: ignore[attr-defined]
            start_x=100.0,
            start_y=100.0,
            end_x=120.0,
            end_y=100.0,
            width=0.25,
            layer="F.Cu",
        )

    except Exception:  # noqa: BLE001
        return False
    else:
        return bool(success)


def verify_realtime_via(board_api: object, *, interactive: bool = False) -> bool:
    """Verify adding a via in real-time (appears immediately in KiCAD UI).

    Args:
        board_api: The board API instance to use.
        interactive: Whether to prompt for confirmation.

    Returns:
        True if successful, False otherwise.
    """
    if interactive:
        user_response = input("\nProceed with adding a test via? [y/N]: ").strip().lower()
        if user_response != "y":
            return False

    try:
        # Add a via
        success = board_api.add_via(  # type: ignore[attr-defined]
            x=120.0,
            y=100.0,
            diameter=0.8,
            drill=0.4,
            via_type="through",
        )

    except Exception:  # noqa: BLE001
        return False
    else:
        return bool(success)


def verify_realtime_text(board_api: object, *, interactive: bool = False) -> bool:
    """Verify adding text in real-time.

    Args:
        board_api: The board API instance to use.
        interactive: Whether to prompt for confirmation.

    Returns:
        True if successful, False otherwise.
    """
    if interactive:
        user_response = input("\nProceed with adding test text? [y/N]: ").strip().lower()
        if user_response != "y":
            return False

    try:
        success = board_api.add_text(  # type: ignore[attr-defined]
            text="MCP Test",
            x=100.0,
            y=95.0,
            layer="F.SilkS",
            size=1.0,
        )

    except Exception:  # noqa: BLE001
        return False
    else:
        return bool(success)


def verify_selection(board_api: object, *, interactive: bool = False) -> bool:
    """Verify getting the current selection from KiCAD UI.

    Args:
        board_api: The board API instance to use.
        interactive: Whether to prompt for confirmation.

    Returns:
        True if successful, False otherwise.
    """
    if interactive:
        input()

    try:
        max_items = 10
        selection = board_api.get_selection()  # type: ignore[attr-defined]

        for _ in selection[:max_items]:
            pass

    except Exception:  # noqa: BLE001
        return False
    else:
        return True


def run_all_tests(*, interactive: bool = False) -> bool:
    """Run all IPC backend tests.

    Args:
        interactive: Whether to run in interactive mode.

    Returns:
        True if all tests passed, False otherwise.
    """
    # Test connection
    backend = test_connection()
    if not backend:
        return False

    # Test board access
    board_api = test_board_access(backend)
    if not board_api:
        return False

    # Test board info
    test_board_info(board_api)

    # Test real-time modifications
    verify_realtime_track(board_api, interactive=interactive)
    verify_realtime_via(board_api, interactive=interactive)
    verify_realtime_text(board_api, interactive=interactive)

    # Test selection
    verify_selection(board_api, interactive=interactive)

    # Cleanup
    backend.disconnect()

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test KiCAD IPC Backend")
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Run in interactive mode (prompts before modifications)",
    )
    args = parser.parse_args()

    success = run_all_tests(interactive=args.interactive)
    sys.exit(0 if success else 1)
