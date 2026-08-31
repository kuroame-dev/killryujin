"""Wire constants for Ryujin III HID + bulk LCD protocol.

Reverse-engineered from AacAIOFanHal / live captures of the White Edition
(0x0B05:0x1ADA, firmware AURJ2-S750-0108). HID reports are 65 bytes and
start with 0xEC. Flow-control notifications use prefix 0xEE (0xEC+2).
"""

VID = 0x0B05
PID_WHITE = 0x1ADA
PID_EXTREME = 0x1BCB
PID_360 = 0x1AA2
PID_EVA = 0x1ADE

PIDS = {
    PID_WHITE: "ROG Ryujin III White Edition",
    PID_EXTREME: "ROG Ryujin III Extreme",
    PID_360: "ROG Ryujin III 360",
    PID_EVA: "ROG Ryujin III EVA",
}

REPORT_LEN = 65
PREFIX = 0xEC
EE_PREFIX = PREFIX + 2  # 0xEE: async flow-control reports

REQ_FIRMWARE = 0x82
RSP_FIRMWARE = 0x02
REQ_STATUS = 0x99
RSP_STATUS = 0x19
REQ_DUTY = 0x9A
RSP_DUTY = 0x1A

CMD_SET_COOLER_SPEED = 0x1A
CMD_SWITCH_DISPLAY_MODE = 0x51
CMD_HW_MONITOR_LAYOUT = 0x52
CMD_HW_MONITOR_STRING = 0x53
CMD_DISPLAY_OPTION = 0x5C
CMD_SET_CLOCK = 0x11
CMD_FLUSH_FRAMEBUFFER = 0x7F
CMD_UPLOAD_BEGIN = 0x71
CMD_UPLOAD_ARM = 0xF1
CMD_UPLOAD_PARAMS = 0x72
CMD_UPLOAD_PREPARE = 0x73
CMD_UPLOAD_SIZE = 0x7F
CMD_WAKE_FRAME = 0xDC
CMD_SELECT_SLOT = 0x5D
CMD_SLIDESHOW = 0x60

EE_SLOT = 0x13  # ee13 0001 = erased; ee13 00ff = flash write complete
EE_CHUNK = 0x14  # ee14 ..10 = chunk accepted

MODE_OFF = 0x00
MODE_ANIMATION = 0x04
MODE_CLOCK = 0x08
MODE_SINGLE_ANIM = 0x10
MODE_SLIDESHOW = 0x1F
MODE_FRAMEBUFFER = 0x20
MODE_HW_MONITOR = 0x21

LCD_WIDTH = 320
LCD_HEIGHT = 240
LCD_BPP = 3
LCD_FRAME_SIZE = LCD_WIDTH * LCD_HEIGHT * LCD_BPP  # 230_400
BULK_EP_OUT = 0x01
FLASH_CHUNK = 4096

# White / III status field offsets
TEMP_OFFSET = 5
PUMP_SPEED_OFFSET = 7
PUMP_FAN_SPEED_OFFSET = 10
DUTY_CHANNEL = 1


def u16le(msg: bytes, offset: int) -> int:
    return int.from_bytes(msg[offset : offset + 2], "little")


def pad_report(data: list[int] | bytes, length: int = REPORT_LEN) -> bytes:
    raw = bytes(data)
    if len(raw) > length:
        raise ValueError(f"HID payload {len(raw)} > {length}")
    return raw + b"\x00" * (length - len(raw))
