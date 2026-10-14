"""VMC'nin MDB 4.3 uzaktan secimi destekleyip desteklemedigini sniffer (X,1) ile tespit eder.

Kaynak: https://docs.qibixx.com/released/mdb-products/cashless-device-remote-product-selection-with-mdb-4-3
- Setup (komut 11, alt komut 00 "config data"): `x,00,<zaman>,11,0003000000` -> ikinci bayt VMC ozellik seviyesi (03 olmali).
- Expansion opsiyonlari (komut 17, alt komut 04): `x,00,<zaman>,17,0400000060` -> son baytin 6. biti (0x40) 1 olmali.
Ikisi de saglaniyorsa REMOTE, aksi halde CREDIT. Setup yalniz VMC cashless'i (yeniden) baslatirken gonderildiginden
cashless `C,0` / `C,1` ile yeniden baslatilir ve setup trafigi dinlenir.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .qibixx import QibixxPort

logger = logging.getLogger("vending_bridge.detect")

REMOTE_OPTION_BIT = 0x40


@dataclass
class Detection:
    mode: str = "CREDIT"
    feature_level: int | None = None
    option_bits: str | None = None
    remote_option: bool = False
    raw: list[str] = field(default_factory=list)

    def as_vmc_info(self) -> dict[str, Any]:
        return {
            "featureLevel": self.feature_level,
            "optionBits": self.option_bits,
            "remoteSelection": self.mode == "REMOTE",
            "detectedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


def parse_sniff(args: tuple[str, ...]) -> tuple[str, str, str] | None:
    """`x,<yon>,<zaman>,<komut>,<veri>` -> (yon, komut, veri). Yon 00 = VMC -> cevre birimi."""
    if len(args) < 4:
        return None
    direction, command, data = args[0], args[2].strip().lower(), args[3].strip().lower().split()[0] if args[3].strip() else ""
    return direction, command, data


def apply_sniff(detection: Detection, args: tuple[str, ...]) -> None:
    parsed = parse_sniff(args)
    if not parsed:
        return
    direction, command, data = parsed
    if direction != "00":
        return
    try:
        if command == "11" and len(data) >= 4 and data[:2] == "00":
            detection.feature_level = int(data[2:4], 16)
        elif command == "17" and len(data) >= 10 and data[:2] == "04":
            detection.option_bits = data[2:10]
            detection.remote_option = bool(int(data[8:10], 16) & REMOTE_OPTION_BIT)
    except ValueError:
        return


def detect_mode(port: QibixxPort, timeout: float = 20.0, settle: float = 1.0) -> Detection:
    detection = Detection()
    port.drain()
    port.send("X,1")
    port.send("C,0")
    port.send("C,1")
    deadline = time.monotonic() + timeout
    enabled_at: float | None = None
    while time.monotonic() < deadline:
        event = port.next_event(0.25)
        if event is not None:
            if event.kind == "SNIFF":
                detection.raw.append(event.raw)
                apply_sniff(detection, event.args)
            elif event.status == "ENABLED" and enabled_at is None:
                enabled_at = time.monotonic()
        if detection.feature_level is not None and detection.option_bits is not None:
            break
        if enabled_at is not None and time.monotonic() - enabled_at >= settle:
            break
    port.send("X,0")
    detection.mode = "REMOTE" if (detection.feature_level or 0) >= 3 and detection.remote_option else "CREDIT"
    logger.info("VMC tespiti: ozellik seviyesi=%s opsiyonlar=%s -> %s", detection.feature_level, detection.option_bits, detection.mode)
    return detection
