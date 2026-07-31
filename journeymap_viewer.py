import re
import tkinter as tk
from collections import OrderedDict
from math import floor
from pathlib import Path

from PIL import Image, ImageTk


TILE_PATTERN = re.compile(r"^(-?\d+),(-?\d+)\.png$", re.IGNORECASE)


class JourneyMapTileCanvas(tk.Canvas):
    MIN_ZOOM = 0.02
    MAX_ZOOM = 4.0
    MAX_CACHE_PIXELS = 32_000_000
    MAX_CACHE_ITEMS = 400

    def __init__(
        self, parent, background="#111827", foreground="#e5e7eb", muted="#94a3b8",
        accent="#ef4444", status_callback=None,
    ):
        super().__init__(parent, bg=background, highlightthickness=0, cursor="fleur")
        self.foreground = foreground
        self.muted = muted
        self.accent = accent
        self.status_callback = status_callback
        self.root_path = None
        self.source = ""
        self.dimension = ""
        self.layer = ""
        self.layer_dir = None
        self.tile_index = {}
        self.tile_width = 512
        self.tile_height = 512
        self.center_x = 0.0
        self.center_z = 0.0
        self.zoom = 0.5
        self.markers = []
        self.marker_provider = None
        self.image_cache = OrderedDict()
        self.cache_pixels = 0
        self.visible_tile_count = 0
        self.drag_anchor = None
        self._redraw_job = None
        self._poll_job = None
        self._destroyed = False

        self.bind("<Configure>", lambda _event: self.schedule_redraw())
        self.bind("<ButtonPress-1>", self._start_drag)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._end_drag)
        self.bind("<MouseWheel>", self._mouse_wheel)
        self.bind("<Motion>", self._motion)
        self.bind("<Double-Button-1>", lambda event: self.zoom_at(event.x, event.y, 1.5))
        self.bind("<Destroy>", self._on_destroy)
        self._schedule_poll()

    def set_layer(self, root, source, dimension, layer, markers=None, marker_provider=None):
        identity = (str(root), source, dimension, layer)
        previous = (str(self.root_path), self.source, self.dimension, self.layer)
        changed = identity != previous
        self.root_path = Path(root)
        self.source = source
        self.dimension = dimension
        self.layer = layer
        self.layer_dir = self.root_path / "data" / Path(source) / dimension / layer
        self.markers = list(markers or [])
        self.marker_provider = marker_provider
        self._scan_tiles()
        if changed:
            self.clear_cache()
            self._center_initial_view()
        self.schedule_redraw()

    def clear_layer(self, message="没有可显示的地图瓦片"):
        self.layer_dir = None
        self.tile_index = {}
        self.markers = []
        self.clear_cache()
        self.delete("all")
        self.create_text(
            max(10, self.winfo_width() // 2), max(10, self.winfo_height() // 2),
            text=message, fill=self.muted, font=("Microsoft YaHei UI", 11),
        )

    def _scan_tiles(self):
        index = {}
        if self.layer_dir and self.layer_dir.is_dir():
            for path in self.layer_dir.glob("*.png"):
                match = TILE_PATTERN.match(path.name)
                if not match:
                    continue
                try:
                    mtime_ns = path.stat().st_mtime_ns
                except OSError:
                    continue
                index[(int(match.group(1)), int(match.group(2)))] = (path, mtime_ns)
        self.tile_index = index
        if index:
            first_path = next(iter(index.values()))[0]
            try:
                with Image.open(first_path) as image:
                    self.tile_width, self.tile_height = image.size
            except OSError:
                self.tile_width = self.tile_height = 512

    def _center_initial_view(self):
        if not self.tile_index:
            return
        min_x, max_x, min_z, max_z = self.world_bounds()
        valid_markers = []
        for marker in self.markers:
            try:
                x, z = float(marker.get("x")), float(marker.get("z"))
            except (TypeError, ValueError):
                continue
            if min_x <= x <= max_x and min_z <= z <= max_z:
                valid_markers.append((x, z))
        if valid_markers:
            self.center_x, self.center_z = valid_markers[0]
            self.zoom = 0.5
        else:
            self.center_x = (min_x + max_x) / 2
            self.center_z = (min_z + max_z) / 2
            self.zoom = 0.25

    def world_bounds(self):
        if not self.tile_index:
            return 0, 0, 0, 0
        xs = [coordinate[0] for coordinate in self.tile_index]
        zs = [coordinate[1] for coordinate in self.tile_index]
        return (
            min(xs) * self.tile_width,
            (max(xs) + 1) * self.tile_width - 1,
            min(zs) * self.tile_height,
            (max(zs) + 1) * self.tile_height - 1,
        )

    def clear_cache(self):
        self.image_cache.clear()
        self.cache_pixels = 0

    def _cache_photo(self, key, path, width, height):
        cached = self.image_cache.get(key)
        if cached is not None:
            self.image_cache.move_to_end(key)
            return cached
        with Image.open(path) as opened:
            image = opened.convert("RGBA")
            if image.size != (width, height):
                image = image.resize((width, height), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(image, master=self)
        pixels = width * height
        self.image_cache[key] = (photo, pixels)
        self.cache_pixels += pixels
        while (
            len(self.image_cache) > self.MAX_CACHE_ITEMS or
            self.cache_pixels > self.MAX_CACHE_PIXELS
        ):
            _old_key, (_old_photo, old_pixels) = self.image_cache.popitem(last=False)
            self.cache_pixels -= old_pixels
        return self.image_cache[key]

    def schedule_redraw(self):
        if self._destroyed or self._redraw_job is not None:
            return
        self._redraw_job = self.after_idle(self.redraw)

    def redraw(self):
        self._redraw_job = None
        if self._destroyed or not self.winfo_exists():
            return
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        if not self.tile_index:
            self.create_text(
                width // 2, height // 2, text="所选图层暂无地图瓦片",
                fill=self.muted, font=("Microsoft YaHei UI", 11),
            )
            self.visible_tile_count = 0
            return

        scaled_width = max(1, round(self.tile_width * self.zoom))
        scaled_height = max(1, round(self.tile_height * self.zoom))
        world_left = self.center_x - width / (2 * self.zoom)
        world_right = self.center_x + width / (2 * self.zoom)
        world_top = self.center_z - height / (2 * self.zoom)
        world_bottom = self.center_z + height / (2 * self.zoom)
        min_tile_x = floor(world_left / self.tile_width) - 1
        max_tile_x = floor(world_right / self.tile_width) + 1
        min_tile_z = floor(world_top / self.tile_height) - 1
        max_tile_z = floor(world_bottom / self.tile_height) + 1

        visible = 0
        for tile_z in range(min_tile_z, max_tile_z + 1):
            for tile_x in range(min_tile_x, max_tile_x + 1):
                entry = self.tile_index.get((tile_x, tile_z))
                if entry is None:
                    continue
                path, mtime_ns = entry
                cache_key = (str(path), mtime_ns, scaled_width, scaled_height)
                try:
                    photo, _pixels = self._cache_photo(
                        cache_key, path, scaled_width, scaled_height
                    )
                except OSError:
                    continue
                screen_x = round(width / 2 + (tile_x * self.tile_width - self.center_x) * self.zoom)
                screen_y = round(height / 2 + (tile_z * self.tile_height - self.center_z) * self.zoom)
                self.create_image(screen_x, screen_y, image=photo, anchor=tk.NW, tags=("tile",))
                visible += 1
        self.visible_tile_count = visible
        self._draw_markers(width, height)
        self.create_text(
            10, 10, anchor=tk.NW,
            text=f"缩放 {self.zoom * 100:.0f}% · 可见瓦片 {visible}/{len(self.tile_index)} · 实时刷新",
            fill=self.foreground, font=("Microsoft YaHei UI", 8, "bold"),
            tags=("overlay",),
        )

    def _draw_markers(self, width, height):
        for marker in self.markers:
            try:
                world_x = float(marker.get("x"))
                world_z = float(marker.get("z"))
            except (TypeError, ValueError):
                continue
            screen_x = width / 2 + (world_x - self.center_x) * self.zoom
            screen_y = height / 2 + (world_z - self.center_z) * self.zoom
            if not (-30 <= screen_x <= width + 30 and -30 <= screen_y <= height + 30):
                continue
            radius = 6
            self.create_oval(
                screen_x - radius, screen_y - radius, screen_x + radius, screen_y + radius,
                fill=self.accent, outline="white", width=2, tags=("marker",),
            )
            self.create_text(
                screen_x + 10, screen_y - 9, anchor=tk.NW,
                text=str(marker.get("name") or "坐标"), fill="white",
                font=("Microsoft YaHei UI", 8, "bold"), tags=("marker",),
            )

    def _start_drag(self, event):
        self.drag_anchor = (event.x, event.y, self.center_x, self.center_z)

    def _drag(self, event):
        if not self.drag_anchor:
            return
        start_x, start_y, center_x, center_z = self.drag_anchor
        self.center_x = center_x - (event.x - start_x) / self.zoom
        self.center_z = center_z - (event.y - start_y) / self.zoom
        self.schedule_redraw()
        self._emit_status(self.center_x, self.center_z, "拖动中")

    def _end_drag(self, _event):
        self.drag_anchor = None

    def _mouse_wheel(self, event):
        self.zoom_at(event.x, event.y, 1.25 if event.delta > 0 else 0.8)
        return "break"

    def zoom_at(self, screen_x, screen_y, factor):
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        world_x, world_z = self.screen_to_world(screen_x, screen_y)
        new_zoom = min(self.MAX_ZOOM, max(self.MIN_ZOOM, self.zoom * factor))
        if abs(new_zoom - self.zoom) < 1e-9:
            return
        self.zoom = new_zoom
        self.center_x = world_x - (screen_x - width / 2) / self.zoom
        self.center_z = world_z - (screen_y - height / 2) / self.zoom
        self.schedule_redraw()
        self._emit_status(world_x, world_z, f"缩放 {self.zoom * 100:.0f}%")

    def zoom_in(self):
        self.zoom_at(self.winfo_width() / 2, self.winfo_height() / 2, 1.5)

    def zoom_out(self):
        self.zoom_at(self.winfo_width() / 2, self.winfo_height() / 2, 2 / 3)

    def fit_bounds(self):
        if not self.tile_index:
            return
        min_x, max_x, min_z, max_z = self.world_bounds()
        world_width = max(1, max_x - min_x + 1)
        world_height = max(1, max_z - min_z + 1)
        self.center_x = (min_x + max_x) / 2
        self.center_z = (min_z + max_z) / 2
        self.zoom = min(
            self.MAX_ZOOM,
            max(self.MIN_ZOOM, min(
                max(1, self.winfo_width() - 24) / world_width,
                max(1, self.winfo_height() - 24) / world_height,
            )),
        )
        self.schedule_redraw()

    def center_on(self, world_x, world_z, zoom=None):
        self.center_x = float(world_x)
        self.center_z = float(world_z)
        if zoom is not None:
            self.zoom = min(self.MAX_ZOOM, max(self.MIN_ZOOM, float(zoom)))
        self.schedule_redraw()

    def screen_to_world(self, screen_x, screen_y):
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        return (
            self.center_x + (screen_x - width / 2) / self.zoom,
            self.center_z + (screen_y - height / 2) / self.zoom,
        )

    def _motion(self, event):
        if self.drag_anchor:
            return
        world_x, world_z = self.screen_to_world(event.x, event.y)
        self._emit_status(world_x, world_z, "鼠标位置")

    def _emit_status(self, world_x, world_z, prefix):
        if self.status_callback:
            self.status_callback(f"{prefix} · X {round(world_x)} · Z {round(world_z)}")

    def refresh_now(self):
        old_index = self.tile_index
        self._scan_tiles()
        tiles_changed = self.tile_index != old_index
        markers_changed = False
        if self.marker_provider:
            try:
                new_markers = list(self.marker_provider() or [])
            except Exception:
                new_markers = self.markers
            marker_key = lambda item: (item.get("name"), str(item.get("x")), str(item.get("z")))
            markers_changed = [marker_key(item) for item in new_markers] != [
                marker_key(item) for item in self.markers
            ]
            self.markers = new_markers
        if tiles_changed:
            self.clear_cache()
        if tiles_changed or markers_changed:
            self.schedule_redraw()
        return tiles_changed, markers_changed

    def _schedule_poll(self):
        if not self._destroyed:
            self._poll_job = self.after(2000, self._poll)

    def _poll(self):
        self._poll_job = None
        if self._destroyed or not self.winfo_exists():
            return
        self.refresh_now()
        self._schedule_poll()

    def _on_destroy(self, event):
        if event.widget is not self:
            return
        self._destroyed = True
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        if self._redraw_job is not None:
            try:
                self.after_cancel(self._redraw_job)
            except tk.TclError:
                pass
            self._redraw_job = None
        self.clear_cache()
