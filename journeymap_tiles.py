import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


TILE_PATTERN = re.compile(r"^(-?\d+),(-?\d+)\.png$", re.IGNORECASE)
DEFAULT_MAX_PIXELS = 250_000_000


class MapTooLargeError(ValueError):
    pass


@dataclass(frozen=True)
class MapLayerInfo:
    source: str
    dimension: str
    layer: str
    tile_count: int
    tile_width: int
    tile_height: int
    min_tile_x: int
    max_tile_x: int
    min_tile_z: int
    max_tile_z: int

    @property
    def grid_width(self):
        return self.max_tile_x - self.min_tile_x + 1

    @property
    def grid_height(self):
        return self.max_tile_z - self.min_tile_z + 1

    @property
    def full_width(self):
        return self.grid_width * self.tile_width

    @property
    def full_height(self):
        return self.grid_height * self.tile_height

    @property
    def missing_tiles(self):
        return self.grid_width * self.grid_height - self.tile_count

    @property
    def min_block_x(self):
        return self.min_tile_x * self.tile_width

    @property
    def max_block_x(self):
        return (self.max_tile_x + 1) * self.tile_width - 1

    @property
    def min_block_z(self):
        return self.min_tile_z * self.tile_height

    @property
    def max_block_z(self):
        return (self.max_tile_z + 1) * self.tile_height - 1


def _source_dir(root, source):
    return Path(root) / "data" / Path(source)


def discover_dimensions(root, source):
    source_dir = _source_dir(root, source)
    if not source_dir.is_dir():
        return []
    dimensions = []
    for path in source_dir.iterdir():
        if path.is_dir() and any(TILE_PATTERN.match(item.name) for item in path.rglob("*.png")):
            dimensions.append(path.name)
    preferred = {"overworld": 0, "the_nether": 1, "the_end": 2}
    return sorted(dimensions, key=lambda value: (preferred.get(value, 99), value.casefold()))


def discover_layers(root, source, dimension):
    dimension_dir = _source_dir(root, source) / dimension
    if not dimension_dir.is_dir():
        return []
    layers = []
    for path in dimension_dir.iterdir():
        if path.is_dir():
            count = sum(1 for item in path.glob("*.png") if TILE_PATTERN.match(item.name))
            if count:
                layers.append((path.name, count))
    preferred = {"day": 0, "night": 1, "topo": 2, "biome": 3}
    return sorted(
        layers,
        key=lambda item: (
            preferred.get(item[0], 10),
            -item[1] if item[0] not in preferred else 0,
            _numeric_layer_key(item[0]),
        ),
    )


def _numeric_layer_key(value):
    try:
        return (0, -int(value))
    except ValueError:
        return (1, value.casefold())


def _layer_tiles(root, source, dimension, layer):
    layer_dir = _source_dir(root, source) / dimension / layer
    tiles = []
    for path in layer_dir.glob("*.png"):
        match = TILE_PATTERN.match(path.name)
        if match:
            tiles.append((int(match.group(1)), int(match.group(2)), path))
    return sorted(tiles, key=lambda item: (item[1], item[0]))


def analyze_layer(root, source, dimension, layer):
    tiles = _layer_tiles(root, source, dimension, layer)
    if not tiles:
        raise ValueError("所选图层没有可拼接的 JourneyMap PNG。")
    with Image.open(tiles[0][2]) as first:
        tile_width, tile_height = first.size
    if tile_width <= 0 or tile_height <= 0:
        raise ValueError("JourneyMap 瓦片尺寸无效。")
    xs = [item[0] for item in tiles]
    zs = [item[1] for item in tiles]
    return MapLayerInfo(
        source=source, dimension=dimension, layer=layer,
        tile_count=len(tiles), tile_width=tile_width, tile_height=tile_height,
        min_tile_x=min(xs), max_tile_x=max(xs), min_tile_z=min(zs), max_tile_z=max(zs),
    )


def output_dimensions(info, scale):
    scale = float(scale)
    if scale <= 0 or scale > 1:
        raise ValueError("地图缩放比例必须大于 0 且不超过 100%。")
    tile_width = max(1, round(info.tile_width * scale))
    tile_height = max(1, round(info.tile_height * scale))
    return info.grid_width * tile_width, info.grid_height * tile_height, tile_width, tile_height


def _marker_font(size):
    candidates = (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    )
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _draw_markers(image, markers, info, scale, scaled_tile_width, scaled_tile_height):
    if not markers:
        return 0
    draw = ImageDraw.Draw(image)
    radius = max(4, round(7 * max(scale, 0.5)))
    font = _marker_font(max(10, round(14 * max(scale, 0.75))))
    count = 0
    for marker in markers:
        try:
            x = float(marker.get("x"))
            z = float(marker.get("z"))
        except (TypeError, ValueError):
            continue
        px = round((x / info.tile_width - info.min_tile_x) * scaled_tile_width)
        py = round((z / info.tile_height - info.min_tile_z) * scaled_tile_height)
        if not (0 <= px < image.width and 0 <= py < image.height):
            continue
        draw.ellipse(
            (px - radius, py - radius, px + radius, py + radius),
            fill=(255, 75, 75, 235), outline=(255, 255, 255, 255), width=max(1, radius // 3),
        )
        name = str(marker.get("name") or "坐标")
        draw.text((px + radius + 3, py - radius), name, font=font, fill=(255, 255, 255, 255),
                  stroke_width=2, stroke_fill=(0, 0, 0, 220))
        count += 1
    return count


def stitch_layer(
    root, source, dimension, layer, output_path, scale=0.25, markers=None,
    max_pixels=DEFAULT_MAX_PIXELS, progress=None, stop_event=None,
):
    info = analyze_layer(root, source, dimension, layer)
    width, height, scaled_tile_width, scaled_tile_height = output_dimensions(info, scale)
    pixels = width * height
    if pixels > max_pixels:
        raise MapTooLargeError(
            f"输出将达到 {width}×{height}（{pixels / 1_000_000:.1f} MP），"
            f"超过安全上限 {max_pixels / 1_000_000:.0f} MP，请降低缩放比例。"
        )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
    metadata_temp = None
    tiles = _layer_tiles(root, source, dimension, layer)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    try:
        for index, (tile_x, tile_z, path) in enumerate(tiles, start=1):
            if stop_event is not None and stop_event.is_set():
                raise InterruptedError("地图拼接已停止。")
            with Image.open(path) as opened:
                tile = opened.convert("RGBA")
                if tile.size != (scaled_tile_width, scaled_tile_height):
                    tile = tile.resize((scaled_tile_width, scaled_tile_height), Image.Resampling.LANCZOS)
                left = (tile_x - info.min_tile_x) * scaled_tile_width
                top = (tile_z - info.min_tile_z) * scaled_tile_height
                canvas.alpha_composite(tile, (left, top))
            if progress:
                progress(index, len(tiles))
        marker_count = _draw_markers(
            canvas, markers or [], info, float(scale), scaled_tile_width, scaled_tile_height
        )
        canvas.save(temp_path, format="PNG", optimize=True)
        temp_path.replace(output_path)
        metadata = {
            **asdict(info),
            "grid_width": info.grid_width,
            "grid_height": info.grid_height,
            "missing_tiles": info.missing_tiles,
            "scale": float(scale),
            "output_width": width,
            "output_height": height,
            "pixels_per_block_x": scaled_tile_width / info.tile_width,
            "pixels_per_block_z": scaled_tile_height / info.tile_height,
            "block_bounds": {
                "min_x": info.min_block_x, "max_x": info.max_block_x,
                "min_z": info.min_block_z, "max_z": info.max_block_z,
            },
            "marker_count": marker_count,
            "orientation": "north_up; x_right; z_down",
        }
        metadata_path = output_path.with_suffix(output_path.suffix + ".json")
        metadata_temp = metadata_path.with_name(f".{metadata_path.name}.{uuid.uuid4().hex}.tmp")
        metadata_temp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        metadata_temp.replace(metadata_path)
        return info, output_path, metadata_path, marker_count
    finally:
        canvas.close()
        if temp_path.exists():
            temp_path.unlink()
        if metadata_temp is not None and metadata_temp.exists():
            metadata_temp.unlink()
