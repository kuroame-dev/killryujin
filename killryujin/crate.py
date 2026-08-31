"""Pause or resume Armoury Crate. A running Crate steals HID flow-control packets."""

from __future__ import annotations

import subprocess
import sys
import time

SERVICES = (
    "ArmouryCrateService",
    "ROG Live Service",
)

PROCESS_NAMES = (
    "ArmouryCrate.exe",
    "ArmouryCrate.UserSessionHelper.exe",
    "ArmouryHtmlDebugServer.exe",
    "ArmourySocketServer.exe",
    "ArmourySwAgent.exe",
    "ROGLiveService.exe",
    "asus_framework.exe",
)


def _creationflags() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, creationflags=_creationflags())


def is_admin() -> bool:
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def is_running() -> bool:
    if sys.platform != "win32":
        return False
    r = _run(["tasklist", "/FI", "IMAGENAME eq ArmouryCrate.exe"])
    if "ArmouryCrate.exe" in (r.stdout or ""):
        return True
    r = _run(["sc", "query", "ArmouryCrateService"])
    return "RUNNING" in (r.stdout or "").upper()


def pause() -> list[str]:
    """Stop AC services and leftover processes. Returns log lines."""
    notes: list[str] = []
    if sys.platform != "win32":
        notes.append("crate pause is Windows-only")
        return notes

    denied = False
    for svc in SERVICES:
        r = _run(["sc", "stop", svc])
        combined = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            notes.append(f"stopped service {svc}")
        elif "1062" in combined:
            notes.append(f"service {svc} already stopped")
        elif "Access is denied" in combined or "FAILED 5" in combined:
            notes.append(f"need Administrator to stop {svc}")
            denied = True
        else:
            notes.append(f"sc stop {svc}: {combined.strip() or r.returncode}")

    time.sleep(0.8)
    for name in PROCESS_NAMES:
        r = _run(["taskkill", "/F", "/IM", name])
        out = (r.stdout or "") + (r.stderr or "")
        if "SUCCESS" in out.upper():
            notes.append(f"killed {name}")
        elif "not found" in out.lower():
            continue
        elif "Access is denied" in out:
            notes.append(f"need Administrator to stop {name}")
            denied = True
        elif r.returncode not in (0, 128):
            notes.append(f"taskkill {name}: {out.strip()}")

    if denied:
        notes.append("relaunch this app as Administrator to fully pause Armoury Crate")
    return notes


def relaunch_as_admin() -> bool:
    """Start an elevated copy of this app. False if UAC was cancelled."""
    if sys.platform != "win32" or is_admin():
        return False
    import ctypes

    if getattr(sys, "frozen", False):
        file = sys.executable
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        file = sys.executable
        from . import APP_SLUG

        params = subprocess.list2cmdline(["-m", APP_SLUG])
    rc = int(
        ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            file,
            params,
            None,
            1,
        )
    )
    return rc > 32


def resume() -> list[str]:
    notes: list[str] = []
    if sys.platform != "win32":
        notes.append("crate resume is Windows-only")
        return notes
    for svc in SERVICES:
        r = _run(["sc", "start", svc])
        combined = (r.stdout or "") + (r.stderr or "")
        if r.returncode == 0:
            notes.append(f"started service {svc}")
        elif "1056" in combined:
            notes.append(f"service {svc} already running")
        elif "Access is denied" in combined or "FAILED 5" in combined:
            notes.append(f"need Administrator to start {svc}")
        else:
            notes.append(f"sc start {svc}: {combined.strip() or r.returncode}")
    return notes
