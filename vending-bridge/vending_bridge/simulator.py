"""Sahte Qibixx karti + VMC: gercek donanim olmadan uctan uca test icin pyserial arayuzunu taklit eder.

Senaryolar (`outcomes` sirasiyla her satisa uygulanir, bitince `default_outcome`):
- success: urun verilir (`c,VEND,SUCCESS`)
- fail: `c,ERR,VEND 3` | cancel: `c,ERR,VEND 1` | reset: `c,ERR,VEND 5` | deny: `c,ERR,-3`
- silent: makine hic sonuc bildirmez (zaman asimi)
- no_press (CREDIT): musteri dugmeye basmaz
- wrong_item (REMOTE): VMC istenenden farkli urun numarasi ister
- wrong_button (CREDIT): musteri once baska dugmeye basar, reddedilince dogru dugmeye basar
Mod: REMOTE (ozellik seviyesi 3 + opsiyon bit 6) ya da CREDIT (ozellik seviyesi 1, C,SEL desteklenmez).
"""
from __future__ import annotations

import queue
import threading
import time
from decimal import Decimal
from typing import Iterable

from .qibixx import format_amount


class SimulatedQibixx:
    def __init__(self, *, mode: str = "REMOTE", outcomes: Iterable[str] = (), default_outcome: str = "success",
                 customer_selection: int | None = None, wrong_selection: int = 99, prices: dict[int, Decimal | str] | None = None,
                 delay: float = 0.02, enabled: bool = False):
        self.mode = mode.upper()
        self.outcomes = list(outcomes)
        self.default_outcome = default_outcome
        self.customer_selection = customer_selection
        self.wrong_selection = wrong_selection
        self.prices = {int(key): Decimal(str(value)) for key, value in (prices or {}).items()}
        self.delay = delay
        self.enabled = enabled
        self.sniffer = False
        self.commands: list[str] = []
        self.emitted: list[str] = []
        self._out: "queue.Queue[str]" = queue.Queue()
        self._buffer = ""
        self._lock = threading.Lock()
        self._closed = False
        self._session_amount: Decimal | None = None
        self._pending_outcome: str | None = None
        self._requested: tuple[Decimal, int] | None = None
        self._after_deny: int | None = None
        if enabled:
            self._emit("c,STATUS,ENABLED")

    # ---- pyserial arayuzu ----
    def write(self, data: bytes) -> int:
        with self._lock:
            self._buffer += data.decode("ascii")
            lines = self._buffer.split("\n")
            self._buffer = lines.pop()
        for line in lines:
            line = line.strip()
            if line:
                self.commands.append(line)
                self._handle(line)
        return len(data)

    def readline(self) -> bytes:
        if self._closed:
            time.sleep(0.05)
            return b""
        try:
            line = self._out.get(timeout=0.1)
        except queue.Empty:
            return b""
        return (line + "\n").encode("ascii")

    def close(self) -> None:
        self._closed = True

    # ---- yardimcilar ----
    def _emit(self, line: str, delay: float = 0.0) -> None:
        def push():
            self.emitted.append(line)
            self._out.put(line)

        if delay <= 0:
            push()
        else:
            timer = threading.Timer(delay, push)
            timer.daemon = True
            timer.start()

    def _next_outcome(self) -> str:
        return self.outcomes.pop(0) if self.outcomes else self.default_outcome

    def _price(self, item: int, fallback: Decimal) -> Decimal:
        return self.prices.get(item, fallback)

    def _finish(self, outcome: str, step: float) -> None:
        results = {
            "success": ["c,VEND,SUCCESS"],
            "fail": ["c,ERR,VEND 3"],
            "cancel": ["c,ERR,VEND 1"],
            "reset": ["c,ERR,VEND 5"],
            "deny": ["c,ERR,-3"],
        }
        lines = results.get(outcome)
        if lines is None:  # silent vb.
            return
        for index, line in enumerate([*lines, "c,STATUS,IDLE", "c,STATUS,ENABLED"]):
            self._emit(line, step * (index + 1))

    # ---- komutlar ----
    def _handle(self, line: str) -> None:
        parts = line.split(",")
        head = parts[0].upper()
        step = self.delay
        if head == "X" and len(parts) > 1:
            self.sniffer = parts[1] == "1"
            self._emit("x,ACK")
            return
        if head != "C" or len(parts) < 2:
            self._emit('c,UNKNOWN," C"')
            return
        command = parts[1].upper()
        if command == "1":
            if self.enabled:
                self._emit('c,ERR,"cashless is on"')
                return
            self.enabled = True
            self._emit("c,STATUS,INACTIVE", step)
            self._emit("c,STATUS,DISABLED", step * 2)
            if self.sniffer:
                level = "03" if self.mode == "REMOTE" else "01"
                options = "60" if self.mode == "REMOTE" else "00"
                self._emit(f"x,00,0014320154,11,00{level}000000", step * 3)
                self._emit("x,80,0014320278,0103175601021e08", step * 3)
                self._emit(f"x,00,0014323438,17,04000000{options}", step * 4)
            self._emit("c,STATUS,ENABLED", step * 5)
            return
        if command == "0":
            self.enabled = False
            self._emit("c,STATUS,INACTIVE", step)
            return
        if command == "SEL":
            if self.mode != "REMOTE" or not self.enabled or len(parts) < 5:
                self._emit('c,ERR,"SEL -3"', step)
                return
            funds, item = Decimal(parts[2]), int(parts[4])
            outcome = self._next_outcome()
            if outcome == "wrong_item":
                item = self.wrong_selection
                outcome = "success"
            self._pending_outcome = outcome
            price = self._price(item, funds)
            self._requested = (price, item)
            self._emit(f"c,STATUS,VEND,{format_amount(price)},{item}", step)
            return
        if command == "START":
            if not self.enabled or len(parts) < 3:
                self._emit('c,ERR,"START -3"', step)
                return
            self._session_amount = Decimal(parts[2])
            self._emit(f"c,STATUS,IDLE,{format_amount(self._session_amount)}", step)
            outcome = self._next_outcome()
            if outcome == "no_press":
                self._pending_outcome = None
                return
            target = self.customer_selection if self.customer_selection is not None else 1
            if outcome == "wrong_button":
                self._pending_outcome = "success"
                self._press(self.wrong_selection, step * 2)
                self._after_deny = target
                return
            self._pending_outcome = outcome
            self._press(target, step * 2)
            return
        if command == "VEND" and len(parts) >= 3:
            if parts[2].strip() == "-1":
                target = getattr(self, "_after_deny", None)
                self._after_deny = None
                self._requested = None
                if target is not None and self._session_amount is not None:
                    self._press(target, step * 2)
                return
            outcome = self._pending_outcome or "success"
            self._pending_outcome = None
            self._requested = None
            self._session_amount = None
            self._finish(outcome, step)
            return
        if command == "STOP":
            self._session_amount = None
            self._pending_outcome = None
            self._emit("c,STATUS,ENABLED", step)
            return
        self._emit('c,UNKNOWN," C"')

    def _press(self, item: int, delay: float) -> None:
        price = self._price(item, self._session_amount or Decimal("0"))
        self._requested = (price, item)
        self._emit(f"c,STATUS,VEND,{format_amount(price)},{item}", delay)
