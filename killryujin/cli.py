"""Ryujin III LCD command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import crate
from .device import FlashSlotStuck, Ryujin, RyujinError, enumerate_coolers
from .protocol import PIDS


def _die(msg: str, code: int = 1) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(code)


def cmd_list(_args: argparse.Namespace) -> None:
    found = enumerate_coolers()
    if not found:
        _die("no Ryujin III found")
    for d in found:
        pid = d["product_id"]
        print(f"{PIDS.get(pid, 'Ryujin III')}  VID=0x0B05 PID=0x{pid:04X}")
        print(f"  {d.get('product_string')}  {d.get('manufacturer_string')}")


def cmd_status(_args: argparse.Namespace) -> None:
    with Ryujin() as dev:
        st = dev.status()
    print(f"{st['product']}  ({st['pid']})")
    print(f"  firmware        {st['firmware']}")
    print(f"  liquid          {st['liquid_c']:.1f} C")
    print(f"  pump            {st['pump_duty']}%  {st['pump_rpm']} rpm")
    print(f"  pump fan        {st['pump_fan_duty']}%  {st['pump_fan_rpm']} rpm")


def cmd_crate(args: argparse.Namespace) -> None:
    fn = crate.pause if args.action == "pause" else crate.resume
    for line in fn():
        print(line)


def _flash_progress(cur: int, total: int) -> None:
    if cur == 1 or cur == total or cur % 8 == 0:
        print(f"  flash {cur}/{total} chunks", file=sys.stderr)


def _maybe_pause(args: argparse.Namespace) -> None:
    if not args.pause_crate:
        return
    print("pausing Armoury Crate...")
    for line in crate.pause():
        print(f"  {line}")


def cmd_pump(args: argparse.Namespace) -> None:
    with Ryujin() as dev:
        if args.fan is not None:
            st = dev.status()
            pump = args.percent if args.channel == "pump" else st["pump_duty"]
            fan = args.percent if args.channel == "fan" else args.fan
            dev.set_pump(pump, fan)
        elif args.channel == "fan":
            st = dev.status()
            dev.set_pump(st["pump_duty"], args.percent)
        else:
            dev.set_pump(args.percent)
    print("ok")


def _persist(dev: Ryujin, args: argparse.Namespace, *, animation: bool) -> None:
    path = Path(args.file)
    if not path.is_file():
        _die(f"not found: {path}")
    if crate.is_running() and crate.is_admin():
        print("pausing Armoury Crate...", file=sys.stderr)
        print(" | ".join(crate.pause()), file=sys.stderr)
    if crate.is_running():
        _die(
            "Armoury Crate is still running and will steal the flash "
            "handshake. Run as Administrator: killryujin crate pause"
        )
    print(
        f"flash upload ({'GIF' if animation else 'JPEG'}) from {path}\n"
        "keep other RGB/AIO tools closed until this finishes"
    )
    result = dev.persist(
        path,
        animation=animation,
        slot=args.slot,
        max_frames=args.max_frames,
        progress=_flash_progress,
    )
    print(json.dumps(result, indent=2))


def cmd_lcd(args: argparse.Namespace) -> None:
    action = args.lcd_action
    _maybe_pause(args)

    try:
        with Ryujin() as dev:
            if action == "wake":
                dev.lcd_wake()
            elif action == "standby":
                dev.lcd_standby()
            elif action == "off":
                dev.lcd_off()
            elif action == "liquid":
                dev.lcd_liquid()
            elif action == "play-saved":
                dev.lcd_play_saved(3, True)
            elif action == "clock":
                dev.lcd_clock(fmt_24h=args.format != "12h")
            elif action == "monitor":
                dev.lcd_monitor()
            elif action == "brightness":
                dev.lcd_brightness(args.value)
            elif action == "orientation":
                dev.lcd_orientation(args.value)
            elif action == "persist-gif":
                _persist(dev, args, animation=True)
                return
            elif action == "persist-image":
                _persist(dev, args, animation=False)
                return
            else:
                _die(f"unknown lcd action {action}")
    except FlashSlotStuck as exc:
        _die(str(exc))
    except (RyujinError, OSError) as exc:
        _die(str(exc))
    print("ok")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="killryujin",
        description="ASUS ROG Ryujin III LCD control without Armoury Crate.",
    )
    p.add_argument(
        "--pause-crate",
        action="store_true",
        help="stop Armoury Crate before talking to the LCD (needs Administrator)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="find the cooler on USB").set_defaults(func=cmd_list)
    sub.add_parser("status", help="firmware, liquid temp, pump/fan").set_defaults(func=cmd_status)

    crate_p = sub.add_parser("crate", help="pause or resume Armoury Crate")
    crate_p.add_argument("action", choices=["pause", "resume"])
    crate_p.set_defaults(func=cmd_crate)

    pump_p = sub.add_parser("pump", help="set pump or pump-fan duty")
    pump_p.add_argument("channel", choices=["pump", "fan"])
    pump_p.add_argument("percent", type=int)
    pump_p.add_argument("--fan", type=int, default=None, help="also set pump-fan duty")
    pump_p.set_defaults(func=cmd_pump)

    lcd = sub.add_parser("lcd", help="drive the 320x240 panel")
    lcd_sub = lcd.add_subparsers(dest="lcd_action", required=True)

    lcd_sub.add_parser("wake")
    lcd_sub.add_parser("standby")
    lcd_sub.add_parser("off")
    lcd_sub.add_parser("liquid", help="built-in ROG animation")
    lcd_sub.add_parser("play-saved", help="play the last GIF stored in flash")
    lcd_sub.add_parser("monitor", help="one-shot hardware monitor overlay")
    clock = lcd_sub.add_parser("clock")
    clock.add_argument("format", nargs="?", default="24h", choices=["24h", "12h"])
    br = lcd_sub.add_parser("brightness")
    br.add_argument("value", type=int)
    ori = lcd_sub.add_parser("orientation")
    ori.add_argument("value", type=int, help="0=0° 1=90° 2=180° 3=270°")

    pg = lcd_sub.add_parser("persist-gif", help="write GIF into onboard flash")
    pg.add_argument("file")
    pg.add_argument("--slot", type=int, default=3, help="animation slots 0-3")
    pg.add_argument("--max-frames", type=int, default=None)

    pi = lcd_sub.add_parser("persist-image", help="write JPEG into onboard flash")
    pi.add_argument("file")
    pi.add_argument("--slot", type=int, default=4, help="static slot is 4")
    pi.add_argument("--max-frames", type=int, default=None)

    lcd.set_defaults(func=cmd_lcd)
    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except RyujinError as exc:
        _die(str(exc))


if __name__ == "__main__":
    main()
