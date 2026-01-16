"""Design rules command implementations for KiCAD interface."""

from collections.abc import Sequence
import json
import logging
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
from typing import Any

import pcbnew

logger = logging.getLogger("kicad_interface")

# Constants for unit conversion
MM_TO_NM_SCALE = 1_000_000  # millimeters to nanometers
NM_TO_MM_SCALE = 1_000_000  # nanometers to millimeters (divisor)

# Timeout constants (in seconds)
DRC_TIMEOUT_SECONDS = 600  # 10 minutes for large boards


class DesignRuleCommands:
    """Handles design rule checking and configuration."""

    def __init__(self, board: pcbnew.BOARD | None = None) -> None:
        """Initialize with optional board instance.

        Args:
            board: Optional KiCAD board instance.
        """
        self.board = board

    def _apply_design_rule_params(
        self, design_settings: Any, params: dict[str, Any], scale: float
    ) -> None:
        """Apply design rule parameters to design settings.

        Args:
            design_settings: KiCAD design settings object.
            params: Dictionary of parameters to apply.
            scale: Scale factor for unit conversion (mm to nm).
        """
        # Define property mappings: param_key -> (setter_method/property_name, is_method)
        property_map = {
            "clearance": ("m_MinClearance", False),
            "microViaDiameter": ("m_MicroViasMinSize", False),
            "microViaDrill": ("m_MicroViasMinDrill", False),
            "minTrackWidth": ("m_TrackMinWidth", False),
            "minViaDiameter": ("m_ViasMinSize", False),
            "minViaDrill": ("m_MinThroughDrill", False),
            "minMicroViaDiameter": ("m_MicroViasMinSize", False),
            "minMicroViaDrill": ("m_MicroViasMinDrill", False),
            "minHoleDiameter": ("m_MinThroughDrill", False),
            "holeClearance": ("m_HoleClearance", False),
            "holeToHoleMin": ("m_HoleToHoleMin", False),
        }

        # Apply properties
        for param_key, (prop_name, _is_method) in property_map.items():
            if param_key in params:
                setattr(design_settings, prop_name, int(params[param_key] * scale))

        # Handle custom track/via values (KiCAD 9.0 API)
        custom_values_set = False
        if "trackWidth" in params:
            design_settings.SetCustomTrackWidth(int(params["trackWidth"] * scale))
            custom_values_set = True
        if "viaDiameter" in params:
            design_settings.SetCustomViaSize(int(params["viaDiameter"] * scale))
            custom_values_set = True
        if "viaDrill" in params:
            design_settings.SetCustomViaDrill(int(params["viaDrill"] * scale))
            custom_values_set = True

        # Activate custom track/via values
        if custom_values_set:
            design_settings.UseCustomTrackViaSize(value=True)

    def set_design_rules(self, params: dict[str, Any]) -> dict[str, Any]:
        """Set design rules for the PCB.

        Args:
            params: Dictionary containing design rule parameters.

        Returns:
            Dictionary with success status and applied rules.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            design_settings = self.board.GetDesignSettings()
            scale = MM_TO_NM_SCALE

            # Apply design rule parameters using mapping
            self._apply_design_rule_params(design_settings, params, scale)

            # Build response with KiCAD 9.0 compatible properties
            # After UseCustomTrackViaSize(True), GetCurrent* returns the custom values
            response_rules = {
                "clearance": design_settings.m_MinClearance / scale,
                "trackWidth": design_settings.GetCurrentTrackWidth() / scale,
                "viaDiameter": design_settings.GetCurrentViaSize() / scale,
                "viaDrill": design_settings.GetCurrentViaDrill() / scale,
                "microViaDiameter": design_settings.m_MicroViasMinSize / scale,
                "microViaDrill": design_settings.m_MicroViasMinDrill / scale,
                "minTrackWidth": design_settings.m_TrackMinWidth / scale,
                "minViaDiameter": design_settings.m_ViasMinSize / scale,
                "minThroughDrill": design_settings.m_MinThroughDrill / scale,
                "minMicroViaDiameter": design_settings.m_MicroViasMinSize / scale,
                "minMicroViaDrill": design_settings.m_MicroViasMinDrill / scale,
                "holeClearance": design_settings.m_HoleClearance / scale,
                "holeToHoleMin": design_settings.m_HoleToHoleMin / scale,
                "viasMinAnnularWidth": design_settings.m_ViasMinAnnularWidth / scale,
            }

            return {"success": True, "message": "Updated design rules", "rules": response_rules}

        except Exception as exc:
            logger.exception("Error setting design rules: %s", exc)
            return {
                "success": False,
                "message": "Failed to set design rules",
                "errorDetails": str(exc),
            }

    def get_design_rules(self, params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        """Get current design rules - KiCAD 9.0 compatible.

        Args:
            params: Dictionary of parameters (unused but required for interface).

        Returns:
            Dictionary with success status and current design rules.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            design_settings = self.board.GetDesignSettings()
            scale = NM_TO_MM_SCALE

            # Build rules dict with KiCAD 9.0 compatible properties
            rules = {
                # Core clearance and track settings
                "clearance": design_settings.m_MinClearance / scale,
                "trackWidth": design_settings.GetCurrentTrackWidth() / scale,
                "minTrackWidth": design_settings.m_TrackMinWidth / scale,
                # Via settings (current values from methods)
                "viaDiameter": design_settings.GetCurrentViaSize() / scale,
                "viaDrill": design_settings.GetCurrentViaDrill() / scale,
                # Via minimum values
                "minViaDiameter": design_settings.m_ViasMinSize / scale,
                "viasMinAnnularWidth": design_settings.m_ViasMinAnnularWidth / scale,
                # Micro via settings
                "microViaDiameter": design_settings.m_MicroViasMinSize / scale,
                "microViaDrill": design_settings.m_MicroViasMinDrill / scale,
                "minMicroViaDiameter": design_settings.m_MicroViasMinSize / scale,
                "minMicroViaDrill": design_settings.m_MicroViasMinDrill / scale,
                # KiCAD 9.0: Hole and drill settings (replaces removed m_ViasMinDrill and
                # m_MinHoleDiameter)
                "minThroughDrill": design_settings.m_MinThroughDrill / scale,
                "holeClearance": design_settings.m_HoleClearance / scale,
                "holeToHoleMin": design_settings.m_HoleToHoleMin / scale,
                # Other constraints
                "copperEdgeClearance": design_settings.m_CopperEdgeClearance / scale,
                "silkClearance": design_settings.m_SilkClearance / scale,
            }

            return {"success": True, "rules": rules}

        except Exception as exc:
            logger.exception("Error getting design rules: %s", exc)
            return {
                "success": False,
                "message": "Failed to get design rules",
                "errorDetails": str(exc),
            }

    def run_drc(self, params: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0911
        """Run Design Rule Check using kicad-cli.

        Args:
            params: Dictionary containing optional reportPath parameter.

        Returns:
            Dictionary with DRC results summary and violations file path.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            report_path = params.get("reportPath")

            # Get the board file path
            board_file = self.board.GetFileName()
            if not board_file or not Path(board_file).exists():
                return {
                    "success": False,
                    "message": "Board file not found",
                    "errorDetails": "Cannot run DRC without a saved board file",
                }

            # Find kicad-cli executable
            kicad_cli = self._find_kicad_cli()
            if not kicad_cli:
                return {
                    "success": False,
                    "message": "kicad-cli not found",
                    "errorDetails": (
                        "KiCAD CLI tool not found in system. Install KiCAD 8.0+ or set PATH."
                    ),
                }

            # Create temporary JSON output file
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
                json_output = tmp.name

            try:
                # Build command
                cmd: list[str] = [
                    kicad_cli,
                    "pcb",
                    "drc",
                    "--format",
                    "json",
                    "--output",
                    json_output,
                    "--units",
                    "mm",
                    board_file,
                ]

                logger.info("Running DRC command: %s", " ".join(cmd))

                # Run DRC
                result = subprocess.run(  # noqa: S603
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=DRC_TIMEOUT_SECONDS,
                    check=False,
                )

                if result.returncode != 0:
                    logger.error("DRC command failed: %s", result.stderr)
                    return {
                        "success": False,
                        "message": "DRC command failed",
                        "errorDetails": result.stderr,
                    }

                # Read JSON output
                with Path(json_output).open(encoding="utf-8") as f:
                    drc_data = json.load(f)

                # Parse violations from kicad-cli output
                violations = self._parse_drc_violations(drc_data)
                violation_counts = self._count_violations_by_type(violations)
                severity_counts = self._count_violations_by_severity(violations)

                # Determine where to save the violations file
                board_path = Path(board_file)
                board_dir = board_path.parent
                board_name = board_path.stem
                violations_file = str(board_dir / f"{board_name}_drc_violations.json")

                # Always save violations to JSON file (for large result sets)
                self._save_violations_file(
                    violations_file=violations_file,
                    board_file=board_file,
                    drc_data=drc_data,
                    violations=violations,
                    violation_counts=violation_counts,
                    severity_counts=severity_counts,
                )

                # Save text report if requested
                final_report_path: str | None = None
                if report_path:
                    final_report_path = self._save_text_report(
                        kicad_cli=kicad_cli,
                        report_path=report_path,
                        board_file=board_file,
                    )

                # Return summary only (not full violations list)
                return {
                    "success": True,
                    "message": f"Found {len(violations)} DRC violations",
                    "summary": {
                        "total": len(violations),
                        "by_severity": severity_counts,
                        "by_type": violation_counts,
                    },
                    "violationsFile": violations_file,
                    "reportPath": final_report_path,
                }

            finally:
                # Clean up temp JSON file
                if Path(json_output).exists():
                    Path(json_output).unlink()

        except subprocess.TimeoutExpired:
            logger.exception("DRC command timed out")
            return {
                "success": False,
                "message": "DRC command timed out",
                "errorDetails": f"Command took longer than {DRC_TIMEOUT_SECONDS} seconds",
            }
        except Exception as exc:
            logger.exception("Error running DRC: %s", exc)
            return {"success": False, "message": "Failed to run DRC", "errorDetails": str(exc)}

    def _parse_drc_violations(
        self,
        drc_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Parse DRC violations from kicad-cli output.

        Args:
            drc_data: Raw DRC data from kicad-cli JSON output.

        Returns:
            List of parsed violation dictionaries.
        """
        violations: list[dict[str, Any]] = []
        for violation in drc_data.get("violations", []):
            vtype = violation.get("type", "unknown")
            vseverity = violation.get("severity", "error")

            violations.append(
                {
                    "type": vtype,
                    "severity": vseverity,
                    "message": violation.get("description", ""),
                    "location": {
                        "x": violation.get("x", 0),
                        "y": violation.get("y", 0),
                        "unit": "mm",
                    },
                }
            )
        return violations

    def _count_violations_by_type(
        self,
        violations: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Count violations by type.

        Args:
            violations: List of violation dictionaries.

        Returns:
            Dictionary mapping violation types to counts.
        """
        violation_counts: dict[str, int] = {}
        for violation in violations:
            vtype = violation["type"]
            violation_counts[vtype] = violation_counts.get(vtype, 0) + 1
        return violation_counts

    def _count_violations_by_severity(
        self,
        violations: list[dict[str, Any]],
    ) -> dict[str, int]:
        """Count violations by severity.

        Args:
            violations: List of violation dictionaries.

        Returns:
            Dictionary mapping severity levels to counts.
        """
        severity_counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
        for violation in violations:
            vseverity = violation["severity"]
            if vseverity in severity_counts:
                severity_counts[vseverity] += 1
        return severity_counts

    def _save_violations_file(
        self,
        *,
        violations_file: str,
        board_file: str,
        drc_data: dict[str, Any],
        violations: list[dict[str, Any]],
        violation_counts: dict[str, int],
        severity_counts: dict[str, int],
    ) -> None:
        """Save violations to JSON file.

        Args:
            violations_file: Path to save violations file.
            board_file: Path to the board file.
            drc_data: Raw DRC data containing timestamp.
            violations: List of parsed violations.
            violation_counts: Violations counted by type.
            severity_counts: Violations counted by severity.
        """
        with Path(violations_file).open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "board": board_file,
                    "timestamp": drc_data.get("date", "unknown"),
                    "total_violations": len(violations),
                    "violation_counts": violation_counts,
                    "severity_counts": severity_counts,
                    "violations": violations,
                },
                f,
                indent=2,
            )

    def _save_text_report(
        self,
        *,
        kicad_cli: str,
        report_path: str,
        board_file: str,
    ) -> str:
        """Save DRC text report using kicad-cli.

        Args:
            kicad_cli: Path to kicad-cli executable.
            report_path: User-specified report path.
            board_file: Path to the board file.

        Returns:
            Absolute path to the saved report.
        """
        abs_report_path = str(Path(report_path).expanduser().resolve())
        cmd_report: list[str] = [
            kicad_cli,
            "pcb",
            "drc",
            "--format",
            "report",
            "--output",
            abs_report_path,
            "--units",
            "mm",
            board_file,
        ]
        subprocess.run(  # noqa: S603
            cmd_report,
            capture_output=True,
            timeout=DRC_TIMEOUT_SECONDS,
            check=False,
        )
        return abs_report_path

    def _find_kicad_cli(self) -> str | None:
        """Find kicad-cli executable.

        Returns:
            Path to kicad-cli executable, or None if not found.
        """
        # Try system PATH first
        cli_name = "kicad-cli.exe" if platform.system() == "Windows" else "kicad-cli"
        cli_path = shutil.which(cli_name)
        if cli_path:
            return cli_path

        # Try common installation paths (version-specific)
        common_paths = self._get_platform_cli_paths()
        for path in common_paths:
            if Path(path).exists():
                return path

        return None

    def _get_platform_cli_paths(self) -> Sequence[str]:
        """Get platform-specific paths for kicad-cli.

        Returns:
            Sequence of paths to check for kicad-cli executable.
        """
        system = platform.system()
        if system == "Windows":
            return [
                r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
                r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
                r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe",
                r"C:\Program Files (x86)\KiCad\10.0\bin\kicad-cli.exe",
                r"C:\Program Files (x86)\KiCad\9.0\bin\kicad-cli.exe",
                r"C:\Program Files (x86)\KiCad\8.0\bin\kicad-cli.exe",
                r"C:\Program Files\KiCad\bin\kicad-cli.exe",
            ]
        if system == "Darwin":  # macOS
            return [
                "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
                "/usr/local/bin/kicad-cli",
            ]
        # Linux
        return [
            "/usr/bin/kicad-cli",
            "/usr/local/bin/kicad-cli",
        ]

    def get_drc_violations(self, params: dict[str, Any]) -> dict[str, Any]:
        """Get list of DRC violations.

        Args:
            params: Dictionary containing optional severity filter.

        Returns:
            Dictionary with success status and list of violations.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            severity = params.get("severity", "all")

            # Get DRC markers
            violations: list[dict[str, Any]] = []
            for marker in self.board.GetDRCMarkers():
                violation: dict[str, Any] = {
                    "type": marker.GetErrorCode(),
                    "severity": "error",  # KiCAD DRC markers are always errors
                    "message": marker.GetDescription(),
                    "location": {
                        "x": marker.GetPos().x / NM_TO_MM_SCALE,
                        "y": marker.GetPos().y / NM_TO_MM_SCALE,
                        "unit": "mm",
                    },
                }

                # Filter by severity if specified
                if severity == "all" or severity == violation["severity"]:
                    violations.append(violation)

            return {"success": True, "violations": violations}

        except Exception as exc:
            logger.exception("Error getting DRC violations: %s", exc)
            return {
                "success": False,
                "message": "Failed to get DRC violations",
                "errorDetails": str(exc),
            }
