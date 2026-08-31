"""Crop, GIF disposal, and BGR packing for the 320x240 panel.

The GUI preview and flash encoder share this. The crop you see is the crop
written to the cooler.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageSequence

from . import protocol as P


def view_rgb(
    img: Image.Image,
    width: int = P.LCD_WIDTH,
    height: int = P.LCD_HEIGHT,
    *,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
) -> Image.Image:
    """Scale relative to cover-fit, then pan. zoom=1, pan=0 is a center crop."""
    src = img.convert("RGB")
    zoom = max(0.05, float(zoom))
    cover = max(width / src.width, height / src.height)
    scale = cover * zoom
    new_w = max(1, int(round(src.width * scale)))
    new_h = max(1, int(round(src.height * scale)))
    scaled = src.resize((new_w, new_h), Image.Resampling.LANCZOS)
    cx = (new_w - width) / 2.0 + pan_x * scale
    cy = (new_h - height) / 2.0 + pan_y * scale
    out = Image.new("RGB", (width, height), (0, 0, 0))
    out.paste(scaled, (int(round(-cx)), int(round(-cy))))
    return out


def composite_gif_rgb(
    path: str | Path, max_frames: int | None = None
) -> tuple[list[Image.Image], list[int]]:
    """Composite GIF disposal to RGB frames at the source size."""
    src = Image.open(path)
    try:
        canvas = Image.new("RGBA", src.size)
        rgb_frames: list[Image.Image] = []
        durations: list[int] = []
        for i, frame in enumerate(ImageSequence.Iterator(src)):
            if max_frames is not None and i >= max_frames:
                break
            layer = frame.convert("RGBA")
            canvas.paste(layer, (0, 0), layer)
            rgb_frames.append(canvas.convert("RGB"))
            durations.append(max(20, int(frame.info.get("duration", 80) or 80)))
            disposal = getattr(frame, "disposal_method", None)
            if disposal is None:
                disposal = frame.info.get("disposal", 1)
            if disposal == 2:
                canvas = Image.new("RGBA", src.size)
    finally:
        src.close()
    if not rgb_frames:
        raise ValueError("GIF has no frames")
    return rgb_frames, durations


def gif_frames(
    path: str | Path,
    max_frames: int | None = None,
    *,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
) -> tuple[list[Image.Image], list[int]]:
    """Composite GIF disposal, then view-crop each frame to the LCD."""
    frames, durations = composite_gif_rgb(path, max_frames)
    return [view_rgb(f, zoom=zoom, pan_x=pan_x, pan_y=pan_y) for f in frames], durations


def gif_global_palette(frames: list[Image.Image], colors: int = 256) -> Image.Image:
    """Build one GIF palette from several frames so later clips keep their hues."""
    method = getattr(Image.Quantize, "MAXCOVERAGE", Image.Quantize.MEDIANCUT)
    rgb = [f.convert("RGB") for f in frames]
    if len(rgb) == 1:
        return rgb[0].quantize(colors=colors, method=method, dither=Image.Dither.NONE)
    n = min(8, len(rgb))
    picks = [rgb[round(i * (len(rgb) - 1) / (n - 1))] for i in range(n)]
    w, h = picks[0].size
    sheet = Image.new("RGB", (w * len(picks), h))
    for i, frame in enumerate(picks):
        sheet.paste(frame, (i * w, 0))
    return sheet.quantize(colors=colors, method=method, dither=Image.Dither.NONE)


def rgb_to_bgr(img: Image.Image) -> bytes:
    """Pack a panel-sized frame as BGR888 for the bulk framebuffer pipe."""
    if img.size != (P.LCD_WIDTH, P.LCD_HEIGHT) or img.mode != "RGB":
        img = view_rgb(img)
    rgb = img.tobytes()
    bgr = bytearray(len(rgb))
    bgr[0::3] = rgb[2::3]
    bgr[1::3] = rgb[1::3]
    bgr[2::3] = rgb[0::3]
    return bytes(bgr)
