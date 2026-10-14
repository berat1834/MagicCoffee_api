"""CasPro (Castles Protocol v3.4) bulut entegrasyonu.

Backend, Castles bulutuna dogrudan `POST {CASPRO_API_URL}/api/send-command` ile
dokumandaki JSON mesaj yapisini gonderir. Bu modul yalnizca tasima katmanini,
mesaj kuruculari ve yanit yorumlayicilari icerir; durum yonetimi main.py'dedir.
"""
from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable
from uuid import uuid4

import httpx

logger = logging.getLogger("caspro")

DEFAULT_CASPRO_API_URL = "https://external-system-api.tur.castlestech.cloud:6443"
DEFAULT_TRANSACTION_TIMEOUT_SECONDS = 150.0
DEFAULT_RESULT_TIMEOUT_SECONDS = 180.0
COMMAND_TIMEOUT_SECONDS = 20.0
SEND_COMMAND_PATH = "/api/send-command"
TRANSACTION_PREFIX = "cp-"
TRANSACTION_PATTERN = re.compile(r"cp-[0-9a-f]{24}")
PRINT_LINE_MAX_LENGTH = 40
DEVICE_OFFLINE_MESSAGE = "Castles POS cihazı çevrimdışı; ödeme gönderilemedi."
TIMEOUT_MESSAGE = "Kart okutma süresi doldu."
CANCELLED_MESSAGE = "Ödeme POS cihazında iptal edildi."

# Appendix-1: banka yanit kodlarinin Turkce karsiliklari (cihaz mesaj dondurmezse kullanilir).
RESPONSE_CODE_MESSAGES = {
    "05": "İşlem reddedildi",
    "12": "Geçersiz işlem",
    "13": "Geçersiz tutar",
    "14": "Geçersiz kart numarası",
    "41": "Kart el konulması gereken kart",
    "43": "Kart el konulması gereken kart",
    "51": "Yetersiz bakiye",
    "54": "Kartın süresi dolmuş",
    "55": "Hatalı PIN",
    "57": "İşleme izin verilmiyor",
    "58": "İşleme izin verilmiyor",
    "62": "Kısıtlı kart",
    "75": "PIN deneme limiti aşıldı",
    "91": "Banka servisi kullanılamıyor",
    "96": "Sistem hatası",
    "98": "İşlem iptal edildi",
    "99": "Banka kodu hatalı",
}

TURKISH_TRANSLATION = str.maketrans({
    "ş": "s", "Ş": "S",
    "ı": "i", "İ": "I",
    "ğ": "g", "Ğ": "G",
    "ç": "c", "Ç": "C",
    "ö": "o", "Ö": "O",
    "ü": "u", "Ü": "U",
})


class CasProError(Exception):
    """CasPro bulutuyla ilgili genel hata."""


class CasProDeviceOffline(CasProError):
    """Bulut `404 Device ... not connected` dondu; cihaz bagli degil."""


class CasProUnavailable(CasProError):
    """Ag hatasi, HTTP zaman asimi veya beklenmeyen HTTP durum kodu."""


@dataclass(frozen=True)
class CasProSettings:
    api_url: str = DEFAULT_CASPRO_API_URL
    api_key: str = ""
    terminal_serial: str = ""
    transaction_timeout: float = DEFAULT_TRANSACTION_TIMEOUT_SECONDS
    result_timeout: float = DEFAULT_RESULT_TIMEOUT_SECONDS


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def caspro_settings() -> CasProSettings | None:
    """CASPRO_API_URL doluysa odemeler Pavo yerine dogrudan Castles bulutuna gider."""
    api_url = os.getenv("CASPRO_API_URL", "").strip().rstrip("/")
    if not api_url:
        return None
    return CasProSettings(
        api_url=api_url,
        api_key=os.getenv("CASPRO_API_KEY", "").strip(),
        terminal_serial=os.getenv("CASPRO_TERMINAL_SERIAL", "").strip(),
        transaction_timeout=_env_float("CASPRO_TRANSACTION_TIMEOUT_SECONDS", DEFAULT_TRANSACTION_TIMEOUT_SECONDS),
        result_timeout=_env_float("CASPRO_RESULT_TIMEOUT_SECONDS", DEFAULT_RESULT_TIMEOUT_SECONDS),
    )


def normalize_caspro_serial(serial: str | None) -> str:
    """Rakam disini atar, son 12 haneyi dondurur (0000136250609467 -> 136250609467)."""
    digits = re.sub(r"\D", "", serial or "")
    return digits[-12:]


def ascii_print_line(text: str | None) -> str:
    """Fis satiri: Turkce karakterler sadelestirilir, ASCII disi atilir, 40 karaktere kirpilir."""
    translated = (text or "").translate(TURKISH_TRANSLATION)
    decomposed = unicodedata.normalize("NFKD", translated)
    ascii_only = "".join(ch for ch in decomposed if 32 <= ord(ch) < 127)
    return ascii_only.strip()[:PRINT_LINE_MAX_LENGTH]


def to_kurus(amount: Decimal | str | int | float) -> int:
    """13,00 TL -> 1300. Decimal ile ROUND_HALF_UP; asla float*100 yapilmaz."""
    if isinstance(amount, float):
        amount = Decimal(str(amount))
    value = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return int((value * 100).to_integral_value(rounding=ROUND_HALF_UP))


def new_transaction_id() -> str:
    return f"{TRANSACTION_PREFIX}{uuid4().hex[:24]}"


def new_client_request_id() -> str:
    return uuid4().hex


def caspro_timestamp(moment: datetime | None = None) -> str:
    moment = moment or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _base_message(message_type: str, serial: str) -> dict[str, Any]:
    return {
        "messageType": message_type,
        "clientRequestId": new_client_request_id(),
        "DeviceSerialNumber": normalize_caspro_serial(serial),
    }


def build_sale(
    serial: str,
    amount_kurus: int,
    ext_transaction_id: str,
    print_lines: list[str] | None = None,
    *,
    currency: str = "TRY",
    timestamp: str | None = None,
    is_print_slips: bool = True,
) -> dict[str, Any]:
    return {
        **_base_message("Transaction", serial),
        "transactionType": "Sale",
        "amount": int(amount_kurus),
        "currency": currency,
        "printLine": [ascii_print_line(line) for line in (print_lines or []) if ascii_print_line(line)],
        "timestamp": timestamp or caspro_timestamp(),
        "isContactEnabled": True,
        "isContactlessEnabled": True,
        "isMagneticEnabled": True,
        "isMkeEnabled": False,
        "isQRCodeEnabled": True,
        "originalBankReferenceCode": "",
        # Castles kiosk uygulamasinda dogrulanmis ornek: tek cekim installment=1 ile gonderilir.
        "installment": 1,
        "extTransactionId": ext_transaction_id,
        "isPrintSlips": is_print_slips,
    }


REVERSAL_TYPES = ("Void", "Refund", "Refundunmatched")


def build_reversal(
    transaction_type: str,
    serial: str,
    amount_kurus: int,
    ext_transaction_id: str,
    original_bank_reference_code: str | None = None,
    print_lines: list[str] | None = None,
) -> dict[str, Any]:
    """Satis iptali/iadesi: Sale ile ayni Transaction mesaji; yalniz transactionType ve referans degisir.

    Void = ayni gun tam iptal, Refund = (kismi) iade; ikisi de satisin bankReferenceNumber'ini
    `originalBankReferenceCode` olarak tasir. Referans yoksa Refundunmatched (eslenik siz iade) gonderilir (Kebo).
    """
    if transaction_type not in REVERSAL_TYPES:
        raise ValueError(f"Gecersiz iade turu: {transaction_type}")
    reference = (original_bank_reference_code or "").strip()
    if transaction_type in ("Void", "Refund") and not reference:
        raise ValueError(f"{transaction_type} icin originalBankReferenceCode zorunlu")
    message = build_sale(serial, amount_kurus, ext_transaction_id, print_lines)
    message["transactionType"] = transaction_type
    message["originalBankReferenceCode"] = reference if transaction_type != "Refundunmatched" else ""
    return message


def build_void(serial: str, amount_kurus: int, ext_transaction_id: str, original_bank_reference_code: str,
               print_lines: list[str] | None = None) -> dict[str, Any]:
    return build_reversal("Void", serial, amount_kurus, ext_transaction_id, original_bank_reference_code, print_lines)


def build_refund(serial: str, amount_kurus: int, ext_transaction_id: str, original_bank_reference_code: str | None,
                 print_lines: list[str] | None = None) -> dict[str, Any]:
    transaction_type = "Refund" if (original_bank_reference_code or "").strip() else "Refundunmatched"
    return build_reversal(transaction_type, serial, amount_kurus, ext_transaction_id, original_bank_reference_code, print_lines)


def build_query(serial: str, ext_transaction_id: str) -> dict[str, Any]:
    return {**_base_message("QueryTransaction", serial), "extTransactionId": ext_transaction_id}


def build_status(serial: str, ext_transaction_id: str) -> dict[str, Any]:
    return {**_base_message("GetTransactionStatus", serial), "transactionId": ext_transaction_id}


def build_cancel(serial: str) -> dict[str, Any]:
    return _base_message("CancelTransaction", serial)


def build_print(serial: str, lines: list[dict[str, str] | str]) -> dict[str, Any]:
    formatted = []
    for line in lines:
        if isinstance(line, str):
            line = {"text": line}
        formatted.append({
            "text": ascii_print_line(line.get("text")),
            "align": line.get("align", "LEFT"),
            "fontSize": line.get("fontSize", "NORMAL"),
        })
    return {**_base_message("Print", serial), "lines": formatted}


def build_pos_information(serial: str) -> dict[str, Any]:
    return {**_base_message("GetPosInformation", serial), "timestamp": ""}


def _default_client_factory(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))


# Testler httpx.MockTransport ile sahte bulut enjekte etmek icin bu kancayi degistirir.
_client_factory: Callable[[float], httpx.AsyncClient] = _default_client_factory


async def send_command(settings: CasProSettings, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
    """Tek komutu buluta gonderir; sozluk dondurur.

    404 "not connected" -> CasProDeviceOffline, ag/HTTP hatasi -> CasProUnavailable.
    """
    message_type = str(payload.get("messageType", ""))
    headers = {"Accept": "application/json"}
    if settings.api_key:
        headers["X-API-Key"] = settings.api_key
    effective_timeout = timeout if timeout is not None else COMMAND_TIMEOUT_SECONDS
    try:
        async with _client_factory(effective_timeout) as client:
            response = await client.post(f"{settings.api_url}{SEND_COMMAND_PATH}", json=payload, headers=headers)
    except httpx.HTTPError as error:
        logger.warning("CasPro %s gonderilemedi: %s", message_type, type(error).__name__)
        raise CasProUnavailable(f"CasPro {message_type} isteği başarısız: {type(error).__name__}") from error
    try:
        body = response.json() if response.content else {}
    except ValueError:
        body = {"detail": response.text[:200]}
    if not isinstance(body, dict):
        body = {"data": body}
    if response.status_code == 404 and "not connected" in str(body.get("detail", "")).lower():
        logger.warning("CasPro %s: cihaz bagli degil", message_type)
        raise CasProDeviceOffline(DEVICE_OFFLINE_MESSAGE)
    if response.status_code >= 400:
        logger.warning("CasPro %s HTTP %s", message_type, response.status_code)
        raise CasProUnavailable(f"CasPro {message_type} HTTP {response.status_code}")
    logger.info(
        "CasPro %s yanit: responseCode=%s status=%s",
        message_type, body.get("responseCode"), body.get("status", body.get("transactionStatus")),
    )
    return body


def decline_message(response_code: Any, message: Any = None) -> str:
    text = str(message or "").strip()
    if text:
        return text
    code = str(response_code or "").strip()
    return RESPONSE_CODE_MESSAGES.get(code) or f"Kart işlemi reddedildi (kod {code or '?'})"


def _first_text(response: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = response.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def result_details(response: dict[str, Any]) -> dict[str, Any]:
    """Onayli satista kayda yazilacak alanlar (ham slipData saklanmaz)."""
    return {
        "rrn": _first_text(response, "rrn"),
        "authCode": _first_text(response, "authCode", "approvalCode"),
        "bankReferenceNumber": _first_text(response, "bankReferenceNumber", "bankReferenceNo"),
        "maskedPan": _first_text(response, "maskedPan", "cardNumber"),
        "cardBrand": _first_text(response, "cardBrand"),
        "acquirerId": _first_text(response, "acquirerId"),
        "batchNumber": _first_text(response, "batchNumber"),
        "stan": _first_text(response, "stan"),
        "approvalTimestamp": _first_text(response, "approvalTimestamp", "dateTime"),
        "responseCode": _first_text(response, "responseCode"),
        "providerMessage": _first_text(response, "message"),
    }


def is_final_response(response: dict[str, Any]) -> bool:
    """responseCode var ve (rrn veya authCode veya message) dolu ise nihai yanittir."""
    if response.get("responseCode") in (None, ""):
        return False
    return any(response.get(key) not in (None, "") for key in ("rrn", "authCode", "message"))


def interpret_transaction_response(response: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]] | None:
    """Transaction yaniti -> (COMPLETED|FAILED, mesaj, detaylar); nihai degilse None."""
    if not is_final_response(response):
        return None
    code = str(response.get("responseCode") or "").strip()
    details = result_details(response)
    if code == "00":
        return "COMPLETED", None, details
    return "FAILED", decline_message(code, response.get("message")), details


def interpret_query_response(response: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]] | None:
    """QueryTransaction yaniti -> sonuc; kayit yoksa/henuz sonuc yoksa None."""
    status = str(response.get("transactionStatus") or "").strip().upper()
    details = result_details(response)
    if status == "APPROVED":
        return "COMPLETED", None, details
    if status == "DECLINED":
        return "FAILED", decline_message(response.get("responseCode"), response.get("message")), details
    return None
