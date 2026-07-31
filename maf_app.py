"""MAF desktop application and automation workers."""

import csv
import ctypes
import json
import os
import queue
import shutil
import sqlite3
import sys
import threading
import time
import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pyautogui
from PIL import Image, ImageDraw, ImageTk

from history_store import HistoryStore
from notification_service import WindowsNotifier
from profile_store import normalize_profile, normalize_profiles
from window_guard import get_foreground_window_title, is_minecraft_foreground
from workflow_model import (
    WORKFLOW_PRESETS,
    WORKFLOW_TYPES,
    build_step,
    describe_workflow_step,
    load_workflow_file,
    normalize_steps,
    save_workflow_file,
    step_to_parameters,
    workflow_document,
)

try:
    import pystray
except ImportError:  # The main app remains usable without tray support.
    pystray = None

from minecraft_auto_fish_subtitle import (
    DEFAULT_KEYWORDS,
    check_tesseract_ready,
    default_subtitle_region,
    ocr_capture,
    ocr_text,
    parse_keywords,
    parse_region,
    text_matches,
)
from journeymap_sync import (
    discover_sources,
    export_waypoints,
    scan_waypoints,
    sync_coordinates,
    waypoint_key,
    write_waypoint,
)
from journeymap_tiles import (
    DEFAULT_MAX_PIXELS,
    analyze_layer,
    discover_dimensions,
    discover_layers,
    output_dimensions,
    stitch_layer,
)
from journeymap_viewer import JourneyMapTileCanvas


APP_NAME = "MAF"
APP_FULL_NAME = "Minecraft Automation Framework"
APPLICATION_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
APP_DATA_DIR = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming")) / APP_NAME
CONFIG_PATH = APP_DATA_DIR / "settings.json"
HISTORY_PATH = APP_DATA_DIR / "data" / "history.db"
DEFAULT_SCREENSHOT_DIR = Path.home() / "Pictures" / APP_NAME
LEGACY_CONFIG_PATH = APPLICATION_DIR / "mc_assistant_settings.json"
LEGACY_HISTORY_PATH = APPLICATION_DIR / "assistant_history.db"


def prepare_app_data(data_dir=APP_DATA_DIR, legacy_config=LEGACY_CONFIG_PATH, legacy_history=LEGACY_HISTORY_PATH):
    """Create MAF's writable data tree and migrate legacy project-local data once."""
    data_dir = Path(data_dir)
    config_path = data_dir / "settings.json"
    history_path = data_dir / "data" / "history.db"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    migrated = []
    legacy_config = Path(legacy_config)
    if not config_path.exists() and legacy_config.is_file():
        shutil.copy2(legacy_config, config_path)
        migrated.append("settings")
    legacy_history = Path(legacy_history)
    if not history_path.exists() and legacy_history.is_file():
        temporary = history_path.with_suffix(".db.migrating")
        temporary.unlink(missing_ok=True)
        source = target = None
        try:
            source = sqlite3.connect(legacy_history)
            target = sqlite3.connect(temporary)
            source.backup(target)
            target.commit()
            target.close()
            target = None
            source.close()
            source = None
            temporary.replace(history_path)
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()
            temporary.unlink(missing_ok=True)
        migrated.append("history")
    return migrated


def upgrade_brand_config(data):
    """Replace legacy default folders without touching user-selected custom paths."""
    if not isinstance(data, dict):
        return {}
    legacy_screenshot_dir = str(Path.home() / "Pictures" / "Minecraft助手截图")
    new_screenshot_dir = str(DEFAULT_SCREENSHOT_DIR)
    tools = data.get("tools")
    if isinstance(tools, dict) and tools.get("screenshot_dir") == legacy_screenshot_dir:
        tools["screenshot_dir"] = new_screenshot_dir
    profiles = data.get("profiles")
    if isinstance(profiles, dict):
        for profile in profiles.values():
            profile_tools = profile.get("tools") if isinstance(profile, dict) else None
            if isinstance(profile_tools, dict) and profile_tools.get("screenshot_dir") == legacy_screenshot_dir:
                profile_tools["screenshot_dir"] = new_screenshot_dir
    return data

DARK_COLORS = {
    "bg": "#0b0f14",
    "sidebar": "#10161f",
    "surface": "#151d28",
    "surface_2": "#1a2431",
    "border": "#263345",
    "text": "#f2f5f8",
    "muted": "#8f9dad",
    "green": "#63d17f",
    "green_dark": "#1b4b2d",
    "blue": "#5da9ff",
    "amber": "#ffbd5c",
    "red": "#ff6b6b",
    "primary_text": "#08110b",
    "danger_bg": "#351e25",
    "danger_active": "#4a252d",
    "danger_text": "#ff9b9b",
    "hero": "#173322",
    "selection": "#28523a",
    "mono_text": "#c8d4df",
    "image_bg": "#080b10",
    "overlay": "#10161f",
}

LIGHT_COLORS = {
    "bg": "#eef3f8",
    "sidebar": "#ffffff",
    "surface": "#ffffff",
    "surface_2": "#e7eef5",
    "border": "#ccd7e2",
    "text": "#17222d",
    "muted": "#647587",
    "green": "#279b55",
    "green_dark": "#d8f0e0",
    "blue": "#2378c9",
    "amber": "#ae6500",
    "red": "#cf3f4d",
    "primary_text": "#ffffff",
    "danger_bg": "#fde8eb",
    "danger_active": "#f7d3d8",
    "danger_text": "#b52f3d",
    "hero": "#dff3e6",
    "selection": "#cce8d6",
    "mono_text": "#34495c",
    "image_bg": "#dce5ed",
    "overlay": "#17222d",
}

COLORS = DARK_COLORS.copy()


def set_color_mode(dark_mode):
    COLORS.clear()
    COLORS.update(DARK_COLORS if dark_mode else LIGHT_COLORS)

HOTKEY_VK = {f"F{number}": 0x6F + number for number in range(6, 13)}


class RegionSelector(tk.Toplevel):
    def __init__(self, parent, on_selected):
        super().__init__(parent)
        self.on_selected = on_selected
        self.start_x = 0
        self.start_y = 0
        self.rect_id = None

        self.withdraw()
        self.screenshot = pyautogui.screenshot()
        self.photo = ImageTk.PhotoImage(self.screenshot)
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.configure(cursor="crosshair")

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW)
        self.canvas.create_rectangle(14, 14, 720, 64, fill=COLORS["overlay"], outline="")
        self.canvas.create_text(
            30,
            39,
            text="拖拽框选字幕文字区域  ·  范围越小识别越快  ·  Esc 取消",
            fill="white",
            anchor=tk.W,
            font=("Microsoft YaHei UI", 14, "bold"),
        )
        self.bind("<Escape>", lambda _event: self.destroy())
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.deiconify()

    def on_press(self, event):
        self.start_x, self.start_y = event.x, event.y
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline=COLORS["green"], width=3
        )

    def on_drag(self, event):
        if self.rect_id:
            self.canvas.coords(self.rect_id, self.start_x, self.start_y, event.x, event.y)

    def on_release(self, event):
        x1, x2 = sorted((self.start_x, event.x))
        y1, y2 = sorted((self.start_y, event.y))
        if x2 - x1 >= 20 and y2 - y1 >= 20:
            self.on_selected((x1, y1, x2 - x1, y2 - y1))
        self.destroy()


class TaskWorker:
    task_name = "任务"

    def __init__(self, settings, event_queue):
        self.settings = settings
        self.event_queue = event_queue
        self.stop_event = threading.Event()
        self.thread = None
        self.started_at = None
        self._guard_paused = False

    def start(self):
        self.thread = threading.Thread(target=self._run_safely, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def wait(self, seconds):
        return self.stop_event.wait(max(0, seconds))

    def emit(self, kind, payload):
        self.event_queue.put((kind, payload))

    def log(self, message):
        self.emit("log", message)

    def stats(self, **values):
        values.update(task=self.task_name, started_at=self.started_at)
        self.emit("stats", values)

    def _run_safely(self):
        self.started_at = time.monotonic()
        self.emit("task_started", self.task_name)
        pyautogui.FAILSAFE = True
        try:
            self.run()
        except pyautogui.FailSafeException:
            self.log("已触发安全停止：鼠标移动到了屏幕左上角。")
        except Exception as exc:
            self.log(f"任务出错：{exc}")
            self.emit("task_error", {"task": self.task_name, "error": str(exc)})
        finally:
            self.stop_event.set()
            self.emit("task_finished", self.task_name)

    def countdown(self):
        delay = self.settings.get("start_delay", 0)
        if delay:
            self.log(f"{delay:g} 秒后开始，请切回 Minecraft。")
            return self.wait(delay)
        return False

    def wait_for_minecraft(self):
        if not self.settings.get("require_minecraft", False):
            return not self.stop_event.is_set()
        while not self.stop_event.is_set() and not is_minecraft_foreground():
            if not self._guard_paused:
                self._guard_paused = True
                self.log("Minecraft 不在前台，已暂停输入；切回游戏后自动继续。")
                self.stats(detail="已暂停 · 等待 Minecraft 回到前台")
            self.stop_event.wait(0.2)
        if self.stop_event.is_set():
            return False
        if self._guard_paused:
            self._guard_paused = False
            self.log("检测到 Minecraft 回到前台，继续任务。")
        return True

    def run(self):
        raise NotImplementedError


class AutoFishWorker(TaskWorker):
    task_name = "自动钓鱼"

    def run(self):
        ready, detail = check_tesseract_ready()
        if not ready:
            raise RuntimeError(f"OCR 不可用：{detail}")
        self.log(f"OCR 已就绪：Tesseract {detail}")
        if self.countdown():
            return

        catches = timeouts = casts = 0
        if not self.wait_for_minecraft():
            return
        self.log("抛竿。")
        pyautogui.click(button="right")
        casts += 1
        self.stats(catches=catches, timeouts=timeouts, actions=casts, detail="等待鱼上钩")
        if self.wait(self.settings["cast_delay"]):
            return

        while not self.stop_event.is_set():
            found, text, waited = self.wait_for_splash()
            if found is None:
                break
            if found:
                catches += 1
                result = "收竿成功"
                self.log(f"识别到水花，收竿。识别文本：{text!r}")
                record = f"{time.strftime('%H:%M:%S')}  ✓ 收竿成功 ({waited:.1f}s)  {text[:40] or '无文本'}"
            else:
                timeouts += 1
                result = "等待超时"
                self.log("等待超时，重置鱼竿。")
                record = f"{time.strftime('%H:%M:%S')}  ! 等待超时 ({waited:.1f}s)  {text[:40] or '无文本'}"
            if not self.wait_for_minecraft():
                break
            pyautogui.click(button="right")
            self.stats(
                catches=catches,
                timeouts=timeouts,
                actions=casts,
                casts=casts,
                last_ocr=text,
                record=record,
                record_data={
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "result": result,
                    "wait_seconds": round(waited, 2),
                    "catches": catches,
                    "timeouts": timeouts,
                    "casts": casts,
                    "ocr_text": text,
                },
                detail=f"最近识别：{text[:60] or '无'}",
            )
            if self.wait(self.settings["recast_delay"]):
                break
            if not self.wait_for_minecraft():
                break
            pyautogui.click(button="right")
            casts += 1
            self.log("再次抛竿。")
            self.stats(
                catches=catches, timeouts=timeouts, actions=casts, casts=casts,
                last_ocr=text, detail="等待鱼上钩",
            )
            if self.wait(self.settings["cast_delay"]):
                break

    def wait_for_splash(self):
        started = time.monotonic()
        last_text = ""
        while not self.stop_event.is_set():
            guard_started = time.monotonic()
            if not self.wait_for_minecraft():
                break
            started += time.monotonic() - guard_started
            last_text = ocr_text(
                self.settings["region"],
                lang=self.settings["lang"],
                scale=self.settings["ocr_scale"],
            )
            if text_matches(last_text, self.settings["keywords"]):
                return True, last_text, time.monotonic() - started
            timeout = self.settings["timeout"]
            if timeout and time.monotonic() - started > timeout:
                return False, last_text, time.monotonic() - started
            if self.wait(self.settings["poll"]):
                break
        return None, last_text, time.monotonic() - started


class AutoClickWorker(TaskWorker):
    task_name = "自动连点"

    def run(self):
        if self.countdown():
            return
        button = self.settings["button"]
        interval = self.settings["interval"]
        limit = self.settings["limit"]
        count = 0
        self.log(f"开始{button == 'left' and '左键' or '右键'}连点。")
        while not self.stop_event.is_set() and (limit == 0 or count < limit):
            if not self.wait_for_minecraft():
                break
            pyautogui.click(button=button)
            count += 1
            if count == 1 or count % 10 == 0:
                self.stats(actions=count, detail=f"间隔 {interval:.2f} 秒")
            if self.wait(interval):
                break
        self.stats(actions=count, detail="已完成" if limit and count >= limit else "已停止")


class KeyHoldWorker(TaskWorker):
    task_name = "按键保持"

    def run(self):
        if self.countdown():
            return
        key = self.settings["key"]
        duration = self.settings["duration"]
        self.log(f"正在保持按键：{key.upper()}。")
        remaining = duration if duration else None
        pressed = False
        while not self.stop_event.is_set() and (remaining is None or remaining > 0):
            if not self.wait_for_minecraft():
                break
            pyautogui.keyDown(key)
            pressed = True
            pressed_at = time.monotonic()
            try:
                while not self.stop_event.wait(0.1):
                    if remaining is not None and time.monotonic() - pressed_at >= remaining:
                        break
                    if self.settings.get("require_minecraft") and not is_minecraft_foreground():
                        break
            finally:
                if pressed:
                    pyautogui.keyUp(key)
                    pressed = False
                if remaining is not None:
                    remaining -= time.monotonic() - pressed_at
        self.stats(actions=1, detail=f"已释放 {key.upper()}")


class ScheduledCommandWorker(TaskWorker):
    task_name = "定时指令"

    def run(self):
        if self.countdown():
            return
        command = self.settings["command"]
        interval = self.settings["interval"]
        limit = self.settings["count"]
        sent = 0
        self.log(f"开始定时发送：{command}")
        while not self.stop_event.is_set() and (limit == 0 or sent < limit):
            if not self.wait_for_minecraft():
                break
            pyautogui.press(self.settings["chat_key"])
            if self.wait(self.settings["chat_delay"]):
                break
            if not self.wait_for_minecraft():
                break
            pyautogui.write(command, interval=0.01)
            pyautogui.press("enter")
            sent += 1
            self.stats(actions=sent, detail=f"已发送 {sent} 次 · 下次间隔 {interval:g} 秒")
            self.log(f"指令已发送（{sent}{'/' + str(limit) if limit else ''}）。")
            if limit and sent >= limit:
                break
            if self.wait(interval):
                break
        self.stats(actions=sent, detail="发送完成" if limit and sent >= limit else "已停止")


class MapStitchWorker(TaskWorker):
    task_name = "地图拼接"

    def run(self):
        def report(done, total):
            self.stats(actions=done, detail=f"正在拼接瓦片 {done}/{total}")

        try:
            info, output_path, metadata_path, marker_count = stitch_layer(
                self.settings["root"], self.settings["source"],
                self.settings["dimension"], self.settings["layer"],
                self.settings["output"], scale=self.settings["scale"],
                markers=self.settings.get("markers"), progress=report,
                stop_event=self.stop_event,
            )
        except InterruptedError:
            self.log("地图拼接已停止，未留下不完整输出。")
            return
        self.stats(actions=info.tile_count, detail=f"地图已导出 · {info.tile_count} 块瓦片")
        self.log(f"地图拼接完成：{output_path}")
        self.log(f"坐标范围元数据：{metadata_path} · 标记 {marker_count} 个")


class WorkflowWorker(TaskWorker):
    task_name = "任务编排"

    def run(self):
        if self.countdown():
            return
        steps = self.settings["steps"]
        loop_limit = self.settings["loops"]
        completed = 0
        loop_index = 0
        while not self.stop_event.is_set() and (loop_limit == 0 or loop_index < loop_limit):
            loop_index += 1
            self.log(f"开始第 {loop_index}{'/' + str(loop_limit) if loop_limit else ''} 轮工作流。")
            step_index = 0
            while step_index < len(steps) and not self.stop_event.is_set():
                step = steps[step_index]
                display_index = step_index + 1
                self.log(f"步骤 {display_index}：{describe_workflow_step(step)}")
                skip = self.execute_step(step)
                if skip is None:
                    self.stats(actions=completed, detail=f"工作流在步骤 {display_index} 停止")
                    return
                completed += 1
                self.stats(
                    actions=completed,
                    detail=f"第 {loop_index} 轮 · 步骤 {display_index}/{len(steps)} · {step['type']}",
                )
                step_index += 1 + skip
        self.stats(actions=completed, detail="工作流已完成" if loop_limit and loop_index >= loop_limit else "工作流已停止")

    def execute_step(self, step):
        step_type = step["type"]
        if step_type == "等待":
            return None if self.wait(step["seconds"]) else 0
        if not self.wait_for_minecraft():
            return None
        if step_type == "鼠标点击":
            for _ in range(step["count"]):
                if not self.wait_for_minecraft():
                    return None
                pyautogui.click(button=step["button"])
                if self.wait(step["interval"]):
                    return None
            return 0
        if step_type == "按键":
            for _ in range(step["count"]):
                if not self.wait_for_minecraft():
                    return None
                pyautogui.press(step["key"])
                if self.wait(step["interval"]):
                    return None
            return 0
        if step_type == "按键保持":
            remaining = step["duration"]
            while remaining > 0 and not self.stop_event.is_set():
                if not self.wait_for_minecraft():
                    return None
                pyautogui.keyDown(step["key"])
                pressed_at = time.monotonic()
                try:
                    while not self.stop_event.wait(min(0.1, remaining)):
                        elapsed = time.monotonic() - pressed_at
                        if elapsed >= remaining:
                            break
                        if self.settings.get("require_minecraft") and not is_minecraft_foreground():
                            break
                finally:
                    pyautogui.keyUp(step["key"])
                    remaining -= time.monotonic() - pressed_at
            return None if self.stop_event.is_set() else 0
        if step_type == "发送指令":
            pyautogui.press(step.get("chat_key", "t"))
            if self.wait(0.15):
                return None
            if not self.wait_for_minecraft():
                return None
            pyautogui.write(step["text"], interval=0.01)
            pyautogui.press("enter")
            return 0
        if step_type == "截图":
            target = Path(step.get("directory") or self.settings["screenshot_dir"]).expanduser()
            target.mkdir(parents=True, exist_ok=True)
            filename = target / f"Workflow_{time.strftime('%Y%m%d_%H%M%S')}_{time.time_ns() % 100000}.png"
            pyautogui.screenshot().save(filename)
            self.log(f"工作流截图已保存：{filename}")
            return 0
        if step_type == "等待字幕":
            started = time.monotonic()
            while not self.stop_event.is_set():
                guard_started = time.monotonic()
                if not self.wait_for_minecraft():
                    return None
                started += time.monotonic() - guard_started
                text = self.read_ocr()
                if text_matches(text, tuple(step["keywords"])):
                    self.log(f"字幕条件已满足：{text!r}")
                    return 0
                if time.monotonic() - started >= step["timeout"]:
                    self.log(f"字幕等待超时：{', '.join(step['keywords'])}")
                    return None if step["on_timeout"] == "停止" else 0
                if self.wait(self.settings.get("ocr_poll", 0.1)):
                    return None
            return None
        if step_type == "OCR 分支":
            text = self.read_ocr()
            if text_matches(text, tuple(step["keywords"])):
                self.log(f"OCR 分支匹配，继续执行：{text!r}")
                return 0
            self.log(f"OCR 分支未匹配，跳过后续 {step['skip']} 步：{text!r}")
            return step["skip"]
        raise ValueError(f"未知工作流步骤：{step_type}")

    def read_ocr(self):
        return ocr_text(
            self.settings["ocr_region"],
            lang=self.settings.get("ocr_lang", "chi_sim+eng"),
            scale=self.settings.get("ocr_scale", 2.0),
        )


class MAFApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1160x760")
        self.minsize(980, 660)
        self.configure(bg=COLORS["bg"])
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.event_queue = queue.Queue()
        self.worker = None
        self.page_frames = {}
        self.nav_buttons = {}
        self.current_page = "dashboard"
        self.session_started = time.monotonic()
        self.timer_job = None
        self.timer_end = None
        self.total_screenshots = 0
        self.latest_stats = {}
        self.last_task_failed = False
        self.task_stop_requested = False
        self.current_run_id = None
        self.fish_session_running = False
        self.fish_records = []
        self.hotkey_stop_event = threading.Event()
        self.tray_icon = None
        self.exit_requested = False
        self.notifier = WindowsNotifier(APP_NAME)
        self.data_migrations = prepare_app_data()
        self.history_store = HistoryStore(HISTORY_PATH)
        self.workflow_edit_index = None
        self.workflow_drag_index = None

        self.config_data = self.load_config()
        self.create_variables()
        set_color_mode(self.dark_mode_var.get())
        self.configure(bg=COLORS["bg"])
        self.configure_styles()
        self.build_shell()
        if self.data_migrations:
            self.append_log(f"旧版数据已迁移到 {APP_DATA_DIR}：{', '.join(self.data_migrations)}")
        self.refresh_journeymap_sources(show_errors=False)
        self.show_page("dashboard")
        self.after(100, self.drain_events)
        self.after(500, self.tick_clock)
        self.after(1500, self.auto_sync_journeymap)
        threading.Thread(target=self.monitor_global_stop_hotkey, daemon=True).start()
        if self.tray_enabled_var.get():
            self.after(300, self.toggle_tray)

    def create_variables(self):
        default_region = default_subtitle_region()
        fish = self.config_data.get("fishing", {})
        click = self.config_data.get("clicker", {})
        key = self.config_data.get("key_hold", {})
        tools = self.config_data.get("tools", {})
        commands = self.config_data.get("commands", {})
        self.coordinates = list(self.config_data.get("coordinates", []))
        workflow = self.config_data.get("workflow", {})
        self.workflow_steps = list(workflow.get("steps", []))
        self.workflow_presets = dict(workflow.get("presets", {}))
        self.profiles = normalize_profiles(self.config_data.get("profiles", {}))
        self.profile_var = tk.StringVar(value=self.config_data.get("active_profile", "默认"))

        self.region_var = tk.StringVar(value=fish.get("region", ",".join(map(str, default_region))))
        self.keywords_var = tk.StringVar(value=fish.get("keywords", ",".join(DEFAULT_KEYWORDS)))
        self.lang_var = tk.StringVar(value=fish.get("lang", "chi_sim+eng"))
        self.poll_var = tk.StringVar(value=fish.get("poll", "0.05"))
        self.ocr_scale_var = tk.StringVar(value=fish.get("ocr_scale", "2.0"))
        self.timeout_var = tk.StringVar(value=fish.get("timeout", "45"))
        self.cast_delay_var = tk.StringVar(value=fish.get("cast_delay", "1.5"))
        self.recast_delay_var = tk.StringVar(value=fish.get("recast_delay", "1.0"))
        self.fish_start_delay_var = tk.StringVar(value=fish.get("start_delay", "5"))

        self.click_button_var = tk.StringVar(value=click.get("button", "左键"))
        self.click_interval_var = tk.StringVar(value=click.get("interval", "0.15"))
        self.click_limit_var = tk.StringVar(value=click.get("limit", "0"))
        self.click_delay_var = tk.StringVar(value=click.get("start_delay", "5"))

        self.hold_key_var = tk.StringVar(value=key.get("key", "w"))
        self.hold_duration_var = tk.StringVar(value=key.get("duration", "0"))
        self.hold_delay_var = tk.StringVar(value=key.get("start_delay", "5"))

        self.command_text_var = tk.StringVar(value=commands.get("text", "/home"))
        self.command_interval_var = tk.StringVar(value=commands.get("interval", "60"))
        self.command_count_var = tk.StringVar(value=commands.get("count", "1"))
        self.command_delay_var = tk.StringVar(value=commands.get("start_delay", "5"))
        self.command_chat_key_var = tk.StringVar(value=commands.get("chat_key", "t"))

        self.workflow_type_var = tk.StringVar(value="等待")
        self.workflow_param1_var = tk.StringVar(value="1")
        self.workflow_param2_var = tk.StringVar()
        self.workflow_param3_var = tk.StringVar()
        self.workflow_hint_var = tk.StringVar(value="参数 1：等待秒数")
        self.workflow_loops_var = tk.StringVar(value=workflow.get("loops", "1"))
        self.workflow_delay_var = tk.StringVar(value=workflow.get("start_delay", "5"))
        self.workflow_preset_var = tk.StringVar(value=workflow.get("current_preset", ""))

        self.coord_name_var = tk.StringVar()
        self.coord_dimension_var = tk.StringVar(value="主世界")
        self.coord_x_var = tk.StringVar()
        self.coord_y_var = tk.StringVar()
        self.coord_z_var = tk.StringVar()
        self.coord_note_var = tk.StringVar()
        journey = self.config_data.get("journeymap", {})
        self.journeymap_root_var = tk.StringVar(value=journey.get("root", ""))
        self.journeymap_source_var = tk.StringVar(value=journey.get("source", ""))
        self.journeymap_auto_sync_var = tk.BooleanVar(value=journey.get("auto_sync", False))
        self.journeymap_status_var = tk.StringVar(value="尚未读取 JourneyMap")
        map_config = journey.get("map_export", {})
        self.map_dimension_var = tk.StringVar(value=map_config.get("dimension", "overworld"))
        self.map_layer_var = tk.StringVar(value=map_config.get("layer", "day"))
        self.map_scale_var = tk.StringVar(value=map_config.get("scale", "25%"))
        self.map_markers_var = tk.BooleanVar(value=map_config.get("markers", True))
        self.map_status_var = tk.StringVar(value="选择服务器、维度和图层后查看范围")
        self.map_cursor_var = tk.StringVar(value="拖动平移 · 滚轮缩放 · 每 2 秒刷新")
        self.map_marker_var = tk.StringVar()
        self.map_marker_lookup = {}

        self.timer_minutes_var = tk.StringVar(value=tools.get("timer_minutes", "5"))
        self.screenshot_dir_var = tk.StringVar(
            value=tools.get("screenshot_dir", str(DEFAULT_SCREENSHOT_DIR))
        )
        self.always_on_top_var = tk.BooleanVar(value=self.config_data.get("always_on_top", False))
        self.dark_mode_var = tk.BooleanVar(value=self.config_data.get("dark_mode", True))
        hotkey = self.config_data.get("stop_hotkey", "F8")
        self.stop_hotkey_var = tk.StringVar(value=hotkey if hotkey in HOTKEY_VK else "F8")
        self.stop_vk = HOTKEY_VK[self.stop_hotkey_var.get()]
        self.tray_enabled_var = tk.BooleanVar(value=self.config_data.get("tray_enabled", False))
        self.tray_status_var = tk.StringVar(value="托盘可用" if pystray else "未安装 pystray")
        self.notifications_enabled_var = tk.BooleanVar(value=self.config_data.get("notifications_enabled", True))
        self.minecraft_guard_var = tk.BooleanVar(value=self.config_data.get("minecraft_guard", True))
        self.foreground_status_var = tk.StringVar(value="正在检测前台窗口…")
        self.notification_status_var = tk.StringVar(
            value="Windows 通知可用" if self.notifier.available else "未安装 winotify（仅声音）"
        )

        self.status_var = tk.StringVar(value="空闲")
        self.status_detail_var = tk.StringVar(value="选择左侧功能开始使用")
        self.dashboard_task_var = tk.StringVar(value="暂无任务")
        self.dashboard_runtime_var = tk.StringVar(value="00:00")
        self.dashboard_actions_var = tk.StringVar(value="0")
        self.dashboard_fish_var = tk.StringVar(value="0")
        self.fish_catches_var = tk.StringVar(value="0")
        self.fish_timeouts_var = tk.StringVar(value="0")
        self.fish_casts_var = tk.StringVar(value="0")
        self.fish_runtime_var = tk.StringVar(value="00:00")
        self.fish_last_ocr_var = tk.StringVar(value="尚无识别结果")
        self.timer_status_var = tk.StringVar(value="尚未设置提醒")

    def configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Dark.TEntry",
            fieldbackground=COLORS["surface_2"],
            foreground=COLORS["text"],
            insertcolor=COLORS["text"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
            padding=8,
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground=COLORS["surface_2"],
            background=COLORS["surface_2"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["muted"],
            bordercolor=COLORS["border"],
            padding=7,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", COLORS["surface_2"])],
            foreground=[("readonly", COLORS["text"])],
        )
        style.configure(
            "Dark.Treeview",
            background=COLORS["surface_2"],
            fieldbackground=COLORS["surface_2"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            rowheight=30,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Dark.Treeview.Heading",
            background=COLORS["surface"],
            foreground=COLORS["muted"],
            bordercolor=COLORS["border"],
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        style.map("Dark.Treeview", background=[("selected", COLORS["selection"])])

    def build_shell(self):
        sidebar = tk.Frame(self, bg=COLORS["sidebar"], width=218)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        logo = tk.Frame(sidebar, bg=COLORS["sidebar"])
        logo.pack(fill=tk.X, padx=20, pady=(24, 28))
        tk.Label(
            logo, text="◆", fg=COLORS["green"], bg=COLORS["sidebar"], font=("Segoe UI", 22, "bold")
        ).pack(side=tk.LEFT)
        logo_text = tk.Frame(logo, bg=COLORS["sidebar"])
        logo_text.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(
            logo_text, text=APP_NAME, fg=COLORS["text"], bg=COLORS["sidebar"],
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            logo_text, text="MINECRAFT TOOLS", fg=COLORS["muted"], bg=COLORS["sidebar"],
            font=("Segoe UI", 7, "bold"),
        ).pack(anchor=tk.W)

        nav_items = [
            ("dashboard", "▦", "总览"),
            ("fishing", "◉", "自动钓鱼"),
            ("clicker", "◎", "自动连点"),
            ("keyhold", "⌨", "按键保持"),
            ("workflow", "⇢", "任务编排"),
            ("commands", "/", "定时指令"),
            ("mapexport", "⌖", "地图与坐标"),
            ("tools", "◇", "便捷工具"),
            ("logs", "≡", "运行日志"),
        ]
        for key, icon, label in nav_items:
            button = tk.Button(
                sidebar,
                text=f"  {icon}    {label}",
                command=lambda page=key: self.show_page(page),
                anchor=tk.W,
                relief=tk.FLAT,
                bd=0,
                padx=18,
                pady=6,
                bg=COLORS["sidebar"],
                fg=COLORS["muted"],
                activebackground=COLORS["surface_2"],
                activeforeground=COLORS["text"],
                font=("Microsoft YaHei UI", 10),
                cursor="hand2",
            )
            button.pack(fill=tk.X, padx=10, pady=1)
            self.nav_buttons[key] = button

        bottom = tk.Frame(sidebar, bg=COLORS["sidebar"])
        bottom.pack(side=tk.BOTTOM, fill=tk.X, padx=16, pady=18)
        self.emergency_button = tk.Button(
            bottom,
            text=f"■  立即停止全部  {self.stop_hotkey_var.get()}",
            command=self.stop_all,
            relief=tk.FLAT,
            bd=0,
            pady=11,
            bg=COLORS["danger_bg"],
            fg=COLORS["danger_text"],
            activebackground=COLORS["danger_active"],
            activeforeground="white",
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="hand2",
        )
        self.emergency_button.pack(fill=tk.X)
        options = tk.Frame(bottom, bg=COLORS["sidebar"])
        options.pack(fill=tk.X, pady=(10, 0))
        tk.Checkbutton(
            options,
            text="窗口置顶",
            variable=self.always_on_top_var,
            command=self.toggle_topmost,
            bg=COLORS["sidebar"],
            fg=COLORS["muted"],
            activebackground=COLORS["sidebar"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["surface_2"],
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT)
        tk.Checkbutton(
            options,
            text="黑夜模式",
            variable=self.dark_mode_var,
            command=self.switch_theme,
            bg=COLORS["sidebar"],
            fg=COLORS["muted"],
            activebackground=COLORS["sidebar"],
            activeforeground=COLORS["text"],
            selectcolor=COLORS["surface_2"],
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.RIGHT)

        main = tk.Frame(self, bg=COLORS["bg"])
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.header = tk.Frame(main, bg=COLORS["bg"], height=88)
        self.header.pack(fill=tk.X, padx=32, pady=(18, 0))
        self.header.pack_propagate(False)
        title_box = tk.Frame(self.header, bg=COLORS["bg"])
        title_box.pack(side=tk.LEFT, fill=tk.Y)
        self.page_title = tk.Label(
            title_box, text="总览", bg=COLORS["bg"], fg=COLORS["text"],
            font=("Microsoft YaHei UI", 22, "bold"),
        )
        self.page_title.pack(anchor=tk.W)
        self.page_subtitle = tk.Label(
            title_box, text="今天想做点什么？", bg=COLORS["bg"], fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9),
        )
        self.page_subtitle.pack(anchor=tk.W, pady=(3, 0))

        status = tk.Frame(self.header, bg=COLORS["surface"], padx=14, pady=8)
        status.pack(side=tk.RIGHT, pady=(4, 20))
        self.status_dot = tk.Label(status, text="●", bg=COLORS["surface"], fg=COLORS["green"], font=("Segoe UI", 9))
        self.status_dot.pack(side=tk.LEFT)
        tk.Label(
            status, textvariable=self.status_var, bg=COLORS["surface"], fg=COLORS["text"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side=tk.LEFT, padx=(7, 0))

        self.content = tk.Frame(main, bg=COLORS["bg"])
        self.content.pack(fill=tk.BOTH, expand=True, padx=32, pady=(0, 28))
        self.build_dashboard_page()
        self.build_fishing_page()
        self.build_clicker_page()
        self.build_keyhold_page()
        self.build_workflow_page()
        self.build_commands_page()
        self.build_map_export_page()
        self.build_tools_page()
        self.build_logs_page()
        self.attributes("-topmost", self.always_on_top_var.get())

    def page(self, key):
        frame = tk.Frame(self.content, bg=COLORS["bg"])
        self.page_frames[key] = frame
        return frame

    def card(self, parent, padding=20):
        return tk.Frame(parent, bg=COLORS["surface"], padx=padding, pady=padding)

    def label(self, parent, text, muted=False, size=9, bold=False, **kwargs):
        return tk.Label(
            parent,
            text=text,
            bg=parent.cget("bg"),
            fg=COLORS["muted"] if muted else COLORS["text"],
            font=("Microsoft YaHei UI", size, "bold" if bold else "normal"),
            **kwargs,
        )

    def action_button(self, parent, text, command, primary=False, danger=False, width=None):
        bg = COLORS["green"] if primary else (COLORS["danger_active"] if danger else COLORS["surface_2"])
        fg = COLORS["primary_text"] if primary else (COLORS["danger_text"] if danger else COLORS["text"])
        return tk.Button(
            parent,
            text=text,
            command=command,
            width=width,
            relief=tk.FLAT,
            bd=0,
            padx=18,
            pady=10,
            bg=bg,
            fg=fg,
            activebackground=COLORS["green"] if primary else COLORS["border"],
            activeforeground=fg,
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="hand2",
        )

    def _map_compact_button(self, parent, text, command, primary=False):
        bg = COLORS["green"] if primary else COLORS["surface_2"]
        fg = COLORS["primary_text"] if primary else COLORS["text"]
        return tk.Button(
            parent, text=text, command=command, relief=tk.FLAT, bd=0,
            padx=9, pady=5, bg=bg, fg=fg,
            activebackground=COLORS["green"] if primary else COLORS["border"],
            activeforeground=fg, font=("Microsoft YaHei UI", 8, "bold"), cursor="hand2",
        )

    def field(self, parent, title, variable, row, column=0, width=24, hint=None):
        box = tk.Frame(parent, bg=parent.cget("bg"))
        box.grid(row=row, column=column, sticky=tk.EW, padx=(0, 18), pady=(0, 15))
        self.label(box, title, muted=True, size=8).pack(anchor=tk.W, pady=(0, 6))
        ttk.Entry(box, textvariable=variable, width=width, style="Dark.TEntry").pack(fill=tk.X)
        if hint:
            self.label(box, hint, muted=True, size=7).pack(anchor=tk.W, pady=(5, 0))
        return box

    def build_dashboard_page(self):
        page = self.page("dashboard")
        hero = tk.Frame(page, bg=COLORS["hero"], padx=26, pady=23)
        hero.pack(fill=tk.X)
        hero_text = tk.Frame(hero, bg=COLORS["hero"])
        hero_text.pack(side=tk.LEFT)
        self.label(hero_text, "一个窗口，常用操作都在这里", size=17, bold=True).pack(anchor=tk.W)
        self.label(
            hero_text, "自动钓鱼只是其中一项。任务开始前会留出切回游戏的时间。",
            muted=True, size=9,
        ).pack(anchor=tk.W, pady=(7, 0))
        self.action_button(hero, "开始钓鱼", lambda: self.show_page("fishing"), primary=True).pack(side=tk.RIGHT)

        stats = tk.Frame(page, bg=COLORS["bg"])
        stats.pack(fill=tk.X, pady=18)
        for index in range(4):
            stats.columnconfigure(index, weight=1)
        stat_defs = [
            ("当前任务", self.dashboard_task_var, COLORS["blue"]),
            ("本次操作", self.dashboard_actions_var, COLORS["amber"]),
            ("钓获次数", self.dashboard_fish_var, COLORS["green"]),
            ("MAF 运行", self.dashboard_runtime_var, COLORS["muted"]),
        ]
        for col, (title, variable, color) in enumerate(stat_defs):
            card = self.card(stats, 16)
            card.grid(row=0, column=col, sticky=tk.NSEW, padx=(0 if col == 0 else 7, 0 if col == 3 else 7))
            self.label(card, title, muted=True, size=8).pack(anchor=tk.W)
            tk.Label(
                card, textvariable=variable, bg=COLORS["surface"], fg=color,
                font=("Microsoft YaHei UI", 16, "bold"),
            ).pack(anchor=tk.W, pady=(10, 0))

        lower = tk.Frame(page, bg=COLORS["bg"])
        lower.pack(fill=tk.BOTH, expand=True)
        quick = self.card(lower)
        quick.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 9))
        self.label(quick, "快捷操作", size=12, bold=True).pack(anchor=tk.W)
        self.label(quick, "打开功能页并按你的参数启动", muted=True, size=8).pack(anchor=tk.W, pady=(3, 8))
        quick_defs = [
            ("◉", "自动钓鱼", "OCR 字幕识别收竿", "fishing"),
            ("⇢", "任务编排", "组合多个自动化步骤", "workflow"),
            ("/", "定时指令", "周期发送常用命令", "commands"),
            ("⌖", "地图与坐标", "浏览地图并管理标记", "mapexport"),
        ]
        for icon, title, sub, target in quick_defs:
            row = tk.Button(
                quick, text=f"{icon}   {title}    ·    {sub}                                      ›",
                command=lambda p=target: self.show_page(p), relief=tk.FLAT, bd=0,
                bg=COLORS["surface_2"], fg=COLORS["text"], activebackground=COLORS["border"],
                activeforeground=COLORS["text"], cursor="hand2", anchor=tk.W,
                padx=12, pady=7, font=("Microsoft YaHei UI", 8, "bold"),
            )
            row.pack(fill=tk.X, pady=2)

        activity = self.card(lower)
        activity.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(9, 0))
        self.label(activity, "状态中心", size=12, bold=True).pack(anchor=tk.W)
        self.label(activity, "当前任务的实时信息", muted=True, size=8).pack(anchor=tk.W, pady=(3, 18))
        tk.Label(
            activity, textvariable=self.status_var, bg=COLORS["surface"], fg=COLORS["green"],
            font=("Microsoft YaHei UI", 20, "bold"),
        ).pack(anchor=tk.W)
        tk.Label(
            activity, textvariable=self.status_detail_var, bg=COLORS["surface"], fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 9), wraplength=310, justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(8, 18))
        self.dashboard_stop_button = self.action_button(
            activity, f"立即停止全部  {self.stop_hotkey_var.get()}", self.stop_all, danger=True
        )
        self.dashboard_stop_button.pack(anchor=tk.W)
        self.label(
            activity, "安全提示：任何自动化都可将鼠标移到屏幕左上角停止。",
            muted=True, size=7, wraplength=320, justify=tk.LEFT,
        ).pack(anchor=tk.W, side=tk.BOTTOM)

    def build_fishing_page(self):
        page = self.page("fishing")
        top = self.card(page)
        top.pack(fill=tk.X)
        self.label(top, "字幕识别", size=12, bold=True).pack(anchor=tk.W)
        self.label(top, "开启 Minecraft 辅助字幕，框选右下角字幕列表。", muted=True, size=8).pack(anchor=tk.W, pady=(3, 14))
        controls = tk.Frame(top, bg=COLORS["surface"])
        controls.pack(fill=tk.X)
        self.action_button(controls, "框选字幕区域", self.select_region, primary=True).pack(side=tk.LEFT)
        self.action_button(controls, "测试一次 OCR", self.test_ocr).pack(side=tk.LEFT, padx=8)
        self.action_button(controls, "OCR 图像预览", self.preview_ocr).pack(side=tk.LEFT, padx=(0, 8))
        self.action_button(controls, "检查 OCR", self.check_ocr_status).pack(side=tk.LEFT)
        self.ocr_badge = tk.Label(
            controls, text="●  正在检查", bg=COLORS["surface"], fg=COLORS["amber"],
            font=("Microsoft YaHei UI", 8, "bold"),
        )
        self.ocr_badge.pack(side=tk.RIGHT)

        body = tk.Frame(page, bg=COLORS["bg"])
        body.pack(fill=tk.BOTH, expand=True, pady=18)

        form = self.card(body)
        form.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 9))
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        self.field(form, "字幕区域  x, y, 宽, 高", self.region_var, 0, 0)
        self.field(form, "识别关键词（逗号分隔）", self.keywords_var, 0, 1)
        self.field(form, "OCR 语言", self.lang_var, 1, 0)
        self.field(form, "OCR 缩放", self.ocr_scale_var, 1, 1, hint="1.5 更快；识别不稳时使用 2.0")
        self.field(form, "扫描间隔（秒）", self.poll_var, 2, 0)
        self.field(form, "等待超时（秒）", self.timeout_var, 2, 1)
        self.field(form, "抛竿后等待（秒）", self.cast_delay_var, 3, 0)
        self.field(form, "再次抛竿间隔（秒）", self.recast_delay_var, 3, 1)
        self.field(form, "启动倒计时（秒）", self.fish_start_delay_var, 4, 0)
        buttons = tk.Frame(form, bg=COLORS["surface"])
        buttons.grid(row=4, column=1, sticky=tk.SE, padx=(0, 18), pady=(0, 15))
        self.action_button(buttons, "恢复默认区域", self.reset_region).pack(side=tk.LEFT, padx=(0, 8))
        self.action_button(buttons, "开始自动钓鱼", self.start_fishing, primary=True).pack(side=tk.LEFT)

        results = self.card(body, 16)
        results.configure(width=322)
        results.pack(side=tk.LEFT, fill=tk.BOTH, padx=(9, 0))
        results.pack_propagate(False)
        header = tk.Frame(results, bg=COLORS["surface"])
        header.pack(fill=tk.X)
        self.label(header, "本次识别", size=11, bold=True).pack(side=tk.LEFT)
        self.action_button(header, "清空", self.reset_fishing_stats).pack(side=tk.RIGHT)
        self.action_button(header, "导出 CSV", self.export_fishing_records).pack(side=tk.RIGHT, padx=(0, 6))

        count_row = tk.Frame(results, bg=COLORS["surface"])
        count_row.pack(fill=tk.X, pady=(14, 12))
        count_defs = [
            ("钓获", self.fish_catches_var, COLORS["green"]),
            ("超时", self.fish_timeouts_var, COLORS["amber"]),
            ("抛竿", self.fish_casts_var, COLORS["blue"]),
        ]
        for index, (title, variable, color) in enumerate(count_defs):
            stat = tk.Frame(count_row, bg=COLORS["surface_2"], padx=10, pady=8)
            stat.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0 if index == 0 else 3, 0 if index == 2 else 3))
            self.label(stat, title, muted=True, size=7).pack(anchor=tk.W)
            tk.Label(
                stat, textvariable=variable, bg=COLORS["surface_2"], fg=color,
                font=("Microsoft YaHei UI", 14, "bold"),
            ).pack(anchor=tk.W, pady=(3, 0))

        runtime_row = tk.Frame(results, bg=COLORS["surface"])
        runtime_row.pack(fill=tk.X)
        self.label(runtime_row, "运行时间", muted=True, size=8).pack(side=tk.LEFT)
        tk.Label(
            runtime_row, textvariable=self.fish_runtime_var, bg=COLORS["surface"],
            fg=COLORS["text"], font=("Cascadia Mono", 9, "bold"),
        ).pack(side=tk.RIGHT)

        self.label(results, "最近 OCR", muted=True, size=8).pack(anchor=tk.W, pady=(14, 5))
        tk.Label(
            results, textvariable=self.fish_last_ocr_var, bg=COLORS["surface_2"],
            fg=COLORS["text"], font=("Microsoft YaHei UI", 8), anchor=tk.W,
            justify=tk.LEFT, wraplength=270, padx=10, pady=8,
        ).pack(fill=tk.X)

        self.label(results, "识别记录", muted=True, size=8).pack(anchor=tk.W, pady=(14, 5))
        self.fish_record_text = tk.Text(
            results, height=7, bg=COLORS["surface_2"], fg=COLORS["mono_text"],
            relief=tk.FLAT, bd=0, padx=9, pady=7, wrap=tk.WORD,
            font=("Cascadia Mono", 8), state=tk.DISABLED,
        )
        self.fish_record_text.pack(fill=tk.BOTH, expand=True)
        self.after(300, self.check_ocr_status)

    def build_clicker_page(self):
        page = self.page("clicker")
        intro = self.card(page)
        intro.pack(fill=tk.X)
        self.label(intro, "自动连点器", size=15, bold=True).pack(anchor=tk.W)
        self.label(intro, "适合重复交互或测试。0 次表示持续运行，全局热键随时停止。", muted=True, size=9).pack(anchor=tk.W, pady=(5, 0))

        form = self.card(page)
        form.pack(fill=tk.X, pady=18)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        box = tk.Frame(form, bg=COLORS["surface"])
        box.grid(row=0, column=0, sticky=tk.EW, padx=(0, 18), pady=(0, 15))
        self.label(box, "鼠标按键", muted=True, size=8).pack(anchor=tk.W, pady=(0, 6))
        ttk.Combobox(
            box, textvariable=self.click_button_var, values=("左键", "右键"),
            state="readonly", style="Dark.TCombobox",
        ).pack(fill=tk.X)
        self.field(form, "点击间隔（秒）", self.click_interval_var, 0, 1, hint="最小 0.03 秒")
        self.field(form, "点击次数", self.click_limit_var, 1, 0, hint="0 = 持续运行")
        self.field(form, "启动倒计时（秒）", self.click_delay_var, 1, 1)
        self.action_button(form, "开始连点", self.start_clicker, primary=True).grid(row=2, column=1, sticky=tk.E, padx=(0, 18))

    def build_keyhold_page(self):
        page = self.page("keyhold")
        intro = self.card(page)
        intro.pack(fill=tk.X)
        self.label(intro, "按键保持", size=15, bold=True).pack(anchor=tk.W)
        self.label(intro, "持续按住移动、潜行或空格；任务停止时会自动释放按键。", muted=True, size=9).pack(anchor=tk.W, pady=(5, 0))
        form = self.card(page)
        form.pack(fill=tk.X, pady=18)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        key_box = tk.Frame(form, bg=COLORS["surface"])
        key_box.grid(row=0, column=0, sticky=tk.EW, padx=(0, 18), pady=(0, 15))
        self.label(key_box, "保持按键", muted=True, size=8).pack(anchor=tk.W, pady=(0, 6))
        ttk.Combobox(
            key_box, textvariable=self.hold_key_var,
            values=("w", "a", "s", "d", "shift", "ctrl", "space"),
            style="Dark.TCombobox",
        ).pack(fill=tk.X)
        self.field(form, "持续时间（秒）", self.hold_duration_var, 0, 1, hint="0 = 直到手动停止")
        self.field(form, "启动倒计时（秒）", self.hold_delay_var, 1, 0)
        self.action_button(form, "开始保持", self.start_key_hold, primary=True).grid(row=1, column=1, sticky=tk.E, padx=(0, 18))

    def build_workflow_page(self):
        page = self.page("workflow")
        preset_bar = self.card(page, 10)
        preset_bar.pack(fill=tk.X, pady=(0, 10))
        self.label(preset_bar, "工作流预设", size=9, bold=True).pack(side=tk.LEFT, padx=(0, 10))
        self.workflow_preset_combo = ttk.Combobox(
            preset_bar, textvariable=self.workflow_preset_var, width=18, style="Dark.TCombobox",
        )
        self.workflow_preset_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def compact_button(text, command, danger=False):
            return tk.Button(
                preset_bar, text=text, command=command, relief=tk.FLAT, bd=0,
                padx=8, pady=6, cursor="hand2", font=("Microsoft YaHei UI", 8, "bold"),
                bg=COLORS["danger_active"] if danger else COLORS["surface_2"],
                fg=COLORS["danger_text"] if danger else COLORS["text"],
                activebackground=COLORS["border"], activeforeground=COLORS["text"],
            )

        compact_button("保存", self.save_workflow_preset).pack(side=tk.LEFT, padx=(8, 0))
        compact_button("载入", self.load_workflow_preset).pack(side=tk.LEFT, padx=(4, 0))
        compact_button("删除", self.delete_workflow_preset, danger=True).pack(side=tk.LEFT, padx=(4, 0))
        compact_button("导入", self.import_workflow).pack(side=tk.LEFT, padx=(8, 0))
        compact_button("导出", self.export_workflow).pack(side=tk.LEFT, padx=(4, 0))

        builder = self.card(page, 16)
        builder.pack(fill=tk.X)
        self.label(builder, "添加步骤", size=11, bold=True).grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 10))
        for column in range(4):
            builder.columnconfigure(column, weight=1)

        type_box = tk.Frame(builder, bg=COLORS["surface"])
        type_box.grid(row=1, column=0, sticky=tk.EW, padx=(0, 10))
        self.label(type_box, "动作类型", muted=True, size=8).pack(anchor=tk.W, pady=(0, 5))
        type_combo = ttk.Combobox(
            type_box, textvariable=self.workflow_type_var, values=WORKFLOW_TYPES,
            state="readonly", style="Dark.TCombobox",
        )
        type_combo.pack(fill=tk.X)
        type_combo.bind("<<ComboboxSelected>>", self.update_workflow_hint)

        for column, (title, variable) in enumerate((
            ("参数 1", self.workflow_param1_var),
            ("参数 2", self.workflow_param2_var),
            ("参数 3", self.workflow_param3_var),
        ), start=1):
            box = tk.Frame(builder, bg=COLORS["surface"])
            box.grid(row=1, column=column, sticky=tk.EW, padx=(0 if column == 3 else 10, 0))
            self.label(box, title, muted=True, size=8).pack(anchor=tk.W, pady=(0, 5))
            ttk.Entry(box, textvariable=variable, style="Dark.TEntry").pack(fill=tk.X)

        hint_row = tk.Frame(builder, bg=COLORS["surface"])
        hint_row.grid(row=2, column=0, columnspan=4, sticky=tk.EW, pady=(10, 0))
        self.label(hint_row, "示例：", muted=True, size=8).pack(side=tk.LEFT)
        tk.Label(
            hint_row, textvariable=self.workflow_hint_var, bg=COLORS["surface"], fg=COLORS["amber"],
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT)
        self.workflow_add_button = self.action_button(
            hint_row, "添加到流程", self.add_workflow_step, primary=True
        )
        self.workflow_add_button.pack(side=tk.RIGHT)

        list_card = self.card(page, 14)
        list_card.pack(fill=tk.BOTH, expand=True, pady=(14, 0))
        toolbar = tk.Frame(list_card, bg=COLORS["surface"])
        toolbar.pack(fill=tk.X, pady=(0, 9))
        self.label(toolbar, "执行顺序", size=11, bold=True).pack(side=tk.LEFT)
        self.action_button(toolbar, "下移", lambda: self.move_workflow_step(1)).pack(side=tk.RIGHT)
        self.action_button(toolbar, "上移", lambda: self.move_workflow_step(-1)).pack(side=tk.RIGHT, padx=6)
        self.action_button(toolbar, "删除", self.delete_workflow_step, danger=True).pack(side=tk.RIGHT)
        self.action_button(toolbar, "复制", self.duplicate_workflow_step).pack(side=tk.RIGHT, padx=6)
        self.action_button(toolbar, "编辑", self.edit_selected_workflow_step).pack(side=tk.RIGHT)

        tree_frame = tk.Frame(list_card, bg=COLORS["surface"])
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.workflow_tree = ttk.Treeview(
            tree_frame, columns=("order", "type", "detail"), show="headings",
            style="Dark.Treeview", selectmode="browse", height=7,
        )
        self.workflow_tree.heading("order", text="#")
        self.workflow_tree.heading("type", text="动作")
        self.workflow_tree.heading("detail", text="参数")
        self.workflow_tree.column("order", width=45, minwidth=40, anchor=tk.CENTER, stretch=False)
        self.workflow_tree.column("type", width=110, minwidth=90, anchor=tk.W, stretch=False)
        self.workflow_tree.column("detail", width=520, minwidth=260, anchor=tk.W)
        tree_scroll = ttk.Scrollbar(tree_frame, command=self.workflow_tree.yview)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.workflow_tree.configure(yscrollcommand=tree_scroll.set)
        self.workflow_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.workflow_tree.bind("<Double-1>", self.edit_selected_workflow_step)
        self.workflow_tree.bind("<ButtonPress-1>", self.begin_workflow_drag)
        self.workflow_tree.bind("<ButtonRelease-1>", self.end_workflow_drag)

        run_bar = tk.Frame(list_card, bg=COLORS["surface"])
        run_bar.pack(fill=tk.X, pady=(10, 0))
        self.label(run_bar, "循环次数", muted=True, size=8).pack(side=tk.LEFT)
        ttk.Entry(run_bar, textvariable=self.workflow_loops_var, width=7, style="Dark.TEntry").pack(side=tk.LEFT, padx=(6, 16))
        self.label(run_bar, "启动倒计时", muted=True, size=8).pack(side=tk.LEFT)
        ttk.Entry(run_bar, textvariable=self.workflow_delay_var, width=7, style="Dark.TEntry").pack(side=tk.LEFT, padx=(6, 5))
        self.label(run_bar, "秒 · 循环 0 = 持续运行", muted=True, size=8).pack(side=tk.LEFT)
        self.action_button(run_bar, "运行工作流", self.start_workflow, primary=True).pack(side=tk.RIGHT)
        self.action_button(run_bar, "从此运行", self.run_workflow_from_selected).pack(side=tk.RIGHT, padx=6)
        self.action_button(run_bar, "单步", self.run_selected_workflow_step).pack(side=tk.RIGHT)
        self.refresh_workflow_presets()
        self.refresh_workflow_steps()

    def build_commands_page(self):
        page = self.page("commands")
        intro = self.card(page)
        intro.pack(fill=tk.X)
        self.label(intro, "定时指令", size=15, bold=True).pack(anchor=tk.W)
        self.label(
            intro, "定时打开聊天框并发送指令；支持次数限制，也可以用全局热键随时停止。",
            muted=True, size=9,
        ).pack(anchor=tk.W, pady=(5, 0))

        form = self.card(page)
        form.pack(fill=tk.X, pady=18)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        self.field(form, "指令或消息", self.command_text_var, 0, 0, hint="例如 /home；当前输入方式支持 ASCII 字符")
        self.field(form, "发送间隔（秒）", self.command_interval_var, 0, 1, hint="最小 0.5 秒")
        self.field(form, "发送次数", self.command_count_var, 1, 0, hint="0 = 持续运行")
        self.field(form, "启动倒计时（秒）", self.command_delay_var, 1, 1)
        self.field(form, "聊天键", self.command_chat_key_var, 2, 0, hint="Minecraft 默认是 T")
        self.action_button(form, "开始定时发送", self.start_scheduled_commands, primary=True).grid(
            row=2, column=1, sticky=tk.E, padx=(0, 18), pady=(0, 15)
        )

        note = self.card(page)
        note.pack(fill=tk.X)
        self.label(note, "使用提示", size=10, bold=True).pack(anchor=tk.W)
        self.label(
            note,
            "启动后先切回游戏。MAF 会按“聊天键 → 输入内容 → 回车”的顺序执行；请确认服务器允许相关自动化。",
            muted=True, size=8, wraplength=760, justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(6, 0))

    def build_map_export_page(self):
        page = self.page("mapexport")
        settings = self.card(page, 10)
        settings.pack(fill=tk.X)
        for column in range(3):
            settings.columnconfigure(column, weight=1)

        header = tk.Frame(settings, bg=COLORS["surface"])
        header.grid(row=0, column=0, columnspan=3, sticky=tk.EW, pady=(0, 8))
        self.label(header, "JourneyMap 实时地图", size=11, bold=True).pack(side=tk.LEFT)
        self.label(
            header, "视野瓦片懒加载 · 2 秒自动刷新", muted=True, size=8
        ).pack(side=tk.LEFT, padx=10)
        self._map_compact_button(header, "扫描", self.refresh_journeymap_sources).pack(side=tk.RIGHT)
        self._map_compact_button(header, "选择目录", self.choose_journeymap_root).pack(
            side=tk.RIGHT, padx=6
        )

        source_box = tk.Frame(settings, bg=COLORS["surface"])
        source_box.grid(row=1, column=0, sticky=tk.EW, padx=(0, 10))
        self.label(source_box, "存档/服务器", muted=True, size=7).pack(anchor=tk.W, pady=(0, 3))
        self.map_source_box = ttk.Combobox(
            source_box, textvariable=self.journeymap_source_var,
            values=(), state="readonly", style="Dark.TCombobox",
        )
        self.map_source_box.pack(fill=tk.X)
        self.map_source_box.bind("<<ComboboxSelected>>", self.on_journeymap_source_changed)

        dimension_box = tk.Frame(settings, bg=COLORS["surface"])
        dimension_box.grid(row=1, column=1, sticky=tk.EW, padx=(0, 10))
        self.label(dimension_box, "维度", muted=True, size=7).pack(anchor=tk.W, pady=(0, 3))
        self.map_dimension_box = ttk.Combobox(
            dimension_box, textvariable=self.map_dimension_var,
            values=(), state="readonly", style="Dark.TCombobox",
        )
        self.map_dimension_box.pack(fill=tk.X)
        self.map_dimension_box.bind("<<ComboboxSelected>>", self.refresh_map_layers)

        layer_box = tk.Frame(settings, bg=COLORS["surface"])
        layer_box.grid(row=1, column=2, sticky=tk.EW)
        self.label(layer_box, "地图图层", muted=True, size=7).pack(anchor=tk.W, pady=(0, 3))
        self.map_layer_box = ttk.Combobox(
            layer_box, textvariable=self.map_layer_var,
            values=(), state="readonly", style="Dark.TCombobox",
        )
        self.map_layer_box.pack(fill=tk.X)
        self.map_layer_box.bind("<<ComboboxSelected>>", self.on_map_layer_changed)

        actions = tk.Frame(settings, bg=COLORS["surface"])
        actions.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=(9, 0))
        self._map_compact_button(actions, "−", lambda: self.map_canvas.zoom_out()).pack(side=tk.LEFT)
        self._map_compact_button(actions, "+", lambda: self.map_canvas.zoom_in()).pack(side=tk.LEFT, padx=4)
        self._map_compact_button(actions, "全图", lambda: self.map_canvas.fit_bounds()).pack(side=tk.LEFT)

        self.map_marker_box = ttk.Combobox(
            actions, textvariable=self.map_marker_var, values=(), state="readonly",
            width=16, style="Dark.TCombobox",
        )
        self.map_marker_box.pack(side=tk.LEFT, padx=(10, 4))
        self._map_compact_button(actions, "定位", self.locate_map_marker).pack(side=tk.LEFT)
        tk.Checkbutton(
            actions, text="显示标记", variable=self.map_markers_var,
            command=self.refresh_map_viewer,
            bg=COLORS["surface"], fg=COLORS["muted"],
            activebackground=COLORS["surface"], activeforeground=COLORS["text"],
            selectcolor=COLORS["surface_2"], font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT, padx=6)

        self._map_compact_button(actions, "导出", self.start_map_export, primary=True).pack(side=tk.RIGHT)
        self.map_scale_box = ttk.Combobox(
            actions, textvariable=self.map_scale_var,
            values=("100%", "50%", "25%", "12.5%"), state="readonly",
            width=6, style="Dark.TCombobox",
        )
        self.map_scale_box.pack(side=tk.RIGHT, padx=(4, 6))
        self.map_scale_box.bind("<<ComboboxSelected>>", self.update_map_summary)

        workspace = tk.Frame(page, bg=COLORS["bg"])
        workspace.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        viewer_card = tk.Frame(workspace, bg=COLORS["surface"], padx=1, pady=1)
        viewer_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        self.map_canvas = JourneyMapTileCanvas(
            viewer_card, background=COLORS["image_bg"], foreground=COLORS["text"],
            muted=COLORS["muted"], accent=COLORS["red"],
            status_callback=self.map_cursor_var.set,
        )
        self.map_canvas.pack(fill=tk.BOTH, expand=True)
        status_bar = tk.Frame(viewer_card, bg=COLORS["surface"], padx=8, pady=5)
        status_bar.pack(fill=tk.X)
        self.label(status_bar, "", muted=True, size=7, textvariable=self.map_cursor_var).pack(side=tk.LEFT)
        self.label(status_bar, "", muted=True, size=7, textvariable=self.map_status_var).pack(side=tk.RIGHT)

        coordinate_panel = tk.Frame(
            workspace, bg=COLORS["surface"], width=250, padx=9, pady=8
        )
        coordinate_panel.pack(side=tk.RIGHT, fill=tk.Y)
        coordinate_panel.pack_propagate(False)
        self.build_coordinate_side_panel(coordinate_panel)

    def build_coordinate_side_panel(self, panel):
        header = tk.Frame(panel, bg=COLORS["surface"])
        header.pack(fill=tk.X)
        self.label(header, "坐标标记", size=10, bold=True).pack(side=tk.LEFT)
        tk.Checkbutton(
            header, text="自动同步", variable=self.journeymap_auto_sync_var,
            command=self.save_config, bg=COLORS["surface"], fg=COLORS["muted"],
            activebackground=COLORS["surface"], activeforeground=COLORS["text"],
            selectcolor=COLORS["surface_2"], font=("Microsoft YaHei UI", 7),
        ).pack(side=tk.RIGHT)

        first_row = tk.Frame(panel, bg=COLORS["surface"])
        first_row.pack(fill=tk.X, pady=(7, 0))
        name_box = tk.Frame(first_row, bg=COLORS["surface"])
        name_box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self.label(name_box, "名称", muted=True, size=7).pack(anchor=tk.W)
        ttk.Entry(name_box, textvariable=self.coord_name_var, style="Dark.TEntry").pack(fill=tk.X)
        dimension_box = tk.Frame(first_row, bg=COLORS["surface"])
        dimension_box.pack(side=tk.LEFT)
        self.label(dimension_box, "维度", muted=True, size=7).pack(anchor=tk.W)
        ttk.Combobox(
            dimension_box, textvariable=self.coord_dimension_var,
            values=("主世界", "下界", "末地"), state="readonly",
            width=7, style="Dark.TCombobox",
        ).pack()

        xyz_row = tk.Frame(panel, bg=COLORS["surface"])
        xyz_row.pack(fill=tk.X, pady=(5, 0))
        for label, variable in (
            ("X", self.coord_x_var), ("Y", self.coord_y_var), ("Z", self.coord_z_var)
        ):
            box = tk.Frame(xyz_row, bg=COLORS["surface"])
            box.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4) if label != "Z" else 0)
            self.label(box, label, muted=True, size=7).pack(anchor=tk.W)
            ttk.Entry(box, textvariable=variable, width=6, style="Dark.TEntry").pack(fill=tk.X)

        note_row = tk.Frame(panel, bg=COLORS["surface"])
        note_row.pack(fill=tk.X, pady=(5, 0))
        self.label(note_row, "备注", muted=True, size=7).pack(anchor=tk.W)
        ttk.Entry(note_row, textvariable=self.coord_note_var, style="Dark.TEntry").pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        self._map_compact_button(note_row, "保存", self.add_coordinate, primary=True).pack(
            side=tk.LEFT, padx=(5, 0)
        )

        tree_frame = tk.Frame(panel, bg=COLORS["surface"])
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(7, 6))
        columns = ("name", "x", "z")
        self.coordinate_tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings",
            style="Dark.Treeview", selectmode="browse", height=4,
        )
        for column, title, width in (
            ("name", "名称", 105), ("x", "X", 52), ("z", "Z", 52)
        ):
            self.coordinate_tree.heading(column, text=title)
            self.coordinate_tree.column(column, width=width, minwidth=38, anchor=tk.W)
        scroll = ttk.Scrollbar(tree_frame, command=self.coordinate_tree.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.coordinate_tree.configure(yscrollcommand=scroll.set)
        self.coordinate_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.coordinate_tree.bind("<Double-1>", lambda _event: self.locate_selected_coordinate())

        primary_actions = tk.Frame(panel, bg=COLORS["surface"])
        primary_actions.pack(fill=tk.X)
        self._map_compact_button(primary_actions, "定位", self.locate_selected_coordinate).pack(side=tk.LEFT)
        self._map_compact_button(primary_actions, "复制", self.copy_coordinate).pack(side=tk.LEFT, padx=4)
        self._map_compact_button(primary_actions, "删除", self.delete_coordinate).pack(side=tk.LEFT)

        sync_actions = tk.Frame(panel, bg=COLORS["surface"])
        sync_actions.pack(fill=tk.X, pady=(5, 0))
        self._map_compact_button(sync_actions, "同步", self.sync_journeymap, primary=True).pack(side=tk.LEFT)
        self._map_compact_button(sync_actions, "CSV", self.export_coordinates_csv).pack(side=tk.LEFT, padx=4)
        self._map_compact_button(sync_actions, "JM", self.export_coordinates_journeymap).pack(side=tk.LEFT)
        self.refresh_coordinates()

    def build_tools_page(self):
        page = self.page("tools")
        profile_bar = self.card(page, 10)
        profile_bar.pack(fill=tk.X, pady=(0, 10))
        self.label(profile_bar, "配置档案", size=9, bold=True).pack(side=tk.LEFT, padx=(0, 10))
        self.profile_combo = ttk.Combobox(
            profile_bar, textvariable=self.profile_var, style="Dark.TCombobox", width=22,
        )
        self.profile_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.profile_combo.configure(values=tuple(sorted(self.profiles)))
        self.action_button(profile_bar, "保存", self.save_profile, primary=True).pack(side=tk.LEFT, padx=(8, 0))
        self.action_button(profile_bar, "载入", self.load_profile).pack(side=tk.LEFT, padx=6)
        self.action_button(profile_bar, "删除", self.delete_profile, danger=True).pack(side=tk.LEFT)

        body = tk.Frame(page, bg=COLORS["bg"])
        body.pack(fill=tk.BOTH, expand=True)
        left = self.card(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 9))
        self.label(left, "定时提醒", size=13, bold=True).pack(anchor=tk.W)
        self.label(left, "挂机或等待时，在指定分钟后提醒你。", muted=True, size=8).pack(anchor=tk.W, pady=(4, 18))
        self.label(left, "分钟", muted=True, size=8).pack(anchor=tk.W, pady=(0, 6))
        ttk.Entry(left, textvariable=self.timer_minutes_var, style="Dark.TEntry").pack(fill=tk.X)
        tk.Label(
            left, textvariable=self.timer_status_var, bg=COLORS["surface"], fg=COLORS["amber"],
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(anchor=tk.W, pady=(22, 16))
        timer_buttons = tk.Frame(left, bg=COLORS["surface"])
        timer_buttons.pack(fill=tk.X)
        self.action_button(timer_buttons, "开始提醒", self.start_timer, primary=True).pack(side=tk.LEFT)
        self.action_button(timer_buttons, "取消", self.cancel_timer).pack(side=tk.LEFT, padx=8)

        right = self.card(body)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(9, 0))
        self.label(right, "快速截图", size=13, bold=True).pack(anchor=tk.W)
        self.label(right, "截取整个屏幕并按时间自动命名。", muted=True, size=8).pack(anchor=tk.W, pady=(4, 18))
        self.label(right, "保存目录", muted=True, size=8).pack(anchor=tk.W, pady=(0, 6))
        ttk.Entry(right, textvariable=self.screenshot_dir_var, style="Dark.TEntry").pack(fill=tk.X)
        shot_buttons = tk.Frame(right, bg=COLORS["surface"])
        shot_buttons.pack(fill=tk.X, pady=15)
        self.action_button(shot_buttons, "选择目录", self.choose_screenshot_dir).pack(side=tk.LEFT)
        self.action_button(shot_buttons, "立即截图", self.take_screenshot, primary=True).pack(side=tk.LEFT, padx=8)

        system = tk.Frame(right, bg=COLORS["surface"])
        system.pack(fill=tk.X, pady=(14, 0))
        self.label(system, "系统与快捷键", size=10, bold=True).pack(anchor=tk.W, pady=(0, 10))
        hotkey_row = tk.Frame(system, bg=COLORS["surface"])
        hotkey_row.pack(fill=tk.X)
        self.label(hotkey_row, "全局停止热键", muted=True, size=8).pack(side=tk.LEFT)
        hotkey_combo = ttk.Combobox(
            hotkey_row, textvariable=self.stop_hotkey_var, values=tuple(HOTKEY_VK),
            state="readonly", width=6, style="Dark.TCombobox",
        )
        hotkey_combo.pack(side=tk.RIGHT)
        hotkey_combo.bind("<<ComboboxSelected>>", self.update_stop_hotkey)

        tray_row = tk.Frame(system, bg=COLORS["surface"])
        tray_row.pack(fill=tk.X, pady=(10, 0))
        self.tray_checkbox = tk.Checkbutton(
            tray_row, text="启用系统托盘（关闭窗口时隐藏）", variable=self.tray_enabled_var,
            command=self.toggle_tray, bg=COLORS["surface"], fg=COLORS["text"],
            activebackground=COLORS["surface"], activeforeground=COLORS["text"],
            selectcolor=COLORS["surface_2"], font=("Microsoft YaHei UI", 8),
        )
        self.tray_checkbox.pack(side=tk.LEFT)
        self.label(tray_row, "", muted=True, size=7, textvariable=self.tray_status_var).pack(side=tk.RIGHT)
        if pystray is None:
            self.tray_checkbox.configure(state=tk.DISABLED)
            self.tray_enabled_var.set(False)
        notification_row = tk.Frame(system, bg=COLORS["surface"])
        notification_row.pack(fill=tk.X, pady=(10, 0))
        tk.Checkbutton(
            notification_row, text="任务完成/失败时通知", variable=self.notifications_enabled_var,
            command=self.save_config, bg=COLORS["surface"], fg=COLORS["text"],
            activebackground=COLORS["surface"], activeforeground=COLORS["text"],
            selectcolor=COLORS["surface_2"], font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT)
        tk.Button(
            notification_row, text="测试", command=lambda: self.notify_user(APP_NAME, "Windows 通知测试成功"),
            relief=tk.FLAT, bd=0, padx=8, pady=4, cursor="hand2",
            bg=COLORS["surface_2"], fg=COLORS["text"], font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.RIGHT)
        self.label(
            system, "", muted=True, size=7, textvariable=self.notification_status_var
        ).pack(anchor=tk.E, pady=(3, 0))
        guard_row = tk.Frame(system, bg=COLORS["surface"])
        guard_row.pack(fill=tk.X, pady=(8, 0))
        tk.Checkbutton(
            guard_row, text="仅在 Minecraft 前台时执行输入", variable=self.minecraft_guard_var,
            command=self.save_config, bg=COLORS["surface"], fg=COLORS["text"],
            activebackground=COLORS["surface"], activeforeground=COLORS["text"],
            selectcolor=COLORS["surface_2"], font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT)
        self.label(
            system, "", muted=True, size=7, textvariable=self.foreground_status_var
        ).pack(anchor=tk.W, pady=(3, 0))
        self.label(
            right, "截图会包含当前屏幕内容。你可以先最小化 MAF，再使用 Minecraft 自带 F2；这里更适合快速留档。",
            muted=True, size=8, wraplength=360, justify=tk.LEFT,
        ).pack(anchor=tk.W, side=tk.BOTTOM)

    def build_logs_page(self):
        page = self.page("logs")
        toolbar = tk.Frame(page, bg=COLORS["bg"])
        toolbar.pack(fill=tk.X, pady=(0, 10))
        self.label(toolbar, "所有任务的开始、停止和错误都会记录在这里。", muted=True, size=8).pack(side=tk.LEFT)
        self.action_button(toolbar, "清空日志", self.clear_log).pack(side=tk.RIGHT)
        self.action_button(toolbar, "任务历史", self.show_history_window, primary=True).pack(side=tk.RIGHT, padx=8)
        card = self.card(page, 1)
        card.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(
            card, bg=COLORS["surface"], fg=COLORS["mono_text"], insertbackground=COLORS["text"],
            relief=tk.FLAT, bd=0, padx=18, pady=16, wrap=tk.WORD,
            font=("Cascadia Mono", 9), state=tk.DISABLED,
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(card, command=self.log_text.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.append_log(f"{APP_NAME} 已启动。{self.stop_hotkey_var.get()} 可停止当前自动化任务。")

    def capture_profile(self):
        return normalize_profile({
            "fishing": {
                "region": self.region_var.get(), "keywords": self.keywords_var.get(),
                "lang": self.lang_var.get(), "poll": self.poll_var.get(),
                "ocr_scale": self.ocr_scale_var.get(), "timeout": self.timeout_var.get(),
                "cast_delay": self.cast_delay_var.get(), "recast_delay": self.recast_delay_var.get(),
                "start_delay": self.fish_start_delay_var.get(),
            },
            "clicker": {
                "button": self.click_button_var.get(), "interval": self.click_interval_var.get(),
                "limit": self.click_limit_var.get(), "start_delay": self.click_delay_var.get(),
            },
            "key_hold": {
                "key": self.hold_key_var.get(), "duration": self.hold_duration_var.get(),
                "start_delay": self.hold_delay_var.get(),
            },
            "commands": {
                "text": self.command_text_var.get(), "interval": self.command_interval_var.get(),
                "count": self.command_count_var.get(), "start_delay": self.command_delay_var.get(),
                "chat_key": self.command_chat_key_var.get(),
            },
            "workflow": {
                "steps": deepcopy(self.workflow_steps), "loops": self.workflow_loops_var.get(),
                "start_delay": self.workflow_delay_var.get(),
            },
            "journeymap": {
                "root": self.journeymap_root_var.get(), "source": self.journeymap_source_var.get(),
                "auto_sync": self.journeymap_auto_sync_var.get(),
                "dimension": self.map_dimension_var.get(), "layer": self.map_layer_var.get(),
                "scale": self.map_scale_var.get(), "markers": self.map_markers_var.get(),
            },
            "tools": {
                "timer_minutes": self.timer_minutes_var.get(),
                "screenshot_dir": self.screenshot_dir_var.get(),
            },
            "system": {
                "stop_hotkey": self.stop_hotkey_var.get(),
                "minecraft_guard": self.minecraft_guard_var.get(),
                "notifications_enabled": self.notifications_enabled_var.get(),
                "dark_mode": self.dark_mode_var.get(),
            },
        })

    @staticmethod
    def _set_profile_values(section, mapping):
        for key, variable in mapping.items():
            if key in section:
                variable.set(section[key])

    def apply_profile(self, profile):
        profile = normalize_profile(profile)
        self._set_profile_values(profile["fishing"], {
            "region": self.region_var, "keywords": self.keywords_var, "lang": self.lang_var,
            "poll": self.poll_var, "ocr_scale": self.ocr_scale_var, "timeout": self.timeout_var,
            "cast_delay": self.cast_delay_var, "recast_delay": self.recast_delay_var,
            "start_delay": self.fish_start_delay_var,
        })
        self._set_profile_values(profile["clicker"], {
            "button": self.click_button_var, "interval": self.click_interval_var,
            "limit": self.click_limit_var, "start_delay": self.click_delay_var,
        })
        self._set_profile_values(profile["key_hold"], {
            "key": self.hold_key_var, "duration": self.hold_duration_var,
            "start_delay": self.hold_delay_var,
        })
        self._set_profile_values(profile["commands"], {
            "text": self.command_text_var, "interval": self.command_interval_var,
            "count": self.command_count_var, "start_delay": self.command_delay_var,
            "chat_key": self.command_chat_key_var,
        })
        workflow = profile["workflow"]
        if "steps" in workflow:
            steps = deepcopy(workflow["steps"])
            self.workflow_steps = normalize_steps(steps) if steps else []
        self._set_profile_values(workflow, {
            "loops": self.workflow_loops_var, "start_delay": self.workflow_delay_var,
        })
        journey = profile["journeymap"]
        self._set_profile_values(journey, {
            "root": self.journeymap_root_var, "source": self.journeymap_source_var,
            "auto_sync": self.journeymap_auto_sync_var, "dimension": self.map_dimension_var,
            "layer": self.map_layer_var, "scale": self.map_scale_var,
            "markers": self.map_markers_var,
        })
        self._set_profile_values(profile["tools"], {
            "timer_minutes": self.timer_minutes_var, "screenshot_dir": self.screenshot_dir_var,
        })
        old_dark_mode = self.dark_mode_var.get()
        self._set_profile_values(profile["system"], {
            "stop_hotkey": self.stop_hotkey_var, "minecraft_guard": self.minecraft_guard_var,
            "notifications_enabled": self.notifications_enabled_var, "dark_mode": self.dark_mode_var,
        })
        self.stop_vk = HOTKEY_VK.get(self.stop_hotkey_var.get(), HOTKEY_VK["F8"])
        self.workflow_edit_index = None
        self.refresh_workflow_steps()
        self.refresh_journeymap_sources(show_errors=False)
        if old_dark_mode != self.dark_mode_var.get():
            self.switch_theme()
        else:
            self.save_config()

    def save_profile(self):
        name = self.profile_var.get().strip()
        if not name:
            messagebox.showerror("缺少名称", "请输入配置档案名称。")
            return
        self.profiles[name] = self.capture_profile()
        self.profile_var.set(name)
        self.profile_combo.configure(values=tuple(sorted(self.profiles)))
        self.save_config()
        self.append_log(f"配置档案已保存：{name}")

    def load_profile(self):
        name = self.profile_var.get().strip()
        if name not in self.profiles:
            messagebox.showinfo("档案不存在", "请选择一个已保存的配置档案。")
            return
        try:
            self.apply_profile(self.profiles[name])
        except (TypeError, ValueError) as exc:
            messagebox.showerror("档案无效", str(exc))
            return
        self.append_log(f"配置档案已载入：{name}")

    def delete_profile(self):
        name = self.profile_var.get().strip()
        if name not in self.profiles:
            messagebox.showinfo("档案不存在", "请选择要删除的配置档案。")
            return
        if not messagebox.askyesno("删除配置档案", f"确认删除“{name}”？"):
            return
        del self.profiles[name]
        self.profile_var.set("")
        self.profile_combo.configure(values=tuple(sorted(self.profiles)))
        self.save_config()
        self.append_log(f"配置档案已删除：{name}")

    def show_page(self, key):
        titles = {
            "dashboard": ("总览", "今天想做点什么？"),
            "fishing": ("自动钓鱼", "通过辅助字幕识别水花，自动收竿和抛竿"),
            "clicker": ("自动连点", "可控制按键、间隔和点击次数"),
            "keyhold": ("按键保持", "释放双手，同时保留随时停止的能力"),
            "workflow": ("任务编排", "组合动作、调整顺序并循环执行"),
            "commands": ("定时指令", "按计划向 Minecraft 聊天框发送命令"),
            "mapexport": ("地图与坐标", "实时浏览 JourneyMap，并在同一页面管理坐标标记"),
            "tools": ("便捷工具", "提醒和截图等轻量功能"),
            "logs": ("运行日志", "查看任务过程与诊断信息"),
        }
        for frame in self.page_frames.values():
            frame.pack_forget()
        self.page_frames[key].pack(fill=tk.BOTH, expand=True)
        self.current_page = key
        self.page_title.configure(text=titles[key][0])
        self.page_subtitle.configure(text=titles[key][1])
        for nav_key, button in self.nav_buttons.items():
            active = nav_key == key
            button.configure(
                bg=COLORS["surface_2"] if active else COLORS["sidebar"],
                fg=COLORS["text"] if active else COLORS["muted"],
            )

    def ensure_idle(self):
        if self.worker and self.worker.thread and self.worker.thread.is_alive():
            messagebox.showinfo("已有任务运行", f"请先按 {self.stop_hotkey_var.get()} 停止当前任务，再启动新的自动化。")
            return False
        return True

    def start_worker(self, worker):
        if not self.ensure_idle():
            return
        self.save_config()
        self.latest_stats = {}
        self.task_stop_requested = False
        self.dashboard_actions_var.set("0")
        if isinstance(worker, AutoFishWorker):
            self.reset_fishing_stats()
        self.worker = worker
        self.worker.start()

    def start_fishing(self):
        try:
            settings = {
                "region": parse_region(self.region_var.get()),
                "keywords": parse_keywords(self.keywords_var.get()),
                "lang": self.lang_var.get().strip() or "chi_sim+eng",
                "poll": max(0.01, float(self.poll_var.get())),
                "ocr_scale": max(0.5, float(self.ocr_scale_var.get())),
                "timeout": max(0, float(self.timeout_var.get())),
                "cast_delay": max(0, float(self.cast_delay_var.get())),
                "recast_delay": max(0, float(self.recast_delay_var.get())),
                "start_delay": max(0, float(self.fish_start_delay_var.get())),
                "require_minecraft": self.minecraft_guard_var.get(),
            }
        except Exception as exc:
            messagebox.showerror("参数有误", str(exc))
            return
        self.start_worker(AutoFishWorker(settings, self.event_queue))

    def start_clicker(self):
        try:
            interval = max(0.03, float(self.click_interval_var.get()))
            limit = max(0, int(self.click_limit_var.get()))
            delay = max(0, float(self.click_delay_var.get()))
        except ValueError:
            messagebox.showerror("参数有误", "间隔、次数和倒计时必须是数字。")
            return
        settings = {
            "button": "left" if self.click_button_var.get() == "左键" else "right",
            "interval": interval,
            "limit": limit,
            "start_delay": delay,
            "require_minecraft": self.minecraft_guard_var.get(),
        }
        self.start_worker(AutoClickWorker(settings, self.event_queue))

    def start_key_hold(self):
        try:
            duration = max(0, float(self.hold_duration_var.get()))
            delay = max(0, float(self.hold_delay_var.get()))
        except ValueError:
            messagebox.showerror("参数有误", "持续时间和倒计时必须是数字。")
            return
        key = self.hold_key_var.get().strip().lower()
        if not key:
            messagebox.showerror("参数有误", "请输入要保持的按键。")
            return
        self.start_worker(KeyHoldWorker({
            "key": key, "duration": duration, "start_delay": delay,
            "require_minecraft": self.minecraft_guard_var.get(),
        }, self.event_queue))

    def update_workflow_hint(self, _event=None):
        first, second, third, hint = WORKFLOW_PRESETS[self.workflow_type_var.get()]
        self.workflow_param1_var.set(first)
        self.workflow_param2_var.set(second)
        self.workflow_param3_var.set(third)
        self.workflow_hint_var.set(hint)

    def add_workflow_step(self):
        step_type = self.workflow_type_var.get()
        first = self.workflow_param1_var.get().strip()
        second = self.workflow_param2_var.get().strip()
        third = self.workflow_param3_var.get().strip()
        try:
            step = build_step(step_type, first, second, third)
        except ValueError as exc:
            messagebox.showerror("步骤参数有误", str(exc))
            return
        if self.workflow_edit_index is not None and self.workflow_edit_index < len(self.workflow_steps):
            index = self.workflow_edit_index
            self.workflow_steps[index] = step
            self.workflow_edit_index = None
            self.workflow_add_button.configure(text="添加到流程")
            self.refresh_workflow_steps(select=index)
            self.append_log(f"工作流步骤 {index + 1} 已更新。")
        else:
            self.workflow_steps.append(step)
            self.refresh_workflow_steps(select=len(self.workflow_steps) - 1)
        self.save_config()

    def edit_selected_workflow_step(self, _event=None):
        if _event is not None:
            row = self.workflow_tree.identify_row(_event.y)
            if row:
                self.workflow_tree.selection_set(row)
        index = self.selected_workflow_index()
        if index is None:
            return
        try:
            step_type, first, second, third = step_to_parameters(self.workflow_steps[index])
        except ValueError as exc:
            messagebox.showerror("无法编辑步骤", str(exc))
            return
        self.workflow_edit_index = index
        self.workflow_type_var.set(step_type)
        self.workflow_param1_var.set(first)
        self.workflow_param2_var.set(second)
        self.workflow_param3_var.set(third)
        self.workflow_hint_var.set(f"正在编辑第 {index + 1} 步；修改后点击“更新步骤”")
        self.workflow_add_button.configure(text="更新步骤")

    def duplicate_workflow_step(self):
        index = self.selected_workflow_index()
        if index is None:
            return
        self.workflow_steps.insert(index + 1, deepcopy(self.workflow_steps[index]))
        self.refresh_workflow_steps(select=index + 1)
        self.save_config()

    def begin_workflow_drag(self, event):
        row = self.workflow_tree.identify_row(event.y)
        self.workflow_drag_index = int(row) if row else None

    def end_workflow_drag(self, event):
        source = self.workflow_drag_index
        self.workflow_drag_index = None
        row = self.workflow_tree.identify_row(event.y)
        if source is None or not row:
            return
        target = int(row)
        if source == target or not 0 <= source < len(self.workflow_steps):
            return
        step = self.workflow_steps.pop(source)
        self.workflow_steps.insert(target, step)
        self.refresh_workflow_steps(select=target)
        self.save_config()

    def refresh_workflow_steps(self, select=None):
        if not hasattr(self, "workflow_tree"):
            return
        self.workflow_tree.delete(*self.workflow_tree.get_children())
        for index, step in enumerate(self.workflow_steps):
            self.workflow_tree.insert(
                "", tk.END, iid=str(index), values=(index + 1, step.get("type", ""), describe_workflow_step(step))
            )
        if select is not None and 0 <= select < len(self.workflow_steps):
            self.workflow_tree.selection_set(str(select))
            self.workflow_tree.see(str(select))

    def selected_workflow_index(self):
        selection = self.workflow_tree.selection()
        if not selection:
            messagebox.showinfo("请选择步骤", "请先在执行顺序中选择一个步骤。")
            return None
        return int(selection[0])

    def move_workflow_step(self, direction):
        index = self.selected_workflow_index()
        if index is None:
            return
        target = index + direction
        if not 0 <= target < len(self.workflow_steps):
            return
        self.workflow_steps[index], self.workflow_steps[target] = self.workflow_steps[target], self.workflow_steps[index]
        self.refresh_workflow_steps(select=target)
        self.save_config()

    def delete_workflow_step(self):
        index = self.selected_workflow_index()
        if index is None:
            return
        self.workflow_steps.pop(index)
        if self.workflow_edit_index == index:
            self.workflow_edit_index = None
            self.workflow_add_button.configure(text="添加到流程")
        self.refresh_workflow_steps(select=min(index, len(self.workflow_steps) - 1))
        self.save_config()

    def refresh_workflow_presets(self):
        if hasattr(self, "workflow_preset_combo"):
            self.workflow_preset_combo.configure(values=tuple(sorted(self.workflow_presets)))

    def current_workflow_document(self, name=None):
        return workflow_document(
            name or self.workflow_preset_var.get() or "未命名工作流",
            self.workflow_steps,
            self.workflow_loops_var.get(),
            self.workflow_delay_var.get(),
        )

    def save_workflow_preset(self):
        name = self.workflow_preset_var.get().strip()
        if not name:
            messagebox.showerror("缺少名称", "请先输入工作流预设名称。")
            return
        try:
            self.workflow_presets[name] = self.current_workflow_document(name)
        except (TypeError, ValueError) as exc:
            messagebox.showerror("无法保存预设", str(exc))
            return
        self.refresh_workflow_presets()
        self.save_config()
        self.append_log(f"工作流预设已保存：{name}")

    def load_workflow_preset(self):
        name = self.workflow_preset_var.get().strip()
        if name not in self.workflow_presets:
            messagebox.showinfo("预设不存在", "请选择一个已经保存的工作流预设。")
            return
        try:
            document = workflow_document(
                name,
                self.workflow_presets[name].get("steps", []),
                self.workflow_presets[name].get("loops", 1),
                self.workflow_presets[name].get("start_delay", 5),
            )
        except (TypeError, ValueError) as exc:
            messagebox.showerror("预设无效", str(exc))
            return
        self.workflow_steps = document["steps"]
        self.workflow_loops_var.set(str(document["loops"]))
        self.workflow_delay_var.set(str(document["start_delay"]))
        self.refresh_workflow_steps()
        self.save_config()
        self.append_log(f"工作流预设已载入：{name}")

    def delete_workflow_preset(self):
        name = self.workflow_preset_var.get().strip()
        if name not in self.workflow_presets:
            messagebox.showinfo("预设不存在", "请选择要删除的工作流预设。")
            return
        if not messagebox.askyesno("删除预设", f"确认删除工作流预设“{name}”？"):
            return
        del self.workflow_presets[name]
        self.workflow_preset_var.set("")
        self.refresh_workflow_presets()
        self.save_config()
        self.append_log(f"工作流预设已删除：{name}")

    def import_workflow(self):
        filename = filedialog.askopenfilename(
            title="导入工作流", filetypes=(("工作流 JSON", "*.json"), ("所有文件", "*.*"))
        )
        if not filename:
            return
        try:
            document = load_workflow_file(filename)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            messagebox.showerror("导入失败", str(exc))
            return
        name = document["name"]
        if name in self.workflow_presets and not messagebox.askyesno("覆盖预设", f"预设“{name}”已存在，是否覆盖？"):
            return
        self.workflow_presets[name] = document
        self.workflow_preset_var.set(name)
        self.workflow_steps = document["steps"]
        self.workflow_loops_var.set(str(document["loops"]))
        self.workflow_delay_var.set(str(document["start_delay"]))
        self.refresh_workflow_presets()
        self.refresh_workflow_steps()
        self.save_config()
        self.append_log(f"工作流已导入：{filename}")

    def export_workflow(self):
        try:
            document = self.current_workflow_document()
        except (TypeError, ValueError) as exc:
            messagebox.showerror("无法导出", str(exc))
            return
        filename = filedialog.asksaveasfilename(
            title="导出工作流", defaultextension=".json",
            initialfile=f"{document['name']}.json",
            filetypes=(("工作流 JSON", "*.json"), ("所有文件", "*.*")),
        )
        if not filename:
            return
        try:
            save_workflow_file(filename, document)
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.append_log(f"工作流已导出：{filename}")

    def start_workflow(self):
        if not self.workflow_steps:
            messagebox.showinfo("流程为空", "请先添加至少一个步骤。")
            return
        self.start_workflow_steps(
            self.workflow_steps, self.workflow_loops_var.get(), self.workflow_delay_var.get()
        )

    def start_workflow_steps(self, steps, loops=1, delay=0):
        try:
            loops = max(0, int(loops))
            delay = max(0, float(delay))
            settings = {
                "steps": normalize_steps([deepcopy(step) for step in steps]),
                "loops": loops,
                "start_delay": delay,
                "screenshot_dir": self.screenshot_dir_var.get(),
                "ocr_region": parse_region(self.region_var.get()),
                "ocr_lang": self.lang_var.get().strip() or "chi_sim+eng",
                "ocr_scale": max(0.5, float(self.ocr_scale_var.get())),
                "ocr_poll": max(0.03, float(self.poll_var.get())),
                "require_minecraft": self.minecraft_guard_var.get(),
            }
        except (TypeError, ValueError) as exc:
            messagebox.showerror("参数有误", str(exc))
            return
        self.start_worker(WorkflowWorker(settings, self.event_queue))

    def run_selected_workflow_step(self):
        index = self.selected_workflow_index()
        if index is not None:
            self.start_workflow_steps([self.workflow_steps[index]], 1, 0)

    def run_workflow_from_selected(self):
        index = self.selected_workflow_index()
        if index is not None:
            self.start_workflow_steps(self.workflow_steps[index:], 1, self.workflow_delay_var.get())

    def start_scheduled_commands(self):
        command = self.command_text_var.get().strip()
        if not command:
            messagebox.showerror("参数有误", "请输入要发送的指令或消息。")
            return
        if not command.isascii():
            messagebox.showerror("暂不支持中文输入", "当前自动输入支持 ASCII 字符；Minecraft 指令可以正常使用。")
            return
        try:
            interval = max(0.5, float(self.command_interval_var.get()))
            count = max(0, int(self.command_count_var.get()))
            delay = max(0, float(self.command_delay_var.get()))
        except ValueError:
            messagebox.showerror("参数有误", "间隔、次数和倒计时必须是数字。")
            return
        chat_key = self.command_chat_key_var.get().strip().lower() or "t"
        settings = {
            "command": command,
            "interval": interval,
            "count": count,
            "start_delay": delay,
            "chat_key": chat_key,
            "chat_delay": 0.15,
            "require_minecraft": self.minecraft_guard_var.get(),
        }
        self.start_worker(ScheduledCommandWorker(settings, self.event_queue))

    def add_coordinate(self):
        name = self.coord_name_var.get().strip()
        values = (self.coord_x_var.get().strip(), self.coord_y_var.get().strip(), self.coord_z_var.get().strip())
        if not name:
            messagebox.showerror("坐标不完整", "请填写地点名称。")
            return
        try:
            for value in values:
                float(value)
        except ValueError:
            messagebox.showerror("坐标不完整", "X、Y、Z 必须是数字。")
            return
        coordinate = {
            "name": name,
            "dimension": self.coord_dimension_var.get().strip() or "主世界",
            "x": values[0], "y": values[1], "z": values[2],
            "note": self.coord_note_var.get().strip(),
            "updated_at": time.time(),
        }
        self.coordinates.append(coordinate)
        root = Path(self.journeymap_root_var.get().strip())
        source = self.journeymap_source_var.get().strip()
        if source and (root / "data").is_dir():
            try:
                path = write_waypoint(coordinate, root, source)
                self.append_log(f"已同时写入 JourneyMap：{path.name}")
            except (OSError, ValueError, TypeError) as exc:
                self.append_log(f"JourneyMap 写入失败，坐标仍已保存在 MAF 中：{exc}")
        self.refresh_coordinates()
        if hasattr(self, "map_canvas"):
            self.refresh_map_viewer()
        self.save_config()
        self.coord_name_var.set("")
        self.coord_x_var.set("")
        self.coord_y_var.set("")
        self.coord_z_var.set("")
        self.coord_note_var.set("")
        self.append_log(f"已保存坐标：{name} ({values[0]}, {values[1]}, {values[2]})")

    def refresh_coordinates(self):
        if not hasattr(self, "coordinate_tree"):
            return
        self.coordinate_tree.delete(*self.coordinate_tree.get_children())
        columns = tuple(self.coordinate_tree["columns"])
        for index, item in enumerate(self.coordinates):
            source = (item.get("journeymap") or {}).get("source", "MAF")
            values_by_column = {
                "name": item.get("name", ""), "dimension": item.get("dimension", ""),
                "x": item.get("x", ""), "y": item.get("y", ""), "z": item.get("z", ""),
                "source": source, "note": item.get("note", ""),
            }
            self.coordinate_tree.insert(
                "", tk.END, iid=str(index),
                values=tuple(values_by_column.get(column, "") for column in columns),
            )

    def choose_journeymap_root(self):
        chosen = filedialog.askdirectory(
            title="选择 JourneyMap 目录",
            initialdir=self.journeymap_root_var.get() or str(Path.home()),
        )
        if chosen:
            self.journeymap_root_var.set(chosen)
            self.journeymap_source_var.set("")
            self.refresh_journeymap_sources()

    def refresh_journeymap_sources(self, show_errors=True):
        root_text = self.journeymap_root_var.get().strip()
        root = Path(root_text) if root_text else None
        sources = discover_sources(root) if root else []
        if hasattr(self, "journeymap_source_box"):
            self.journeymap_source_box.configure(values=sources)
        if hasattr(self, "map_source_box"):
            self.map_source_box.configure(values=sources)
        current = self.journeymap_source_var.get()
        if current not in sources:
            preferred = ""
            for source in sources:
                if any((root / "data" / Path(source) / "waypoints").glob("*.json")):
                    preferred = source
                    break
            self.journeymap_source_var.set(preferred or (sources[0] if sources else ""))
        if sources:
            self.journeymap_status_var.set(f"发现 {len(sources)} 个存档/服务器")
            self.save_config()
        else:
            self.journeymap_status_var.set("未找到路径点目录")
            if show_errors:
                messagebox.showerror("未找到 JourneyMap", "请选择包含 data 文件夹的 JourneyMap 根目录。")
        if hasattr(self, "map_dimension_box"):
            self.refresh_map_options()
        if sources:
            self.refresh_journeymap_coordinates_readonly()
        return sources

    def refresh_journeymap_coordinates_readonly(self):
        root = Path(self.journeymap_root_var.get().strip())
        source = self.journeymap_source_var.get().strip()
        if not source or not (root / "data").is_dir():
            return False
        remote, errors = scan_waypoints(root, source)
        local_by_key = {
            waypoint_key(item): item for item in self.coordinates if waypoint_key(item)
        }
        changed = False
        for remote_item in remote:
            key = waypoint_key(remote_item)
            local_item = local_by_key.get(key)
            if local_item is None:
                self.coordinates.append(remote_item)
                local_by_key[key] = remote_item
                changed = True
                continue
            remote_mtime = float((remote_item.get("journeymap") or {}).get("mtime", 0))
            known_mtime = float((local_item.get("journeymap") or {}).get("mtime", 0))
            if remote_mtime > known_mtime + 0.001:
                note = local_item.get("note", "")
                local_item.update(remote_item)
                local_item["note"] = note
                changed = True
        if changed:
            self.refresh_coordinates()
            self.save_config()
            if hasattr(self, "map_canvas"):
                self.refresh_map_viewer()
        for path, error in errors:
            self.append_log(f"只读刷新跳过无效路径点 {Path(path).name}：{error}")
        return changed

    def on_journeymap_source_changed(self, _event=None):
        self.save_config()
        self.refresh_map_options()
        self.refresh_journeymap_coordinates_readonly()

    def refresh_map_options(self):
        root = Path(self.journeymap_root_var.get().strip())
        source = self.journeymap_source_var.get().strip()
        dimensions = discover_dimensions(root, source) if source else []
        if hasattr(self, "map_dimension_box"):
            self.map_dimension_box.configure(values=dimensions)
        if self.map_dimension_var.get() not in dimensions:
            self.map_dimension_var.set(dimensions[0] if dimensions else "")
        if not dimensions:
            self.map_layer_var.set("")
            if hasattr(self, "map_layer_box"):
                self.map_layer_box.configure(values=())
            self.map_status_var.set("所选服务器没有可用的 JourneyMap 地图瓦片。")
            if hasattr(self, "map_canvas"):
                self.map_canvas.clear_layer("所选服务器没有地图瓦片")
            return
        self.refresh_map_layers()

    def refresh_map_layers(self, _event=None):
        root = Path(self.journeymap_root_var.get().strip())
        source = self.journeymap_source_var.get().strip()
        dimension = self.map_dimension_var.get().strip()
        layer_info = discover_layers(root, source, dimension)
        layers = [name for name, _count in layer_info]
        if hasattr(self, "map_layer_box"):
            self.map_layer_box.configure(values=layers)
        if self.map_layer_var.get() not in layers:
            self.map_layer_var.set(layers[0] if layers else "")
        if layers:
            self.update_map_summary()
            self.refresh_map_viewer()
        else:
            self.map_status_var.set("所选维度没有可拼接的 PNG 图层。")
            if hasattr(self, "map_canvas"):
                self.map_canvas.clear_layer("所选维度没有地图瓦片")

    def on_map_layer_changed(self, _event=None):
        self.update_map_summary()
        self.refresh_map_viewer()

    def map_scale(self):
        value = self.map_scale_var.get().strip().rstrip("%")
        return float(value) / 100

    def update_map_summary(self, _event=None):
        root = Path(self.journeymap_root_var.get().strip())
        source = self.journeymap_source_var.get().strip()
        dimension = self.map_dimension_var.get().strip()
        layer = self.map_layer_var.get().strip()
        if not all((source, dimension, layer)):
            self.map_status_var.set("请选择服务器、维度和图层。")
            return
        try:
            info = analyze_layer(root, source, dimension, layer)
            scale = self.map_scale()
            width, height, _tile_width, _tile_height = output_dimensions(info, scale)
        except (OSError, ValueError) as exc:
            self.map_status_var.set(f"分析失败：{exc}")
            return
        memory_gb = width * height * 4 / (1024 ** 3)
        self.map_status_var.set(
            f"{info.tile_count} 块 · X {info.min_block_x}…{info.max_block_x} · "
            f"Z {info.min_block_z}…{info.max_block_z} · 导出 {width}×{height} / {memory_gb:.2f} GB"
        )
        self.save_config()

    def map_markers_for_dimension(self, root, source, dimension, log_errors=False):
        aliases = {
            "overworld": {"主世界", "minecraft:overworld", "overworld"},
            "the_nether": {"下界", "minecraft:the_nether", "the_nether"},
            "the_end": {"末地", "minecraft:the_end", "the_end"},
        }
        accepted = aliases.get(dimension, {dimension})
        candidates = list(self.coordinates)
        remote, errors = scan_waypoints(root, source)
        candidates.extend(remote)
        if log_errors:
            for path, error in errors:
                self.append_log(f"地图标记跳过无效路径点 {Path(path).name}：{error}")
        markers = []
        seen = set()
        for item in candidates:
            if item.get("dimension") not in accepted:
                continue
            key = (item.get("name"), str(item.get("x")), str(item.get("z")))
            if key not in seen:
                seen.add(key)
                markers.append(item)
        return markers

    def refresh_map_viewer(self):
        if not hasattr(self, "map_canvas"):
            return
        root = Path(self.journeymap_root_var.get().strip())
        source = self.journeymap_source_var.get().strip()
        dimension = self.map_dimension_var.get().strip()
        layer = self.map_layer_var.get().strip()
        if not all((source, dimension, layer)):
            self.map_canvas.clear_layer("请选择服务器、维度和图层")
            return
        markers = (
            self.map_markers_for_dimension(root, source, dimension)
            if self.map_markers_var.get() else []
        )
        self.map_marker_lookup = {}
        marker_values = []
        for index, marker in enumerate(markers, start=1):
            label = f"{marker.get('name') or '坐标'} · {marker.get('x')}, {marker.get('z')}"
            if label in self.map_marker_lookup:
                label = f"{label} #{index}"
            self.map_marker_lookup[label] = marker
            marker_values.append(label)
        if hasattr(self, "map_marker_box"):
            self.map_marker_box.configure(values=marker_values)
        if self.map_marker_var.get() not in marker_values:
            self.map_marker_var.set(marker_values[0] if marker_values else "")

        def marker_provider():
            if not self.map_markers_var.get():
                return []
            return self.map_markers_for_dimension(root, source, dimension)

        self.map_canvas.set_layer(
            root, source, dimension, layer, markers=markers, marker_provider=marker_provider
        )

    def locate_map_marker(self):
        marker = self.map_marker_lookup.get(self.map_marker_var.get())
        if not marker:
            messagebox.showinfo("没有坐标标记", "当前图层中没有可定位的坐标标记。")
            return
        try:
            x, z = float(marker.get("x")), float(marker.get("z"))
        except (TypeError, ValueError):
            messagebox.showerror("坐标无效", "该标记的 X 或 Z 不是有效数字。")
            return
        self.map_canvas.center_on(x, z, zoom=max(0.5, self.map_canvas.zoom))
        self.map_cursor_var.set(f"已定位 {marker.get('name') or '坐标'} · X {x:g} · Z {z:g}")

    def start_map_export(self):
        if not self.ensure_idle():
            return
        root = Path(self.journeymap_root_var.get().strip())
        source = self.journeymap_source_var.get().strip()
        dimension = self.map_dimension_var.get().strip()
        layer = self.map_layer_var.get().strip()
        try:
            info = analyze_layer(root, source, dimension, layer)
            scale = self.map_scale()
            width, height, _tile_width, _tile_height = output_dimensions(info, scale)
        except (OSError, ValueError) as exc:
            messagebox.showerror("地图无法导出", str(exc))
            return
        pixels = width * height
        if pixels > DEFAULT_MAX_PIXELS:
            messagebox.showerror(
                "地图过大",
                f"当前输出为 {width}×{height}，超过安全上限。请降低输出比例。",
            )
            return
        if pixels > 80_000_000 and not messagebox.askyesno(
            "确认导出大地图",
            f"输出将达到 {width}×{height}（{pixels / 1_000_000:.1f} MP），\n"
            f"预计至少占用 {pixels * 4 / (1024 ** 3):.2f} GB 画布内存，是否继续？",
        ):
            return
        source_name = source.split("/")[-1][:24] or "world"
        filename = filedialog.asksaveasfilename(
            title="导出 JourneyMap 拼接地图", defaultextension=".png",
            filetypes=[("PNG 地图", "*.png")],
            initialfile=f"JourneyMap_{source_name}_{dimension}_{layer}_{self.map_scale_var.get().replace('%', 'pct')}.png",
        )
        if not filename:
            return
        markers = self.map_markers_for_dimension(
            root, source, dimension, log_errors=True
        ) if self.map_markers_var.get() else []
        settings = {
            "root": root, "source": source, "dimension": dimension, "layer": layer,
            "output": Path(filename), "scale": scale, "markers": markers,
        }
        self.start_worker(MapStitchWorker(settings, self.event_queue))

    def sync_journeymap(self, manual=True):
        root = Path(self.journeymap_root_var.get().strip())
        source = self.journeymap_source_var.get().strip()
        if not (root / "data").is_dir():
            if manual:
                messagebox.showerror("JourneyMap 路径无效", "所选目录中没有 data 文件夹。")
            self.journeymap_status_var.set("路径无效")
            return False
        if not source:
            sources = self.refresh_journeymap_sources(show_errors=manual)
            source = self.journeymap_source_var.get().strip()
            if not sources or not source:
                return False
        try:
            result = sync_coordinates(self.coordinates, root, source)
        except (OSError, ValueError, TypeError) as exc:
            self.journeymap_status_var.set("同步失败")
            self.append_log(f"JourneyMap 同步失败：{exc}")
            if manual:
                messagebox.showerror("同步失败", str(exc))
            return False
        self.refresh_coordinates()
        if hasattr(self, "map_canvas"):
            self.refresh_map_viewer()
        self.save_config()
        summary = (
            f"导入 {result['imported']} · 更新 {result['updated']} · 写入 {result['written']}"
        )
        self.journeymap_status_var.set(summary)
        if any((result["imported"], result["updated"], result["written"])):
            self.append_log(f"JourneyMap 同步完成：{summary}")
        for path, error in result["errors"]:
            self.append_log(f"跳过无效路径点 {Path(path).name}：{error}")
        if manual:
            messagebox.showinfo("同步完成", summary + (f"\n跳过 {len(result['errors'])} 个无效文件" if result["errors"] else ""))
        return True

    def auto_sync_journeymap(self):
        if self.journeymap_auto_sync_var.get():
            self.sync_journeymap(manual=False)
        else:
            self.refresh_journeymap_coordinates_readonly()
        self.after(5000, self.auto_sync_journeymap)

    def export_coordinates_csv(self):
        if not self.coordinates:
            messagebox.showinfo("没有坐标", "当前没有可导出的坐标。")
            return
        filename = filedialog.asksaveasfilename(
            title="导出坐标 CSV", defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv")],
            initialfile=f"minecraft_coordinates_{time.strftime('%Y%m%d_%H%M%S')}.csv",
        )
        if not filename:
            return
        try:
            with open(filename, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.writer(handle)
                writer.writerow(["名称", "维度", "X", "Y", "Z", "备注", "JourneyMap来源", "JourneyMap ID"])
                for item in self.coordinates:
                    metadata = item.get("journeymap") or {}
                    writer.writerow([
                        item.get("name", ""), item.get("dimension", ""), item.get("x", ""),
                        item.get("y", ""), item.get("z", ""), item.get("note", ""),
                        metadata.get("source", ""), metadata.get("id", ""),
                    ])
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.append_log(f"已导出 {len(self.coordinates)} 个坐标到 CSV：{filename}")

    def export_coordinates_journeymap(self):
        if not self.coordinates:
            messagebox.showinfo("没有坐标", "当前没有可导出的坐标。")
            return
        parent = filedialog.askdirectory(title="选择 JourneyMap 路径点导出位置")
        if not parent:
            return
        destination = Path(parent) / f"journeymap_waypoints_{time.strftime('%Y%m%d_%H%M%S')}"
        try:
            exported = export_waypoints(self.coordinates, destination)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("导出失败", str(exc))
            return
        self.append_log(f"已导出 {len(exported)} 个 JourneyMap JSON：{destination}")
        messagebox.showinfo("导出完成", f"已导出 {len(exported)} 个路径点到：\n{destination}")

    def selected_coordinate_index(self):
        selection = self.coordinate_tree.selection()
        if not selection:
            messagebox.showinfo("请选择地点", "请先在列表中选择一个坐标。")
            return None
        return int(selection[0])

    def locate_selected_coordinate(self):
        index = self.selected_coordinate_index()
        if index is None:
            return
        item = self.coordinates[index]
        dimension_aliases = {
            "主世界": "overworld", "minecraft:overworld": "overworld",
            "下界": "the_nether", "minecraft:the_nether": "the_nether",
            "末地": "the_end", "minecraft:the_end": "the_end",
        }
        target_dimension = dimension_aliases.get(item.get("dimension"), item.get("dimension"))
        available = tuple(self.map_dimension_box["values"]) if hasattr(self, "map_dimension_box") else ()
        if target_dimension in available and self.map_dimension_var.get() != target_dimension:
            self.map_dimension_var.set(target_dimension)
            self.refresh_map_layers()
        try:
            x, z = float(item.get("x")), float(item.get("z"))
        except (TypeError, ValueError):
            messagebox.showerror("坐标无效", "该标记的 X 或 Z 不是有效数字。")
            return
        self.map_canvas.center_on(x, z, zoom=max(0.5, self.map_canvas.zoom))
        self.map_cursor_var.set(f"已定位 {item.get('name') or '坐标'} · X {x:g} · Z {z:g}")

    def copy_coordinate(self):
        index = self.selected_coordinate_index()
        if index is None:
            return
        item = self.coordinates[index]
        text = (
            f"{item.get('name', '')} | {item.get('dimension', '')} | "
            f"X {item.get('x', '')}  Y {item.get('y', '')}  Z {item.get('z', '')}"
        )
        if item.get("note"):
            text += f" | {item['note']}"
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()
        self.append_log(f"坐标已复制：{text}")

    def delete_coordinate(self):
        index = self.selected_coordinate_index()
        if index is None:
            return
        item = self.coordinates[index]
        metadata = item.get("journeymap") or {}
        if metadata.get("source") and metadata.get("filename"):
            confirmed = messagebox.askyesno(
                "删除 JourneyMap 路径点",
                f"“{item.get('name', '')}”来自 JourneyMap。\n\n"
                "确定后会同时删除 JourneyMap JSON；建议先关闭游戏中的路径点管理界面。",
            )
            if not confirmed:
                return
            waypoint_dir = (
                Path(self.journeymap_root_var.get().strip()) / "data" /
                Path(metadata["source"]) / "waypoints"
            ).resolve()
            waypoint_path = (waypoint_dir / metadata["filename"]).resolve()
            try:
                waypoint_path.relative_to(waypoint_dir)
                waypoint_path.unlink(missing_ok=True)
            except (OSError, ValueError) as exc:
                messagebox.showerror("删除失败", f"未能删除 JourneyMap 文件：{exc}")
                return
        self.coordinates.pop(index)
        self.refresh_coordinates()
        if hasattr(self, "map_canvas"):
            self.refresh_map_viewer()
        self.save_config()
        self.append_log(f"已删除坐标：{item.get('name', '')}")

    def stop_all(self):
        if self.worker and self.worker.thread and self.worker.thread.is_alive():
            self.task_stop_requested = True
            self.worker.stop()
            self.status_var.set("正在停止")
            self.status_detail_var.set("正在释放输入并结束任务…")
            self.status_dot.configure(fg=COLORS["amber"])
            self.append_log("用户请求停止当前任务。")
        else:
            self.status_var.set("空闲")
            self.status_detail_var.set("当前没有正在运行的自动化任务")

    def select_region(self):
        self.withdraw()

        def selected(region):
            self.region_var.set(",".join(map(str, region)))
            self.deiconify()
            self.lift()
            self.append_log(f"已选择字幕区域：{region}")

        selector = RegionSelector(self, selected)
        selector.bind("<Destroy>", lambda _event: self.after(100, self.restore_after_selector))

    def restore_after_selector(self):
        if not self.winfo_viewable():
            self.deiconify()

    def reset_region(self):
        region = default_subtitle_region()
        self.region_var.set(",".join(map(str, region)))
        self.append_log(f"已恢复默认字幕区域：{region}")

    def test_ocr(self):
        try:
            region = parse_region(self.region_var.get())
            scale = float(self.ocr_scale_var.get())
            ready, detail = check_tesseract_ready()
            if not ready:
                raise RuntimeError(detail)
            started = time.monotonic()
            text = ocr_text(region, lang=self.lang_var.get().strip() or "chi_sim+eng", scale=scale)
            elapsed = int((time.monotonic() - started) * 1000)
            matched = text_matches(text, parse_keywords(self.keywords_var.get()))
            self.append_log(f"OCR 测试：{elapsed} ms；匹配={matched}；文本={text!r}")
            messagebox.showinfo("OCR 测试完成", f"耗时：{elapsed} ms\n匹配关键词：{'是' if matched else '否'}\n\n{text or '未识别到文字'}")
        except Exception as exc:
            messagebox.showerror("OCR 测试失败", str(exc))

    def preview_ocr(self):
        try:
            region = parse_region(self.region_var.get())
            scale = float(self.ocr_scale_var.get())
            ready, detail = check_tesseract_ready()
            if not ready:
                raise RuntimeError(detail)
            started = time.monotonic()
            original, processed, text = ocr_capture(
                region, lang=self.lang_var.get().strip() or "chi_sim+eng", scale=scale
            )
            elapsed = int((time.monotonic() - started) * 1000)
            matched = text_matches(text, parse_keywords(self.keywords_var.get()))

            window = tk.Toplevel(self)
            window.title("OCR 图像预览")
            window.geometry("940x610")
            window.minsize(760, 520)
            window.configure(bg=COLORS["bg"])
            window.transient(self)

            title = tk.Frame(window, bg=COLORS["bg"])
            title.pack(fill=tk.X, padx=24, pady=(20, 12))
            self.label(title, "OCR 图像预览", size=16, bold=True).pack(anchor=tk.W)
            self.label(
                title, f"耗时 {elapsed} ms · 关键词匹配：{'是' if matched else '否'}",
                muted=True, size=8,
            ).pack(anchor=tk.W, pady=(4, 0))

            images = tk.Frame(window, bg=COLORS["bg"])
            images.pack(fill=tk.BOTH, expand=True, padx=24)
            original_card = self.card(images, 12)
            original_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
            processed_card = self.card(images, 12)
            processed_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
            self.label(original_card, "原始字幕截图", size=10, bold=True).pack(anchor=tk.W, pady=(0, 10))
            self.label(processed_card, "OCR 预处理结果", size=10, bold=True).pack(anchor=tk.W, pady=(0, 10))

            original_preview = original.copy()
            original_preview.thumbnail((410, 330), Image.Resampling.LANCZOS)
            processed_preview = Image.fromarray(processed).convert("RGB")
            processed_preview.thumbnail((410, 330), Image.Resampling.LANCZOS)
            window.original_photo = ImageTk.PhotoImage(original_preview)
            window.processed_photo = ImageTk.PhotoImage(processed_preview)
            tk.Label(original_card, image=window.original_photo, bg=COLORS["image_bg"]).pack(fill=tk.BOTH, expand=True)
            tk.Label(processed_card, image=window.processed_photo, bg=COLORS["image_bg"]).pack(fill=tk.BOTH, expand=True)

            result = self.card(window, 12)
            result.pack(fill=tk.X, padx=24, pady=16)
            self.label(result, "识别文本", muted=True, size=8).pack(anchor=tk.W)
            self.label(
                result, text or "未识别到文字", size=9, wraplength=850, justify=tk.LEFT,
            ).pack(anchor=tk.W, pady=(5, 0))
            self.append_log(f"OCR 图像预览：{elapsed} ms；匹配={matched}；文本={text!r}")
        except Exception as exc:
            messagebox.showerror("OCR 预览失败", str(exc))

    def export_fishing_records(self):
        if not self.fish_records:
            messagebox.showinfo("暂无记录", "本次还没有可导出的钓鱼识别记录。")
            return
        filename = filedialog.asksaveasfilename(
            title="导出钓鱼记录",
            defaultextension=".csv",
            initialfile=f"钓鱼记录_{time.strftime('%Y%m%d_%H%M%S')}.csv",
            filetypes=(("CSV 文件", "*.csv"), ("所有文件", "*.*")),
        )
        if not filename:
            return
        fields = ("time", "result", "wait_seconds", "catches", "timeouts", "casts", "ocr_text")
        try:
            with open(filename, "w", newline="", encoding="utf-8-sig") as file:
                writer = csv.DictWriter(file, fieldnames=fields)
                writer.writeheader()
                writer.writerows(self.fish_records)
            self.append_log(f"钓鱼记录已导出：{filename}")
            messagebox.showinfo("导出完成", filename)
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc))

    def check_ocr_status(self):
        ready, detail = check_tesseract_ready()
        if ready:
            self.ocr_badge.configure(text="●  OCR 已就绪", fg=COLORS["green"])
        else:
            self.ocr_badge.configure(text="●  OCR 未就绪", fg=COLORS["red"])
        self.append_log(f"OCR {'已就绪' if ready else '不可用'}：{detail}")

    def start_timer(self):
        try:
            minutes = float(self.timer_minutes_var.get())
            if minutes <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("参数有误", "提醒分钟数必须大于 0。")
            return
        self.cancel_timer(log=False)
        self.timer_end = time.monotonic() + minutes * 60
        self.timer_status_var.set("计时中…")
        self.append_log(f"已设置 {minutes:g} 分钟提醒。")
        self.update_timer()

    def update_timer(self):
        if self.timer_end is None:
            return
        remaining = max(0, int(self.timer_end - time.monotonic()))
        self.timer_status_var.set(f"{remaining // 60:02d}:{remaining % 60:02d}")
        if remaining <= 0:
            self.timer_end = None
            self.timer_job = None
            self.timer_status_var.set("时间到了！")
            self.bell()
            self.lift()
            messagebox.showinfo(f"{APP_NAME} 提醒", "你设置的时间到了。")
            self.append_log("定时提醒已到时。")
        else:
            self.timer_job = self.after(250, self.update_timer)

    def cancel_timer(self, log=True):
        if self.timer_job:
            self.after_cancel(self.timer_job)
        self.timer_job = None
        self.timer_end = None
        self.timer_status_var.set("尚未设置提醒")
        if log:
            self.append_log("已取消定时提醒。")

    def choose_screenshot_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.screenshot_dir_var.get() or str(Path.home()))
        if chosen:
            self.screenshot_dir_var.set(chosen)

    def take_screenshot(self):
        try:
            target = Path(self.screenshot_dir_var.get()).expanduser()
            target.mkdir(parents=True, exist_ok=True)
            filename = target / f"Minecraft_{time.strftime('%Y%m%d_%H%M%S')}.png"
            pyautogui.screenshot().save(filename)
            self.total_screenshots += 1
            self.append_log(f"截图已保存：{filename}")
            messagebox.showinfo("截图已保存", str(filename))
        except Exception as exc:
            messagebox.showerror("截图失败", str(exc))

    def toggle_topmost(self):
        self.attributes("-topmost", self.always_on_top_var.get())

    def notify_user(self, title, message):
        if not self.notifications_enabled_var.get():
            return

        def send():
            try:
                self.notifier.notify(title, message)
            except Exception as exc:
                self.event_queue.put(("notification_error", str(exc)))

        threading.Thread(target=send, daemon=True).start()

    def switch_theme(self):
        current_page = self.current_page
        was_withdrawn = self.state() == "withdrawn"
        log_content = self.log_text.get("1.0", tk.END) if hasattr(self, "log_text") else ""
        fish_content = self.fish_record_text.get("1.0", tk.END) if hasattr(self, "fish_record_text") else ""

        self.withdraw()
        set_color_mode(self.dark_mode_var.get())
        self.configure(bg=COLORS["bg"])
        for child in list(self.winfo_children()):
            child.destroy()
        self.page_frames.clear()
        self.nav_buttons.clear()
        self.configure_styles()
        self.build_shell()
        self.refresh_journeymap_sources(show_errors=False)
        self.show_page(current_page if current_page in self.page_frames else "dashboard")

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert("1.0", log_content)
        self.log_text.configure(state=tk.DISABLED)
        self.fish_record_text.configure(state=tk.NORMAL)
        self.fish_record_text.delete("1.0", tk.END)
        self.fish_record_text.insert("1.0", fish_content)
        self.fish_record_text.configure(state=tk.DISABLED)
        self.attributes("-topmost", self.always_on_top_var.get())
        self.save_config()
        self.append_log(f"已切换到{'黑夜' if self.dark_mode_var.get() else '白天'}模式。")
        if not was_withdrawn:
            self.deiconify()
            self.lift()

    def update_stop_hotkey(self, _event=None):
        hotkey = self.stop_hotkey_var.get()
        self.stop_vk = HOTKEY_VK.get(hotkey, HOTKEY_VK["F8"])
        self.emergency_button.configure(text=f"■  立即停止全部  {hotkey}")
        self.dashboard_stop_button.configure(text=f"立即停止全部  {hotkey}")
        self.save_config()
        self.append_log(f"全局停止热键已改为 {hotkey}。")

    def create_tray_image(self):
        image = Image.new("RGBA", (64, 64), COLORS["sidebar"])
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=10, fill=COLORS["green"])
        draw.polygon(((32, 16), (47, 32), (32, 48), (17, 32)), fill=COLORS["sidebar"])
        return image

    def toggle_tray(self):
        if self.tray_enabled_var.get():
            if pystray is None:
                self.tray_enabled_var.set(False)
                self.tray_status_var.set("未安装 pystray")
                messagebox.showinfo("系统托盘不可用", "请运行 pip install pystray 后重新启动 MAF。")
                return
            if self.tray_icon is None:
                try:
                    menu = pystray.Menu(
                        pystray.MenuItem(f"显示 {APP_NAME}", lambda _icon, _item: self.event_queue.put(("tray_show", None)), default=True),
                        pystray.MenuItem("停止当前任务", lambda _icon, _item: self.event_queue.put(("global_stop", None))),
                        pystray.MenuItem("退出", lambda _icon, _item: self.event_queue.put(("tray_exit", None))),
                    )
                    self.tray_icon = pystray.Icon("maf", self.create_tray_image(), APP_NAME, menu)
                    self.tray_icon.run_detached()
                except Exception as exc:
                    self.tray_icon = None
                    self.tray_enabled_var.set(False)
                    self.tray_status_var.set("托盘启动失败")
                    self.append_log(f"系统托盘启动失败：{exc}")
                    messagebox.showerror("系统托盘启动失败", str(exc))
                    return
            self.tray_status_var.set("托盘运行中")
            self.append_log("系统托盘已启用；关闭窗口会隐藏到托盘。")
        else:
            self.stop_tray()
            self.tray_status_var.set("托盘已关闭" if pystray else "未安装 pystray")
        self.save_config()

    def stop_tray(self):
        icon = self.tray_icon
        self.tray_icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception as exc:
                self.append_log(f"停止系统托盘失败：{exc}")

    def drain_events(self):
        while True:
            try:
                kind, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.append_log(payload)
            elif kind == "global_stop":
                self.stop_all()
            elif kind == "tray_show":
                self.deiconify()
                self.lift()
                self.focus_force()
            elif kind == "tray_exit":
                self.exit_requested = True
                self.on_close()
                return
            elif kind == "task_started":
                self.last_task_failed = False
                self.task_stop_requested = False
                try:
                    self.current_run_id = self.history_store.start_run(payload)
                except Exception as exc:
                    self.current_run_id = None
                    self.append_log(f"无法写入任务历史：{exc}")
                self.status_var.set("运行中")
                self.status_detail_var.set(f"{payload}正在运行 · {self.stop_hotkey_var.get()} 可立即停止")
                self.dashboard_task_var.set(payload)
                if payload == "自动钓鱼":
                    self.fish_session_running = True
                self.status_dot.configure(fg=COLORS["green"])
                self.append_log(f"{payload}已启动。")
            elif kind == "stats":
                self.latest_stats.update(payload)
                try:
                    self.history_store.update_run(
                        self.current_run_id, payload.get("actions"), payload.get("detail")
                    )
                except Exception as exc:
                    self.append_log(f"更新任务历史失败：{exc}")
                if "actions" in payload:
                    self.dashboard_actions_var.set(str(payload["actions"]))
                if "catches" in payload:
                    self.dashboard_fish_var.set(str(payload["catches"]))
                    self.fish_catches_var.set(str(payload.get("catches", 0)))
                    self.fish_timeouts_var.set(str(payload.get("timeouts", 0)))
                    self.fish_casts_var.set(str(payload.get("casts", payload.get("actions", 0))))
                    if "last_ocr" in payload:
                        self.fish_last_ocr_var.set(payload["last_ocr"][:120] or "未识别到文字")
                    if payload.get("record"):
                        self.append_fishing_record(payload["record"])
                    if payload.get("record_data"):
                        self.fish_records.append(payload["record_data"])
                self.status_detail_var.set(payload.get("detail", f"{payload.get('task', '任务')}正在运行"))
            elif kind == "task_error":
                self.last_task_failed = True
                self.status_var.set("出错")
                self.status_detail_var.set(payload.get("error", "任务执行失败"))
                self.status_dot.configure(fg=COLORS["red"])
                self.notify_user(f"{payload.get('task', '任务')}失败", payload.get("error", "未知错误"))
            elif kind == "notification_error":
                self.append_log(f"Windows 通知发送失败：{payload}")
            elif kind == "task_finished":
                history_status = "失败" if self.last_task_failed else ("已停止" if self.task_stop_requested else "已完成")
                try:
                    self.history_store.finish_run(
                        self.current_run_id, history_status, self.latest_stats.get("detail", "")
                    )
                except Exception as exc:
                    self.append_log(f"结束任务历史失败：{exc}")
                self.current_run_id = None
                if self.last_task_failed:
                    self.status_var.set("出错")
                    self.status_detail_var.set(f"{payload}因错误结束，请查看运行日志")
                    self.status_dot.configure(fg=COLORS["red"])
                else:
                    self.status_var.set("空闲")
                    self.status_detail_var.set(f"{payload}已结束")
                    self.status_dot.configure(fg=COLORS["green"])
                self.dashboard_task_var.set("暂无任务")
                if payload == "自动钓鱼":
                    self.fish_session_running = False
                self.append_log(f"{payload}已结束。")
                if not self.last_task_failed:
                    self.notify_user("任务已结束", f"{payload}已经结束。")
        self.after(100, self.drain_events)

    def monitor_global_stop_hotkey(self):
        """Listen for the configured function key while Minecraft has focus."""
        try:
            get_key_state = ctypes.windll.user32.GetAsyncKeyState
        except (AttributeError, OSError):
            return
        was_down = False
        while not self.hotkey_stop_event.wait(0.04):
            is_down = bool(get_key_state(self.stop_vk) & 0x8000)
            if is_down and not was_down:
                self.event_queue.put(("global_stop", None))
            was_down = is_down

    def tick_clock(self):
        elapsed = int(time.monotonic() - self.session_started)
        self.dashboard_runtime_var.set(f"{elapsed // 60:02d}:{elapsed % 60:02d}")
        started_at = self.latest_stats.get("started_at")
        if started_at and self.latest_stats.get("task") == "自动钓鱼" and self.fish_session_running:
            fish_elapsed = int(time.monotonic() - started_at)
            self.fish_runtime_var.set(f"{fish_elapsed // 60:02d}:{fish_elapsed % 60:02d}")
        title = get_foreground_window_title()
        if is_minecraft_foreground():
            self.foreground_status_var.set(f"前台已就绪 · {title[:42]}")
        else:
            self.foreground_status_var.set(f"等待 Minecraft · 当前：{title[:34] or '未知窗口'}")
        self.after(500, self.tick_clock)

    def append_fishing_record(self, message):
        self.fish_record_text.configure(state=tk.NORMAL)
        self.fish_record_text.insert(tk.END, f"{message}\n")
        self.fish_record_text.see(tk.END)
        self.fish_record_text.configure(state=tk.DISABLED)

    def reset_fishing_stats(self):
        self.fish_records.clear()
        self.fish_catches_var.set("0")
        self.fish_timeouts_var.set("0")
        self.fish_casts_var.set("0")
        self.fish_runtime_var.set("00:00")
        self.fish_last_ocr_var.set("尚无识别结果")
        if hasattr(self, "fish_record_text"):
            self.fish_record_text.configure(state=tk.NORMAL)
            self.fish_record_text.delete("1.0", tk.END)
            self.fish_record_text.configure(state=tk.DISABLED)

    def append_log(self, message):
        if not hasattr(self, "log_text"):
            return
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{timestamp}]  {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def show_history_window(self):
        window = tk.Toplevel(self)
        window.title(f"任务历史 · {APP_NAME}")
        window.geometry("900x480")
        window.minsize(720, 360)
        window.configure(bg=COLORS["bg"])
        toolbar = tk.Frame(window, bg=COLORS["bg"])
        toolbar.pack(fill=tk.X, padx=16, pady=(16, 8))
        self.label(toolbar, "SQLite 任务历史", size=12, bold=True).pack(side=tk.LEFT)
        tree_frame = self.card(window, 1)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))
        columns = ("started", "task", "status", "actions", "detail")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", style="Dark.Treeview")
        headings = {
            "started": "开始时间", "task": "任务", "status": "状态",
            "actions": "动作数", "detail": "最后状态",
        }
        widths = {"started": 155, "task": 110, "status": 70, "actions": 65, "detail": 390}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor=tk.W, stretch=column == "detail")
        scroll = ttk.Scrollbar(tree_frame, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def refresh():
            tree.delete(*tree.get_children())
            for row in self.history_store.list_runs(300):
                started = row["started_at"].replace("T", " ")[:19]
                tree.insert("", tk.END, values=(
                    started, row["task"], row["status"], row["actions"], row["detail"],
                ))

        def clear_history():
            if messagebox.askyesno("清空任务历史", "确认删除全部任务历史？", parent=window):
                self.history_store.clear()
                refresh()

        self.action_button(toolbar, "清空历史", clear_history, danger=True).pack(side=tk.RIGHT)
        self.action_button(toolbar, "刷新", refresh).pack(side=tk.RIGHT, padx=8)
        refresh()

    def load_config(self):
        try:
            return upgrade_brand_config(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def save_config(self):
        data = {
            "always_on_top": self.always_on_top_var.get(),
            "dark_mode": self.dark_mode_var.get(),
            "stop_hotkey": self.stop_hotkey_var.get(),
            "tray_enabled": self.tray_enabled_var.get(),
            "minecraft_guard": self.minecraft_guard_var.get(),
            "profiles": self.profiles,
            "active_profile": self.profile_var.get(),
            "fishing": {
                "region": self.region_var.get(), "keywords": self.keywords_var.get(),
                "lang": self.lang_var.get(), "poll": self.poll_var.get(),
                "ocr_scale": self.ocr_scale_var.get(), "timeout": self.timeout_var.get(),
                "cast_delay": self.cast_delay_var.get(), "recast_delay": self.recast_delay_var.get(),
                "start_delay": self.fish_start_delay_var.get(),
            },
            "clicker": {
                "button": self.click_button_var.get(), "interval": self.click_interval_var.get(),
                "limit": self.click_limit_var.get(), "start_delay": self.click_delay_var.get(),
            },
            "key_hold": {
                "key": self.hold_key_var.get(), "duration": self.hold_duration_var.get(),
                "start_delay": self.hold_delay_var.get(),
            },
            "commands": {
                "text": self.command_text_var.get(), "interval": self.command_interval_var.get(),
                "count": self.command_count_var.get(), "start_delay": self.command_delay_var.get(),
                "chat_key": self.command_chat_key_var.get(),
            },
            "workflow": {
                "steps": self.workflow_steps,
                "loops": self.workflow_loops_var.get(),
                "start_delay": self.workflow_delay_var.get(),
                "presets": self.workflow_presets,
                "current_preset": self.workflow_preset_var.get(),
            },
            "coordinates": self.coordinates,
            "journeymap": {
                "root": self.journeymap_root_var.get(),
                "source": self.journeymap_source_var.get(),
                "auto_sync": self.journeymap_auto_sync_var.get(),
                "map_export": {
                    "dimension": self.map_dimension_var.get(),
                    "layer": self.map_layer_var.get(),
                    "scale": self.map_scale_var.get(),
                    "markers": self.map_markers_var.get(),
                },
            },
            "tools": {
                "timer_minutes": self.timer_minutes_var.get(),
                "screenshot_dir": self.screenshot_dir_var.get(),
            },
            "notifications_enabled": self.notifications_enabled_var.get(),
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            self.append_log(f"保存设置失败：{exc}")

    def on_close(self):
        self.save_config()
        if self.tray_enabled_var.get() and self.tray_icon is not None and not self.exit_requested:
            self.withdraw()
            self.append_log("窗口已隐藏到系统托盘。")
            return
        self.hotkey_stop_event.set()
        if self.worker:
            self.worker.stop()
        self.stop_tray()
        self.destroy()


if __name__ == "__main__":
    app = MAFApp()
    app.mainloop()
