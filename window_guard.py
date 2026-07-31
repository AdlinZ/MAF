import ctypes
import os
import sys


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _foreground_window_details():
    """Return the active window's title, class name, and executable name."""
    if sys.platform != "win32":
        return "", "", ""

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    handle = user32.GetForegroundWindow()
    if not handle:
        return "", "", ""

    title_length = user32.GetWindowTextLengthW(handle)
    title_buffer = ctypes.create_unicode_buffer(max(1, title_length + 1))
    user32.GetWindowTextW(handle, title_buffer, len(title_buffer))

    class_buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(handle, class_buffer, len(class_buffer))

    process_id = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
    executable = ""
    process = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value
    )
    if process:
        try:
            path_buffer = ctypes.create_unicode_buffer(32768)
            path_length = ctypes.c_ulong(len(path_buffer))
            if kernel32.QueryFullProcessImageNameW(
                process, 0, path_buffer, ctypes.byref(path_length)
            ):
                executable = os.path.basename(path_buffer.value)
        finally:
            kernel32.CloseHandle(process)

    return title_buffer.value.strip(), class_buffer.value, executable


def get_foreground_window_title():
    """Return the active window title on Windows, or an empty string."""
    title, _class_name, _executable = _foreground_window_details()
    return title


def is_minecraft_foreground(title=None):
    """Recognise Minecraft by its title or its Java GLFW game window."""
    active_title, class_name, executable = _foreground_window_details()
    title = active_title if title is None else str(title)
    lowered = title.casefold()
    if "minecraft" in lowered:
        return True

    # Modpacks can replace the title completely (for example, "方块相册-2026-夏至").
    # Minecraft Java uses a GLFW window hosted by java.exe/javaw.exe, which remains
    # stable even when a mod or launcher changes the visible title.
    return (
        title == active_title
        and executable.casefold() in {"java.exe", "javaw.exe"}
        and class_name.casefold().startswith("glfw")
    )
