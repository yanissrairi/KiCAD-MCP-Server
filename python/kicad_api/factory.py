"""Backend factory for creating appropriate KiCAD API backend.

Auto-detects available backends and provides fallback mechanism.
"""

import logging
import os

from kicad_api.base import APINotAvailableError, KiCADBackend

logger = logging.getLogger(__name__)


def create_backend(backend_type: str | None = None) -> KiCADBackend:
    """Create appropriate KiCAD backend.

    Args:
        backend_type: Backend to use:
            - 'ipc': Use IPC API (recommended)
            - 'swig': Use legacy SWIG bindings
            - None or 'auto': Auto-detect (try IPC first, fall back to SWIG)

    Returns:
        KiCADBackend instance

    Raises:
        APINotAvailableError: If no backend is available

    Environment Variables:
        KICAD_BACKEND: Override backend selection ('ipc', 'swig', or 'auto')
    """
    # Check environment variable override
    if backend_type is None:
        backend_type = os.environ.get("KICAD_BACKEND", "auto").lower()

    logger.info("Requested backend: %s", backend_type)

    # Try specific backend if requested
    if backend_type == "ipc":
        return _create_ipc_backend()
    if backend_type == "swig":
        return _create_swig_backend()
    if backend_type == "auto":
        return _auto_detect_backend()
    msg = f"Unknown backend type: {backend_type}"
    raise ValueError(msg)


def _create_ipc_backend() -> KiCADBackend:
    """Create IPC backend.

    Returns:
        IPCBackend instance

    Raises:
        APINotAvailableError: If kicad-python not available
    """
    try:
        from kicad_api.ipc_backend import IPCBackend

        logger.info("Creating IPC backend")
        return IPCBackend()
    except ImportError as e:
        logger.exception("IPC backend not available: %s", e)
        msg = "IPC backend requires 'kicad-python' package. Install with: pip install kicad-python"
        raise APINotAvailableError(
            msg
        ) from e


def _create_swig_backend() -> KiCADBackend:
    """Create SWIG backend.

    Returns:
        SWIGBackend instance

    Raises:
        APINotAvailableError: If pcbnew not available
    """
    try:
        from kicad_api.swig_backend import SWIGBackend

        logger.info("Creating SWIG backend")
        logger.warning(
            "SWIG backend is DEPRECATED and will be removed in KiCAD 10.0. "
            "Please migrate to IPC backend."
        )
        return SWIGBackend()
    except ImportError as e:
        logger.exception("SWIG backend not available: %s", e)
        msg = "SWIG backend requires 'pcbnew' module. Ensure KiCAD Python module is in PYTHONPATH."
        raise APINotAvailableError(
            msg
        ) from e


def _auto_detect_backend() -> KiCADBackend:
    """Auto-detect best available backend.

    Priority:
        1. IPC API (if kicad-python available and KiCAD running)
        2. SWIG API (if pcbnew available)

    Returns:
        Best available KiCADBackend

    Raises:
        APINotAvailableError: If no backend available
    """
    logger.info("Auto-detecting available KiCAD backend...")

    # Try IPC first (preferred)
    try:
        backend = _create_ipc_backend()
        # Test connection
        if backend.connect():
            logger.info("✓ IPC backend available and connected")
            return backend
        logger.warning("IPC backend available but connection failed")
    except (ImportError, APINotAvailableError) as e:
        logger.debug("IPC backend not available: %s", e)

    # Fall back to SWIG
    try:
        backend = _create_swig_backend()
        logger.warning(
            "Using deprecated SWIG backend. For best results, use IPC API with KiCAD running."
        )
        return backend
    except (ImportError, APINotAvailableError) as e:
        logger.exception("SWIG backend not available: %s", e)

    # No backend available
    msg = (
        "No KiCAD backend available. Please install either:\n"
        "  - kicad-python (recommended): pip install kicad-python\n"
        "  - Ensure KiCAD Python module (pcbnew) is in PYTHONPATH"
    )
    raise APINotAvailableError(
        msg
    )


def get_available_backends() -> dict:
    """Check which backends are available.

    Returns:
        Dictionary with backend availability:
            {
                'ipc': {'available': bool, 'version': str or None},
                'swig': {'available': bool, 'version': str or None}
            }
    """
    results = {}

    # Check IPC (kicad-python uses 'kipy' module name)
    try:
        import kipy

        results["ipc"] = {"available": True, "version": getattr(kipy, "__version__", "unknown")}
    except ImportError:
        results["ipc"] = {"available": False, "version": None}

    # Check SWIG
    try:
        import pcbnew

        results["swig"] = {"available": True, "version": pcbnew.GetBuildVersion()}
    except ImportError:
        results["swig"] = {"available": False, "version": None}

    return results


if __name__ == "__main__":
    # Quick diagnostic
    import sys

    print("Checking available KiCAD backends...")  # noqa: T201
    available = get_available_backends()
    for name, info in available.items():
        status = "available" if info["available"] else "not available"
        version = info.get("version", "unknown")
        print(f"  {name}: {status} (version: {version})")  # noqa: T201

    try:
        backend = create_backend()
        if backend.connect():
            print("Successfully connected to backend")  # noqa: T201
        else:
            print("Backend created but connection failed")  # noqa: T201
    except Exception as e:  # noqa: BLE001
        print(f"Failed to create backend: {e}")  # noqa: T201
        sys.exit(1)
