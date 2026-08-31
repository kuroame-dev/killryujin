"""HID + WinUSB access for Ryujin III LCD."""

from __future__ import annotations

import io
import math
import time
from pathlib import Path
from typing import Any, Callable, Optional, TypedDict

import hid
from PIL import Image

from . import protocol as P
from .media import gif_frames, gif_global_palette, rgb_to_bgr, view_rgb
from .winusb_bulk import WinUsbBulk


class RyujinError(RuntimeError):
    pass


class FlashSlotStuck(RyujinError):
    """Firmware reported ee13 1001. Flash state machine is wedged."""


class CoolerStatus(TypedDict):
    product: str
    pid: str
    firmware: str
    liquid_c: float
    pump_rpm: int
    pump_fan_rpm: int
    pump_duty: int
    pump_fan_duty: int


class PersistResult(TypedDict):
    bytes: int
    chunks: int
    slot: int
    animation: bool


def enumerate_coolers() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for d in hid.enumerate(P.VID):
        pid = d.get("product_id")
        if pid in P.PIDS and d.get("interface_number", 1) == 1:
            found.append(d)
    return found


class Ryujin:
    def __init__(self, hid_path: Optional[bytes] = None):
        self._hid: Optional[hid.device] = None
        self._usb: Optional[WinUsbBulk] = None
        self._hid_path = hid_path
        self.product = "ROG Ryujin III"
        self.pid = P.PID_WHITE

    def open(self) -> "Ryujin":
        matches = enumerate_coolers()
        if not matches:
            raise RyujinError(
                "No Ryujin III HID interface found (VID 0x0B05, PIDs "
                + ", ".join(f"0x{p:04X}" for p in P.PIDS)
                + "). Is the AIO USB header plugged in?"
            )
        info = matches[0]
        if self._hid_path:
            info = next((m for m in matches if m["path"] == self._hid_path), info)
        self.pid = info["product_id"]
        self.product = P.PIDS.get(self.pid) or info.get("product_string") or self.product
        self._hid = hid.device()
        try:
            self._hid.open_path(info["path"])
        except OSError as exc:
            raise RyujinError(
                f"HID open failed ({exc}). Pause Armoury Crate: "
                "killryujin crate pause"
            ) from exc
        self._hid.set_nonblocking(0)
        try:
            self._usb = WinUsbBulk(P.VID, self.pid)
        except OSError as exc:
            raise RyujinError(f"WinUSB bulk open failed: {exc}") from exc
        return self

    def close(self) -> None:
        if self._hid is not None:
            try:
                self._hid.close()
            except Exception:
                pass
            self._hid = None
        if self._usb is not None:
            try:
                self._usb.close()
            except Exception:
                pass
            self._usb = None

    def __enter__(self) -> "Ryujin":
        return self.open()

    def __exit__(self, *_exc) -> None:
        self.close()

    def _require_hid(self) -> hid.device:
        if self._hid is None:
            raise RyujinError("device not open")
        return self._hid

    def hid_write(self, data: list[int] | bytes) -> None:
        payload = P.pad_report(data)
        n = self._require_hid().write(payload)
        if n < 0:
            raise RyujinError(f"HID write failed: {self._require_hid().error()}")

    def hid_read(self, timeout_ms: int = 400) -> Optional[bytes]:
        raw = self._require_hid().read(P.REPORT_LEN, timeout_ms=timeout_ms)
        if not raw:
            return None
        return bytes(raw)

    def clear_reports(self) -> None:
        hid_dev = self._require_hid()
        hid_dev.set_nonblocking(1)
        try:
            while hid_dev.read(P.REPORT_LEN):
                continue
        finally:
            hid_dev.set_nonblocking(0)

    def request(self, cmd: int, expect: Optional[int] = None, timeout_ms: int = 800) -> bytes:
        self.clear_reports()
        self.hid_write([P.PREFIX, cmd])
        deadline = time.monotonic() + timeout_ms / 1000
        last = None
        while time.monotonic() < deadline:
            report = self.hid_read(timeout_ms=200)
            if not report:
                continue
            last = report
            if report[0] == P.PREFIX and (expect is None or report[1] == expect):
                return report
        raise RyujinError(
            f"no HID response for cmd 0x{cmd:02X} "
            f"(last={last[:8].hex() if last else 'none'})"
        )

    def ee_read(self, timeout_s: float = 0.5) -> Optional[bytes]:
        return self.hid_read(timeout_ms=int(timeout_s * 1000))

    def ee_wait(self, pred: Callable[[bytes], bool], timeout_s: float = 4.0) -> Optional[bytes]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            report = self.ee_read(min(0.5, timeout_s))
            if report and pred(report):
                return report
        return None

    def bulk_write(self, data: bytes, timeout_ms: int = 5000, retries: int = 1) -> int:
        if self._usb is None:
            raise RyujinError("USB bulk not open")
        return int(self._usb.write(P.BULK_EP_OUT, data, timeout_ms=timeout_ms, retries=retries))

    def firmware(self) -> str:
        msg = self.request(P.REQ_FIRMWARE, P.RSP_FIRMWARE)
        return msg[3:18].split(b"\x00", 1)[0].decode("ascii", errors="replace")

    def status(self) -> CoolerStatus:
        st = self.request(P.REQ_STATUS, P.RSP_STATUS)
        duty = self.request(P.REQ_DUTY, P.RSP_DUTY)
        liquid = st[P.TEMP_OFFSET] + st[P.TEMP_OFFSET + 1] / 10
        return {
            "product": self.product,
            "pid": f"0x{self.pid:04X}",
            "firmware": self.firmware(),
            "liquid_c": liquid,
            "pump_rpm": P.u16le(st, P.PUMP_SPEED_OFFSET),
            "pump_fan_rpm": P.u16le(st, P.PUMP_FAN_SPEED_OFFSET),
            "pump_duty": duty[4],
            "pump_fan_duty": duty[5],
        }

    def set_pump(self, pump_duty: int, fan_duty: Optional[int] = None) -> None:
        pump_duty = max(0, min(100, pump_duty))
        if fan_duty is None:
            fan_duty = self.status()["pump_fan_duty"]
        fan_duty = max(0, min(100, fan_duty))
        self.hid_write([P.PREFIX, P.CMD_SET_COOLER_SPEED, P.DUTY_CHANNEL, pump_duty, fan_duty])

    def lcd_mode(self, mode: int, *extra: int) -> None:
        self.hid_write([P.PREFIX, P.CMD_SWITCH_DISPLAY_MODE, mode, *extra])

    def lcd_wake(self) -> None:
        self.hid_write([P.PREFIX, P.CMD_DISPLAY_OPTION, 0x10])

    def lcd_standby(self) -> None:
        self.hid_write([P.PREFIX, P.CMD_DISPLAY_OPTION, 0x20])

    def lcd_off(self) -> None:
        self.lcd_mode(P.MODE_OFF)

    def lcd_liquid(self) -> None:
        self.lcd_mode(P.MODE_ANIMATION)

    def lcd_play_saved(self, slot: int = 3, animation: bool = True) -> None:
        """Switch the panel to a GIF/image already stored in flash."""
        self.lcd_wake()
        time.sleep(0.05)
        if animation:
            self.hid_write(
                [P.PREFIX, P.CMD_SELECT_SLOT, 0x00, 0x01, P.MODE_SINGLE_ANIM, 0x01, slot, 0x05]
            )
            time.sleep(0.05)
            self.lcd_mode(P.MODE_SINGLE_ANIM, 0x01, slot)
        else:
            self.lcd_mode(P.MODE_SLIDESHOW)

    def lcd_brightness(self, percent: int) -> None:
        percent = max(0, min(100, percent))
        self.hid_write([P.PREFIX, P.CMD_DISPLAY_OPTION, 0x01, 0x00, 0x00, 0x00, 0x00, percent])

    def lcd_orientation(self, orient: int) -> None:
        orient = max(0, min(3, orient))
        self.hid_write([P.PREFIX, P.CMD_DISPLAY_OPTION, 0x01, 0x00, 0x00, orient])

    def lcd_clock(self, fmt_24h: bool = True) -> None:
        hr_fmt = 0x00 if fmt_24h else 0x01
        self.hid_write([P.PREFIX, P.CMD_SELECT_SLOT, 0x00, 0x01, 0x08, 0x00, hr_fmt, 0x05])
        time.sleep(0.05)
        t = time.localtime()

        def bcd(val: int) -> int:
            return ((val // 10) << 4) | (val % 10)

        hour = t.tm_hour
        pm = 0x00
        if not fmt_24h:
            pm = 0x01 if hour >= 12 else 0x00
            hour = hour % 12 or 12
        self.hid_write(
            [
                P.PREFIX,
                P.CMD_SET_CLOCK,
                0x00,
                0x00,
                0x08,
                0x00,
                hr_fmt,
                bcd(hour),
                bcd(t.tm_min),
                bcd(t.tm_sec),
                pm,
                0x01,
            ]
        )
        time.sleep(0.05)
        self.lcd_mode(P.MODE_CLOCK, 0x00, hr_fmt)

    def lcd_monitor(self) -> None:
        st = self.status()
        self.hid_write(
            [
                P.PREFIX,
                P.CMD_HW_MONITOR_LAYOUT,
                0x02,
                0x02,
                0x02,
                0x00,
                0,
                0,
                0,
                0xFF,
                255,
                255,
                255,
                0xFF,
                255,
                255,
                255,
                0xFF,
                255,
                255,
                255,
                0xFF,
                255,
                255,
                255,
                0xFF,
            ]
        )
        self.lcd_mode(P.MODE_HW_MONITOR)
        time.sleep(0.2)
        lines = [
            ("Liquid", f"{st['liquid_c']:.1f}C"),
            ("Pump", f"{st['pump_rpm']}RPM"),
            ("Fan", f"{st['pump_fan_rpm']}RPM"),
        ]
        for i, (label, value) in enumerate(lines):
            lb = list(label.encode("utf-8")[:18]) + [0] * 18
            vb = list(value.encode("utf-8")[:12]) + [0] * 12
            self.hid_write([P.PREFIX, P.CMD_HW_MONITOR_STRING, i, *lb[:18], *vb[:12]])

    def _flush_framebuffer(self) -> None:
        self.hid_write([P.PREFIX, P.CMD_FLUSH_FRAMEBUFFER, 0x03, 0x00, 0x84, 0x03])

    def lcd_image(self, path: str | Path) -> None:
        """Volatile 320x240 BGR framebuffer write. Does not survive reboot."""
        with Image.open(path) as img:
            frame = rgb_to_bgr(img)
        self.lcd_wake()
        time.sleep(0.05)
        self.lcd_brightness(100)
        time.sleep(0.05)
        self.lcd_mode(P.MODE_FRAMEBUFFER)
        time.sleep(0.1)
        self.bulk_write(frame, timeout_ms=8000)
        self._flush_framebuffer()

    def lcd_gif_stream(
        self,
        path: str | Path,
        loops: int = 0,
        stop: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Decode a GIF and stream frames to the live framebuffer until interrupted.

        Does not touch onboard flash, so the slot state machine stays clear.
        The process must keep running or the panel falls back to the built-in anim.
        """
        try:
            rgb_frames, durations = gif_frames(path)
        except ValueError as exc:
            raise RyujinError(str(exc)) from exc
        frames = [(rgb_to_bgr(f), d / 1000.0) for f, d in zip(rgb_frames, durations)]

        self.lcd_wake()
        time.sleep(0.05)
        self.lcd_brightness(100)
        time.sleep(0.05)
        self.lcd_mode(P.MODE_FRAMEBUFFER)
        time.sleep(0.1)

        # Firmware drops back to the built-in ROG clip if the next 230400-byte
        # frame is late or the bulk pipe aborts. Cap rate so huge source GIFs
        # (30ms, 600px) don't hammer WinUSB, and re-assert framebuffer mode.
        min_delay = 0.04
        n = 0
        frame_i = 0
        while loops == 0 or n < loops:
            for pixels, delay in frames:
                if stop and stop():
                    return
                if frame_i % 12 == 0:
                    self.lcd_mode(P.MODE_FRAMEBUFFER)
                t0 = time.monotonic()
                try:
                    self.bulk_write(pixels, timeout_ms=5000, retries=1)
                except OSError as exc:
                    raise RyujinError(f"USB frame write failed: {exc}") from exc
                self._flush_framebuffer()
                frame_i += 1
                elapsed = time.monotonic() - t0
                wait = max(delay, min_delay) - elapsed
                if wait > 0:
                    time.sleep(wait)
            n += 1

    def _encode_flash(
        self,
        path: str | Path,
        animation: bool,
        max_frames: Optional[int],
        *,
        zoom: float = 1.0,
        pan_x: float = 0.0,
        pan_y: float = 0.0,
    ) -> tuple[bytes, list[int]]:
        buf = io.BytesIO()
        if animation:
            try:
                frames, durations = gif_frames(path, zoom=zoom, pan_x=pan_x, pan_y=pan_y)
            except ValueError as exc:
                raise RyujinError(str(exc)) from exc
            # SPI flash is slow and the size field is 24-bit (~16 MiB). Cap
            # frame count so the write can finish; keep 256-color GIF so the
            # panel does not look washed-out vs the source.
            cap = max_frames if max_frames is not None else 80
            if len(frames) > cap:
                step = math.ceil(len(frames) / cap)
                frames = frames[::step]
                durations = [min(400, d * step) for d in durations[::step]]
            palette = gif_global_palette(frames, 256)
            qframes = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
            if len(qframes) == 1:
                qframes[0].save(buf, "GIF")
            else:
                qframes[0].save(
                    buf,
                    "GIF",
                    save_all=True,
                    append_images=qframes[1:],
                    loop=0,
                    duration=durations,
                    disposal=2,
                    optimize=False,
                )
            return buf.getvalue(), [0x01, 0x02, 0x03]
        with Image.open(path) as src:
            view_rgb(src, zoom=zoom, pan_x=pan_x, pan_y=pan_y).save(buf, "JPEG", quality=90)
        return buf.getvalue(), [0x01, 0x01]

    def _unstick_flash(self) -> None:
        """One sweep of begin/arm/params/erase cycles. Clears a *light* wedge.

        A deep ee13 1001 (USB 5V never dropped) still needs a PSU power-cycle.
        """
        self.lcd_mode(P.MODE_ANIMATION)
        time.sleep(0.3)
        for page in range(1, 11):
            self.clear_reports()
            self.hid_write([P.PREFIX, P.CMD_UPLOAD_BEGIN, 0x01, 0x01])
            self.ee_read(0.3)
            self.hid_write([P.PREFIX, P.CMD_UPLOAD_ARM, 0x00])
            self.ee_read(0.3)
            self.hid_write([P.PREFIX, P.CMD_UPLOAD_PARAMS, 0x01, 0x02, page])
            self.ee_read(0.3)
            self.clear_reports()
            self.hid_write([P.PREFIX, P.CMD_UPLOAD_PREPARE, 0x01])
            self.ee_wait(
                lambda r: len(r) >= 4 and r[0] == P.EE_PREFIX and r[1] == P.EE_SLOT,
                2.0,
            )
        self.clear_reports()

    def persist(
        self,
        path: str | Path,
        animation: bool,
        slot: Optional[int] = None,
        max_frames: Optional[int] = None,
        progress: Optional[Callable[[int, int], None]] = None,
        zoom: float = 1.0,
        pan_x: float = 0.0,
        pan_y: float = 0.0,
    ) -> PersistResult:
        """Upload JPEG/GIF89a into onboard SPI flash. Survives reboot.

        Must have exclusive HID: pause Armoury Crate first or the ee handshake
        is stolen and the slot wedges until a full power cycle.
        """
        try:
            return self._attempt_persist(
                path, animation, slot, max_frames, progress, zoom, pan_x, pan_y
            )
        except FlashSlotStuck:
            self._unstick_flash()
            return self._attempt_persist(
                path, animation, slot, max_frames, progress, zoom, pan_x, pan_y
            )
        except RyujinError:
            try:
                self._unstick_flash()
            except Exception:
                pass
            raise

    def _attempt_persist(
        self,
        path: str | Path,
        animation: bool,
        slot: Optional[int],
        max_frames: Optional[int],
        progress: Optional[Callable[[int, int], None]],
        zoom: float = 1.0,
        pan_x: float = 0.0,
        pan_y: float = 0.0,
    ) -> PersistResult:
        if slot is None:
            slot = 3 if animation else 4
        payload, fmt = self._encode_flash(
            path, animation, max_frames, zoom=zoom, pan_x=pan_x, pan_y=pan_y
        )
        size = len(payload)
        padded = payload + b"\x00" * ((P.FLASH_CHUNK - (size % P.FLASH_CHUNK)) % P.FLASH_CHUNK)
        n_chunks = len(padded) // P.FLASH_CHUNK

        def ee_slot(r: bytes, h3: Optional[int] = None) -> bool:
            return (
                len(r) >= 4
                and r[0] == P.EE_PREFIX
                and r[1] == P.EE_SLOT
                and (h3 is None or r[3] == h3)
            )

        def ee_chunk(r: bytes) -> bool:
            return len(r) >= 4 and r[0] == P.EE_PREFIX and r[1] == P.EE_CHUNK

        # Playing the custom slot keeps flash busy (ee13 1001 on the next Save).
        self.lcd_mode(P.MODE_ANIMATION)
        time.sleep(0.4)

        self.clear_reports()
        self.hid_write([P.PREFIX, P.CMD_UPLOAD_BEGIN, 0x01, 0x01])
        self.ee_read(0.6)
        self.hid_write([P.PREFIX, P.CMD_UPLOAD_ARM, 0x00])
        self.ee_wait(lambda r: r[:4] == bytes([P.PREFIX, P.CMD_UPLOAD_BEGIN, 0x00, 0x01]), 2.0)
        self.hid_write([P.PREFIX, P.CMD_UPLOAD_PARAMS, *fmt])
        self.ee_read(0.6)

        self.clear_reports()
        self.hid_write([P.PREFIX, P.CMD_UPLOAD_PREPARE, 0x01])
        erased = self.ee_wait(lambda r: ee_slot(r) and r[2] == 0x00 and r[3] == 0x01, 6.0)
        if erased is None:
            raise FlashSlotStuck(
                "flash slot not ready (erase status timeout / ee13 1001). "
                "Power-cycle the PC (PSU off, hold power 30s) then retry. "
                "Do not keep retrying. Extra retries deepen the wedge."
            )

        size_le = list(size.to_bytes(3, "little"))
        self.hid_write([P.PREFIX, P.CMD_UPLOAD_SIZE, 0x02, *size_le])
        ready = self.ee_wait(lambda r: r[:2] == bytes([P.PREFIX, P.CMD_UPLOAD_SIZE]), 2.0)
        if ready is None or ready[2] != 0x00:
            raise RyujinError(
                "device rejected size declaration "
                f"({ready[:4].hex() if ready else 'timeout'})"
            )

        for i in range(n_chunks):
            chunk = padded[i * P.FLASH_CHUNK : (i + 1) * P.FLASH_CHUNK]
            self.bulk_write(chunk, timeout_ms=8000, retries=1)
            ack = self.ee_wait(ee_chunk, 6.0)
            if ack is None:
                ack = self.ee_wait(ee_chunk, 4.0)
            if ack is None:
                raise RyujinError(
                    f"no ee14 ack for chunk {i + 1}/{n_chunks}. "
                    "Armoury Crate is still reading HID (run this app as Administrator "
                    "and Pause Crate), or a previous upload left the slot wedged. "
                    "If Save keeps failing, power-cycle: PSU off, hold the case power "
                    "button 30 seconds, then retry."
                )
            if progress is not None:
                progress(i + 1, n_chunks)

        self.hid_write([P.PREFIX, P.CMD_UPLOAD_PREPARE, 0xFF])
        self.ee_read(0.4)
        done = self.ee_wait(lambda r: ee_slot(r, 0xFF), 10.0)
        if done is None:
            raise RyujinError("commit timed out (ee13 ..ff not received)")
        time.sleep(0.5)

        self.hid_write([P.PREFIX, P.CMD_WAKE_FRAME, 0x00])
        self.ee_read(0.2)
        self.hid_write(
            [
                P.PREFIX,
                P.CMD_DISPLAY_OPTION,
                0x01,
                0x00,
                0x64,
                0x00,
                0x00,
                0x64,
                0x14,
                0x00,
                0x00,
                0x00,
                0x64,
                0x14,
            ]
        )
        self.ee_read(0.2)
        self.hid_write([P.PREFIX, P.CMD_WAKE_FRAME, 0x00])
        self.ee_read(0.2)
        if animation:
            self.hid_write(
                [P.PREFIX, P.CMD_SELECT_SLOT, 0x00, 0x01, P.MODE_SINGLE_ANIM, 0x01, slot, 0x05]
            )
            self.ee_read(0.2)
            self.lcd_mode(P.MODE_SINGLE_ANIM, 0x01, slot)
        else:
            self.hid_write([P.PREFIX, P.CMD_SLIDESHOW, 0x00, 0x01, 0x10])
            self.ee_read(0.2)
            offs = [0x17, 0x3F, 0x67, 0x8F, 0xB7, 0xDF]
            for i in range(6):
                self.hid_write(
                    [
                        P.PREFIX,
                        P.CMD_SLIDESHOW,
                        0x00,
                        0x02,
                        i,
                        0x03,
                        0x00,
                        0xFF,
                        0xFF,
                        0xFF,
                        0xFF,
                        0x08,
                        0x00,
                        offs[i],
                    ]
                )
                self.ee_read(0.2)
            self.hid_write([P.PREFIX, P.CMD_SELECT_SLOT, 0x00, 0x01, 0x04, 0x00, 0x00, 0x05])
            self.ee_read(0.2)
            self.lcd_mode(P.MODE_SLIDESHOW)
        return {"bytes": size, "chunks": n_chunks, "slot": slot, "animation": animation}
