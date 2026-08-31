"""WinUSB bulk OUT for Ryujin III interface 0 (Windows).

libusb cannot claim the composite parent. The bulk function is a WinUSB
device without a DeviceInterfaceGUID, so we open it by PDO name.

The handle is opened overlapped (WinUsb_Initialize requires that). Writes
must pass an OVERLAPPED struct; a NULL overlapped pointer yields
ERROR_OPERATION_ABORTED (995) on larger transfers.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Optional

from .protocol import BULK_EP_OUT, FLASH_CHUNK, PID_WHITE, PIDS, VID

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
winusb = ctypes.WinDLL("winusb", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE = ctypes.c_void_p(-1).value
DIGCF_PRESENT = 0x02
DIGCF_ALLCLASSES = 0x04
ERROR_IO_PENDING = 997
ERROR_OPERATION_ABORTED = 995
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
PIPE_TRANSFER_TIMEOUT = 0x03
SHORT_PACKET_TERMINATE = 0x01

DEVPROP_TYPE_STRING = 0x00000012


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("ClassGuid", GUID),
        ("DevInst", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class DEVPROPKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.ULONG)]


class OVERLAPPED(ctypes.Structure):
    # ULONG_PTR + ULONG_PTR + DWORD + DWORD + HANDLE, 8-byte aligned (32 bytes on x64).
    _fields_ = [
        ("Internal", ctypes.c_ulonglong),
        ("InternalHigh", ctypes.c_ulonglong),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", ctypes.c_void_p),
    ]


DEVPKEY_Device_PDOName = DEVPROPKEY(
    GUID(0xA45C254E, 0xDF1C, 0x4EFD, (ctypes.c_ubyte * 8)(0x80, 0x20, 0x67, 0xD1, 0x46, 0xA8, 0x50, 0xE0)),
    16,
)

SetupDiGetClassDevsW = setupapi.SetupDiGetClassDevsW
SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
SetupDiGetClassDevsW.restype = ctypes.c_void_p
SetupDiEnumDeviceInfo = setupapi.SetupDiEnumDeviceInfo
SetupDiEnumDeviceInfo.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(SP_DEVINFO_DATA),
]
SetupDiEnumDeviceInfo.restype = wintypes.BOOL
SetupDiGetDeviceInstanceIdW = setupapi.SetupDiGetDeviceInstanceIdW
SetupDiGetDeviceInstanceIdW.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(SP_DEVINFO_DATA),
    wintypes.LPWSTR,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
SetupDiGetDeviceInstanceIdW.restype = wintypes.BOOL
SetupDiGetDevicePropertyW = setupapi.SetupDiGetDevicePropertyW
SetupDiGetDevicePropertyW.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(SP_DEVINFO_DATA),
    ctypes.POINTER(DEVPROPKEY),
    ctypes.POINTER(wintypes.ULONG),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    wintypes.DWORD,
]
SetupDiGetDevicePropertyW.restype = wintypes.BOOL
SetupDiDestroyDeviceInfoList = setupapi.SetupDiDestroyDeviceInfoList
SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

CreateFileW = kernel32.CreateFileW
CreateFileW.restype = ctypes.c_void_p
CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
]
CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [ctypes.c_void_p]
CloseHandle.restype = wintypes.BOOL
CreateEventW = kernel32.CreateEventW
CreateEventW.restype = ctypes.c_void_p
CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
ResetEvent = kernel32.ResetEvent
ResetEvent.argtypes = [ctypes.c_void_p]
ResetEvent.restype = wintypes.BOOL
WaitForSingleObject = kernel32.WaitForSingleObject
WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
WaitForSingleObject.restype = wintypes.DWORD
CancelIoEx = kernel32.CancelIoEx
CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
CancelIoEx.restype = wintypes.BOOL
GetOverlappedResult = kernel32.GetOverlappedResult
GetOverlappedResult.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(OVERLAPPED),
    ctypes.POINTER(wintypes.DWORD),
    wintypes.BOOL,
]
GetOverlappedResult.restype = wintypes.BOOL

WinUsb_Initialize = winusb.WinUsb_Initialize
WinUsb_Initialize.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
WinUsb_Initialize.restype = wintypes.BOOL
WinUsb_WritePipe = winusb.WinUsb_WritePipe
WinUsb_WritePipe.argtypes = [
    ctypes.c_void_p,
    ctypes.c_ubyte,
    ctypes.c_void_p,
    wintypes.ULONG,
    ctypes.c_void_p,
    ctypes.POINTER(OVERLAPPED),
]
WinUsb_WritePipe.restype = wintypes.BOOL
WinUsb_AbortPipe = winusb.WinUsb_AbortPipe
WinUsb_AbortPipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte]
WinUsb_AbortPipe.restype = wintypes.BOOL
WinUsb_ResetPipe = winusb.WinUsb_ResetPipe
WinUsb_ResetPipe.argtypes = [ctypes.c_void_p, ctypes.c_ubyte]
WinUsb_ResetPipe.restype = wintypes.BOOL
WinUsb_SetPipePolicy = winusb.WinUsb_SetPipePolicy
WinUsb_SetPipePolicy.argtypes = [
    ctypes.c_void_p,
    ctypes.c_ubyte,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
]
WinUsb_SetPipePolicy.restype = wintypes.BOOL
WinUsb_Free = winusb.WinUsb_Free
WinUsb_Free.argtypes = [ctypes.c_void_p]
WinUsb_Free.restype = wintypes.BOOL


def _property_string(devinfo: ctypes.c_void_p, data: SP_DEVINFO_DATA, key: DEVPROPKEY) -> Optional[str]:
    prop_type = wintypes.ULONG()
    needed = wintypes.DWORD()
    SetupDiGetDevicePropertyW(
        devinfo,
        ctypes.byref(data),
        ctypes.byref(key),
        ctypes.byref(prop_type),
        None,
        0,
        ctypes.byref(needed),
        0,
    )
    if needed.value == 0:
        return None
    buf = (ctypes.c_ubyte * needed.value)()
    if not SetupDiGetDevicePropertyW(
        devinfo,
        ctypes.byref(data),
        ctypes.byref(key),
        ctypes.byref(prop_type),
        buf,
        needed,
        None,
        0,
    ):
        return None
    return ctypes.wstring_at(ctypes.addressof(buf))


def find_pdo_name(vid: int = VID, pid: int = PID_WHITE) -> str:
    needle = f"USB\\VID_{vid:04X}&PID_{pid:04X}&MI_00"
    h = SetupDiGetClassDevsW(None, "USB", None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
    if not h or h == INVALID_HANDLE:
        raise OSError(ctypes.get_last_error(), "SetupDiGetClassDevsW failed")
    try:
        idx = 0
        while True:
            info = SP_DEVINFO_DATA()
            info.cbSize = ctypes.sizeof(info)
            if not SetupDiEnumDeviceInfo(ctypes.c_void_p(h), idx, ctypes.byref(info)):
                break
            inst_buf = (wintypes.WCHAR * 512)()
            if SetupDiGetDeviceInstanceIdW(ctypes.c_void_p(h), ctypes.byref(info), inst_buf, 512, None):
                inst = ctypes.wstring_at(inst_buf)
                if inst.upper().startswith(needle.upper()):
                    pdo = _property_string(ctypes.c_void_p(h), info, DEVPKEY_Device_PDOName)
                    if not pdo:
                        raise OSError("WinUSB interface has no PDO name")
                    return pdo
            idx += 1
    finally:
        SetupDiDestroyDeviceInfoList(ctypes.c_void_p(h))
    known = ", ".join(f"0x{p:04X}" for p in PIDS)
    raise OSError(
        f"WinUSB MI_00 not found for VID 0x{vid:04X} PID 0x{pid:04X} "
        f"(known Ryujin III PIDs: {known})"
    )


class WinUsbBulk:
    def __init__(self, vid: int, pid: int):
        self._file = None
        self._iface = ctypes.c_void_p()
        self._event = None
        pdo = find_pdo_name(vid, pid)
        path = r"\\?\GLOBALROOT" + pdo
        handle = CreateFileW(
            path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            None,
        )
        if not handle or handle == INVALID_HANDLE:
            raise OSError(ctypes.get_last_error(), f"CreateFileW({path}) failed")
        self._file = handle
        try:
            if not WinUsb_Initialize(handle, ctypes.byref(self._iface)):
                raise OSError(ctypes.get_last_error(), "WinUsb_Initialize failed")
            self._event = CreateEventW(None, True, False, None)
            if not self._event:
                raise OSError(ctypes.get_last_error(), "CreateEventW failed")
            no_zlp = ctypes.c_ubyte(0)
            WinUsb_SetPipePolicy(
                self._iface,
                BULK_EP_OUT,
                SHORT_PACKET_TERMINATE,
                ctypes.sizeof(no_zlp),
                ctypes.byref(no_zlp),
            )
        except Exception:
            self.close()
            raise

    def _set_timeout(self, pipe_id: int, timeout_ms: int) -> None:
        value = wintypes.ULONG(max(100, timeout_ms))
        WinUsb_SetPipePolicy(
            self._iface,
            pipe_id,
            PIPE_TRANSFER_TIMEOUT,
            ctypes.sizeof(value),
            ctypes.byref(value),
        )

    def reset_pipe(self, pipe_id: int) -> None:
        WinUsb_AbortPipe(self._iface, pipe_id)
        WinUsb_ResetPipe(self._iface, pipe_id)

    def _write_once(self, pipe_id: int, data: bytes, timeout_ms: int) -> int:
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        ov = OVERLAPPED()
        ov.hEvent = self._event
        ResetEvent(self._event)
        # LengthTransferred is unused for overlapped I/O; pass NULL (MSDN).
        ok = WinUsb_WritePipe(
            self._iface,
            pipe_id,
            buf,
            len(data),
            None,
            ctypes.byref(ov),
        )
        if ok:
            return len(data)
        err = ctypes.get_last_error()
        if err != ERROR_IO_PENDING:
            raise OSError(err, "WinUsb_WritePipe failed")
        wait = WaitForSingleObject(self._event, max(100, timeout_ms))
        if wait != WAIT_OBJECT_0:
            CancelIoEx(ctypes.c_void_p(self._file), ctypes.byref(ov))
            WaitForSingleObject(self._event, 2000)
            raise OSError(ERROR_OPERATION_ABORTED, "WinUsb_WritePipe timed out")
        # GLOBALROOT PDO handles make kernel32/WinUsb GetOverlappedResult
        # return ERROR_NO_SUCH_DEVICE (433) even when the URB completed.
        # The event + NTSTATUS in OVERLAPPED.Internal is the completion signal.
        status = int(ov.Internal) & 0xFFFFFFFF
        if status & 0x80000000:
            raise OSError(status & 0xFFFF, f"USB write NTSTATUS 0x{status:08X}")
        n = int(ov.InternalHigh)
        return n if n else len(data)

    def _write_with_retries(
        self, pipe_id: int, data: bytes, timeout_ms: int, retries: int
    ) -> int:
        last_err: Optional[OSError] = None
        attempts = max(1, retries)
        for attempt in range(attempts):
            try:
                return self._write_once(pipe_id, data, timeout_ms)
            except OSError as exc:
                last_err = exc
                if attempt + 1 < attempts:
                    self.reset_pipe(pipe_id)
                    time.sleep(0.03)
        assert last_err is not None
        raise last_err

    def write(self, pipe_id: int, data: bytes, timeout_ms: int = 5000, retries: int = 1) -> int:
        """Write bulk OUT. Large framebuffer frames are split into 4 KiB URBs.

        Save-to-flash already uses 4 KiB and completes synchronously. A single
        230400-byte WritePipe goes async and fails with ERROR_NO_SUCH_DEVICE (433)
        on this WinUSB stack. HID flush after the full frame is what displays it.
        """
        if not data:
            return 0
        self._set_timeout(pipe_id, timeout_ms)
        if len(data) <= FLASH_CHUNK:
            return self._write_with_retries(pipe_id, data, timeout_ms, retries)
        sent = 0
        offset = 0
        while offset < len(data):
            piece = data[offset : offset + FLASH_CHUNK]
            sent += self._write_with_retries(pipe_id, piece, timeout_ms, retries)
            offset += len(piece)
        return sent

    def close(self) -> None:
        if self._iface:
            try:
                WinUsb_Free(self._iface)
            except Exception:
                pass
            self._iface = ctypes.c_void_p()
        if self._event:
            try:
                CloseHandle(ctypes.c_void_p(self._event))
            except Exception:
                pass
            self._event = None
        if self._file:
            CloseHandle(ctypes.c_void_p(self._file))
            self._file = None

    def __enter__(self) -> "WinUsbBulk":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
