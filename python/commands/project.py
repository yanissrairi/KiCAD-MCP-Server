"""Project-related command implementations for KiCAD interface."""

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pcbnew  # type: ignore[import-untyped]

logger = logging.getLogger("kicad_interface")


class ProjectCommands:
    """Handles project-related KiCAD operations."""

    def __init__(self, board: pcbnew.BOARD | None = None) -> None:
        """Initialize with optional board instance.

        Args:
            board: Optional KiCAD board instance.
        """
        self.board = board

    def create_project(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a new KiCAD project.

        Args:
            params: Dictionary containing project parameters including name, path, and template.

        Returns:
            Dictionary with success status and project information.
        """
        try:
            # Accept both 'name' (from MCP tool) and 'projectName' (legacy)
            project_name = params.get("name") or params.get("projectName", "New_Project")
            path = params.get("path", str(Path.cwd()))
            template = params.get("template")

            # Generate the full project path
            project_path = Path(path) / project_name
            if not str(project_path).endswith(".kicad_pro"):
                project_path = Path(f"{project_path}.kicad_pro")

            # Create project directory if it doesn't exist
            project_path.parent.mkdir(parents=True, exist_ok=True)

            # Create a new board
            board = pcbnew.BOARD()

            # Set project properties
            board.GetTitleBlock().SetTitle(project_name)

            # Set current date with proper parameter and timezone
            current_date = datetime.now(tz=UTC).strftime("%Y-%m-%d")
            board.GetTitleBlock().SetDate(current_date)

            # If template is specified, try to load it
            if template:
                template_path = Path(template).expanduser()
                if template_path.exists():
                    template_board = pcbnew.LoadBoard(str(template_path))
                    # Copy settings from template
                    board.SetDesignSettings(template_board.GetDesignSettings())
                    board.SetLayerStack(template_board.GetLayerStack())

            # Save the board
            board_path = Path(str(project_path).replace(".kicad_pro", ".kicad_pcb"))
            board.SetFileName(str(board_path))
            pcbnew.SaveBoard(str(board_path), board)

            # Create schematic from template (use expanded template with many component types)
            schematic_path = Path(str(project_path).replace(".kicad_pro", ".kicad_sch"))
            template_sch_path = (
                Path(__file__).parent / ".." / "templates" / "template_with_symbols_expanded.kicad_sch"
            ).resolve()

            if template_sch_path.exists():
                # Copy template schematic
                shutil.copy(template_sch_path, schematic_path)
                logger.info("Created schematic from template: %s", schematic_path)
            else:
                # Fallback: create minimal schematic
                logger.warning(
                    "Template not found at %s, creating minimal schematic",
                    template_sch_path,
                )
                schematic_path.write_text(
                    '(kicad_sch (version 20230121) (generator "KiCAD-MCP-Server")\n\n'
                    "  (uuid 00000000-0000-0000-0000-000000000000)\n\n"
                    '  (paper "A4")\n\n'
                    "  (lib_symbols\n  )\n\n"
                    '  (sheet_instances\n    (path "/" (page "1"))\n  )\n'
                    ")\n"
                )

            # Create project file with schematic reference
            project_content = (
                "{\n"
                '  "board": {\n'
                f'    "filename": "{board_path.name}"\n'
                "  },\n"
                '  "sheets": [\n'
                f'    ["root", "{schematic_path.name}"]\n'
                "  ]\n"
                "}\n"
            )
            project_path.write_text(project_content)

            self.board = board

            return {
                "success": True,
                "message": f"Created project: {project_name}",
                "project": {
                    "name": project_name,
                    "path": str(project_path),
                    "boardPath": str(board_path),
                    "schematicPath": str(schematic_path),
                },
            }

        except Exception as e:
            logger.exception("Error creating project")
            return {
                "success": False,
                "message": "Failed to create project",
                "errorDetails": str(e),
            }

    def open_project(self, params: dict[str, Any]) -> dict[str, Any]:
        """Open an existing KiCAD project.

        Args:
            params: Dictionary containing the filename parameter.

        Returns:
            Dictionary with success status and project information.
        """
        try:
            filename = params.get("filename")
            if not filename:
                return {
                    "success": False,
                    "message": "No filename provided",
                    "errorDetails": "The filename parameter is required",
                }

            # Expand user path and make absolute
            file_path = Path(filename).expanduser().resolve()

            # If it's a project file, get the board file
            if str(file_path).endswith(".kicad_pro"):
                board_path = Path(str(file_path).replace(".kicad_pro", ".kicad_pcb"))
            else:
                board_path = file_path

            # Load the board
            board = pcbnew.LoadBoard(str(board_path))
            self.board = board

            return {
                "success": True,
                "message": f"Opened project: {board_path.name}",
                "project": {
                    "name": board_path.stem,
                    "path": str(file_path),
                    "boardPath": str(board_path),
                },
            }

        except Exception as e:
            logger.exception("Error opening project")
            return {
                "success": False,
                "message": "Failed to open project",
                "errorDetails": str(e),
            }

    def save_project(self, params: dict[str, Any]) -> dict[str, Any]:
        """Save the current KiCAD project.

        Args:
            params: Dictionary containing optional filename parameter.

        Returns:
            Dictionary with success status and project information.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            filename = params.get("filename")
            if filename:
                # Save to new location
                file_path = Path(filename).expanduser().resolve()
                self.board.SetFileName(str(file_path))

            # Save the board
            pcbnew.SaveBoard(self.board.GetFileName(), self.board)

            board_file = Path(self.board.GetFileName())
            return {
                "success": True,
                "message": f"Saved project to: {self.board.GetFileName()}",
                "project": {
                    "name": board_file.stem,
                    "path": self.board.GetFileName(),
                },
            }

        except Exception as e:
            logger.exception("Error saving project")
            return {
                "success": False,
                "message": "Failed to save project",
                "errorDetails": str(e),
            }

    def get_project_info(self, params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        """Get information about the current project.

        Args:
            params: Dictionary of parameters (unused but required for interface consistency).

        Returns:
            Dictionary with success status and project information.
        """
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            title_block = self.board.GetTitleBlock()
            filename = self.board.GetFileName()
            file_path = Path(filename)

            return {
                "success": True,
                "project": {
                    "name": file_path.stem,
                    "path": filename,
                    "title": title_block.GetTitle(),
                    "date": title_block.GetDate(),
                    "revision": title_block.GetRevision(),
                    "company": title_block.GetCompany(),
                    "comment1": title_block.GetComment(0),
                    "comment2": title_block.GetComment(1),
                    "comment3": title_block.GetComment(2),
                    "comment4": title_block.GetComment(3),
                },
            }

        except Exception as e:
            logger.exception("Error getting project info")
            return {
                "success": False,
                "message": "Failed to get project information",
                "errorDetails": str(e),
            }
