"""Schematic management operations using kicad-skip library.

This module provides the SchematicManager class for creating, loading,
saving, and extracting metadata from KiCAD schematic files.
"""

import logging
from pathlib import Path
import shutil
from typing import Any

from skip import Schematic

logger = logging.getLogger("kicad_interface")


class SchematicManager:
    """Core schematic operations using kicad-skip."""

    @staticmethod
    def create_schematic(
        name: str,
        metadata: dict[str, Any] | None = None,  # noqa: ARG004
    ) -> Schematic:
        """Create a new empty schematic from template.

        Args:
            name: The name or path for the new schematic file.
            metadata: Optional metadata dictionary (currently unused).

        Returns:
            A loaded Schematic object.

        Raises:
            OSError: If the schematic file cannot be created or loaded.
        """
        # Determine template path (use template_with_symbols for component cloning)
        template_path = (
            Path(__file__).parent.resolve()
            / ".."
            / "templates"
            / "template_with_symbols.kicad_sch"
        )

        # Determine output path
        output_path = Path(name if name.endswith(".kicad_sch") else f"{name}.kicad_sch")

        if template_path.exists():
            # Copy template to target location
            shutil.copy(template_path, output_path)
            logger.info("Created schematic from template: %s", output_path)
        else:
            # Fallback: create minimal schematic
            logger.warning("Template not found at %s, creating minimal schematic", template_path)
            output_path.write_text(
                '(kicad_sch (version 20230121) (generator "KiCAD-MCP-Server")\n\n'
                "  (uuid 00000000-0000-0000-0000-000000000000)\n\n"
                '  (paper "A4")\n\n'
                "  (lib_symbols\n  )\n\n"
                '  (sheet_instances\n    (path "/" (page "1"))\n  )\n'
                ")\n"
            )

        # Load the schematic
        sch = Schematic(str(output_path))
        logger.info("Loaded new schematic: %s", output_path)
        return sch

    @staticmethod
    def load_schematic(file_path: str) -> Schematic | None:
        """Load an existing schematic.

        Args:
            file_path: Path to the schematic file to load.

        Returns:
            A Schematic object if successful, None otherwise.
        """
        path = Path(file_path)
        if not path.exists():
            logger.error("Schematic file not found at %s", file_path)
            return None
        try:
            sch = Schematic(file_path)
            logger.info("Loaded schematic from: %s", file_path)
        except OSError:
            logger.exception("Error loading schematic from %s", file_path)
            return None
        else:
            return sch

    @staticmethod
    def save_schematic(schematic: Schematic, file_path: str) -> bool:
        """Save a schematic to file.

        Args:
            schematic: The Schematic object to save.
            file_path: Path where the schematic will be saved.

        Returns:
            True if successful, False otherwise.
        """
        try:
            # kicad-skip uses write method, not save
            schematic.write(file_path)
            logger.info("Saved schematic to: %s", file_path)
        except OSError:
            logger.exception("Error saving schematic to %s", file_path)
            return False
        else:
            return True

    @staticmethod
    def get_schematic_metadata(schematic: Schematic) -> dict[str, Any]:
        """Extract metadata from schematic.

        Args:
            schematic: The Schematic object to extract metadata from.

        Returns:
            A dictionary containing version and generator information.
        """
        # kicad-skip doesn't expose a direct metadata object on Schematic.
        # We can return basic info like version and generator.
        result = {
            "version": schematic.version,
            "generator": schematic.generator,
        }
        logger.debug("Extracted schematic metadata")
        return result


if __name__ == "__main__":
    # Example Usage (for testing)
    new_sch = SchematicManager.create_schematic("MyTestSchematic")

    test_file = "test_schematic.kicad_sch"
    SchematicManager.save_schematic(new_sch, test_file)

    loaded_sch = SchematicManager.load_schematic(test_file)
    if loaded_sch:
        _metadata = SchematicManager.get_schematic_metadata(loaded_sch)

    # Clean up test file
    test_path = Path(test_file)
    if test_path.exists():
        test_path.unlink()
