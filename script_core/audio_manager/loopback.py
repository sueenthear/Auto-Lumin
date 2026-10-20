"""Capture only the bound process tree via Windows WASAPI process loopback.

The COM objects are created, used and released on the same worker thread.
No system-mix fallback is provided: doing so would capture other applications.
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import uuid
from ctypes import wintypes

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2

# GUIDs and vtable indices from mmdeviceapi.h and audioclient.h.
AUDIO_CLIENT_IID = uuid.UUID("1cb9ad4c-dbfa-4c32-b178-c2f568a703b2")
CAPTURE_CLIENT_IID = uuid.UUID("c8adbd64-e71e-48a0-a4de-185c395cd317")
COM_IUNKNOWN_IID = uuid.UUID("00000000-0000-0000-c000-000000000046")
COM_AGILE_IID = uuid.UUID("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90")
COM_COMPLETION_IID = uuid.UUID("41d949ab-9862-444a-80f6-c261334da5eb")
DEVICE_PATH = "VAD\\Process_Loopback"
EVENT_CALLBACK = 0x00040000
AUTO_CONVERT_PCM = 0x80000000
LOOPBACK = 0x00020000
SILENT = 0x00000002
VT_BLOB = 65


class GUID(ctypes.Structure):
    _fields_ = [("data1", wintypes.DWORD), ("data2", wintypes.WORD), ("data3", wintypes.WORD), ("data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> GUID:
        return cls.from_buffer_copy(value.bytes_le)


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.ULONG), ("data", ctypes.c_void_p)]


class _PropVariantData(ctypes.Union):
    _fields_ = [("blob", _Blob), ("padding", ctypes.c_byte * 16)]


class _PropVariant(ctypes.Structure):
    _fields_ = [("vt", wintypes.USHORT), ("w1", wintypes.USHORT), ("w2", wintypes.USHORT), ("w3", wintypes.USHORT), ("value", _PropVariantData)]


class _ActivationParams(ctypes.Structure):
    _fields_ = [("activation_type", wintypes.DWORD), ("target_pid", wintypes.DWORD), ("mode", wintypes.DWORD)]


class _WaveFormat(ctypes.Structure):
    _fields_ = [("format", wintypes.WORD), ("channels", wintypes.WORD), ("rate", wintypes.DWORD), ("bytes_per_second", wintypes.DWORD), ("block_align", wintypes.WORD), ("bits", wintypes.WORD), ("extra", wintypes.WORD)]


def _method(pointer: ctypes.c_void_p, slot: int, restype, *argtypes):
    address = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents[slot]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(address)


def _ok(value: int, operation: str) -> None:
    if value < 0:
        raise OSError(f"{operation} failed: HRESULT 0x{value & 0xffffffff:08X}")


def _release(pointer: ctypes.c_void_p) -> None:
    if pointer.value:
        _method(pointer, 2, wintypes.ULONG)(pointer)
        pointer.value = None


_Query = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)) if os.name == "nt" else None
_Ref = ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p) if os.name == "nt" else None
_Completed = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p) if os.name == "nt" else None


class _Handler:
    """Pinned COM completion callback; only called by Windows while activation is pending."""

    def __init__(self) -> None:
        self.ready = threading.Event()
        self.result = -1
        self.audio_client = ctypes.c_void_p()
        self.error: BaseException | None = None
        self._count = 1
        self._abandoned = False
        self._operation = ctypes.c_void_p()
        self._guard = threading.Lock()

        def query(_self, iid, destination):
            if bytes(iid.contents) in (bytes(GUID.from_uuid(key)) for key in (COM_IUNKNOWN_IID, COM_AGILE_IID, COM_COMPLETION_IID)):
                destination[0] = ctypes.cast(ctypes.pointer(self.interface), ctypes.c_void_p)
                with self._guard:
                    self._count += 1
                return 0
            destination[0] = None
            return -2147467262  # E_NOINTERFACE

        def add_ref(_self):
            with self._guard:
                self._count += 1
                return self._count

        def release(_self):
            with self._guard:
                self._count -= 1
                return self._count

        def completed(_self, operation):
            client = ctypes.c_void_p()
            try:
                result = ctypes.c_long(-1)
                hr = _method(ctypes.c_void_p(operation), 3, ctypes.c_long, ctypes.POINTER(ctypes.c_long), ctypes.POINTER(ctypes.c_void_p))(
                    operation, ctypes.byref(result), ctypes.byref(client)
                )
                self.result = hr if hr < 0 else result.value
            except BaseException as exc:
                # Exceptions must never escape a ctypes COM callback.
                self.error = exc
            finally:
                with self._guard:
                    abandoned = self._abandoned
                    if not abandoned:
                        self.audio_client = client
                        operation_to_release = ctypes.c_void_p()
                    else:
                        operation_to_release = self._operation
                        self._operation = ctypes.c_void_p()
                if abandoned:
                    _release(client)
                    _release(operation_to_release)
                # Keep the callback trampoline pinned until process exit: ready can be
                # signalled before the native caller has returned from this function.
                self.ready.set()
            return 0

        self._query = _Query(query)
        self._add = _Ref(add_ref)
        self._release = _Ref(release)
        self._complete = _Completed(completed)
        self.vtable = (ctypes.c_void_p * 4)(*map(lambda callback: ctypes.cast(callback, ctypes.c_void_p).value, (self._query, self._add, self._release, self._complete)))
        self.interface = ctypes.cast(self.vtable, ctypes.POINTER(ctypes.c_void_p))


# Python cannot observe when the native callback trampoline has returned, so
# handlers stay pinned for the lifetime of the process. Freeing one while COM
# still holds its vtable would crash the interpreter.
_PENDING: set[_Handler] = set()
_PENDING_LOCK = threading.Lock()


def check_supported() -> None:
    if os.name != "nt" or sys.getwindowsversion().build < 20348:
        raise RuntimeError("进程独立音频监听仅支持 Windows Build 20348 或更新版本")


class ProcessLoopback:
    """PCM16 mono 16 kHz audio from one process and its children.

    Must be opened and closed on the *same* thread. read() waits up to 100 ms.
    """

    def __init__(self, pid: int):
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError("pid must be a positive integer")
        self.pid = pid
        self._client = ctypes.c_void_p()
        self._capture = ctypes.c_void_p()
        self._event = None
        self._kernel32 = None
        self._started = False
        self._com_initialized = False

    def __enter__(self) -> ProcessLoopback:
        check_supported()
        ole32 = ctypes.WinDLL("ole32")
        ole32.CoInitializeEx.argtypes = (ctypes.c_void_p, wintypes.DWORD)
        ole32.CoInitializeEx.restype = ctypes.c_long
        result = ole32.CoInitializeEx(None, 0)
        if result == -2147417850:
            # RPC_E_CHANGED_MODE: the calling thread already lives in an STA
            # (for example a GUI main thread). COM is usable; do not uninitialize it.
            self._com_initialized = False
        else:
            _ok(result, "CoInitializeEx")
            self._com_initialized = True
        try:
            self._open()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _open(self) -> None:
        device = ctypes.WinDLL("mmdevapi")
        device.ActivateAudioInterfaceAsync.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(GUID), ctypes.POINTER(_PropVariant), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))
        device.ActivateAudioInterfaceAsync.restype = ctypes.c_long
        options = _ActivationParams(1, self.pid, 0)  # PROCESS_LOOPBACK, INCLUDE_TARGET_PROCESS_TREE
        variant = _PropVariant()
        variant.vt = VT_BLOB
        variant.value.blob = _Blob(ctypes.sizeof(options), ctypes.cast(ctypes.pointer(options), ctypes.c_void_p))
        handler = _Handler()
        # Keep activation parameters alive even if the activation finishes after our timeout.
        handler._options = options
        handler._variant = variant
        operation = ctypes.c_void_p()
        activated = False
        with _PENDING_LOCK:
            _PENDING.add(handler)
        try:
            _ok(device.ActivateAudioInterfaceAsync(DEVICE_PATH, ctypes.byref(GUID.from_uuid(AUDIO_CLIENT_IID)), ctypes.byref(variant), ctypes.cast(ctypes.pointer(handler.interface), ctypes.c_void_p), ctypes.byref(operation)), "ActivateAudioInterfaceAsync")
            activated = True
            if not handler.ready.wait(10):
                raise TimeoutError("等待进程音频接口激活超时")
            if handler.error is not None:
                raise RuntimeError("进程音频接口激活回调失败") from handler.error
            _ok(handler.result, "process loopback activation")
            with handler._guard:
                self._client = handler.audio_client
                handler.audio_client = ctypes.c_void_p()
        finally:
            with handler._guard:
                pending = activated and bool(operation.value) and not handler.ready.is_set()
                if pending:
                    handler._operation = operation
                    handler._abandoned = True
                    operation = ctypes.c_void_p()
                else:
                    _release(handler.audio_client)
            _release(operation)
            # Completion signals ready before the native callback returns. Keep
            # its Python vtable pinned; COM may still execute on that thread.

        format_ = _WaveFormat(1, CHANNELS, SAMPLE_RATE, SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH, CHANNELS * SAMPLE_WIDTH, SAMPLE_WIDTH * 8, 0)
        _ok(_method(self._client, 3, ctypes.c_long, wintypes.DWORD, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong, ctypes.POINTER(_WaveFormat), ctypes.c_void_p)(self._client, 0, LOOPBACK | EVENT_CALLBACK | AUTO_CONVERT_PCM, 0, 0, ctypes.byref(format_), None), "IAudioClient.Initialize")
        _ok(_method(self._client, 14, ctypes.c_long, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(self._client, ctypes.byref(GUID.from_uuid(CAPTURE_CLIENT_IID)), ctypes.byref(self._capture)), "IAudioClient.GetService")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
        kernel.CreateEventW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self._kernel32 = kernel
        self._event = kernel.CreateEventW(None, False, False, None)
        if not self._event:
            raise ctypes.WinError(ctypes.get_last_error())
        _ok(_method(self._client, 13, ctypes.c_long, wintypes.HANDLE)(self._client, self._event), "IAudioClient.SetEventHandle")
        _ok(_method(self._client, 10, ctypes.c_long)(self._client), "IAudioClient.Start")
        self._started = True

    def read(self) -> bytes:
        """Return ready samples; may be empty when silent or timed out."""
        result = self._kernel32.WaitForSingleObject(self._event, 100)
        if result == 258:  # WAIT_TIMEOUT
            return b""
        if result != 0:
            raise OSError(f"WaitForSingleObject failed: {result}")
        blocks = []
        while True:
            available = wintypes.UINT()
            _ok(_method(self._capture, 5, ctypes.c_long, ctypes.POINTER(wintypes.UINT))(self._capture, ctypes.byref(available)), "IAudioCaptureClient.GetNextPacketSize")
            if not available.value:
                break
            data = ctypes.c_void_p()
            frames = wintypes.UINT()
            flags = wintypes.DWORD()
            _ok(_method(self._capture, 3, ctypes.c_long, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p)(self._capture, ctypes.byref(data), ctypes.byref(frames), ctypes.byref(flags), None, None), "IAudioCaptureClient.GetBuffer")
            try:
                blocks.append(bytes(frames.value * SAMPLE_WIDTH) if flags.value & SILENT else ctypes.string_at(data, frames.value * SAMPLE_WIDTH))
            finally:
                _ok(_method(self._capture, 4, ctypes.c_long, wintypes.UINT)(self._capture, frames), "IAudioCaptureClient.ReleaseBuffer")
        return b"".join(blocks)

    def __exit__(self, _type, _value, _traceback):
        if self._started:
            _method(self._client, 11, ctypes.c_long)(self._client)  # Stop
            self._started = False
        _release(self._capture)
        _release(self._client)
        if self._event:
            self._kernel32.CloseHandle(self._event)
            self._event = None
        if self._com_initialized:
            ctypes.WinDLL("ole32").CoUninitialize()
            self._com_initialized = False
