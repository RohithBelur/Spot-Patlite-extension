import argparse
import struct
import sys
import threading
from dataclasses import dataclass
from typing import Optional

import usb.core
import usb.util
from usb.core import Device


VENDOR_ID = 0x191A
DEVICE_ID = 0x8003
COMMAND_VERSION = 0x00
COMMAND_ID = 0x00
ENDPOINT_ADDRESS = 1
SEND_TIMEOUT = 1000

DEBUG = False

# 3-tier tower colors
LED_COLOR_RED = 0
LED_COLOR_YELLOW = 1
LED_COLOR_GREEN = 2

COLOR_NAME_TO_ID = {
    "red": LED_COLOR_RED,
    "yellow": LED_COLOR_YELLOW,
    "green": LED_COLOR_GREEN,
}

# LED patterns
LED_OFF = 0x0
LED_ON = 0x1
LED_PATTERN1 = 0x2
LED_PATTERN2 = 0x3
LED_PATTERN3 = 0x4
LED_PATTERN4 = 0x5
LED_KEEP = 0xF

STATE_NAME_TO_ID = {
    "off": LED_OFF,
    "on": LED_ON,
    "flash1": LED_PATTERN1,
    "flash2": LED_PATTERN2,
    "flash3": LED_PATTERN3,
    "flash4": LED_PATTERN4,
    "keep": LED_KEEP,
}

# Buzzer patterns
BUZZER_OFF = 0x0
BUZZER_ON = 0x1
BUZZER_PATTERN1 = 0x2
BUZZER_PATTERN2 = 0x3
BUZZER_PATTERN3 = 0x4
BUZZER_PATTERN4 = 0x5
BUZZER_KEEP = 0xF

BUZZER_NAME_TO_ID = {
    "off": BUZZER_OFF,
    "on": BUZZER_ON,
    "pattern1": BUZZER_PATTERN1,
    "pattern2": BUZZER_PATTERN2,
    "pattern3": BUZZER_PATTERN3,
    "pattern4": BUZZER_PATTERN4,
}

# Buzzer pitch
BUZZER_PITCH_OFF = 0x0
BUZZER_PITCH1 = 0x1
BUZZER_PITCH2 = 0x2
BUZZER_PITCH3 = 0x3
BUZZER_PITCH4 = 0x4
BUZZER_PITCH5 = 0x5
BUZZER_PITCH6 = 0x6
BUZZER_PITCH7 = 0x7
BUZZER_PITCH8 = 0x8
BUZZER_PITCH9 = 0x9
BUZZER_PITCH10 = 0xA
BUZZER_PITCH11 = 0xB
BUZZER_PITCH12 = 0xC
BUZZER_PITCH13 = 0xD
BUZZER_PITCH_DFLT_A = 0xE
BUZZER_PITCH_DFLT_B = 0xF

PITCH_NAME_TO_ID = {
    "off": BUZZER_PITCH_OFF,
    "a6": BUZZER_PITCH1,
    "bb6": BUZZER_PITCH2,
    "b6": BUZZER_PITCH3,
    "c7": BUZZER_PITCH4,
    "db7": BUZZER_PITCH5,
    "d7": BUZZER_PITCH6,
    "eb7": BUZZER_PITCH7,
    "e7": BUZZER_PITCH8,
    "f7": BUZZER_PITCH9,
    "gb7": BUZZER_PITCH10,
    "g7": BUZZER_PITCH11,
    "ab7": BUZZER_PITCH12,
    "a7": BUZZER_PITCH13,
    "default_a": BUZZER_PITCH_DFLT_A,
    "default_b": BUZZER_PITCH_DFLT_B,
}


def debug_hex(data: bytes) -> None:
    if DEBUG:
        print("TX:", " ".join(f"{b:02X}" for b in data))


def usb_open() -> Device:
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=DEVICE_ID)
    if dev is None:
        raise RuntimeError(
            f"device not found (vendor=0x{VENDOR_ID:04X}, product=0x{DEVICE_ID:04X})"
        )

    if sys.platform.startswith("linux"):
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except usb.core.USBError:
            pass

    dev.set_configuration()
    return dev


def usb_close(dev: Optional[Device]) -> None:
    if dev is None:
        return
    try:
        usb.util.dispose_resources(dev)
    except AttributeError:
        pass
    except usb.core.USBError:
        pass


def send_command(dev: Device, data: bytes) -> bool:
    try:
        debug_hex(data)
        write_length = dev.write(ENDPOINT_ADDRESS, data, SEND_TIMEOUT)

        if sys.platform == "win32":
            write_length -= 1

        if write_length != len(data):
            print(f"failed to send: expected {len(data)} bytes, wrote {write_length}")
            return False

        return True

    finally:
        dev.reset()


@dataclass(frozen=True)
class PatliteCommand:
    command: str
    color: str = "red"
    state: str = "off"
    red: str = "off"
    yellow: str = "off"
    green: str = "off"
    pattern: str = "off"
    limit: int = 0
    pitch1: str = "default_a"
    pitch2: str = "default_b"


class PatliteController:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _pack_command(
        self,
        buzzer_control: int,
        buzzer_pitch: int,
        led_ry: int,
        led_gb: int,
        led_w_: int,
    ) -> bytes:
        return struct.pack(
            "BBBBBBBx",
            COMMAND_VERSION,
            COMMAND_ID,
            buzzer_control,
            buzzer_pitch,
            led_ry,
            led_gb,
            led_w_,
        )

    def _send(self, data: bytes) -> bool:
        with self._lock:
            dev = usb_open()
            try:
                return send_command(dev, data)
            finally:
                usb_close(dev)

    def set_light(self, color: int, state: int) -> bool:
        led_ry = (LED_KEEP << 4) | LED_KEEP
        led_gb = (LED_KEEP << 4) | LED_KEEP
        led_w_ = LED_KEEP << 4

        if color == LED_COLOR_RED:
            led_ry = (state << 4) | LED_KEEP
        elif color == LED_COLOR_YELLOW:
            led_ry = (LED_KEEP << 4) | state
        elif color == LED_COLOR_GREEN:
            led_gb = (state << 4) | LED_KEEP
        else:
            raise ValueError("unsupported color for this tower")

        data = self._pack_command(BUZZER_KEEP, 0, led_ry, led_gb, led_w_)
        return self._send(data)

    def set_tower(self, red: int, yellow: int, green: int) -> bool:
        led_ry = (red << 4) | yellow
        led_gb = (green << 4) | LED_KEEP
        led_w_ = LED_KEEP << 4

        data = self._pack_command(BUZZER_KEEP, 0, led_ry, led_gb, led_w_)
        return self._send(data)

    def set_buzzer(self, buz_state: int, limit: int) -> bool:
        buzzer_control = (limit << 4) | buz_state
        buzzer_pitch = (BUZZER_PITCH_DFLT_A << 4) | BUZZER_PITCH_DFLT_B
        led_ry = (LED_KEEP << 4) | LED_KEEP
        led_gb = (LED_KEEP << 4) | LED_KEEP
        led_w_ = LED_KEEP << 4

        data = self._pack_command(
            buzzer_control,
            buzzer_pitch,
            led_ry,
            led_gb,
            led_w_,
        )
        return self._send(data)

    def set_buzzer_ex(
        self, buz_state: int, limit: int, pitch1: int, pitch2: int
    ) -> bool:
        buzzer_control = (limit << 4) | buz_state
        buzzer_pitch = (pitch1 << 4) | pitch2
        led_ry = (LED_KEEP << 4) | LED_KEEP
        led_gb = (LED_KEEP << 4) | LED_KEEP
        led_w_ = LED_KEEP << 4

        data = self._pack_command(
            buzzer_control,
            buzzer_pitch,
            led_ry,
            led_gb,
            led_w_,
        )
        return self._send(data)

    def reset_tower(self) -> bool:
        data = self._pack_command(
            BUZZER_OFF,
            BUZZER_PITCH_OFF,
            (LED_OFF << 4) | LED_OFF,
            (LED_OFF << 4) | LED_KEEP,
            LED_KEEP << 4,
        )
        return self._send(data)

    def execute(self, command: PatliteCommand) -> bool:
        if command.command == "single":
            return self.set_light(
                COLOR_NAME_TO_ID[command.color],
                STATE_NAME_TO_ID[command.state],
            )
        if command.command == "tower":
            return self.set_tower(
                STATE_NAME_TO_ID[command.red],
                STATE_NAME_TO_ID[command.yellow],
                STATE_NAME_TO_ID[command.green],
            )
        if command.command == "buzzer":
            return self.set_buzzer(
                BUZZER_NAME_TO_ID[command.pattern],
                command.limit,
            )
        if command.command == "buzzer_ex":
            return self.set_buzzer_ex(
                BUZZER_NAME_TO_ID[command.pattern],
                command.limit,
                PITCH_NAME_TO_ID[command.pitch1],
                PITCH_NAME_TO_ID[command.pitch2],
            )
        if command.command == "off":
            return self.reset_tower()
        raise ValueError(f"unsupported command: {command.command}")


def validate_limit(limit: int) -> int:
    if not 0 <= limit <= 15:
        raise argparse.ArgumentTypeError("limit must be between 0 and 15")
    return limit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PATLITE LR6-USB controller")
    parser.add_argument("--debug", action="store_true")

    sub = parser.add_subparsers(dest="command", required=True)

    p_single = sub.add_parser("single", help="set one color")
    p_single.add_argument("color", choices=COLOR_NAME_TO_ID.keys())
    p_single.add_argument("state", choices=STATE_NAME_TO_ID.keys())

    p_tower = sub.add_parser("tower", help="set red/yellow/green together")
    p_tower.add_argument("--red", required=True, choices=STATE_NAME_TO_ID.keys())
    p_tower.add_argument("--yellow", required=True, choices=STATE_NAME_TO_ID.keys())
    p_tower.add_argument("--green", required=True, choices=STATE_NAME_TO_ID.keys())

    p_buzzer = sub.add_parser("buzzer", help="set buzzer pattern")
    p_buzzer.add_argument("pattern", choices=BUZZER_NAME_TO_ID.keys())
    p_buzzer.add_argument("limit", type=validate_limit)

    p_buzzer_ex = sub.add_parser("buzzer_ex", help="set buzzer pattern and pitches")
    p_buzzer_ex.add_argument("pattern", choices=BUZZER_NAME_TO_ID.keys())
    p_buzzer_ex.add_argument("limit", type=validate_limit)
    p_buzzer_ex.add_argument("pitch1", choices=PITCH_NAME_TO_ID.keys())
    p_buzzer_ex.add_argument("pitch2", choices=PITCH_NAME_TO_ID.keys())

    sub.add_parser("off", help="turn off all lights and stop buzzer")

    return parser.parse_args()


def main() -> int:
    global DEBUG
    args = parse_args()
    DEBUG = args.debug

    controller = PatliteController()
    command = PatliteCommand(
        command=args.command,
        color=getattr(args, "color", "red"),
        state=getattr(args, "state", "off"),
        red=getattr(args, "red", "off"),
        yellow=getattr(args, "yellow", "off"),
        green=getattr(args, "green", "off"),
        pattern=getattr(args, "pattern", "off"),
        limit=getattr(args, "limit", 0),
        pitch1=getattr(args, "pitch1", "default_a"),
        pitch2=getattr(args, "pitch2", "default_b"),
    )
    ok = controller.execute(command)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
