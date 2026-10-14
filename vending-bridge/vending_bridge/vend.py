"""REMOTE (MDB 4.3 uzaktan secim) ve CREDIT (eski VMC, kredi verme) satis durum makineleri.

Kesin eslesme kurali: makine yalnizca kioskta secilen urun numarasini yapar.
- REMOTE: `C,SEL,<tutar>,<odemeID>,<urunNo>` -> VMC `c,STATUS,VEND,<fiyat>,<urunNo>` ister -> urun numarasi esitse ve fiyat
  odenen tutari asmiyorsa `C,VEND,<fiyat>`, aksi halde `C,VEND,-1` ve is FAILED (iade edilir).
- CREDIT: `C,START,<tutar>` -> musteri makinede dugmeye basar -> urun numarasi VE fiyat esitse `C,VEND,<fiyat>`;
  baska dugmede `C,VEND,-1` ve beklemeye devam (kiosk "<no> numarali urune basin" der). Sure dolarsa `C,STOP` -> FAILED.
Sonuc olaylari: `c,VEND,SUCCESS` | `c,ERR,VEND 3` (basarisiz) | `c,ERR,VEND 1` (makine iptal) | `c,ERR,VEND 5` (bus reset) |
`c,ERR,-3` (VMC reddetti).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from .qibixx import QibixxEvent, QibixxPort, format_amount, parse_amount, parse_item

logger = logging.getLogger("vending_bridge.vend")

READY_STATUSES = {"ENABLED", "IDLE"}
ERROR_MESSAGES = {
    "VEND 1": "Makine satışı iptal etti.",
    "VEND 3": "Makine ürünü veremedi.",
    "VEND 5": "Makine veri yolunu sıfırladı (bus reset).",
    "-3": "Makine (VMC) satışı reddetti.",
    "START -3": "Makine kredi oturumunu kabul etmedi (cashless etkin değil).",
    "SEL -3": "Makine uzaktan seçimi kabul etmedi.",
}


@dataclass(frozen=True)
class VendJob:
    id: str
    selection_no: int
    amount: Decimal
    mode: str
    timeout_sec: float
    payment_id: str = "FFFFFFFF"

    @classmethod
    def from_api(cls, job: dict[str, Any], *, forced_mode: str | None = None) -> "VendJob":
        payment_id = str(job.get("paymentId") or "FFFFFFFF").upper()
        if len(payment_id) != 8 or any(ch not in "0123456789ABCDEF" for ch in payment_id):
            payment_id = "FFFFFFFF"
        return cls(
            id=str(job["id"]),
            selection_no=int(job["selectionNo"]),
            amount=Decimal(str(job.get("amountText") or job["amount"])),
            mode=(forced_mode or str(job.get("mode") or "REMOTE")).upper(),
            timeout_sec=float(job.get("timeoutSec") or 90),
            payment_id=payment_id,
        )


@dataclass(frozen=True)
class VendResult:
    status: str  # SUCCESS | FAILED
    detail: str


# detail: metin ya da {"message", "pressedSelectionNo", ...} sozlugu (API ikisini de kabul eder).
Reporter = Callable[[str, "str | dict[str, Any] | None"], None]


def error_message(code: str) -> str:
    code = (code or "").strip().strip('"')
    return ERROR_MESSAGES.get(code, f"Qibixx hatası: {code or 'bilinmiyor'}")


class VendRunner:
    def __init__(self, port: QibixxPort, report: Reporter, *, ready_timeout: float = 10.0, clock: Callable[[], float] = time.monotonic):
        self.port = port
        self.report = report
        self.ready_timeout = ready_timeout
        self.clock = clock

    # ---- ortak ----
    def _ensure_ready(self, deadline: float) -> bool:
        if self.port.state in READY_STATUSES:
            return True
        if self.port.state in (None, "INACTIVE"):
            # Cashless kapali olabilir; acmayi dene (zaten aciksa "cashless is on" doner, zararsiz).
            self.port.send("C,1")
        return self.port.wait_for_status({"ENABLED"}, max(0.0, min(self.ready_timeout, deadline - self.clock())))

    def _safe_report(self, status: str, detail: "str | dict[str, Any] | None") -> None:
        try:
            self.report(status, detail)
        except Exception:  # noqa: BLE001 - ara durum bildirimi satisi bozmamali
            logger.warning("Ara durum bildirilemedi: %s", status)

    def run(self, job: VendJob) -> VendResult:
        if job.mode == "CREDIT":
            return self.run_credit(job)
        return self.run_remote(job)

    @staticmethod
    def _result_event(event: QibixxEvent) -> VendResult | None:
        if event.kind == "VEND" and event.args and event.args[0].upper() == "SUCCESS":
            return VendResult("SUCCESS", "c,VEND,SUCCESS")
        if event.kind == "ERR":
            return VendResult("FAILED", error_message(event.args[0] if event.args else ""))
        return None

    # ---- REMOTE ----
    def run_remote(self, job: VendJob) -> VendResult:
        deadline = self.clock() + job.timeout_sec
        if not self._ensure_ready(deadline):
            return VendResult("FAILED", "Makine hazır değil (cashless etkin değil); satış gönderilmedi.")
        self.port.drain()
        amount = format_amount(job.amount)
        self.port.send(f"C,SEL,{amount},{job.payment_id},{job.selection_no}")
        self._safe_report("SENT", f"C,SEL,{amount},{job.payment_id},{job.selection_no}")
        approved = False
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            event = self.port.next_event(min(remaining, 0.5))
            if event is None:
                continue
            if event.kind == "STATUS" and event.status == "VEND" and not approved:
                price = parse_amount(event.args[1] if len(event.args) > 1 else None)
                item = parse_item(event.args[2] if len(event.args) > 2 else None)
                if item != job.selection_no:
                    self.port.send("C,VEND,-1")
                    return VendResult("FAILED", f"Makine farklı ürün istedi ({item}); beklenen {job.selection_no}. Satış reddedildi.")
                if price is None or price > job.amount:
                    self.port.send("C,VEND,-1")
                    return VendResult("FAILED", f"Makine fiyatı ({price}) ödenen tutarı ({amount}) aşıyor. Satış reddedildi.")
                self.port.send(f"C,VEND,{format_amount(price)}")
                approved = True
                self._safe_report("DISPENSING", f"C,VEND,{format_amount(price)}")
                continue
            result = self._result_event(event)
            if result:
                return result
        if not approved:
            self.port.send("C,STOP")
            return VendResult("FAILED", "Makine seçimi onaylamadı (süre doldu).")
        return VendResult("FAILED", "Makineden satış sonucu alınamadı (süre doldu).")

    # ---- CREDIT ----
    def run_credit(self, job: VendJob) -> VendResult:
        deadline = self.clock() + job.timeout_sec
        if not self._ensure_ready(deadline):
            return VendResult("FAILED", "Makine hazır değil (cashless etkin değil); kredi verilmedi.")
        self.port.drain()
        amount = format_amount(job.amount)
        self.port.send(f"C,START,{amount}")
        self._safe_report("WAITING_SELECTION", f"Lütfen makinede {job.selection_no} numaralı ürüne basın.")
        approved = False
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                break
            event = self.port.next_event(min(remaining, 0.5))
            if event is None:
                continue
            if event.kind == "STATUS" and event.status == "VEND" and not approved:
                price = parse_amount(event.args[1] if len(event.args) > 1 else None)
                item = parse_item(event.args[2] if len(event.args) > 2 else None)
                if item == job.selection_no and price == job.amount:
                    self.port.send(f"C,VEND,{format_amount(price)}")
                    approved = True
                    self._safe_report("DISPENSING", f"C,VEND,{format_amount(price)}")
                else:
                    self.port.send("C,VEND,-1")
                    # API kioskta "yanlis urun" uyarisini gosterir (wrongSelection / pressedSelectionNo).
                    self._safe_report("WAITING_SELECTION", {
                        "message": f"Yanlış ürün ({item}, {price}); lütfen makinede {job.selection_no} numaralı ürüne basın.",
                        "pressedSelectionNo": item,
                        "pressedPrice": str(price) if price is not None else None,
                    })
                continue
            if event.kind == "ERR" and not approved:
                code = event.args[0] if event.args else ""
                if code.upper().startswith("START"):
                    return VendResult("FAILED", error_message(code))
                if code.strip() in ("VEND 1", "VEND 5"):
                    # Musteri makinede iptal etti / bus reset: kredi oturumu kapandi.
                    return VendResult("FAILED", error_message(code))
                # -3 vb.: reddedilen secimden sonra oturum surer; beklemeye devam.
                continue
            result = self._result_event(event)
            if result and approved:
                return result
        if not approved:
            self.port.send("C,STOP")
            return VendResult("FAILED", "Müşteri makinede ürünü seçmedi (süre doldu).")
        return VendResult("FAILED", "Makineden satış sonucu alınamadı (süre doldu).")
