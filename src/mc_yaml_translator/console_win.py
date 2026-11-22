"""Windows console helpers: hide on first double-click, ANSI, pause-on-exit."""

from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """Directory that should hold config.toml.

    When packaged with PyInstaller, this is the folder containing the exe
    (not the temporary _MEIPASS unpack directory). Otherwise the cwd,
    which is what you want when launching from a project folder.
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.getcwd())


def owns_console() -> bool:
    """True when this process is the only one attached to the console.

    On Windows, a console exe launched by double-click or drag-and-drop
    gets a freshly created console (process list length 1). When launched
    from an existing cmd/PowerShell window, other processes are attached
    and we must not hide or FreeConsole that window.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        # GetConsoleProcessList(LPDWORD, DWORD) -> DWORD
        kernel32.GetConsoleProcessList.argtypes = [
            ctypes.POINTER(wintypes.DWORD),
            wintypes.DWORD,
        ]
        kernel32.GetConsoleProcessList.restype = wintypes.DWORD
        buf = (wintypes.DWORD * 8)()
        n = kernel32.GetConsoleProcessList(buf, 8)
        return n <= 1
    except Exception:
        return False


def hide_and_free_console() -> None:
    """Hide and detach our dedicated console so a double-click does not flash.

    Build the exe as a *console* application (--console / no --noconsole) so
    that drag-and-drop still has a real stdout for tqdm. On the no-args
    first-run path we immediately hide the window and call FreeConsole.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass


def enable_windows_ansi() -> None:
    """Enable VT sequences so tqdm/colorama render correctly on Windows."""
    try:
        import colorama

        colorama.init()
    except Exception:
        pass
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING | ENABLE_PROCESSED_OUTPUT
            kernel32.SetConsoleMode(handle, mode.value | 0x0004 | 0x0001)
    except Exception:
        pass


def pause_if_needed(enabled: bool) -> None:
    """Keep the console open after drag-and-drop so the user can read the result."""
    if not enabled:
        return
    if sys.platform == "win32" and not owns_console() and not os.environ.get("MCYT_PAUSE"):
        return
    if sys.platform != "win32" and not os.environ.get("MCYT_PAUSE"):
        return
    try:
        input("发生错误，按 Enter 键退出…")
    except EOFError:
        pass
