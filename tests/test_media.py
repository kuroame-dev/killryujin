from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from killryujin.media import composite_gif_rgb, rgb_to_bgr, view_rgb
from killryujin.protocol import LCD_HEIGHT, LCD_WIDTH


def test_view_rgb_output_is_panel_sized() -> None:
    src = Image.new("RGB", (640, 240), (255, 0, 0))
    out = view_rgb(src)
    assert out.size == (LCD_WIDTH, LCD_HEIGHT)
    assert out.getpixel((0, 0)) == (255, 0, 0)


def test_view_rgb_cover_fit_fills_panel() -> None:
    src = Image.new("RGB", (100, 400), (0, 255, 0))
    out = view_rgb(src)
    assert out.size == (LCD_WIDTH, LCD_HEIGHT)
    assert out.getpixel((LCD_WIDTH // 2, LCD_HEIGHT // 2)) == (0, 255, 0)


def test_rgb_to_bgr_swaps_channels() -> None:
    img = Image.new("RGB", (LCD_WIDTH, LCD_HEIGHT), (255, 0, 0))
    packed = rgb_to_bgr(img)
    assert len(packed) == LCD_WIDTH * LCD_HEIGHT * 3
    assert packed[0:3] == bytes((0, 0, 255))


def test_composite_gif_keeps_frame_count(tmp_path: Path) -> None:
    path = tmp_path / "clip.gif"
    frames = [
        Image.new("RGB", (32, 32), (255, 0, 0)),
        Image.new("RGB", (32, 32), (0, 0, 255)),
    ]
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=80,
        loop=0,
    )
    rgb, durations = composite_gif_rgb(path)
    assert len(rgb) == 2
    assert rgb[0].size == (32, 32)
    assert durations[0] >= 20


def test_composite_gif_rejects_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.gif"
    Image.new("RGB", (8, 8), (0, 0, 0)).save(path, "GIF")
    # A single-frame GIF is valid; a truncated file is not.
    path.write_bytes(b"GIF89a")
    with pytest.raises((OSError, ValueError)):
        composite_gif_rgb(path)
