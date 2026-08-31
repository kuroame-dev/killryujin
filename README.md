# killryujin

Windows app for an **ASUS ROG Ryujin III**. Sends GIFs and stills to the 320×240 LCD over USB. Armoury Crate is not required.

## Download

1. Open **[v0.1.0-alpha](https://github.com/kuroame-dev/killryujin/releases/tag/v0.1.0-alpha)**.
2. Download `killryujin-setup.exe`.
3. Run the installer. It goes into Program Files and adds a Start Menu shortcut.
4. Open **Killryujin** from the Start Menu.

If Armoury Crate is still running, click **Relaunch as Admin**, then **Pause Crate**.

Then:

1. Confirm the cooler shows up at the top.
2. **Pause Crate** (Administrator) before **Save to cooler**.
3. Choose a GIF or image.
4. **Save to cooler**.

If Crate starts at boot and switches back to the ROG animation, click **Play saved GIF**. The file stays on the cooler.

## Bugs

This is an alpha. If something breaks, open an [Issue](https://github.com/kuroame-dev/killryujin/issues/new).

Include Windows version, cooler model (White / Extreme / 360 / EVA), and the steps you took. If Save fails, copy the error from the window.

## Limits and safety

- Windows only.
- **Pause Crate** (Administrator) before **Save to cooler**. A live Crate HID hold fails the flash handshake.
- Save **writes cooler flash**. The write survives reboot. The on-PC preview is not the panel.
- If save reports **flash slot not ready** (`ee13 1001`), **do not retry in a loop**. Shut down, hold the case power button about 30 seconds so firmware exits a mid-erase state, then try **once**.
- killryujin drives the LCD and a few HID controls (brightness, clock, built-in modes). RGB, fan curves, and the rest of the AIO stay with Armoury Crate.

## Supported hardware

ASUS VID `0x0B05`, these PIDs:

| Cooler                   | PID      |
| ------------------------ | -------- |
| Ryujin III White Edition | `0x1ADA` |
| Ryujin III Extreme       | `0x1BCB` |
| Ryujin III 360           | `0x1AA2` |
| Ryujin III EVA           | `0x1ADE` |

Plug the AIO USB cable into a motherboard **USB 2.0** header. Tested on White Edition firmware `AURJ2-S750-0108`.

## USB protocol

The process opens two USB interfaces. Nothing is copied into Armoury Crate folders.

- **HID** (interface 1): 65-byte reports prefixed `0xEC` for status, brightness, clock, and display mode. Persist waits on async `0xEE` handshake packets (`ee13` erase/slot, `ee14` chunk ACK).
- **WinUSB bulk OUT** (interface 0, endpoint `0x01`): 320×240 BGR frames, plus 4096-byte chunks into onboard SPI flash (FAT16 GIF/JPEG slot). Windows already binds WinUSB here. Zadig is not needed.

Close Armoury Crate, Aura, and OpenRGB first. Those programs poll the same HID interface and steal `0xEE` packets.

## Why

Armoury Crate's custom-GIF path hangs and desyncs the flash handshake. The LCD then stays uneditable until a full PSU drain. killryujin crops a GIF, waits for the `0xEE` handshake, and writes flash.

## From source

For development, or if you want the `killryujin` command:

```powershell
python -m pip install -e ".[dev]"
killryujin-gui
killryujin --help
python -m pytest
```

```powershell
killryujin list
killryujin status
killryujin crate pause
killryujin lcd persist-gif C:\path\to\anim.gif
killryujin lcd play-saved
killryujin lcd clock 24h
killryujin lcd liquid
```

If Windows says the command is not found, add Python's `Scripts` folder to PATH and open a new terminal. User installs often land in `%APPDATA%\Python\Python3xx\Scripts`.

Build the portable exe on your machine. If [Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed, this also builds `killryujin-setup.exe`. Both land in `dist\` (gitignored):

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_windows.ps1
```

## License

MIT. Hardware protocol details come from community reverse engineering of the Ryujin III (liquidctl and live White Edition captures).
