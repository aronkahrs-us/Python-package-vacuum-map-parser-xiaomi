"""Xiaomi map image parser."""

import logging

from PIL import Image
from PIL.Image import Image as ImageType
from PIL.Image import Resampling
from vacuum_map_parser_base.config.color import ColorsPalette, SupportedColor
from vacuum_map_parser_base.config.drawable import Drawable
from vacuum_map_parser_base.config.image_config import ImageConfig
from vacuum_map_parser_base.map_data import Point

_LOGGER = logging.getLogger(__name__)


class XiaomiImageParser:
    """Xiaomi map image parser.
    
    Parses Xiaomi vacuum cleaner map data and converts it into visual representations.
    Handles room detection, area coloring, and image generation with configurable scaling and trimming.
    """

    # Pixel type constants - these values represent different areas in the raw map data
    MAP_OUTSIDE = 0x00              # Areas outside the mapped space
    MAP_WALL = 128                  # Wall segments
    MAP_INSIDE = 127                # Floor inside rooms (generic)
    MAP_SCAN = 0x01                 # Currently being scanned area
    MAP_NEW_DISCOVERED_AREA = 0x02  # Newly discovered areas
    MAP_ROOM_MIN = 10               # Minimum room ID value
    MAP_ROOM_MAX = 59               # Maximum room ID value
    MAP_SELECTED_ROOM_MIN = 60      # Minimum selected/cleaned room ID
    MAP_SELECTED_ROOM_MAX = 109     # Maximum selected/cleaned room ID

    def __init__(self, palette: ColorsPalette, image_config: ImageConfig, drawables: list[Drawable]):
        """Initialize the Xiaomi image parser.
        
        Args:
            palette: Color palette for rendering different map elements
            image_config: Configuration for image generation (scale, trim, etc.)
            drawables: List of drawable elements to overlay on the map
        """
        self._palette = palette
        self._image_config = image_config
        self._drawables = drawables
        
        # Pre-map common pixel types to their colors for efficient lookup
        self.color_map = {
            XiaomiImageParser.MAP_OUTSIDE: palette.get_color(SupportedColor.MAP_OUTSIDE),
            XiaomiImageParser.MAP_WALL: palette.get_color(SupportedColor.MAP_WALL_V2),
            XiaomiImageParser.MAP_SCAN: palette.get_color(SupportedColor.SCAN),
            XiaomiImageParser.MAP_NEW_DISCOVERED_AREA: palette.get_color(SupportedColor.NEW_DISCOVERED_AREA),
            XiaomiImageParser.MAP_INSIDE: palette.get_color(SupportedColor.MAP_INSIDE)
        }

    def parse(
        self, map_data: bytes, width: int, height: int
    ) -> tuple[ImageType | None, dict[int, tuple[int, int, int, int]], set[int]]:
        """Parse raw map data into an image with room detection.
        
        Args:
            map_data: Raw byte data from the vacuum's map
            width: Width of the raw map data in pixels
            height: Height of the raw map data in pixels
            
        Returns:
            A tuple containing:
            - Generated PIL Image (or None if invalid dimensions)
            - Dictionary mapping room IDs to their bounding boxes (left, top, right, bottom)
            - Set of room IDs that are currently being cleaned
        """
        # Initialize room tracking data structures
        rooms: dict[int, tuple[int, int, int, int]] = {}  # room_id -> (min_x, min_y, max_x, max_y)
        cleaned_areas = set()  # Set of room IDs being actively cleaned
        
        _LOGGER.debug("xiaomi parser: image_config = %s", self._image_config)
        
        # Calculate trimming values - trim values are specified as percentages
        scale = self._image_config.scale
        trim_left = int(self._image_config.trim.left * width / 100)
        trim_right = int(self._image_config.trim.right * width / 100)
        trim_top = int(self._image_config.trim.top * height / 100)
        trim_bottom = int(self._image_config.trim.bottom * height / 100)
        
        # Calculate final image dimensions after trimming
        trimmed_height = height - trim_top - trim_bottom
        trimmed_width = width - trim_left - trim_right
        
        # Validate that we have a valid image after trimming
        if trimmed_width == 0 or trimmed_height == 0:
            return None, {}, set()

        # Create a new RGBA image for the map
        image = Image.new('RGBA', (trimmed_width, trimmed_height))
        pixels = image.load()
        
        _LOGGER.debug("trim_bottom = %s, trim_top = %s, trim_left = %s, trim_right = %s",
                      trim_bottom, trim_top, trim_left, trim_right)
        
        # Track unknown pixel types for debugging
        unknown_pixels = set()
        
        # Iterate through each pixel in the trimmed image
        for img_y in range(trimmed_height):
            # Flip Y coordinate to convert from image coordinates to map coordinates
            y = trimmed_height - 1 - img_y
            
            for img_x in range(trimmed_width):
                x = img_x
                
                # Get the pixel type from the raw map data (accounting for trimming)
                pixel_type = map_data[(img_y + trim_bottom) * width + x + trim_left]
                
                # Check if this is a standard map element (wall, floor, etc.)
                if pixel_type in self.color_map:
                    pixels[x, y] = self.color_map[pixel_type]
                
                # Check if this pixel represents a room (either normal or selected/cleaned)
                elif XiaomiImageParser.MAP_ROOM_MIN <= pixel_type <= XiaomiImageParser.MAP_SELECTED_ROOM_MAX:
                    # Calculate position in original (untrimmed) coordinates for room bounds
                    room_x = img_x + trim_left
                    room_y = img_y + trim_bottom
                    room_number = pixel_type
                    
                    # Selected rooms (being cleaned) have IDs offset by 50
                    # Convert them back to normal room IDs and track as cleaned
                    if pixel_type >= XiaomiImageParser.MAP_SELECTED_ROOM_MIN:
                        room_number = pixel_type - XiaomiImageParser.MAP_SELECTED_ROOM_MIN + \
                            XiaomiImageParser.MAP_ROOM_MIN
                        cleaned_areas.add(room_number)
                    
                    # Update room bounding box - expand to include this pixel
                    if room_number not in rooms:
                        # First pixel of this room - initialize bounds
                        rooms[room_number] = (room_x, room_y, room_x, room_y)
                    else:
                        # Expand existing bounds to include this pixel
                        rooms[room_number] = (
                            min(rooms[room_number][0], room_x),  # min_x
                            min(rooms[room_number][1], room_y),  # min_y
                            max(rooms[room_number][2], room_x),  # max_x
                            max(rooms[room_number][3], room_y)   # max_y
                        )
                    
                    # Color the pixel according to its room
                    pixels[x, y] = self._palette.get_room_color(room_number)
                
                # Unknown pixel type - mark it and log for debugging
                else:
                    pixels[x, y] = self._palette.get_color(SupportedColor.UNKNOWN)
                    unknown_pixels.add(pixel_type)
                    _LOGGER.debug("unknown pixel [%s,%s] = %s", x, y, pixel_type)
        # Apply scaling if configured (use nearest neighbor to preserve pixel boundaries)
        if self._image_config.scale != 1 and trimmed_width != 0 and trimmed_height != 0:
            image = image.resize(
                (int(trimmed_width * scale), int(trimmed_height * scale)),
                resample=Resampling.NEAREST
            )
        
        # Log any unknown pixel types encountered during parsing
        if len(unknown_pixels) > 0:
            _LOGGER.warning('unknown pixel_types: %s', unknown_pixels)
        
        return image, rooms, cleaned_areas

    @staticmethod
    def get_current_vacuum_room(map_data: bytes, vacuum_position_on_image: Point, image_width: int) -> int | None:
        """Determine which room the vacuum is currently in.
        
        Args:
            map_data: Raw byte data from the vacuum's map
            vacuum_position_on_image: Current position of the vacuum on the map
            image_width: Width of the map image in pixels
            
        Returns:
            The room ID where the vacuum is located, or None if not in a room
        """
        _LOGGER.debug("pos on image: %s", vacuum_position_on_image)
        
        # Calculate the pixel type at the vacuum's position
        pixel_type = map_data[
            int(vacuum_position_on_image.y) * image_width + int(vacuum_position_on_image.x)
        ]
        
        # Check if the vacuum is in a normal (unselected) room
        if XiaomiImageParser.MAP_ROOM_MIN <= pixel_type <= XiaomiImageParser.MAP_ROOM_MAX:
            return pixel_type
        
        # Check if the vacuum is in a selected/cleaned room and convert to normal room ID
        if XiaomiImageParser.MAP_SELECTED_ROOM_MIN <= pixel_type <= XiaomiImageParser.MAP_SELECTED_ROOM_MAX:
            return pixel_type - XiaomiImageParser.MAP_SELECTED_ROOM_MIN + XiaomiImageParser.MAP_ROOM_MIN
        
        # Vacuum is not in a room (might be on wall, outside, etc.)
        return None
