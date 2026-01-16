"""Export command implementations for KiCAD interface."""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
import platform
import shutil
import subprocess
from typing import TYPE_CHECKING, Any
import xml.etree.ElementTree as ET

import pcbnew

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger("kicad_interface")

# Constants
_SUBPROCESS_TIMEOUT_SECONDS = 60
_3D_EXPORT_TIMEOUT_SECONDS = 300
_VALID_LAYER_ID = 0


class ExportCommands:
    """Handles export-related KiCAD operations."""

    def __init__(self, board: pcbnew.BOARD | None = None) -> None:
        """Initialize with optional board instance.

        Args:
            board: Optional KiCAD board instance.
        """
        self.board = board

    def _setup_gerber_plotter(
        self, output_path: Path, params: dict[str, Any]
    ) -> pcbnew.PLOT_CONTROLLER:
        """Setup Gerber plot controller with options.

        Args:
            output_path: Output directory path
            params: Export parameters

        Returns:
            Configured plot controller
        """
        plotter = pcbnew.PLOT_CONTROLLER(self.board)
        plot_opts = plotter.GetPlotOptions()

        plot_opts.SetOutputDirectory(str(output_path))
        plot_opts.SetFormat(pcbnew.PLOT_FORMAT_GERBER)
        plot_opts.SetUseGerberProtelExtensions(params.get("useProtelExtensions", False))
        plot_opts.SetUseAuxOrigin(params.get("useAuxOrigin", False))
        plot_opts.SetCreateGerberJobFile(params.get("generateMapFile", False))
        plot_opts.SetSubtractMaskFromSilk(True)

        return plotter

    def _plot_gerber_layers(
        self, plotter: pcbnew.PLOT_CONTROLLER, layers: list[str]
    ) -> list[str]:
        """Plot Gerber layers.

        Args:
            plotter: Plot controller
            layers: List of layer names to plot (empty for all)

        Returns:
            List of plotted layer names
        """
        plotted_layers = []

        if layers:
            # Plot specific layers
            for layer_name in layers:
                layer_id = self.board.GetLayerID(layer_name)
                if layer_id >= _VALID_LAYER_ID:
                    plotter.SetLayer(layer_id)
                    plotter.PlotLayer()
                    plotted_layers.append(layer_name)
        else:
            # Plot all enabled layers
            for layer_id in range(pcbnew.PCB_LAYER_ID_COUNT):
                if self.board.IsLayerEnabled(layer_id):
                    layer_name = self.board.GetLayerName(layer_id)
                    plotter.SetLayer(layer_id)
                    plotter.PlotLayer()
                    plotted_layers.append(layer_name)

        return plotted_layers

    def export_gerber(self, params: dict[str, Any]) -> dict[str, Any]:
        """Export Gerber files.

        Args:
            params: Export parameters including outputDir, layers, etc.

        Returns:
            Dictionary with success status and exported file information.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            output_dir = params.get("outputDir")
            if not output_dir:
                return {
                    "success": False,
                    "message": "Missing output directory",
                    "errorDetails": "outputDir parameter is required",
                }

            # Create output directory
            output_path = Path(output_dir).expanduser().resolve()
            output_path.mkdir(parents=True, exist_ok=True)

            # Setup plotter and export
            plotter = self._setup_gerber_plotter(output_path, params)
            plotted_layers = self._plot_gerber_layers(plotter, params.get("layers", []))

            # Generate drill files if requested
            drill_files = []
            if params.get("generateDrillFiles", True):
                drill_files = self._generate_drill_files(output_path)

            return {
                "success": True,
                "message": "Exported Gerber files",
                "files": {
                    "gerber": plotted_layers,
                    "drill": drill_files,
                    "map": ["job.gbrjob"] if params.get("generateMapFile", False) else [],
                },
                "outputDir": str(output_path),
            }

        except (OSError, ValueError) as e:
            logger.exception("Error exporting Gerber files")
            return {
                "success": False,
                "message": "Failed to export Gerber files",
                "errorDetails": str(e),
            }
        except Exception as e:
            # Handle pcbnew.KiCadError and other exceptions
            logger.exception("Error exporting Gerber files")
            return {
                "success": False,
                "message": "Failed to export Gerber files",
                "errorDetails": str(e),
            }

    def _generate_drill_files(self, output_path: Path) -> list[str]:
        """Generate drill files using kicad-cli.

        Args:
            output_path: Output directory path.

        Returns:
            List of generated drill file names.
        """
        drill_files: list[str] = []

        if not self.board:
            return drill_files

        # KiCAD 9.0: Use kicad-cli for more reliable drill file generation
        # The Python API's EXCELLON_WRITER.SetOptions() signature changed
        board_file = self.board.GetFileName()
        kicad_cli = self._find_kicad_cli()

        if not kicad_cli or not board_file:
            logger.warning("kicad-cli not available for drill file generation")
            return drill_files

        board_path = Path(board_file)
        if not board_path.exists():
            logger.warning("Board file does not exist: %s", board_file)
            return drill_files

        # Generate drill files using kicad-cli
        cmd: list[str] = [
            kicad_cli,
            "pcb",
            "export",
            "drill",
            "--output",
            str(output_path),
            "--format",
            "excellon",
            "--drill-origin",
            "absolute",
            "--excellon-separate-th",  # Separate plated/non-plated
            str(board_path),
        ]

        try:
            result = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT_SECONDS,
                check=False,
            )
            if result.returncode == 0:
                # Get list of generated drill files - use list.extend for better performance
                drill_files.extend(
                    file_path.name
                    for file_path in output_path.iterdir()
                    if file_path.suffix in {".drl", ".cnc"}
                )
            else:
                logger.warning("Drill file generation failed: %s", result.stderr)
        except subprocess.TimeoutExpired:
            logger.warning("Drill file generation timed out")
        except OSError as drill_error:
            logger.warning("Could not generate drill files: %s", str(drill_error))

        return drill_files

    def export_pdf(self, params: dict[str, Any]) -> dict[str, Any]:
        """Export PDF files.

        Args:
            params: Export parameters including outputPath, layers, etc.

        Returns:
            Dictionary with success status and exported file information.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            output_path_str = params.get("outputPath")
            layers: list[str] = params.get("layers", [])
            black_and_white: bool = params.get("blackAndWhite", False)
            frame_reference: bool = params.get("frameReference", True)
            page_size: str = params.get("pageSize", "A4")

            if not output_path_str:
                return {
                    "success": False,
                    "message": "Missing output path",
                    "errorDetails": "outputPath parameter is required",
                }

            # Create output directory if it doesn't exist
            output_path = Path(output_path_str).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Create plot controller
            plotter = pcbnew.PLOT_CONTROLLER(self.board)

            # Set up plot options
            plot_opts = plotter.GetPlotOptions()
            plot_opts.SetOutputDirectory(str(output_path.parent))
            plot_opts.SetFormat(pcbnew.PLOT_FORMAT_PDF)
            plot_opts.SetPlotFrameRef(frame_reference)
            plot_opts.SetPlotValue(True)
            plot_opts.SetPlotReference(True)
            plot_opts.SetBlackAndWhite(black_and_white)

            # KiCAD 9.0 page size handling:
            # - SetPageSettings() was removed in KiCAD 9.0
            # - SetA4Output(bool) forces A4 page size when True
            # - For other sizes, KiCAD auto-scales to fit the board
            # - SetAutoScale(True) enables automatic scaling to fit page
            if page_size == "A4":
                plot_opts.SetA4Output(True)
            else:
                # For non-A4 sizes, disable A4 forcing and use auto-scale
                plot_opts.SetA4Output(False)
                plot_opts.SetAutoScale(True)
                # Note: KiCAD 9.0 doesn't support explicit page size selection
                # for formats other than A4. The PDF will auto-scale to fit.
                logger.warning(
                    "Page size '%s' requested, but KiCAD 9.0 only supports A4 explicitly. "
                    "Using auto-scale instead.",
                    page_size,
                )

            # Open plot for writing
            # Note: For PDF, all layers are combined into a single file
            # KiCAD prepends the board filename to the plot file name
            base_name = output_path.stem
            plotter.OpenPlotfile(base_name, pcbnew.PLOT_FORMAT_PDF, "")

            # Plot specified layers or all enabled layers
            plotted_layers = self._plot_layers(plotter, layers)

            # Close the plot file to finalize the PDF
            plotter.ClosePlot()

            # KiCAD automatically prepends the board name to the output file
            # Get the actual output filename that was created
            board_name = Path(self.board.GetFileName()).stem
            actual_filename = f"{board_name}-{base_name}.pdf"
            actual_output_path = output_path.parent / actual_filename

            return {
                "success": True,
                "message": "Exported PDF file",
                "file": {
                    "path": str(actual_output_path),
                    "requestedPath": str(output_path),
                    "layers": plotted_layers,
                    "pageSize": page_size if page_size == "A4" else "auto-scaled",
                },
            }

        except (OSError, ValueError) as e:
            logger.exception("Error exporting PDF file")
            return {
                "success": False,
                "message": "Failed to export PDF file",
                "errorDetails": str(e),
            }
        except Exception as e:
            # Handle pcbnew.KiCadError and other exceptions
            logger.exception("Error exporting PDF file")
            return {
                "success": False,
                "message": "Failed to export PDF file",
                "errorDetails": str(e),
            }

    def _plot_layers(
        self,
        plotter: pcbnew.PLOT_CONTROLLER,
        layers: list[str],
    ) -> list[str]:
        """Plot specified layers or all enabled layers.

        Args:
            plotter: KiCAD plot controller.
            layers: List of layer names to plot, or empty for all.

        Returns:
            List of plotted layer names.
        """
        if not self.board:
            return []

        plotted_layers: list[str] = []
        if layers:
            for layer_name in layers:
                layer_id = self.board.GetLayerID(layer_name)
                if layer_id >= _VALID_LAYER_ID:
                    plotter.SetLayer(layer_id)
                    plotter.PlotLayer()
                    plotted_layers.append(layer_name)
        else:
            for layer_id in range(pcbnew.PCB_LAYER_ID_COUNT):
                if self.board.IsLayerEnabled(layer_id):
                    layer_name = self.board.GetLayerName(layer_id)
                    plotter.SetLayer(layer_id)
                    plotter.PlotLayer()
                    plotted_layers.append(layer_name)
        return plotted_layers

    def export_svg(self, params: dict[str, Any]) -> dict[str, Any]:
        """Export SVG files.

        Args:
            params: Export parameters including outputPath, layers, etc.

        Returns:
            Dictionary with success status and exported file information.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            output_path_str = params.get("outputPath")
            layers: list[str] = params.get("layers", [])
            black_and_white: bool = params.get("blackAndWhite", False)
            include_components: bool = params.get("includeComponents", True)

            if not output_path_str:
                return {
                    "success": False,
                    "message": "Missing output path",
                    "errorDetails": "outputPath parameter is required",
                }

            # Create output directory if it doesn't exist
            output_path = Path(output_path_str).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Create plot controller
            plotter = pcbnew.PLOT_CONTROLLER(self.board)

            # Set up plot options
            plot_opts = plotter.GetPlotOptions()
            plot_opts.SetOutputDirectory(str(output_path.parent))
            plot_opts.SetFormat(pcbnew.PLOT_FORMAT_SVG)
            plot_opts.SetPlotValue(include_components)
            plot_opts.SetPlotReference(include_components)
            plot_opts.SetBlackAndWhite(black_and_white)

            # Plot specified layers or all enabled layers
            plotted_layers = self._plot_layers(plotter, layers)

            return {
                "success": True,
                "message": "Exported SVG file",
                "file": {"path": str(output_path), "layers": plotted_layers},
            }

        except (OSError, ValueError) as e:
            logger.exception("Error exporting SVG file")
            return {
                "success": False,
                "message": "Failed to export SVG file",
                "errorDetails": str(e),
            }
        except Exception as e:
            # Handle pcbnew.KiCadError and other exceptions
            logger.exception("Error exporting SVG file")
            return {
                "success": False,
                "message": "Failed to export SVG file",
                "errorDetails": str(e),
            }

    def export_3d(self, params: dict[str, Any]) -> dict[str, Any]:  # noqa: PLR0911
        """Export 3D model files using kicad-cli (KiCAD 9.0 compatible).

        Args:
            params: Export parameters including outputPath, format, etc.

        Returns:
            Dictionary with success status and exported file information.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            output_path_str = params.get("outputPath")
            output_format: str = params.get("format", "STEP")
            include_components: bool = params.get("includeComponents", True)
            include_copper: bool = params.get("includeCopper", True)
            include_solder_mask: bool = params.get("includeSolderMask", True)
            include_silkscreen: bool = params.get("includeSilkscreen", True)

            if not output_path_str:
                return {
                    "success": False,
                    "message": "Missing output path",
                    "errorDetails": "outputPath parameter is required",
                }

            # Get board file path
            board_file = self.board.GetFileName()
            if not board_file:
                return {
                    "success": False,
                    "message": "Board file not found",
                    "errorDetails": "Board must be saved before exporting 3D models",
                }

            board_path = Path(board_file)
            if not board_path.exists():
                return {
                    "success": False,
                    "message": "Board file not found",
                    "errorDetails": "Board must be saved before exporting 3D models",
                }

            # Create output directory if it doesn't exist
            output_path = Path(output_path_str).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Find kicad-cli executable
            kicad_cli = self._find_kicad_cli()
            if not kicad_cli:
                return {
                    "success": False,
                    "message": "kicad-cli not found",
                    "errorDetails": "KiCAD CLI tool not found. Install KiCAD 8.0+ or set PATH.",
                }

            # Build command based on format
            format_upper = output_format.upper()
            cmd = self._build_3d_export_command(
                kicad_cli=kicad_cli,
                output_path=output_path,
                board_path=board_path,
                output_format=format_upper,
                include_components=include_components,
                include_copper=include_copper,
                include_silkscreen=include_silkscreen,
                include_solder_mask=include_solder_mask,
            )

            if cmd is None:
                return {
                    "success": False,
                    "message": "Unsupported format",
                    "errorDetails": (
                        f"Format {output_format} is not supported. Use 'STEP' or 'VRML'."
                    ),
                }

            # Execute kicad-cli command
            logger.info("Running 3D export command: %s", " ".join(cmd))

            result = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                timeout=_3D_EXPORT_TIMEOUT_SECONDS,
                check=False,
            )

            if result.returncode != 0:
                logger.error("3D export command failed: %s", result.stderr)
                return {
                    "success": False,
                    "message": "3D export command failed",
                    "errorDetails": result.stderr,
                }

            return {
                "success": True,
                "message": f"Exported {format_upper} file",
                "file": {"path": str(output_path), "format": format_upper},
            }

        except subprocess.TimeoutExpired:
            logger.exception("3D export command timed out")
            return {
                "success": False,
                "message": "3D export timed out",
                "errorDetails": "Export took longer than 5 minutes",
            }
        except (OSError, ValueError) as e:
            logger.exception("Error exporting 3D model")
            return {
                "success": False,
                "message": "Failed to export 3D model",
                "errorDetails": str(e),
            }

    def _build_3d_export_command(
        self,
        *,
        kicad_cli: str,
        output_path: Path,
        board_path: Path,
        output_format: str,
        include_components: bool,
        include_copper: bool,
        include_silkscreen: bool,
        include_solder_mask: bool,
    ) -> list[str] | None:
        """Build the kicad-cli command for 3D export.

        Args:
            kicad_cli: Path to kicad-cli executable.
            output_path: Output file path.
            board_path: Board file path.
            output_format: Export format (STEP or VRML).
            include_components: Whether to include components.
            include_copper: Whether to include copper layers.
            include_silkscreen: Whether to include silkscreen.
            include_solder_mask: Whether to include solder mask.

        Returns:
            Command list or None if format is unsupported.
        """
        if output_format == "STEP":
            cmd: list[str] = [
                kicad_cli,
                "pcb",
                "export",
                "step",
                "--output",
                str(output_path),
                "--force",  # Overwrite existing file
            ]

            # Add options based on parameters
            if not include_components:
                cmd.append("--no-components")
            if include_copper:
                cmd.extend(["--include-tracks", "--include-pads", "--include-zones"])
            if include_silkscreen:
                cmd.append("--include-silkscreen")
            if include_solder_mask:
                cmd.append("--include-soldermask")

            cmd.append(str(board_path))
            return cmd

        if output_format == "VRML":
            cmd = [
                kicad_cli,
                "pcb",
                "export",
                "vrml",
                "--output",
                str(output_path),
                "--units",
                "mm",  # Use mm for consistency
                "--force",
            ]

            # Note: VRML export doesn't have a direct --no-components flag
            # The models will be included by default
            _ = include_components  # Acknowledge unused parameter

            cmd.append(str(board_path))
            return cmd

        return None

    def export_bom(self, params: dict[str, Any]) -> dict[str, Any]:
        """Export Bill of Materials.

        Args:
            params: Export parameters including outputPath, format, etc.

        Returns:
            Dictionary with success status and exported file information.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            output_path_str = params.get("outputPath")
            output_format: str = params.get("format", "CSV")
            group_by_value: bool = params.get("groupByValue", True)
            include_attributes: list[str] = params.get("includeAttributes", [])

            if not output_path_str:
                return {
                    "success": False,
                    "message": "Missing output path",
                    "errorDetails": "outputPath parameter is required",
                }

            # Create output directory if it doesn't exist
            output_path = Path(output_path_str).expanduser().resolve()
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Get all components
            components = self._get_components(include_attributes)

            # Group by value if requested
            if group_by_value:
                components = self._group_components_by_value(components)

            # Export based on format
            export_result = self._export_bom_by_format(
                output_path,
                components,
                output_format,
            )
            if export_result is not None:
                return export_result

            return {
                "success": True,
                "message": f"Exported BOM to {output_format}",
                "file": {
                    "path": str(output_path),
                    "format": output_format,
                    "componentCount": len(components),
                },
            }

        except (OSError, ValueError) as e:
            logger.exception("Error exporting BOM")
            return {
                "success": False,
                "message": "Failed to export BOM",
                "errorDetails": str(e),
            }

    def _get_components(
        self,
        include_attributes: Sequence[str],
    ) -> list[dict[str, Any]]:
        """Get all components from the board.

        Args:
            include_attributes: Additional attributes to include.

        Returns:
            List of component dictionaries.
        """
        if not self.board:
            return []

        components: list[dict[str, Any]] = []
        for module in self.board.GetFootprints():
            component: dict[str, Any] = {
                "reference": module.GetReference(),
                "value": module.GetValue(),
                "footprint": str(module.GetFPID()),
                "layer": self.board.GetLayerName(module.GetLayer()),
            }

            # Add requested attributes
            for attr in include_attributes:
                getter_name = f"Get{attr}"
                if hasattr(module, getter_name):
                    component[attr] = getattr(module, getter_name)()

            components.append(component)

        return components

    def _group_components_by_value(
        self,
        components: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Group components by value and footprint.

        Args:
            components: List of component dictionaries.

        Returns:
            Grouped list of components.
        """
        grouped: dict[str, dict[str, Any]] = {}
        for comp in components:
            key = f"{comp['value']}_{comp['footprint']}"
            if key not in grouped:
                grouped[key] = {
                    "value": comp["value"],
                    "footprint": comp["footprint"],
                    "quantity": 1,
                    "references": [comp["reference"]],
                }
            else:
                grouped[key]["quantity"] += 1
                grouped[key]["references"].append(comp["reference"])
        return list(grouped.values())

    def _export_bom_by_format(
        self,
        output_path: Path,
        components: list[dict[str, Any]],
        output_format: str,
    ) -> dict[str, Any] | None:
        """Export BOM in the specified format.

        Args:
            output_path: Output file path.
            components: List of component dictionaries.
            output_format: Export format (CSV, XML, HTML, JSON).

        Returns:
            Error dictionary if format is unsupported, None on success.
        """
        if output_format == "CSV":
            self._export_bom_csv(output_path, components)
        elif output_format == "XML":
            self._export_bom_xml(output_path, components)
        elif output_format == "HTML":
            self._export_bom_html(output_path, components)
        elif output_format == "JSON":
            self._export_bom_json(output_path, components)
        else:
            return {
                "success": False,
                "message": "Unsupported format",
                "errorDetails": f"Format {output_format} is not supported",
            }
        return None

    def _export_bom_csv(
        self,
        path: Path,
        components: list[dict[str, Any]],
    ) -> None:
        """Export BOM to CSV format.

        Args:
            path: Output file path.
            components: List of component dictionaries.
        """
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(components[0].keys()))
            writer.writeheader()
            writer.writerows(components)

    def _export_bom_xml(
        self,
        path: Path,
        components: list[dict[str, Any]],
    ) -> None:
        """Export BOM to XML format.

        Args:
            path: Output file path.
            components: List of component dictionaries.
        """
        root = ET.Element("bom")
        for comp in components:
            comp_elem = ET.SubElement(root, "component")
            for key, value in comp.items():
                elem = ET.SubElement(comp_elem, key)
                elem.text = str(value)
        tree = ET.ElementTree(root)
        tree.write(str(path), encoding="utf-8", xml_declaration=True)

    def _export_bom_html(
        self,
        path: Path,
        components: list[dict[str, Any]],
    ) -> None:
        """Export BOM to HTML format.

        Args:
            path: Output file path.
            components: List of component dictionaries.
        """
        html = ["<html><head><title>Bill of Materials</title></head><body>"]
        html.append("<table border='1'><tr>")
        # Headers - use list.extend for better performance
        html.extend(f"<th>{key}</th>" for key in components[0])
        html.append("</tr>")
        # Data - use list.extend for better performance
        for comp in components:
            html.append("<tr>")
            html.extend(f"<td>{value}</td>" for value in comp.values())
            html.append("</tr>")
        html.append("</table></body></html>")
        path.write_text("\n".join(html))

    def _export_bom_json(
        self,
        path: Path,
        components: list[dict[str, Any]],
    ) -> None:
        """Export BOM to JSON format.

        Args:
            path: Output file path.
            components: List of component dictionaries.
        """
        with path.open("w") as f:
            json.dump({"components": components}, f, indent=2)

    def _find_kicad_cli(self) -> str | None:
        """Find kicad-cli executable in system PATH or common locations.

        Returns:
            Path to kicad-cli executable, or None if not found.
        """
        # Try system PATH first
        cli_path = shutil.which("kicad-cli")
        if cli_path:
            return cli_path

        # Try platform-specific default locations
        system = platform.system()

        possible_paths: list[str]
        if system == "Windows":
            possible_paths = [
                r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
                r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe",
                r"C:\Program Files (x86)\KiCad\9.0\bin\kicad-cli.exe",
                r"C:\Program Files (x86)\KiCad\8.0\bin\kicad-cli.exe",
            ]
        elif system == "Darwin":  # macOS
            possible_paths = [
                "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
                "/usr/local/bin/kicad-cli",
            ]
        else:  # Linux
            possible_paths = [
                "/usr/bin/kicad-cli",
                "/usr/local/bin/kicad-cli",
            ]

        for path_str in possible_paths:
            path = Path(path_str)
            if path.exists():
                return str(path)

        return None
