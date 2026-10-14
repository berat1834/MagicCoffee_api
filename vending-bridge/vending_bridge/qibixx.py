"""Qibixx MDB karti seri port surucusu (Cashless Device / slave API).

Kart 115200 8N1 calisir, komutlar ve olaylar satir bazlidir (satir sonu `\\n`). Kaynak:
https://docs.qibixx.com/released/mdb-products/api-cashless-slave

Komutlar (kopru -> kart): `C,1` / `C,0`, `C,START,<tutar>`, `C,VEND,<tutar>` / `C,VEND,-1`, `C,STOP`,
`C,SEL,<tutar>,<odemeID 8 hex>,<urunNo>` (MDB 4.3 uzaktan secim), `X,1` / `X,0` (sniffer).
Olaylar (kart -> kopru): `c,STATUS,INACTIVE|DISABLED|ENABLED|IDLE[,<tutar>]`, `c,STATUS,VEND,<fiyat>,<urunNo>`,
`c,VEND,SUCCESS`, `c,ERR,VEND 1|3|5`, `c,ERR,-3`, `c,ERR,"START -3"`, `c,SALE,<fiyat>,<urunNo>`, `x,...` (sniffer).
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Protocol

logger = logging.getLogger("vending_bridge.qibixx")

BAUDRATE = 115200


class Transport(Protocol):
    """pyserial.Serial ile ayni asgari arayuz (simulator de bunu uygular)."""

    def write(self, data: bytes) -> Any: ...

    def readline(self) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class QibixxEvent:
    kind: str  # STATUS | VEND | ERR | SALE | SNIFF | DEVICE | ACK | OTHER
    args: tuple[str, ...] = field(default_factory=tuple)
    raw: str = ""

    @property
    def status(self) -> str | None:
        return self.args[0].upper() if self.kind == "STATUS" and self.args else None


def parse_line(line: str | bytes) -> QibixxEvent | None:
    if isinstance(line, bytes):
        line = line.decode("ascii", errors="replace")
    text = line.strip()
    if not text:
        return None
    parts = [part.strip().strip('"').strip() for part in text.split(",")]
    prefix = parts[0]
    if prefix == "c" and len(parts) >= 2:
        head = parts[1].upper()
        rest = tuple(parts[2:])
        if head == "STATUS":
            return QibixxEvent("STATUS", rest, text)
        if head == "VEND":
            return QibixxEvent("VEND", rest, text)
        if head == "ERR":
            return QibixxEvent("ERR", (",".join(rest).strip('"').strip(),), text)
        if head == "SALE":
            return QibixxEvent("SALE", rest, text)
        return QibixxEvent("OTHER", tuple(parts[1:]), text)
    if prefix == "x":
        if len(parts) >= 2 and parts[1].upper() == "ACK":
            return QibixxEvent("ACK", tuple(parts[1:]), text)
        return QibixxEvent("SNIFF", tuple(parts[1:]), text)
    if prefix == "d":
        return QibixxEvent("DEVICE", tuple(parts[1:]), text)
    return QibixxEvent("OTHER", tuple(parts), text)


def format_amount(amount: Any) -> str:
    """TL, nokta ondalik, 2 hane (92 -> '92.00'). Olcek faktorunu Qibixx yonetir."""
    return f"{Decimal(str(amount)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def parse_amount(value: str | None) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None


def parse_item(value: str | None) -> int | None:
    try:
        item = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    # 0xFFFF: urun numarasi iletilmedi.
    return None if item < 0 or item == 0xFFFF else item


class QibixxPort:
    """Seri hat ustunde satir bazli okuma/yazma; okuma ayri bir is parcaciginda kuyruga yazilir."""

    def __init__(self, transport: Transport, *, on_line: Callable[[str], None] | None = None):
        self.transport = transport
        self.on_line = on_line
        self.events: "queue.Queue[QibixxEvent]" = queue.Queue()
        self.state: str | None = None  # son c,STATUS degeri
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._reader = threading.Thread(target=self._read_loop, name="qibixx-reader", daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        while not self._closed.is_set():
            try:
                raw = self.transport.readline()
            except Exception as error:  # noqa: BLE001 - port kapandi/koptu
                if not self._closed.is_set():
                    logger.warning("Seri port okunamadi: %s", error)
                    time.sleep(0.5)
                continue
            if not raw:
                continue
            text = raw.decode("ascii", errors="replace").strip() if isinstance(raw, bytes) else str(raw).strip()
            if self.on_line:
                self.on_line(text)
            event = parse_line(text)
            if event is None:
                continue
            logger.debug("<- %s", text)
            if event.kind == "STATUS" and event.status:
                self.state = event.status
            self.events.put(event)

    def send(self, command: str) -> None:
        logger.debug("-> %s", command)
        with self._lock:
            self.transport.write((command + "\n").encode("ascii"))

    def next_event(self, timeout: float) -> QibixxEvent | None:
        try:
            return self.events.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def drain(self) -> list[QibixxEvent]:
        drained = []
        while True:
            try:
                drained.append(self.events.get_nowait())
            except queue.Empty:
                return drained

    def wait_for_status(self, statuses: set[str], timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        if self.state in statuses:
            return True
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self.state in statuses
            event = self.next_event(min(remaining, 0.5))
            if event and event.status in statuses:
                return True

    def close(self) -> None:
        self._closed.set()
        try:
            self.transport.close()
        except Exception:  # noqa: BLE001
            pass


def open_serial(port: str, baudrate: int = BAUDRATE) -> Transport:
    """Gercek seri port (pyserial yalnizca burada gerekir; testler simulatorle calisir)."""
    import serial  # type: ignore[import-not-found]

    return serial.Serial(port, baudrate=baudrate, bytesize=8, parity="N", stopbits=1, timeout=0.5, write_timeout=2)
