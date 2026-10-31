"""One-time process-wide DPI awareness setup.

Win32 coordinate APIs are DPI-virtualized for an unaware process: on a
display scaled above 100%, ``GetClientRect`` / ``ClientToScreen`` return
logical pixels while Windows Graphics Capture frames are always physical
pixels.  Cropping a physical frame with logical client coordinates then
fails with "Client crop ... exceeds WGC frame".

Task scripts import :mod:`script_core` before creating any window, so the
package switches the process to per-monitor DPI awareness (v2 when
available).  Every Win32, mouse, and capture coordinate is then expressed
in physical pixels and agrees with the WGC frame.
"""

from __future__ import annotations

import ctypes
import os

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)
_PROCESS_PER_MONITOR_DPI_AWARE = 2

_state: str | None = None


def _win_dll(name: str):
    return ctypes.WinDLL(name, use_last_error=True)  # type: ignore[attr-defined]


def _query_awareness() -> str:
    try:
        shcore = _win_dll("shcore")
        query = shcore.GetProcessDpiAwareness
        query.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        query.restype = ctypes.c_long
        value = ctypes.c_int()
        if query(None, ctypes.byref(value)) == 0:
            return {
                0: "unaware",
                1: "system",
                2: "per-monitor",
            }.get(value.value, f"unknown ({value.value})")
    except (AttributeError, OSError):
        pass
    return "unknown"


def ensure_process_dpi_awareness() -> str:
    """Make this process DPI aware once and return the resulting mode.

    ``SetProcessDpiAwarenessContext`` only succeeds before the process has
    created any window.  Callers that embed this package in an existing GUI
    should invoke it as early as possible; if the process is already aware,
    or awareness cannot be changed, the current mode is reported instead.
    """
    global _state
    if _state is not None:
        return _state
    if os.name != "nt":
        _state = "not-windows"
        return _state

    # Per-monitor v2 (Windows 10 1703+): physical pixels on every monitor,
    # including mixed-DPI setups.
    try:
        user32 = _win_dll("user32")
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = ctypes.c_bool
        if set_context(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
            _state = "per-monitor-v2"
            return _state
    except (AttributeError, OSError):
        pass

    # Per-monitor awareness (Windows 8.1+).
    try:
        shcore = _win_dll("shcore")
        set_awareness = shcore.SetProcessDpiAwareness
        set_awareness.argtypes = [ctypes.c_int]
        set_awareness.restype = ctypes.c_long
        if set_awareness(_PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            _state = "per-monitor"
            return _state
    except (AttributeError, OSError):
        pass

    # System DPI awareness (Vista+): still physical pixels, but only for the
    # system DPI rather than per monitor.  SetProcessDPIAware can report
    # success without changing a process that was already configured, so the
    # real state is queried below rather than trusted here.
    try:
        user32 = _win_dll("user32")
        set_aware = user32.SetProcessDPIAware
        set_aware.argtypes = []
        set_aware.restype = ctypes.c_bool
        set_aware()
    except (AttributeError, OSError):
        pass

    _state = _query_awareness()
    return _state
