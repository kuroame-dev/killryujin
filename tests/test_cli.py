from __future__ import annotations

import pytest

from killryujin.cli import build_parser


def test_parser_requires_a_command() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_package_entry_is_importable() -> None:
    from killryujin.__main__ import main

    assert callable(main)


def test_persist_gif_defaults() -> None:
    args = build_parser().parse_args(["lcd", "persist-gif", r"C:\anim.gif"])
    assert args.lcd_action == "persist-gif"
    assert args.slot == 3
    assert args.file.endswith("anim.gif")
