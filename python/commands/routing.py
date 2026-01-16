"""Routing-related command implementations for KiCAD interface."""

import logging
import math
from typing import Any

import pcbnew

logger = logging.getLogger("kicad_interface")

# Minimum points required for polygon zone
MIN_POLYGON_POINTS = 3

# Track search radius in nanometers (KiCAD internal unit: 1mm = 1,000,000 nm)
TRACK_SEARCH_RADIUS_NM = 1_000_000  # 1mm


class RoutingCommands:
    """Handles routing-related KiCAD operations."""

    def __init__(self, board: pcbnew.BOARD | None = None) -> None:
        """Initialize with optional board instance."""
        self.board = board

    def _apply_netclass_properties(
        self, netclass: pcbnew.NETCLASS, params: dict[str, Any]
    ) -> None:
        """Apply properties to a netclass using data-driven approach.

        Args:
            netclass: The netclass object to configure.
            params: Dictionary of parameters to apply.
        """
        scale = 1000000  # mm to nm

        # Define property mappings: param_key -> (setter_method, needs_scaling)
        property_setters = {
            "clearance": ("SetClearance", True),
            "trackWidth": ("SetTrackWidth", True),
            "viaDiameter": ("SetViaDiameter", True),
            "viaDrill": ("SetViaDrill", True),
            "uviaDiameter": ("SetMicroViaDiameter", True),
            "uviaDrill": ("SetMicroViaDrill", True),
            "diffPairWidth": ("SetDiffPairWidth", True),
            "diffPairGap": ("SetDiffPairGap", True),
        }

        # Apply properties from params
        for param_key, (setter_name, needs_scaling) in property_setters.items():
            value = params.get(param_key)
            if value is not None:
                setter = getattr(netclass, setter_name)
                setter(int(value * scale) if needs_scaling else value)

    def _assign_nets_to_netclass(
        self, netclass: pcbnew.NETCLASS, net_names: list[str]
    ) -> None:
        """Assign nets to a netclass.

        Args:
            netclass: The netclass to assign nets to.
            net_names: List of net names to assign.
        """
        netinfo = self.board.GetNetInfo()
        nets_map = netinfo.NetsByName()
        for net_name in net_names:
            if net_name in nets_map:
                net = nets_map[net_name]
                net.SetClass(netclass)

    def add_net(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add a new net to the PCB."""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            name = params.get("name")
            net_class = params.get("class")

            if not name:
                return {
                    "success": False,
                    "message": "Missing net name",
                    "errorDetails": "name parameter is required",
                }

            # Create new net
            netinfo = self.board.GetNetInfo()
            nets_map = netinfo.NetsByName()
            if name in nets_map:
                net = nets_map[name]
            else:
                net = pcbnew.NETINFO_ITEM(self.board, name)
                self.board.Add(net)

            # Set net class if provided
            if net_class:
                net_classes = self.board.GetNetClasses()
                if net_classes.Find(net_class):
                    net.SetClass(net_classes.Find(net_class))

            return {
                "success": True,
                "message": f"Added net: {name}",
                "net": {
                    "name": name,
                    "class": net_class if net_class else "Default",
                    "netcode": net.GetNetCode(),
                },
            }

        except Exception as e:
            logger.exception("Error adding net: %s", e)
            return {"success": False, "message": "Failed to add net", "errorDetails": str(e)}

    def route_trace(self, params: dict[str, Any]) -> dict[str, Any]:
        """Route a trace between two points or pads."""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            start = params.get("start")
            end = params.get("end")
            layer = params.get("layer", "F.Cu")
            width = params.get("width")
            net = params.get("net")
            via = params.get("via", False)

            if not start or not end:
                return {
                    "success": False,
                    "message": "Missing parameters",
                    "errorDetails": "start and end points are required",
                }

            # Get layer ID
            layer_id = self.board.GetLayerID(layer)
            if layer_id < 0:
                return {
                    "success": False,
                    "message": "Invalid layer",
                    "errorDetails": f"Layer '{layer}' does not exist",
                }

            # Get start point
            start_point = self._get_point(start)
            end_point = self._get_point(end)

            # Create track segment
            track = pcbnew.PCB_TRACK(self.board)
            track.SetStart(start_point)
            track.SetEnd(end_point)
            track.SetLayer(layer_id)

            # Set width (default to board's current track width)
            if width:
                track.SetWidth(int(width * 1000000))  # Convert mm to nm
            else:
                track.SetWidth(self.board.GetDesignSettings().GetCurrentTrackWidth())

            # Set net if provided
            if net:
                netinfo = self.board.GetNetInfo()
                nets_map = netinfo.NetsByName()
                if net in nets_map:
                    net_obj = nets_map[net]
                    track.SetNet(net_obj)

            # Add track to board
            self.board.Add(track)

            # Add via if requested and net is specified
            if via and net:
                via_point = end_point
                self.add_via(
                    {
                        "position": {
                            "x": via_point.x / 1000000,
                            "y": via_point.y / 1000000,
                            "unit": "mm",
                        },
                        "net": net,
                    }
                )

            return {
                "success": True,
                "message": "Added trace",
                "trace": {
                    "start": {
                        "x": start_point.x / 1000000,
                        "y": start_point.y / 1000000,
                        "unit": "mm",
                    },
                    "end": {"x": end_point.x / 1000000, "y": end_point.y / 1000000, "unit": "mm"},
                    "layer": layer,
                    "width": track.GetWidth() / 1000000,
                    "net": net,
                },
            }

        except Exception as e:
            logger.exception("Error routing trace: %s", e)
            return {"success": False, "message": "Failed to route trace", "errorDetails": str(e)}

    def add_via(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add a via at the specified location."""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            position = params.get("position")
            size = params.get("size")
            drill = params.get("drill")
            net = params.get("net")
            from_layer = params.get("from_layer", "F.Cu")
            to_layer = params.get("to_layer", "B.Cu")

            if not position:
                return {
                    "success": False,
                    "message": "Missing position",
                    "errorDetails": "position parameter is required",
                }

            # Create via
            via = pcbnew.PCB_VIA(self.board)

            # Set position
            scale = 1000000 if position["unit"] == "mm" else 25400000  # mm or inch to nm
            x_nm = int(position["x"] * scale)
            y_nm = int(position["y"] * scale)
            via.SetPosition(pcbnew.VECTOR2I(x_nm, y_nm))

            # Set size and drill (default to board's current via settings)
            design_settings = self.board.GetDesignSettings()
            via.SetWidth(int(size * 1000000) if size else design_settings.GetCurrentViaSize())
            via.SetDrill(int(drill * 1000000) if drill else design_settings.GetCurrentViaDrill())

            # Set layers
            from_id = self.board.GetLayerID(from_layer)
            to_id = self.board.GetLayerID(to_layer)
            if from_id < 0 or to_id < 0:
                return {
                    "success": False,
                    "message": "Invalid layer",
                    "errorDetails": "Specified layers do not exist",
                }
            via.SetLayerPair(from_id, to_id)

            # Set net if provided
            if net:
                netinfo = self.board.GetNetInfo()
                nets_map = netinfo.NetsByName()
                if net in nets_map:
                    net_obj = nets_map[net]
                    via.SetNet(net_obj)

            # Add via to board
            self.board.Add(via)

            return {
                "success": True,
                "message": "Added via",
                "via": {
                    "position": {"x": position["x"], "y": position["y"], "unit": position["unit"]},
                    "size": via.GetWidth() / 1000000,
                    "drill": via.GetDrill() / 1000000,
                    "from_layer": from_layer,
                    "to_layer": to_layer,
                    "net": net,
                },
            }

        except Exception as e:
            logger.exception("Error adding via: %s", e)
            return {"success": False, "message": "Failed to add via", "errorDetails": str(e)}

    def _delete_trace_by_uuid(self, trace_uuid: str) -> dict[str, Any]:
        """Delete a trace by its UUID.

        Args:
            trace_uuid: UUID of the track to delete.

        Returns:
            Success/failure dictionary.
        """
        track = self._find_track_by_uuid(trace_uuid)
        
        if not track:
            return {
                "success": False,
                "message": "Track not found",
                "errorDetails": f"Could not find track with UUID: {trace_uuid}",
            }

        self.board.Remove(track)
        return {"success": True, "message": f"Deleted track: {trace_uuid}"}

    def _delete_trace_by_position(self, position: dict[str, Any]) -> dict[str, Any]:
        """Delete a trace by its position (finds closest track).

        Args:
            position: Position specification with x, y, and unit.

        Returns:
            Success/failure dictionary.
        """
        scale = 1000000 if position["unit"] == "mm" else 25400000  # mm or inch to nm
        x_nm = int(position["x"] * scale)
        y_nm = int(position["y"] * scale)
        point = pcbnew.VECTOR2I(x_nm, y_nm)

        closest_track, min_distance = self._find_closest_track(point)

        if closest_track and min_distance < TRACK_SEARCH_RADIUS_NM:  # Within 1mm
            self.board.Remove(closest_track)
            return {"success": True, "message": "Deleted track at specified position"}
        
        return {
            "success": False,
            "message": "No track found",
            "errorDetails": "No track found near specified position",
        }

    def _find_track_by_uuid(self, trace_uuid: str) -> pcbnew.PCB_TRACK | None:
        """Find a track by its UUID.

        Args:
            trace_uuid: UUID string to search for.

        Returns:
            Track object if found, None otherwise.
        """
        for item in self.board.Tracks():
            if str(item.m_Uuid) == trace_uuid:
                return item
        return None

    def _find_closest_track(self, point: pcbnew.VECTOR2I) -> tuple[pcbnew.PCB_TRACK | None, float]:
        """Find the track closest to a given point.

        Args:
            point: Point to search near.

        Returns:
            Tuple of (closest_track, min_distance). Track may be None if no tracks exist.
        """
        closest_track = None
        min_distance = float("inf")
        
        for track in self.board.Tracks():
            dist = self._point_to_track_distance(point, track)
            if dist < min_distance:
                min_distance = dist
                closest_track = track
        
        return closest_track, min_distance

    def delete_trace(self, params: dict[str, Any]) -> dict[str, Any]:
        """Delete a trace from the PCB."""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            trace_uuid = params.get("traceUuid")
            position = params.get("position")

            if not trace_uuid and not position:
                return {
                    "success": False,
                    "message": "Missing parameters",
                    "errorDetails": "Either traceUuid or position must be provided",
                }

            # Dispatch to appropriate deletion method
            if trace_uuid:
                return self._delete_trace_by_uuid(trace_uuid)
            
            return self._delete_trace_by_position(position)

        except Exception as e:
            logger.exception("Error deleting trace: %s", e)
            return {"success": False, "message": "Failed to delete trace", "errorDetails": str(e)}

    def get_nets_list(self, params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        """Get a list of all nets in the PCB."""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            nets = []
            netinfo = self.board.GetNetInfo()
            for net_code in range(netinfo.GetNetCount()):
                net = netinfo.GetNetItem(net_code)
                if net:
                    nets.append(
                        {
                            "name": net.GetNetname(),
                            "code": net.GetNetCode(),
                            "class": net.GetClassName(),
                        }
                    )

            return {"success": True, "nets": nets}

        except Exception as e:
            logger.exception("Error getting nets list: %s", e)
            return {"success": False, "message": "Failed to get nets list", "errorDetails": str(e)}

    def create_netclass(self, params: dict[str, Any]) -> dict[str, Any]:
        """Create a new net class with specified properties."""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            name = params.get("name")
            if not name:
                return {
                    "success": False,
                    "message": "Missing netclass name",
                    "errorDetails": "name parameter is required",
                }

            # Get or create net class
            net_classes = self.board.GetNetClasses()
            if not net_classes.Find(name):
                netclass = pcbnew.NETCLASS(name)
                net_classes.Add(netclass)
            else:
                netclass = net_classes.Find(name)

            # Apply netclass properties using mapping
            self._apply_netclass_properties(netclass, params)

            # Assign nets to netclass
            nets = params.get("nets", [])
            self._assign_nets_to_netclass(netclass, nets)

            # Build response
            scale = 1000000  # mm to nm
            return {
                "success": True,
                "message": f"Created net class: {name}",
                "netClass": {
                    "name": name,
                    "clearance": netclass.GetClearance() / scale,
                    "trackWidth": netclass.GetTrackWidth() / scale,
                    "viaDiameter": netclass.GetViaDiameter() / scale,
                    "viaDrill": netclass.GetViaDrill() / scale,
                    "uviaDiameter": netclass.GetMicroViaDiameter() / scale,
                    "uviaDrill": netclass.GetMicroViaDrill() / scale,
                    "diffPairWidth": netclass.GetDiffPairWidth() / scale,
                    "diffPairGap": netclass.GetDiffPairGap() / scale,
                    "nets": nets,
                },
            }

        except Exception as e:
            logger.exception("Error creating net class: %s", e)
            return {
                "success": False,
                "message": "Failed to create net class",
                "errorDetails": str(e),
            }

    def add_copper_pour(self, params: dict[str, Any]) -> dict[str, Any]:
        """Add a copper pour (zone) to the PCB."""
        try:
            if not self.board:
                return {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }

            layer = params.get("layer", "F.Cu")
            net = params.get("net")
            clearance = params.get("clearance")
            min_width = params.get("minWidth", 0.2)
            points = params.get("points", [])
            priority = params.get("priority", 0)
            fill_type = params.get("fillType", "solid")  # solid or hatched

            if not points or len(points) < MIN_POLYGON_POINTS:
                return {
                    "success": False,
                    "message": "Missing points",
                    "errorDetails": "At least 3 points are required for copper pour outline",
                }

            # Get layer ID
            layer_id = self.board.GetLayerID(layer)
            if layer_id < 0:
                return {
                    "success": False,
                    "message": "Invalid layer",
                    "errorDetails": f"Layer '{layer}' does not exist",
                }

            # Create zone
            zone = pcbnew.ZONE(self.board)
            zone.SetLayer(layer_id)

            # Set net if provided
            if net:
                netinfo = self.board.GetNetInfo()
                nets_map = netinfo.NetsByName()
                if net in nets_map:
                    net_obj = nets_map[net]
                    zone.SetNet(net_obj)

            # Set zone properties
            scale = 1000000  # mm to nm
            zone.SetAssignedPriority(priority)

            if clearance is not None:
                zone.SetLocalClearance(int(clearance * scale))

            zone.SetMinThickness(int(min_width * scale))

            # Set fill type
            if fill_type == "hatched":
                zone.SetFillMode(pcbnew.ZONE_FILL_MODE_HATCH_PATTERN)
            else:
                zone.SetFillMode(pcbnew.ZONE_FILL_MODE_POLYGONS)

            # Create outline
            outline = zone.Outline()
            outline.NewOutline()  # Create a new outline contour first

            # Add points to outline
            for point in points:
                scale = 1000000 if point.get("unit", "mm") == "mm" else 25400000
                x_nm = int(point["x"] * scale)
                y_nm = int(point["y"] * scale)
                outline.Append(pcbnew.VECTOR2I(x_nm, y_nm))  # Add point to outline

            # Add zone to board
            self.board.Add(zone)

            # Note: Zone filling can cause issues with SWIG API
            # Zones will be automatically filled when the board is saved/opened in KiCAD

            return {
                "success": True,
                "message": "Added copper pour",
                "pour": {
                    "layer": layer,
                    "net": net,
                    "clearance": clearance,
                    "minWidth": min_width,
                    "priority": priority,
                    "fillType": fill_type,
                    "pointCount": len(points),
                },
            }

        except Exception as e:
            logger.exception("Error adding copper pour: %s", e)
            return {
                "success": False,
                "message": "Failed to add copper pour",
                "errorDetails": str(e),
            }

    def route_differential_pair(self, params: dict[str, Any]) -> dict[str, Any]:
        """Route a differential pair between two sets of points or pads."""
        try:
            # Validate parameters and layer
            validation_result = self._validate_diff_pair_params(params)
            if validation_result["error"]:
                return validation_result["error"]
            validated = validation_result["validated"]

            # Resolve differential nets
            nets_result = self._resolve_differential_nets(validated["net_pos"], validated["net_neg"])
            if "error" in nets_result:
                return nets_result["error"]

            # Calculate differential pair geometry
            geometry = self._calculate_diff_pair_geometry(
                validated["start_pos"], validated["end_pos"], validated["gap"]
            )
            if "error" in geometry:
                return geometry["error"]

            # Create differential pair tracks
            pos_track, neg_track = self._create_diff_pair_tracks(
                geometry, nets_result, validated["layer_id"], validated["width"]
            )

            # Add to board and return response
            self.board.Add(pos_track)
            self.board.Add(neg_track)
            return self._build_diff_pair_response(
                validated["net_pos"],
                validated["net_neg"],
                validated["layer"],
                pos_track,
                validated["gap"],
                geometry["length"],
            )

        except Exception as e:
            logger.exception("Error routing differential pair: %s", e)
            return {
                "success": False,
                "message": "Failed to route differential pair",
                "errorDetails": str(e),
            }

    def _validate_diff_pair_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Validate parameters and layer for differential pair routing.

        Returns:
            Dict with 'error' (or None) and 'validated' params.
        """
        if not self.board:
            return {
                "error": {
                    "success": False,
                    "message": "No board is loaded",
                    "errorDetails": "Load or create a board first",
                }
            }

        start_pos = params.get("startPos")
        end_pos = params.get("endPos")
        net_pos = params.get("netPos")
        net_neg = params.get("netNeg")

        if not start_pos or not end_pos or not net_pos or not net_neg:
            return {
                "error": {
                    "success": False,
                    "message": "Missing parameters",
                    "errorDetails": "startPos, endPos, netPos, and netNeg are required",
                }
            }

        layer = params.get("layer", "F.Cu")
        layer_id = self.board.GetLayerID(layer)
        if layer_id < 0:
            return {
                "error": {
                    "success": False,
                    "message": "Invalid layer",
                    "errorDetails": f"Layer '{layer}' does not exist",
                }
            }

        return {
            "error": None,
            "validated": {
                "start_pos": start_pos,
                "end_pos": end_pos,
                "net_pos": net_pos,
                "net_neg": net_neg,
                "layer": layer,
                "layer_id": layer_id,
                "width": params.get("width"),
                "gap": params.get("gap", 0.2),
            },
        }

    def _resolve_differential_nets(self, net_pos: str, net_neg: str) -> dict[str, Any]:
        """Resolve net objects for differential pair.

        Returns:
            Dict with net_pos_obj and net_neg_obj, or dict with 'error' key.
        """
        netinfo = self.board.GetNetInfo()
        nets_map = netinfo.NetsByName()

        net_pos_obj = nets_map.get(net_pos, None)
        net_neg_obj = nets_map.get(net_neg, None)

        if not net_pos_obj or not net_neg_obj:
            return {
                "error": {
                    "success": False,
                    "message": "Nets not found",
                    "errorDetails": "One or both nets specified for the differential pair do not exist",
                }
            }

        return {"net_pos_obj": net_pos_obj, "net_neg_obj": net_neg_obj}

    def _calculate_diff_pair_geometry(
        self, start_pos: dict[str, Any], end_pos: dict[str, Any], gap: float
    ) -> dict[str, Any]:
        """Calculate geometry for differential pair routing.

        Returns:
            Dict with points, offsets, and length, or dict with 'error' key.
        """
        start_point = self._get_point(start_pos)
        end_point = self._get_point(end_pos)

        # Calculate direction vector
        dx = end_point.x - start_point.x
        dy = end_point.y - start_point.y
        length = math.sqrt(dx * dx + dy * dy)

        if length <= 0:
            return {
                "error": {
                    "success": False,
                    "message": "Invalid points",
                    "errorDetails": "Start and end points must be different",
                }
            }

        # Normalize and get perpendicular
        dx /= length
        dy /= length
        px = -dy
        py = dx

        # Calculate offsets
        gap_nm = int(gap * 1000000)
        offset_x = int(px * gap_nm / 2)
        offset_y = int(py * gap_nm / 2)

        # Create trace points
        return {
            "pos_start": pcbnew.VECTOR2I(int(start_point.x + offset_x), int(start_point.y + offset_y)),
            "pos_end": pcbnew.VECTOR2I(int(end_point.x + offset_x), int(end_point.y + offset_y)),
            "neg_start": pcbnew.VECTOR2I(int(start_point.x - offset_x), int(start_point.y - offset_y)),
            "neg_end": pcbnew.VECTOR2I(int(end_point.x - offset_x), int(end_point.y - offset_y)),
            "length": length,
        }

    def _create_diff_pair_tracks(
        self, geometry: dict[str, Any], nets: dict[str, Any], layer_id: int, width: float | None
    ) -> tuple[pcbnew.PCB_TRACK, pcbnew.PCB_TRACK]:
        """Create positive and negative tracks for differential pair."""
        # Create positive trace
        pos_track = pcbnew.PCB_TRACK(self.board)
        pos_track.SetStart(geometry["pos_start"])
        pos_track.SetEnd(geometry["pos_end"])
        pos_track.SetLayer(layer_id)
        pos_track.SetNet(nets["net_pos_obj"])

        # Create negative trace
        neg_track = pcbnew.PCB_TRACK(self.board)
        neg_track.SetStart(geometry["neg_start"])
        neg_track.SetEnd(geometry["neg_end"])
        neg_track.SetLayer(layer_id)
        neg_track.SetNet(nets["net_neg_obj"])

        # Set width
        if width:
            trace_width_nm = int(width * 1000000)
            pos_track.SetWidth(trace_width_nm)
            neg_track.SetWidth(trace_width_nm)
        else:
            trace_width = self.board.GetDesignSettings().GetCurrentTrackWidth()
            pos_track.SetWidth(trace_width)
            neg_track.SetWidth(trace_width)

        return pos_track, neg_track

    def _build_diff_pair_response(
        self,
        net_pos: str,
        net_neg: str,
        layer: str,
        pos_track: pcbnew.PCB_TRACK,
        gap: float,
        length: float,
    ) -> dict[str, Any]:
        """Build success response for differential pair routing."""
        return {
            "success": True,
            "message": "Added differential pair traces",
            "diffPair": {
                "posNet": net_pos,
                "negNet": net_neg,
                "layer": layer,
                "width": pos_track.GetWidth() / 1000000,
                "gap": gap,
                "length": length / 1000000,
            },
        }

    def _get_point(self, point_spec: dict[str, Any]) -> pcbnew.VECTOR2I:
        """Convert point specification to KiCAD point."""
        if "x" in point_spec and "y" in point_spec:
            scale = 1000000 if point_spec.get("unit", "mm") == "mm" else 25400000
            x_nm = int(point_spec["x"] * scale)
            y_nm = int(point_spec["y"] * scale)
            return pcbnew.VECTOR2I(x_nm, y_nm)
        if "pad" in point_spec and "componentRef" in point_spec:
            module = self.board.FindFootprintByReference(point_spec["componentRef"])
            if module:
                pad = module.FindPadByName(point_spec["pad"])
                if pad:
                    return pad.GetPosition()
        msg = "Invalid point specification"
        raise ValueError(msg)

    def _point_to_track_distance(self, point: pcbnew.VECTOR2I, track: pcbnew.PCB_TRACK) -> float:
        """Calculate distance from point to track segment."""
        start = track.GetStart()
        end = track.GetEnd()

        # Vector from start to end
        v = pcbnew.VECTOR2I(end.x - start.x, end.y - start.y)
        # Vector from start to point
        w = pcbnew.VECTOR2I(point.x - start.x, point.y - start.y)

        # Length of track squared
        c1 = v.x * v.x + v.y * v.y
        if c1 == 0:
            return self._point_distance(point, start)

        # Projection coefficient
        c2 = float(w.x * v.x + w.y * v.y) / c1

        if c2 < 0:
            return self._point_distance(point, start)
        if c2 > 1:
            return self._point_distance(point, end)

        # Point on line
        proj = pcbnew.VECTOR2I(int(start.x + c2 * v.x), int(start.y + c2 * v.y))
        return self._point_distance(point, proj)

    def _point_distance(self, p1: pcbnew.VECTOR2I, p2: pcbnew.VECTOR2I) -> float:
        """Calculate distance between two points."""
        dx = p1.x - p2.x
        dy = p1.y - p2.y
        return (dx * dx + dy * dy) ** 0.5
