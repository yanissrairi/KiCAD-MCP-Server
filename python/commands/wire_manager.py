"""Wire Manager for KiCad Schematics.

Handles wire creation using S-expression manipulation, similar to dynamic symbol loading.
kicad-skip's wire API doesn't support creating wires with standard parameters, so we
manipulate the .kicad_sch file directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import uuid

import sexpdata
from sexpdata import Symbol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

logger = logging.getLogger("kicad_interface")


class WireManager:
    """Manage wires in KiCad schematics using S-expression manipulation."""

    @staticmethod
    def add_wire(
        schematic_path: Path,
        start_point: Sequence[float],
        end_point: Sequence[float],
        stroke_width: float = 0,
        stroke_type: str = "default",
    ) -> bool:
        """Add a wire to the schematic using S-expression manipulation.

        Args:
            schematic_path: Path to .kicad_sch file
            start_point: [x, y] coordinates for wire start
            end_point: [x, y] coordinates for wire end
            stroke_width: Wire width (default 0 for standard)
            stroke_type: Stroke type (default, solid, dashed, etc.)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Read schematic
            with schematic_path.open(encoding="utf-8") as f:
                sch_content = f.read()

            sch_data = sexpdata.loads(sch_content)

            # Create wire S-expression
            # Format: (wire (pts (xy x1 y1) (xy x2 y2)) (stroke (width N) (type default)) (uuid ...))
            wire_sexp = [
                Symbol("wire"),
                [
                    Symbol("pts"),
                    [Symbol("xy"), start_point[0], start_point[1]],
                    [Symbol("xy"), end_point[0], end_point[1]],
                ],
                [
                    Symbol("stroke"),
                    [Symbol("width"), stroke_width],
                    [Symbol("type"), Symbol(stroke_type)],
                ],
                [Symbol("uuid"), str(uuid.uuid4())],
            ]

            # Find insertion point (before sheet_instances)
            sheet_instances_index = None
            for i, item in enumerate(sch_data):
                if (
                    isinstance(item, list)
                    and len(item) > 0
                    and item[0] == Symbol("sheet_instances")
                ):
                    sheet_instances_index = i
                    break

            if sheet_instances_index is None:
                logger.error("No sheet_instances section found in schematic")
                return False

            # Insert wire before sheet_instances
            sch_data.insert(sheet_instances_index, wire_sexp)
            logger.info("Injected wire from %s to %s", start_point, end_point)

            # Write back
            with schematic_path.open("w", encoding="utf-8") as f:
                output = sexpdata.dumps(sch_data)
                f.write(output)

            logger.info("Successfully added wire to %s", schematic_path.name)
            return True

        except Exception:
            logger.exception("Error adding wire")
            return False

    @staticmethod
    def add_polyline_wire(
        schematic_path: Path,
        points: Sequence[Sequence[float]],
        stroke_width: float = 0,
        stroke_type: str = "default",
    ) -> bool:
        """Add a multi-segment wire (polyline) to the schematic.

        Args:
            schematic_path: Path to .kicad_sch file
            points: List of [x, y] coordinates for each point in the path
            stroke_width: Wire width
            stroke_type: Stroke type

        Returns:
            True if successful, False otherwise
        """
        try:
            if len(points) < 2:  # noqa: PLR2004
                logger.error("Polyline requires at least 2 points")
                return False

            # Read schematic
            with schematic_path.open(encoding="utf-8") as f:
                sch_content = f.read()

            sch_data = sexpdata.loads(sch_content)

            # Create pts list
            pts_list: list[Symbol | list[Symbol | float]] = [Symbol("pts")]
            for point in points:
                pts_list.append([Symbol("xy"), point[0], point[1]])

            # Create wire S-expression with multiple points
            wire_sexp = [
                Symbol("wire"),
                pts_list,
                [
                    Symbol("stroke"),
                    [Symbol("width"), stroke_width],
                    [Symbol("type"), Symbol(stroke_type)],
                ],
                [Symbol("uuid"), str(uuid.uuid4())],
            ]

            # Find insertion point
            sheet_instances_index = None
            for i, item in enumerate(sch_data):
                if (
                    isinstance(item, list)
                    and len(item) > 0
                    and item[0] == Symbol("sheet_instances")
                ):
                    sheet_instances_index = i
                    break

            if sheet_instances_index is None:
                logger.error("No sheet_instances section found in schematic")
                return False

            # Insert wire
            sch_data.insert(sheet_instances_index, wire_sexp)
            logger.info("Injected polyline wire with %d points", len(points))

            # Write back
            with schematic_path.open("w", encoding="utf-8") as f:
                output = sexpdata.dumps(sch_data)
                f.write(output)

            logger.info("Successfully added polyline wire to %s", schematic_path.name)
            return True

        except Exception:
            logger.exception("Error adding polyline wire")
            return False

    @staticmethod
    def add_label(
        schematic_path: Path,
        text: str,
        position: Sequence[float],
        label_type: str = "label",
        orientation: int = 0,
    ) -> bool:
        """Add a net label to the schematic.

        Args:
            schematic_path: Path to .kicad_sch file
            text: Label text (net name)
            position: [x, y] coordinates for label
            label_type: Type of label ('label', 'global_label', 'hierarchical_label')
            orientation: Rotation angle (0, 90, 180, 270)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Read schematic
            with schematic_path.open(encoding="utf-8") as f:
                sch_content = f.read()

            sch_data = sexpdata.loads(sch_content)

            # Create label S-expression
            # Format: (label "TEXT" (at x y angle) (effects (font (size 1.27 1.27))))
            label_sexp = [
                Symbol(label_type),
                text,
                [Symbol("at"), position[0], position[1], orientation],
                [Symbol("fields_autoplaced"), Symbol("yes")],
                [
                    Symbol("effects"),
                    [Symbol("font"), [Symbol("size"), 1.27, 1.27]],
                    [Symbol("justify"), Symbol("left"), Symbol("bottom")],
                ],
                [Symbol("uuid"), str(uuid.uuid4())],
            ]

            # Find insertion point
            sheet_instances_index = None
            for i, item in enumerate(sch_data):
                if (
                    isinstance(item, list)
                    and len(item) > 0
                    and item[0] == Symbol("sheet_instances")
                ):
                    sheet_instances_index = i
                    break

            if sheet_instances_index is None:
                logger.error("No sheet_instances section found in schematic")
                return False

            # Insert label
            sch_data.insert(sheet_instances_index, label_sexp)
            logger.info("Injected label '%s' at %s", text, position)

            # Write back
            with schematic_path.open("w", encoding="utf-8") as f:
                output = sexpdata.dumps(sch_data)
                f.write(output)

            logger.info("Successfully added label to %s", schematic_path.name)
            return True

        except Exception:
            logger.exception("Error adding label")
            return False

    @staticmethod
    def add_junction(
        schematic_path: Path,
        position: Sequence[float],
        diameter: float = 0,
    ) -> bool:
        """Add a junction (connection dot) to the schematic.

        Args:
            schematic_path: Path to .kicad_sch file
            position: [x, y] coordinates for junction
            diameter: Junction diameter (0 for default)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Read schematic
            with schematic_path.open(encoding="utf-8") as f:
                sch_content = f.read()

            sch_data = sexpdata.loads(sch_content)

            # Create junction S-expression
            # Format: (junction (at x y) (diameter 0) (color 0 0 0 0) (uuid ...))
            junction_sexp = [
                Symbol("junction"),
                [Symbol("at"), position[0], position[1]],
                [Symbol("diameter"), diameter],
                [Symbol("color"), 0, 0, 0, 0],
                [Symbol("uuid"), str(uuid.uuid4())],
            ]

            # Find insertion point
            sheet_instances_index = None
            for i, item in enumerate(sch_data):
                if (
                    isinstance(item, list)
                    and len(item) > 0
                    and item[0] == Symbol("sheet_instances")
                ):
                    sheet_instances_index = i
                    break

            if sheet_instances_index is None:
                logger.error("No sheet_instances section found in schematic")
                return False

            # Insert junction
            sch_data.insert(sheet_instances_index, junction_sexp)
            logger.info("Injected junction at %s", position)

            # Write back
            with schematic_path.open("w", encoding="utf-8") as f:
                output = sexpdata.dumps(sch_data)
                f.write(output)

            logger.info("Successfully added junction to %s", schematic_path.name)
            return True

        except Exception:
            logger.exception("Error adding junction")
            return False

    @staticmethod
    def add_no_connect(schematic_path: Path, position: Sequence[float]) -> bool:
        """Add a no-connect flag to the schematic.

        Args:
            schematic_path: Path to .kicad_sch file
            position: [x, y] coordinates for no-connect flag

        Returns:
            True if successful, False otherwise
        """
        try:
            # Read schematic
            with schematic_path.open(encoding="utf-8") as f:
                sch_content = f.read()

            sch_data = sexpdata.loads(sch_content)

            # Create no_connect S-expression
            # Format: (no_connect (at x y) (uuid ...))
            no_connect_sexp = [
                Symbol("no_connect"),
                [Symbol("at"), position[0], position[1]],
                [Symbol("uuid"), str(uuid.uuid4())],
            ]

            # Find insertion point
            sheet_instances_index = None
            for i, item in enumerate(sch_data):
                if (
                    isinstance(item, list)
                    and len(item) > 0
                    and item[0] == Symbol("sheet_instances")
                ):
                    sheet_instances_index = i
                    break

            if sheet_instances_index is None:
                logger.error("No sheet_instances section found in schematic")
                return False

            # Insert no_connect
            sch_data.insert(sheet_instances_index, no_connect_sexp)
            logger.info("Injected no-connect at %s", position)

            # Write back
            with schematic_path.open("w", encoding="utf-8") as f:
                output = sexpdata.dumps(sch_data)
                f.write(output)

            logger.info("Successfully added no-connect to %s", schematic_path.name)
            return True

        except Exception:
            logger.exception("Error adding no-connect")
            return False

    @staticmethod
    def create_orthogonal_path(
        start: Sequence[float],
        end: Sequence[float],
        *,
        prefer_horizontal_first: bool = True,
    ) -> list[list[float]]:
        """Create an orthogonal (right-angle) path between two points.

        Args:
            start: [x, y] start coordinates
            end: [x, y] end coordinates
            prefer_horizontal_first: If True, route horizontally first, else vertically first

        Returns:
            List of points defining the path: [start, corner, end]
        """
        x1, y1 = start[0], start[1]
        x2, y2 = end[0], end[1]

        corner = [x2, y1] if prefer_horizontal_first else [x1, y2]

        # If start and end are already aligned, return direct path
        if x1 == x2 or y1 == y2:
            return [[x1, y1], [x2, y2]]

        return [[x1, y1], corner, [x2, y2]]
