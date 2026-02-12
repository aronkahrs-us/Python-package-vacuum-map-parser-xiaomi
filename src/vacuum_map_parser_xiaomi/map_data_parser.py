"""Xiaomi map data parser.

Parses Xiaomi vacuum cleaner map data from both legacy protobuf format and newer JSON format.
Handles decryption, coordinate transformation, room detection, and visualization.
"""

import base64
import json
import logging
import math
import zlib
from types import SimpleNamespace
from typing import Any

from vacuum_map_parser_base.config.color import ColorsPalette
from vacuum_map_parser_base.config.drawable import Drawable
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.config.size import Sizes
from vacuum_map_parser_base.config.text import Text
from vacuum_map_parser_base.map_data import Area, ImageData, MapData, Path, Point, Room, Wall, Zone
from vacuum_map_parser_base.map_data_parser import MapDataParser

from .aes_decryptor import decrypt
from .image_parser import XiaomiImageParser
from .xiaomi_coordinate_transforms import Transformer

_LOGGER = logging.getLogger(__name__)


class XiaomiMapDataParser(MapDataParser):
    """Xiaomi map data parser.

    Parses encrypted map data from Xiaomi vacuum cleaners, supporting both legacy
    protobuf-based maps and newer JSON-based maps. Handles coordinate transformations,
    room detection, virtual walls, no-go zones, and path tracking.
    """

    # Constants for map features
    POSITION_UNKNOWN = 1100  # Marker for unknown/invalid positions
    VIRTUALWALL_TYPE_WALL = 2  # Virtual wall barrier
    VIRTUALWALL_TYPE_NO_MOP = 6  # No-mopping zone
    VIRTUALWALL_TYPE_NO_GO = 3  # No-go zone (completely restricted)

    def __init__(
        self,
        palette: ColorsPalette,
        sizes: Sizes,
        drawables: list[Drawable],
        image_config: ImageConfig,
        texts: list[Text],
    ):
        """Initialize the Xiaomi map data parser.

        Args:
            palette: Color palette for rendering map elements
            sizes: Size configuration for map elements (icons, lines, etc.)
            drawables: List of elements to draw on the map
            image_config: Configuration for image generation (scale, trim, rotation)
            texts: Text labels to display on the map
        """
        super().__init__(palette, sizes, drawables, image_config, texts)
        self._image_parser = XiaomiImageParser(palette, image_config, drawables)
        self.robot_map: Any = None
        self.coord_transformer: Any = None

    def unpack_map(self, raw_encoded: bytes, *args: Any, **kwargs: Any) -> str:  # type: ignore[override]
        """Decrypt raw encrypted map data from Xiaomi vacuum.

        Args:
            raw_encoded: Encrypted map data bytes
            *args: Additional positional arguments (unused)
            **kwargs: Must contain 'model' and 'device_id' for decryption

        Returns:
            Decrypted map data (JSON string)
        """
        return decrypt(raw_encoded, kwargs["model"], kwargs["device_id"])

    def parse(self, raw: Any, *args: Any, **kwargs: Any) -> MapData:
        """Parse decrypted map data into a MapData object.

        Supports multiple input formats:
        - JSON string (newer Xiaomi models)
        - Dictionary (pre-parsed JSON)

        Args:
            raw: Decrypted map data in various formats
            *args: Additional positional arguments (unused)
            **kwargs: Additional keyword arguments (unused)

        Returns:
            Parsed MapData object

        Raises:
            ValueError: If JSON string is malformed or input is None
            TypeError: If input type is not supported
        """
        if raw is None:
            raise ValueError("Map data cannot be None")

        # Decryptor returns a JSON string for newer Xiaomi vacuums
        if isinstance(raw, str):
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("Map data is a string but not valid JSON") from exc
            return self._parse_json_payload(payload)

        # Some callers might already pass the decoded dict
        if isinstance(raw, dict):
            return self._parse_json_payload(raw)

        raise TypeError(f"Unsupported map data type: {type(raw)!r}")

    @staticmethod
    def _json_yaw_to_degrees(yaw: Any) -> float:
        """Convert Xiaomi JSON yaw values to degrees.

        Xiaomi uses different angle formats across models/firmwares:
        - Centi-degrees: Large values like 2470 represent 24.70°
        - Radians: Values within [-2π, 2π]
        - Degrees: Values already in degree format

        Args:
            yaw: Angle value in unknown format

        Returns:
            Normalized angle in degrees [0, 180)
        """
        try:
            value = float(yaw)
        except (TypeError, ValueError):
            return 0.0

        # Radians are typically within [-2π, 2π] (approximately ±6.28)
        if abs(value) <= (2 * math.pi + 0.001):
            return value * 180.0 / math.pi

        # Many Xiaomi payloads use centi-degrees (multiply by 100)
        # Values > 180 are likely in this format
        if abs(value) > 180.0:
            return (value / 100.0) % 180.0

        # Already in degrees
        return value % 180.0

    @staticmethod
    def _room_number_to_grid_id(room_number: int) -> int:
        """Convert pixel-based room_number to Xiaomi grid_id.

        Image parser uses room numbers 10-59 for rooms in the pixel data.
        Xiaomi's JSON format uses grid_ids starting from 3.
        This converts between the two numbering schemes.

        Args:
            room_number: Room number from image parser (10-59)

        Returns:
            Grid ID for Xiaomi JSON format (3+)
        """
        return int(room_number) - XiaomiImageParser.MAP_ROOM_MIN + 3

    def _normalize_json_map_pixels(self, raw: bytes) -> bytes:
        """Normalize JSON-based map pixels to match image parser expectations.

        The JSON format uses different pixel values than the image parser expects:
        - JSON: 0=unknown, 1/2=free space, 3-63=rooms, >63=walls
        - Parser: 0=outside, 127=inside, 10-59=rooms, 128=wall

        This method converts from JSON format to parser format.

        Args:
            raw: Raw pixel data from JSON map

        Returns:
            Normalized pixel data compatible with XiaomiImageParser
        """
        ROOM_MIN = XiaomiImageParser.MAP_ROOM_MIN  # usually 10

        out = bytearray(len(raw))

        for i, v in enumerate(raw):
            # Unknown/outside areas
            if v == 0:
                out[i] = 0

            # JSON free space / base floor pixels (1 or 2)
            elif v in (1, 2):
                out[i] = 127  # MAP_INSIDE

            # JSON room IDs (3-63) -> convert to parser room numbers (10-59)
            # This is critical for proper room detection
            elif 3 <= v <= 63:
                out[i] = ROOM_MIN + (v - 3)

            # Everything else is a wall or blocked area
            else:
                out[i] = 128  # MAP_WALL

        return bytes(out)

    def _parse_json_payload(self, payload: dict[str, Any]) -> MapData:
        """Parse decrypted JSON payload into a MapData object.

        Processes JSON-format map data from newer Xiaomi vacuum models.
        Extracts map image, rooms, walls, zones, paths, and device positions.

        Args:
            payload: Decrypted JSON payload as a dictionary

        Returns:
            Complete MapData object with all map features
        """

        # ---- Basic map metadata -------------------------------------------------
        # Extract core map properties from JSON payload
        map_id = payload.get("map_id", 0)
        map_width = payload.get("width")
        map_height = payload.get("height")
        map_resolution = payload.get("resolution", 50)  # mm per pixel (typical)
        origin_x = payload.get("origin_x", 0)  # World coordinates of map origin
        origin_y = payload.get("origin_y", 0)

        # Map data is base64-encoded and zlib-compressed
        raw_map_data_b64 = payload.get("map_data")
        if not raw_map_data_b64 or map_width is None or map_height is None:
            _LOGGER.debug("JSON map payload missing map_data/width/height")
            return MapData(0, 1)

        # Decode and decompress the pixel data
        try:
            map_bytes = zlib.decompress(base64.b64decode(raw_map_data_b64))
        except Exception as exc:
            raise RuntimeError("Failed to decode JSON map_data (base64+zlib)") from exc

        # ---- Create MapData -----------------------------------------------------
        map_data = MapData(0, 1)

        # ---- Set up coordinate transformer --------------------------------------
        # Create a minimal map header structure for coordinate transformation
        # This mimics the protobuf format expected by the Transformer class
        self.robot_map = SimpleNamespace()
        self.robot_map.mapHead = SimpleNamespace(
            mapHeadId=map_id,
            sizeX=int(map_width),
            sizeY=int(map_height),
            resolution=float(map_resolution),  # Millimeters per pixel
            minX=float(origin_x),  # World coordinate bounds
            minY=float(origin_y),
            maxX=float(origin_x) + float(map_width) * float(map_resolution),
            maxY=float(origin_y) + float(map_height) * float(map_resolution),
        )

        # Initialize coordinate transformer for converting between map and image coordinates
        self.coord_transformer = Transformer(self.robot_map)

        # Convert JSON pixel format to image parser format
        normalized_map = self._normalize_json_map_pixels(map_bytes)

        # ---- Parse image --------------------------------------------------------
        # Use the image parser to render the map and detect rooms
        image, rooms_raw, cleaned_areas = self._image_parser.parse(normalized_map, int(map_width), int(map_height))

        # Fall back to empty image if parsing failed
        if image is None:
            image = self._image_generator.create_empty_map_image()

        # Create ImageData object with coordinate transformation function
        map_data.image = ImageData(
            int(map_width) * int(map_height),  # Total pixel count
            0,  # Additional data offset (unused)
            0,  # Additional data length (unused)
            int(map_height),
            int(map_width),
            self._image_config,
            image,
            self.coord_transformer.map_to_image,  # Coordinate conversion function
        )

        # ---- Rooms --------------------------------------------------------------
        rooms_out: dict[int, Room] = {}

        # Build mapping between different room ID systems
        # Xiaomi uses three different identifiers:
        # - pixel room_number: 10-59 (used in image data)
        # - grid_id: 3+ (used in JSON metadata)
        # - room_id: varies (user-visible room identifier)
        grid_to_room: dict[int, int] = {}
        room_to_grid: dict[int, int] = {}

        map_room_info = payload.get("map_room_info")
        if isinstance(map_room_info, list):
            for entry in map_room_info:
                if not isinstance(entry, dict):
                    continue
                try:
                    grid_id_val = entry.get("grid_id")
                    room_id_val = entry.get("room_id")
                    if grid_id_val is None or room_id_val is None:
                        continue
                    grid_id = int(grid_id_val)
                    room_id = int(room_id_val)
                except (TypeError, ValueError):
                    continue
                grid_to_room[grid_id] = room_id
                # Keep bidirectional mapping for name attachment
                # Some payloads use room_id, others use grid_id
                room_to_grid[room_id] = grid_id

        # Convert rooms from image parser output to Room objects
        for room_number, room in rooms_raw.items():
            # Convert pixel room number to grid_id, then to room_id
            grid_id = self._room_number_to_grid_id(room_number)
            room_id = grid_to_room.get(grid_id, grid_id)  # Default to grid_id if no mapping

            # Create Room object with bounding box in map coordinates
            # room is (min_x, min_y, max_x, max_y) in image coordinates
            rooms_out[room_id] = Room(
                self.coord_transformer.image_to_map_x(room[0]),  # left
                self.coord_transformer.image_to_map_y(room[1]),  # top
                self.coord_transformer.image_to_map_x(room[2]),  # right
                self.coord_transformer.image_to_map_y(room[3]),  # bottom
                room_id,
            )

        # Attach room names and label positions from room_attrs
        # This is a best-effort process due to inconsistent JSON formats across models
        room_attrs = payload.get("room_attrs")
        if isinstance(room_attrs, list):
            for r in room_attrs:
                if not isinstance(r, dict):
                    continue

                # Extract room identifier (varies by model/firmware)
                rid = r.get("room_id") or r.get("grid_id") or r.get("id")
                if rid is None:
                    continue
                try:
                    rid_int = int(rid)
                except (TypeError, ValueError):
                    continue

                # The JSON payload is inconsistent across models/firmwares:
                # - `room_attrs.id` may be a grid_id or room_id
                # - `room_attrs.room_id` may be a room_id
                # - `map_room_info` may map grid_id <-> room_id
                # Try multiple approaches to find the correct room
                target_id: int | None = None

                # Try 1: Direct match
                if rid_int in rooms_out:
                    target_id = rid_int
                else:
                    # Try 2: Treat as grid_id and look up room_id
                    mapped_room_id = grid_to_room.get(rid_int)
                    if mapped_room_id is not None and mapped_room_id in rooms_out:
                        target_id = mapped_room_id
                    else:
                        # Try 3: Treat as room_id and look up grid_id
                        mapped_grid_id = room_to_grid.get(rid_int)
                        if mapped_grid_id is not None and mapped_grid_id in rooms_out:
                            target_id = mapped_grid_id

                # Apply room name and label position if we found a match
                if target_id is not None:
                    rooms_out[target_id].name = r.get("name") or r.get("room_name")
                    # Label position varies by payload format
                    rooms_out[target_id].pos_x = r.get("text_x", r.get("name_pos_x"))
                    rooms_out[target_id].pos_y = r.get("text_y", r.get("name_pos_y"))

        map_data.rooms = rooms_out

        # Convert cleaned room numbers to room IDs
        # cleaned_areas contains pixel-based room numbers from the image parser
        cleaned_rooms_out = set()
        for room_number in cleaned_areas:
            try:
                grid_id = self._room_number_to_grid_id(int(room_number))
            except (TypeError, ValueError):
                continue
            # Convert grid_id to room_id (or use grid_id if no mapping exists)
            cleaned_rooms_out.add(grid_to_room.get(grid_id, grid_id))
        map_data.cleaned_rooms = cleaned_rooms_out

        # ---- Charger dock position ----------------------------------------------
        # "pile" is Xiaomi's internal term for the charging dock
        if payload.get("have_pile"):
            map_data.charger = Point(
                x=payload.get("pile_x", 0),
                y=payload.get("pile_y", 0),
                a=self._json_yaw_to_degrees(payload.get("pile_yaw", 0)),
            )

        # ---- Robot vacuum position ----------------------------------------------
        # Current position and orientation of the vacuum
        position = payload.get("position",0)
        if isinstance(position, dict):
            map_data.vacuum_position = Point(
                x=position.get("x", 0),
                y=position.get("y", 0),
                a=self._json_yaw_to_degrees(position.get("yaw", 0)),  # Heading angle
            )

        # ---- Cleaning path history ----------------------------------------------
        # Track where the vacuum has traveled during cleaning
        paths = payload.get("paths")
        points_src = None

        # Handle different path data formats
        if isinstance(paths, dict):
            points_src = paths.get("points")
        elif isinstance(paths, list):
            points_src = paths

        if isinstance(points_src, list):
            points = []  # All path points
            points_mop = []  # Points where mopping was active

            for p in points_src:
                if not isinstance(p, dict):
                    continue

                pt = Point(
                    x=p.get("x", 0),
                    y=p.get("y", 0),
                )

                # Include heading angle if available
                if "yaw" in p:
                    pt.a = self._json_yaw_to_degrees(p.get("yaw"))

                # Separate tracking for mopping path
                if "sweep_mop_mode" in p:
                    points_mop.append(pt)

                points.append(pt)

            # Create path objects if we have points
            if points:
                map_data.path = Path(len(points), 1, 0, [points])
            if points_mop:
                map_data.mop_path = Path(len(points_mop), 1, 0, [points_mop])

        # ---- Virtual walls and restricted zones ---------------------------------
        # User-defined barriers and restricted areas
        walls = []  # Virtual walls (line barriers)
        no_go = []  # No-go zones (vacuum won't enter)
        no_mop = []  # No-mopping zones (vacuum can sweep but not mop)

        # fb_regions = "forbidden regions"
        for area in payload.get("fb_regions", []) or []:
            if not isinstance(area, dict):
                continue

            pts = area.get("points")
            if not pts or len(pts) != 4:
                continue

            # Convert points to Point objects
            p = [Point(pt["x"], pt["y"]) for pt in pts]

            # Categorize based on region type
            atype = area.get("type")
            if atype == "wall":
                # Virtual walls are defined by two points (start and end)
                walls.append(Wall(p[0].x, p[0].y, p[2].x, p[2].y))
            elif atype == "no_go":
                # No-go zones are rectangular areas defined by 4 corners
                no_go.append(Area(p[0].x, p[0].y, p[1].x, p[1].y, p[2].x, p[2].y, p[3].x, p[3].y))
            elif atype == "no_mop":
                # No-mopping zones use the same format as no-go zones
                no_mop.append(Area(p[0].x, p[0].y, p[1].x, p[1].y, p[2].x, p[2].y, p[3].x, p[3].y))

        map_data.walls = walls
        map_data.no_go_areas = no_go
        map_data.no_mopping_areas = no_mop

        # ---- Cleaning zones -----------------------------------------------------
        # Zones are user-defined rectangular areas for targeted cleaning
        zones = []
        for z in payload.get("current_cleaning_config", {}).get("zones", []):
            if not isinstance(z, dict):
                continue
            # Zones are defined by two corners (top-left and bottom-right)
            zones.append(
                Zone(
                    z["x1"],  # Left X
                    z["y1"],  # Top Y
                    z["x2"],  # Right X
                    z["y2"],  # Bottom Y
                )
            )
        map_data.zones = zones

        # ---- Final rendering ----------------------------------------------------
        # Draw all map elements (rooms, paths, zones, etc.) on the base image
        if map_data.image is not None and not map_data.image.is_empty:
            self._image_generator.draw_map(map_data)

        return map_data
