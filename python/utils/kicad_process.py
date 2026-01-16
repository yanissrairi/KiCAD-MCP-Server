"""KiCAD Process Management Utilities.

Detects if KiCAD is running and provides auto-launch functionality.
"""

from __future__ import annotations

import ctypes
import logging
import platform
import shutil
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# Constants for process detection
_PROCESS_WAIT_ITERATIONS = 10
_PROCESS_WAIT_INTERVAL = 0.5
_SUBPROCESS_TIMEOUT = 5


class KiCADProcessManager:
    """Manages KiCAD process detection and launching."""

    @staticmethod
    def _windows_list_processes() -> list[dict[str, Any]]:
        """List running processes on Windows using Toolhelp API.

        Returns:
            List of process dictionaries with pid, name, and command keys.
        """
        processes: list[dict[str, Any]] = []
        try:
            th32cs_snapprocess = 0x00000002
            try:
                ulong_ptr = wintypes.ULONG_PTR  # type: ignore[attr-defined]
            except AttributeError:
                ulong_ptr = (
                    ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
                )

            class PROCESSENTRY32W(ctypes.Structure):
                """Windows process entry structure."""

                _fields_: Sequence[tuple[str, type[ctypes._CData]] | tuple[str, type[ctypes._CData], int]] = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ulong_ptr),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * wintypes.MAX_PATH),
                ]

            create_toolhelp32_snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot
            process32_first_w = ctypes.windll.kernel32.Process32FirstW
            process32_next_w = ctypes.windll.kernel32.Process32NextW
            close_handle = ctypes.windll.kernel32.CloseHandle

            snapshot = create_toolhelp32_snapshot(th32cs_snapprocess, 0)
            if snapshot == wintypes.HANDLE(-1).value:
                return processes

            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)

            if process32_first_w(snapshot, ctypes.byref(entry)):
                while True:
                    processes.append(
                        {
                            "pid": str(entry.th32ProcessID),
                            "name": entry.szExeFile,
                            "command": entry.szExeFile,
                        }
                    )
                    if not process32_next_w(snapshot, ctypes.byref(entry)):
                        break

            close_handle(snapshot)
        except Exception:
            logger.exception("Error listing Windows processes")

        return processes

    @staticmethod
    def is_running() -> bool:
        """Check if KiCAD is currently running.

        Returns:
            True if KiCAD process found, False otherwise.
        """
        system = platform.system()

        try:
            if system == "Linux":
                return KiCADProcessManager._check_linux_processes()

            if system == "Darwin":  # macOS
                return KiCADProcessManager._check_macos_processes()

            if system == "Windows":
                return KiCADProcessManager._check_windows_processes()

            logger.warning("Process detection not implemented for %s", system)
            return False

        except Exception:
            logger.exception("Error checking if KiCAD is running")
            return False

    @staticmethod
    def _check_linux_processes() -> bool:
        """Check for KiCAD processes on Linux.

        Returns:
            True if KiCAD process found, False otherwise.
        """
        pgrep_path = shutil.which("pgrep")
        ps_path = shutil.which("ps")

        if not pgrep_path:
            logger.warning("pgrep not found in PATH")
            return False

        # Check for actual pcbnew/kicad binaries (not python scripts)
        # Use exact process name matching to avoid matching our own kicad_interface.py
        # Security: pgrep_path from shutil.which() is trusted, args are hardcoded
        result = subprocess.run(  # noqa: S603
            [pgrep_path, "-x", "pcbnew|kicad"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return True

        # Also check with -f for full path matching, but exclude our script
        # Security: pgrep_path from shutil.which() is trusted, args are hardcoded
        result = subprocess.run(  # noqa: S603
            [pgrep_path, "-f", "/pcbnew|/kicad"],
            capture_output=True,
            text=True,
            check=False,
        )
        # Double-check it's not our own process
        if result.returncode == 0 and ps_path:
            pids = result.stdout.strip().split("\n")
            for pid in pids:
                # Security: Validate PID is numeric to prevent command injection
                if not pid.strip().isdigit():
                    continue
                try:
                    # Security: ps_path from shutil.which(), pid validated as numeric
                    cmdline = subprocess.run(  # noqa: S603
                        [ps_path, "-p", pid, "-o", "command="],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if "kicad_interface.py" not in cmdline.stdout:
                        return True
                except OSError:
                    pass
        return False

    @staticmethod
    def _check_macos_processes() -> bool:
        """Check for KiCAD processes on macOS.

        Returns:
            True if KiCAD process found, False otherwise.
        """
        pgrep_path = shutil.which("pgrep")
        if not pgrep_path:
            logger.warning("pgrep not found in PATH")
            return False

        # Security: pgrep_path from shutil.which() is trusted, args are hardcoded
        result = subprocess.run(  # noqa: S603
            [pgrep_path, "-f", "KiCad|pcbnew"],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    @staticmethod
    def _check_windows_processes() -> bool:
        """Check for KiCAD processes on Windows.

        Returns:
            True if KiCAD process found, False otherwise.
        """
        processes = KiCADProcessManager._windows_list_processes()
        for proc in processes:
            name = (proc.get("name") or "").lower()
            if name in ("pcbnew.exe", "kicad.exe"):
                return True
        return False

    @staticmethod
    def get_executable_path() -> Path | None:
        """Get path to KiCAD executable.

        Returns:
            Path to pcbnew/kicad executable, or None if not found.
        """
        system = platform.system()

        # Try to find executable in PATH first using shutil.which (secure)
        for cmd in ["pcbnew", "kicad"]:
            exe_path = shutil.which(cmd)
            if exe_path:
                logger.info("Found KiCAD executable: %s", exe_path)
                return Path(exe_path)

        # Platform-specific default paths
        candidates = KiCADProcessManager._get_platform_candidates(system)

        for path in candidates:
            if path.exists():
                logger.info("Found KiCAD executable: %s", path)
                return path

        logger.warning("Could not find KiCAD executable")
        return None

    @staticmethod
    def _get_platform_candidates(system: str) -> list[Path]:
        """Get platform-specific candidate paths for KiCAD executable.

        Args:
            system: The platform system name (Linux, Darwin, Windows).

        Returns:
            List of candidate paths to check.
        """
        if system == "Linux":
            return [
                Path("/usr/bin/pcbnew"),
                Path("/usr/local/bin/pcbnew"),
                Path("/usr/bin/kicad"),
            ]
        if system == "Darwin":  # macOS
            return [
                Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad"),
                Path("/Applications/KiCad/pcbnew.app/Contents/MacOS/pcbnew"),
            ]
        if system == "Windows":
            return [
                Path("C:/Program Files/KiCad/9.0/bin/pcbnew.exe"),
                Path("C:/Program Files/KiCad/8.0/bin/pcbnew.exe"),
                Path("C:/Program Files (x86)/KiCad/9.0/bin/pcbnew.exe"),
            ]
        return []

    @staticmethod
    def launch(project_path: Path | None = None, *, wait_for_start: bool = True) -> bool:
        """Launch KiCAD PCB Editor.

        Args:
            project_path: Optional path to .kicad_pcb file to open.
            wait_for_start: Wait for process to start before returning.

        Returns:
            True if launch successful, False otherwise.
        """
        try:
            # Check if already running
            if KiCADProcessManager.is_running():
                logger.info("KiCAD is already running")
                return True

            # Find executable
            exe_path = KiCADProcessManager.get_executable_path()
            if not exe_path:
                logger.error("Cannot launch KiCAD: executable not found")
                return False

            # Build command
            cmd = [str(exe_path)]
            if project_path:
                cmd.append(str(project_path))

            logger.info("Launching KiCAD: %s", " ".join(cmd))

            # Launch process in background
            system = platform.system()
            if system == "Windows":
                # Windows: Use CREATE_NEW_PROCESS_GROUP to detach
                subprocess.Popen(  # noqa: S603
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                # Unix: Use nohup or start in background
                subprocess.Popen(  # noqa: S603
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )

            # Wait for process to start
            if wait_for_start:
                logger.info("Waiting for KiCAD to start...")
                for _i in range(_PROCESS_WAIT_ITERATIONS):
                    time.sleep(_PROCESS_WAIT_INTERVAL)
                    if KiCADProcessManager.is_running():
                        logger.info("KiCAD started successfully")
                        return True

                logger.warning("KiCAD process not detected after launch")
                # Return True anyway, it might be starting
                return True

            return True

        except Exception:
            logger.exception("Error launching KiCAD")
            return False

    @staticmethod
    def get_process_info() -> list[dict[str, Any]]:
        """Get information about running KiCAD processes.

        Returns:
            List of process info dicts with pid, name, and command.
        """
        system = platform.system()
        processes: list[dict[str, Any]] = []

        try:
            if system in ("Linux", "Darwin"):
                processes = KiCADProcessManager._get_unix_process_info()
            elif system == "Windows":
                processes = KiCADProcessManager._get_windows_process_info()

        except Exception:
            logger.exception("Error getting process info")

        return processes

    @staticmethod
    def _get_unix_process_info() -> list[dict[str, Any]]:
        """Get KiCAD process info on Unix systems.

        Returns:
            List of process info dicts.
        """
        processes: list[dict[str, Any]] = []
        ps_path = shutil.which("ps")

        if not ps_path:
            logger.warning("ps not found in PATH")
            return processes

        # Security: ps_path from shutil.which() is trusted, args are hardcoded
        result = subprocess.run(  # noqa: S603
            [ps_path, "aux"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.split("\n"):
            # Only match actual KiCAD binaries, not our MCP server processes
            # Must have /pcbnew or /kicad in the path
            if (
                ("pcbnew" in line.lower() or "kicad" in line.lower())
                and "kicad_interface.py" not in line
                and "grep" not in line
                and ("/pcbnew" in line or "/kicad" in line or "KiCad.app" in line)
            ):
                parts = line.split()
                min_parts = 11
                if len(parts) >= min_parts:
                    processes.append(
                        {
                            "pid": parts[1],
                            "name": parts[10],
                            "command": " ".join(parts[10:]),
                        }
                    )
        return processes

    @staticmethod
    def _get_windows_process_info() -> list[dict[str, Any]]:
        """Get KiCAD process info on Windows.

        Returns:
            List of process info dicts.
        """
        processes: list[dict[str, Any]] = []
        for proc in KiCADProcessManager._windows_list_processes():
            name = (proc.get("name") or "").lower()
            if "pcbnew" in name or "kicad" in name:
                processes.append(proc)
        return processes


def check_and_launch_kicad(
    project_path: Path | None = None,
    *,
    auto_launch: bool = True,
) -> dict[str, Any]:
    """Check if KiCAD is running and optionally launch it.

    Args:
        project_path: Optional path to .kicad_pcb file to open.
        auto_launch: If True, launch KiCAD if not running.

    Returns:
        Dict with status information.
    """
    manager = KiCADProcessManager()

    is_running = manager.is_running()

    if is_running:
        processes = manager.get_process_info()
        return {
            "running": True,
            "launched": False,
            "processes": processes,
            "message": "KiCAD is already running",
        }

    if not auto_launch:
        return {
            "running": False,
            "launched": False,
            "processes": [],
            "message": "KiCAD is not running (auto-launch disabled)",
        }

    # Try to launch
    logger.info("KiCAD not detected, attempting to launch...")
    success = manager.launch(project_path)

    return {
        "running": success,
        "launched": success,
        "processes": manager.get_process_info() if success else [],
        "message": "KiCAD launched successfully" if success else "Failed to launch KiCAD",
        "project": str(project_path) if project_path else None,
    }
