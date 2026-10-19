from __future__ import annotations

from datetime import datetime
import threading


class DoLogger:
    """Thread-safe optional operation logger for the public do facade."""

    def __init__(self) -> None:
        self._enabled = False
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def output(self, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise ValueError("log output must be True or False")
        with self._lock:
            if enabled:
                self._enabled = True
                self._write_locked("do.log.output", "True")
            else:
                self._write_locked("do.log.output", "False")
                self._enabled = False

    def write(self, source: str, message: str) -> None:
        with self._lock:
            if self._enabled:
                self._write_locked(source, message)

    @staticmethod
    def _write_locked(source: str, message: str) -> None:
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        print(f"[{timestamp}][来源：{source}]<{message}>", flush=True)
