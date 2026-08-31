from __future__ import annotations

import pytest

from killryujin.protocol import (
    CMD_SLIDESHOW,
    EE_PREFIX,
    PREFIX,
    REPORT_LEN,
    pad_report,
    u16le,
)


def test_pad_report_fills_to_hid_length() -> None:
    raw = pad_report([PREFIX, 0x82])
    assert len(raw) == REPORT_LEN
    assert raw[0] == PREFIX
    assert raw[1] == 0x82
    assert raw[2:] == b"\x00" * (REPORT_LEN - 2)


def test_pad_report_rejects_oversize() -> None:
    with pytest.raises(ValueError, match="HID payload"):
        pad_report(bytes(REPORT_LEN + 1))


def test_u16le() -> None:
    assert u16le(bytes([0x34, 0x12, 0x00]), 0) == 0x1234


def test_ee_prefix_is_ec_plus_two() -> None:
    assert EE_PREFIX == PREFIX + 2
    assert CMD_SLIDESHOW == 0x60
