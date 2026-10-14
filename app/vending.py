"""Kahve otomati (MDB kopru cihazi) entegrasyonu - Magic Coffee.

MagicPay Kiosk platformundaki (app/vending.py) mantigin Magic Coffee'ye uyarlanmis hali. Magic Coffee tek kiosklu
ve durumu bellekte tutup DB'ye yazan tek pod'luk bir servis oldugundan otomat kayitlari da bellekte tutulur ve her
degisiklikte PostgreSQL tablolarina (coffee_machines, coffee_machine_products, coffee_vend_jobs) ya da yerel dosya
modunda JSON durumuna yazilir.

Akis: odeme Castles POS'ta (CasPro) tamamlanip siparis olusunca, aktif otomat icin sepetteki her urun ADEDI bir
"vend isi" olur. Kopru (Raspberry Pi + Qibixx MDB karti) isleri `/api/machine/agent/*` uclarindan uzun sorguyla alir,
makineye urunu verdirir ve sonucu bildirir. Basarisiz / zaman asimina ugrayan islerin tutari otomatik iade edilir:
ayni gun tam siparis icin Transaction/Void, aksi halde Transaction/Refund (referans yoksa Refundunmatched).

Kurallar:
- Aktif otomat yoksa (ya da hic eslemesi yoksa) kiosk bugunku gibi calisir; bu modul hicbir alani degistirmez.
- Otomatli kiosk yalnizca otomatta eslemesi olan urun/varyantlari satar ("ne secildiyse makine onu yapar").
- Kioskta ayni anda tek aktif otomat kullanilir.
"""
from __future__ import annotations

import asyncio
import hashlib
import itertools
import json
import logging
import os
import re
import secrets
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, ValidationError, field_validator

from . import caspro
from .caspro import CasProDeviceOffline, CasProUnavailable, caspro_settings

logger = logging.getLogger("vending")
router = APIRouter()

# ---- sabitler ---------------------------------------------------------------------------

MACHINE_MODES = ("AUTO", "REMOTE", "CREDIT")
DETECTED_MODES = ("REMOTE", "CREDIT")
QUEUED = "QUEUED"
ACTIVE_STATUSES = ("SENT", "WAITING_SELECTION", "DISPENSING")
FAILURE_STATUSES = ("FAILED", "EXPIRED")
REFUNDED = "REFUNDED"
REFUND_FAILED = "REFUND_FAILED"
SUCCESS = "SUCCESS"
EVENT_RANK = {"SENT": 1, "WAITING_SELECTION": 2, "DISPENSING": 3}

VEND_IN_PROGRESS = "IN_PROGRESS"
VEND_ALL_DONE = "ALL_DONE"
VEND_PARTIAL_REFUNDED = "PARTIAL_REFUNDED"
VEND_REFUNDED = "REFUNDED"
VEND_NEEDS_ATTENTION = "NEEDS_ATTENTION"

MAX_OPTION_COMBINATIONS = 500
SELECTION_MAX = 65534  # MDB urun numarasi; 0xFFFF "bilinmiyor" demektir.

UNMAPPED_PRODUCT_MESSAGE = "Bu üründen bu kioskta satış yapılamaz."
UNMAPPED_VARIANT_MESSAGE = "Bu seçim bu otomatta hazırlanamaz; lütfen başka bir seçenek seçin."
KEY_MISMATCH_MESSAGE = "Otomat seçim anahtarı sepetteki seçimle uyuşmuyor; lütfen ürünü yeniden seçin."
CREDIT_MODE_WARNING = "Kredi modu: müşteri makinede ürün düğmesine basmalı (kiosk numarayı gösterir)."
ACTIVE_TAKEN = "Kioska bağlı başka bir aktif otomat var; önce onu pasif yapın."
STORE_ERROR = "Otomat kaydı veritabanına yazılamadı."

# Magic Coffee tek kiosklu: kiosk kimligi X-MagicCoffee-Kiosk-Fingerprint; otomat kaydinda bu sabit kimlik gorunur.
KIOSK_ID = "magiccoffee"
KIOSK_NAME = "Magic Coffee Kiosk"

# Testler kucuk degerlerle degistirir.
SWEEP_INTERVAL_SECONDS = 5.0
LONG_POLL_STEP_SECONDS = 0.25
MAX_LONG_POLL_SECONDS = 30
MACHINE_ONLINE_SECONDS = 60.0
MACHINE_SEEN_WRITE_SECONDS = 30.0
POLL_INTERVAL_MS = 1000
REFUND_STALE_SECONDS = 15 * 60.0
CASPRO_REFUND_QUERY_INTERVAL_SECONDS = 3.0
TR_TZ = timezone(timedelta(hours=3))

TOKEN_PREFIX = "mkv_"
MACHINE_ID_PATTERN = re.compile(r"^m(\d+)$")

# ---- bellekteki durum ----------------------------------------------------------------------

MACHINES: dict[str, dict[str, Any]] = {}
MAPPINGS: list[dict[str, Any]] = []
JOBS: dict[str, dict[str, Any]] = {}

BACKGROUND_TASKS: list[asyncio.Task] = []
REFUND_TASKS: dict[str, asyncio.Task] = {}
REFUND_RERUN: set[str] = set()
MACHINE_SEEN: dict[str, float] = {}
# Taramanin calismasi gereken is var mi (bekleyen/sureli/iadesi beklenen vend isi).
WORK_STATE = {"pending": False, "generation": 0}


def _core():
    from . import main  # dongusel import: main bu modulu ice aktarir

    return main


def state_lock():
    return _core().STATE_LOCK


def reset_state() -> None:
    """Testler icin: bellekteki otomat durumunu temizler."""
    MACHINES.clear()
    MAPPINGS.clear()
    JOBS.clear()
    MACHINE_SEEN.clear()
    REFUND_RERUN.clear()
    WORK_STATE.update(pending=False, generation=0)


def mark_work_pending() -> None:
    with state_lock():
        WORK_STATE["pending"] = True
        WORK_STATE["generation"] += 1


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


def vend_timeouts() -> dict[str, float]:
    """REMOTE is 90 sn, CREDIT is 120 sn (musterinin dugmeye basmasi), kopru hic almazsa 60 sn."""
    return {
        "REMOTE": _env_float("VEND_REMOTE_TIMEOUT_SECONDS", 90.0),
        "CREDIT": _env_float("VEND_CREDIT_TIMEOUT_SECONDS", 120.0),
        "pickup": _env_float("VEND_PICKUP_TIMEOUT_SECONDS", 60.0),
        # Koprunun kendi zaman asimi sonucunu bildirebilmesi icin sunucu bu kadar daha bekler.
        "grace": _env_float("VEND_REPORT_GRACE_SECONDS", 15.0),
    }


# ---- yardimcilar ------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return {}
    try:
        return json.loads(value)
    except ValueError:
        return {}


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def tl_text(value: Any) -> str:
    """92.5 -> '92,50' (kiosk mesajlari icin)."""
    return f"{money(value):.2f}".replace(".", ",")


def parse_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def age_seconds(value: Any) -> float:
    parsed = parse_iso(value)
    if parsed is None:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_machine_token() -> str:
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def payment_id_for(job_id: str) -> str:
    """C,SEL icin 8 hane hex odeme kimligi (FFFFFFFF 'bilinmiyor' anlamina geldiginden kullanilmaz)."""
    value = hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:8].upper()
    return "FFFFFFFE" if value == "FFFFFFFF" else value


def machine_online(row: dict[str, Any] | None) -> bool:
    if not row or not row.get("last_seen_at"):
        return False
    return age_seconds(row["last_seen_at"]) <= MACHINE_ONLINE_SECONDS


def effective_mode(row: dict[str, Any] | None) -> str:
    """Panelde secilen mod; AUTO ise koprunun tespit ettigi mod, tespit yoksa REMOTE."""
    mode = str((row or {}).get("mode") or "AUTO").upper()
    if mode in DETECTED_MODES:
        return mode
    detected = str((row or {}).get("detected_mode") or "").upper()
    return detected if detected in DETECTED_MODES else "REMOTE"


def machine_option_steps(row: dict[str, Any] | None) -> tuple[str, ...] | None:
    """Panelde secenek kombinasyonu listelenirken kullanilacak adimlar; None = urunun tum etkin adimlari."""
    raw = (row or {}).get("option_steps")
    if isinstance(raw, str):
        raw = loads(raw)
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        steps = tuple(dict.fromkeys(item.strip() for item in raw if item.strip()))
        return steps or None
    return None


# ---- kalicilik (PostgreSQL tablolari ya da yerel JSON durumu) ---------------------------------

SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS coffee_machines (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        kiosk_id TEXT,
        mode TEXT NOT NULL DEFAULT 'AUTO',
        detected_mode TEXT,
        token_hash TEXT,
        active BOOLEAN NOT NULL DEFAULT TRUE,
        last_seen_at TEXT,
        agent_version TEXT,
        vmc_info JSONB,
        option_steps JSONB,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    # Varyant bazli esleme: option_key secimi etkileyen ozellestirmelerin kanonik anahtari ('' = seceneksiz).
    """
    CREATE TABLE IF NOT EXISTS coffee_machine_products (
        machine_id TEXT NOT NULL,
        product_id TEXT NOT NULL,
        option_key TEXT NOT NULL DEFAULT '',
        selection_no INTEGER NOT NULL,
        price NUMERIC(12, 2),
        active BOOLEAN NOT NULL DEFAULT TRUE,
        PRIMARY KEY (machine_id, product_id, option_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS coffee_vend_jobs (
        id TEXT PRIMARY KEY,
        order_number TEXT NOT NULL,
        machine_id TEXT NOT NULL,
        product_id TEXT NOT NULL,
        selection_no INTEGER,
        amount NUMERIC(12, 2) NOT NULL,
        unit_index INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        message TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        data JSONB NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    "CREATE INDEX IF NOT EXISTS coffee_vend_jobs_order_idx ON coffee_vend_jobs (order_number)",
    "CREATE INDEX IF NOT EXISTS coffee_vend_jobs_status_idx ON coffee_vend_jobs (status)",
)

MACHINE_COLUMNS = ("id", "name", "kiosk_id", "mode", "detected_mode", "token_hash", "active", "last_seen_at",
                   "agent_version", "vmc_info", "option_steps", "created_at", "updated_at")
JOB_COLUMNS = ("id", "order_number", "machine_id", "product_id", "selection_no", "amount", "unit_index", "status",
               "message", "attempts", "created_at", "updated_at", "data")
JSON_COLUMNS = {"vmc_info", "option_steps", "data"}


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _clean_machine(row: dict[str, Any]) -> dict[str, Any]:
    record = {column: row.get(column) for column in MACHINE_COLUMNS}
    record["active"] = bool(record.get("active"))
    for column in ("vmc_info", "option_steps"):
        if isinstance(record.get(column), str):
            record[column] = loads(record[column]) or None
    return record


def _clean_mapping(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "machine_id": row["machine_id"],
        "product_id": row["product_id"],
        "option_key": row.get("option_key") or "",
        "selection_no": int(row["selection_no"]),
        "price": _number(row.get("price")),
        "active": bool(row.get("active", True)),
    }


def _clean_job(row: dict[str, Any]) -> dict[str, Any]:
    record = {column: row.get(column) for column in JOB_COLUMNS}
    record["amount"] = _number(record.get("amount")) or 0.0
    record["selection_no"] = int(record["selection_no"]) if record.get("selection_no") is not None else None
    record["unit_index"] = int(record.get("unit_index") or 0)
    record["attempts"] = int(record.get("attempts") or 0)
    record["data"] = loads(record.get("data")) or {}
    return record


def ensure_schema(conn) -> None:
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)


def load_from_database(conn) -> None:
    """Baslangicta tablolari (idempotent) olusturur ve kayitlari bellege yukler."""
    ensure_schema(conn)
    reset_state()
    for row in conn.execute("SELECT * FROM coffee_machines").fetchall():
        MACHINES[row["id"]] = _clean_machine(row)
    MAPPINGS.extend(_clean_mapping(row) for row in conn.execute("SELECT * FROM coffee_machine_products").fetchall())
    for row in conn.execute("SELECT * FROM coffee_vend_jobs").fetchall():
        JOBS[row["id"]] = _clean_job(row)


def load_from_data(data: dict[str, Any]) -> None:
    """Yerel dosya modu: JSON durumundaki otomat kayitlari."""
    reset_state()
    for row in data.get("coffeeMachines") or []:
        if isinstance(row, dict) and row.get("id"):
            MACHINES[row["id"]] = _clean_machine(row)
    MAPPINGS.extend(_clean_mapping(row) for row in data.get("coffeeMachineProducts") or [] if isinstance(row, dict))
    for row in data.get("coffeeVendJobs") or []:
        if isinstance(row, dict) and row.get("id"):
            JOBS[row["id"]] = _clean_job(row)


def export_state() -> dict[str, Any]:
    """Yerel dosya modunda save_state'in JSON'a ekledigi otomat kayitlari."""
    return {
        "coffeeMachines": deepcopy(list(MACHINES.values())),
        "coffeeMachineProducts": deepcopy(MAPPINGS),
        "coffeeVendJobs": deepcopy(list(JOBS.values())),
    }


def has_pending_work() -> bool:
    statuses = {QUEUED, *ACTIVE_STATUSES, *FAILURE_STATUSES}
    return any(job["status"] in statuses for job in JOBS.values())


def _db_params(record: dict[str, Any], columns: tuple[str, ...]) -> list[Any]:
    return [dumps(record.get(column)) if column in JSON_COLUMNS and record.get(column) is not None else record.get(column)
            for column in columns]


def _upsert_sql(table: str, columns: tuple[str, ...]) -> str:
    values = ", ".join("%s::jsonb" if column in JSON_COLUMNS else "%s" for column in columns)
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns if column != "id")
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values}) ON CONFLICT (id) DO UPDATE SET {updates}"


def _write(operation, *, strict: bool) -> None:
    """DB modunda kaydi tabloya, yerel modda JSON durumuna yazar. strict=False ise hata loglanir (bellek gecerli kalir)."""
    core = _core()
    try:
        if core.DATABASE_URL:
            with core.connect_db() as conn:
                operation(conn)
        else:
            core.save_state()
    except Exception as error:  # noqa: BLE001
        if strict:
            logger.exception("Otomat kaydi yazilamadi")
            raise HTTPException(status_code=503, detail=STORE_ERROR) from error
        logger.exception("Otomat kaydi yazilamadi; bellekteki durum gecerli")


def persist_machine(machine: dict[str, Any], *, strict: bool = True) -> None:
    _write(lambda conn: conn.execute(_upsert_sql("coffee_machines", MACHINE_COLUMNS), _db_params(machine, MACHINE_COLUMNS)),
           strict=strict)


def persist_jobs(jobs: list[dict[str, Any]], *, strict: bool = False) -> None:
    if not jobs:
        return

    def operation(conn):
        for job in jobs:
            conn.execute(_upsert_sql("coffee_vend_jobs", JOB_COLUMNS), _db_params(job, JOB_COLUMNS))

    _write(operation, strict=strict)


def persist_mappings(machine_id: str) -> None:
    rows = [row for row in MAPPINGS if row["machine_id"] == machine_id]

    def operation(conn):
        conn.execute("DELETE FROM coffee_machine_products WHERE machine_id = %s", (machine_id,))
        for row in rows:
            conn.execute(
                "INSERT INTO coffee_machine_products (machine_id, product_id, option_key, selection_no, price, active) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (row["machine_id"], row["product_id"], row["option_key"], row["selection_no"], row["price"], row["active"]),
            )

    _write(operation, strict=True)


def persist_machine_delete(machine_id: str) -> None:
    def operation(conn):
        conn.execute("DELETE FROM coffee_machine_products WHERE machine_id = %s", (machine_id,))
        conn.execute("DELETE FROM coffee_machines WHERE id = %s", (machine_id,))

    _write(operation, strict=True)


# ---- secenek anahtari (varyant) -------------------------------------------------------------

def enabled_steps(product: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        step_id: step for step_id, step in (product.get("customization") or {}).items()
        if isinstance(step, dict) and step.get("enabled")
    }


def option_key(choices: dict[str, list[str]] | None) -> str:
    """Kiosk `variantKeyOf` ile birebir ayni kanonik anahtar: secimi olan adimlar adim kimligine gore sirali,
    adim icinde secenek kimlikleri sirali ve `,` ile, adimlar `|` ile birlesir: `milk=oat|size=large`; secimsiz "".
    """
    parts = []
    for step_id in sorted((choices or {}).keys()):
        options = sorted({str(option) for option in (choices or {}).get(step_id) or [] if str(option)})
        if options:
            parts.append(f"{step_id}={','.join(options)}")
    return "|".join(parts)


def parse_option_key(raw: str | None) -> dict[str, list[str]]:
    choices: dict[str, list[str]] = {}
    for part in (raw or "").split("|"):
        part = part.strip()
        if not part:
            continue
        step_id, separator, options = part.partition("=")
        step_id = step_id.strip()
        values = [item.strip() for item in options.split(",") if item.strip()]
        if not separator or not step_id or not values:
            raise ValueError(part)
        choices.setdefault(step_id, []).extend(values)
    return choices


def canonical_option_key(raw: str | None, product: dict[str, Any]) -> str:
    """Panelden gelen anahtari dogrular ve kanonik bicime getirir; gecersizse 400."""
    try:
        choices = parse_option_key(raw)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=f"Geçersiz seçenek anahtarı: {raw} (biçim: adim=secenek,secenek|adim=secenek)") from error
    product_steps = enabled_steps(product)
    for step_id, options in choices.items():
        step = product_steps.get(step_id)
        if not step:
            raise HTTPException(status_code=400, detail=f"{product.get('name')} ürününde '{step_id}' seçimi yok.")
        valid = {str(option.get("id")) for option in step.get("options") or [] if option.get("enabled", True)}
        unknown = [option for option in options if option not in valid]
        if unknown:
            raise HTTPException(status_code=400, detail=f"{product.get('name')} için geçersiz seçenek: {', '.join(unknown)}")
    return option_key(choices)


def combination_price(product: dict[str, Any], choices: dict[str, list[str]]) -> float:
    steps = enabled_steps(product)
    total = Decimal(str(product.get("price") or 0))
    for step_id, options in choices.items():
        deltas = {str(option.get("id")): option.get("priceDelta") or 0 for option in (steps.get(step_id) or {}).get("options") or []}
        for option in options:
            total += Decimal(str(deltas.get(option, 0)))
    return float(money(total))


def option_combinations(product: dict[str, Any], steps: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    """Urunun etkin adimlarindan olusabilecek secenek anahtarlari (panelde eslemek icin; ilk kayit standart hal)."""
    per_step: list[tuple[str, list[tuple[str, ...]]]] = []
    for step_id, step in sorted(enabled_steps(product).items()):
        if steps is not None and step_id not in steps:
            continue
        options = [str(option["id"]) for option in step.get("options") or [] if option.get("enabled", True) and option.get("id")]
        if not options:
            continue
        min_select = int(step.get("minSelect") or (1 if step.get("required") else 0))
        max_select = 1 if step_id == "shot" else int(step.get("maxSelect") or 1)
        max_select = max(1, min(max_select, len(options)))
        choices: list[tuple[str, ...]] = []
        if min_select == 0:
            choices.append(())
        for size in range(max(1, min_select), max_select + 1):
            choices.extend(itertools.combinations(sorted(options), size))
        per_step.append((step_id, choices))
    combos: list[dict[str, Any]] = []
    for selection in itertools.product(*[choices for _, choices in per_step]) if per_step else [()]:
        picked = {per_step[index][0]: list(values) for index, values in enumerate(selection) if values}
        combos.append({"optionKey": option_key(picked), "choices": picked, "price": combination_price(product, picked)})
        if len(combos) >= MAX_OPTION_COMBINATIONS:
            break
    return combos


def option_label(product: dict[str, Any] | None, key: str) -> str:
    if not key:
        return "Standart"
    try:
        choices = parse_option_key(key)
    except ValueError:
        return key
    labels = []
    steps = enabled_steps(product or {})
    for step_id, options in choices.items():
        names = {str(option.get("id")): str(option.get("name") or option.get("id")) for option in (steps.get(step_id) or {}).get("options") or []}
        labels.extend(names.get(option, option) for option in options)
    return " · ".join(labels) or key


# ---- kayit donusturuculer -------------------------------------------------------------------

def machine_record(row: dict[str, Any]) -> dict[str, Any]:
    mode = effective_mode(row)
    vmc_info = row.get("vmc_info") if isinstance(row.get("vmc_info"), dict) else None
    steps = machine_option_steps(row)
    machine_id = row["id"]
    jobs = [job for job in JOBS.values() if job["machine_id"] == machine_id]
    return {
        "id": machine_id,
        "name": row["name"],
        "kioskId": row.get("kiosk_id"),
        "kioskName": KIOSK_NAME if row.get("kiosk_id") else None,
        "mode": str(row.get("mode") or "AUTO"),
        "detectedMode": row.get("detected_mode"),
        "effectiveMode": mode,
        "modeWarning": CREDIT_MODE_WARNING if mode == "CREDIT" else None,
        "vendingOptionSteps": list(steps) if steps else None,
        "active": bool(row.get("active")),
        "online": machine_online(row),
        "lastSeenAt": row.get("last_seen_at"),
        "agentVersion": row.get("agent_version"),
        "agentStatus": (vmc_info or {}).get("agentStatus"),
        "vmcStatus": (vmc_info or {}).get("vmcStatus"),
        "vmcInfo": vmc_info,
        "hasToken": bool(row.get("token_hash")),
        "productCount": sum(1 for item in MAPPINGS if item["machine_id"] == machine_id and item["active"]),
        "activeJobCount": sum(1 for job in jobs if job["status"] in (QUEUED, *ACTIVE_STATUSES)),
        "attentionCount": sum(1 for job in jobs if job["status"] == REFUND_FAILED),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


JOB_MESSAGES = {
    QUEUED: "Sırada bekliyor.",
    "SENT": "Makineye gönderildi.",
    "DISPENSING": "Makine hazırlıyor…",
    SUCCESS: "Afiyet olsun!",
    "FAILED": "Ürün hazırlanamadı; iade ediliyor.",
    "EXPIRED": "Süre doldu; iade ediliyor.",
    REFUND_FAILED: "İade yapılamadı; lütfen görevliye başvurun.",
}


def job_message(status: str, data: dict[str, Any], selection_no: int | None) -> str:
    if status == "WAITING_SELECTION":
        return f"Lütfen makinede {selection_no} numaralı ürüne basın."
    if status == REFUNDED:
        return f"Ürün hazırlanamadı; {tl_text(data.get('refundAmount'))} TL kartınıza iade edildi."
    if status in FAILURE_STATUSES and (data.get("refund") or {}).get("needsCard"):
        return "Ürün hazırlanamadı; iade için lütfen kartınızı POS cihazına okutun."
    return JOB_MESSAGES.get(status, "")


def job_refund_view(data: dict[str, Any]) -> dict[str, Any] | None:
    refund = data.get("refund")
    if not isinstance(refund, dict):
        return None
    return {
        "state": refund.get("state"),
        "provider": refund.get("provider"),
        "type": refund.get("type"),
        "amount": refund.get("amount"),
        "needsCard": bool(refund.get("needsCard")),
        "message": refund.get("message"),
        "needsManualRefund": bool(refund.get("needsManualRefund")),
        "retryable": bool(refund.get("retryable")),
    }


def kiosk_job_record(row: dict[str, Any]) -> dict[str, Any]:
    data = row.get("data") or {}
    status = row["status"]
    selection_no = row.get("selection_no")
    pressed = data.get("pressedSelectionNo")
    wrong = status == "WAITING_SELECTION" and pressed is not None and pressed != selection_no
    message = row.get("message") or job_message(status, data, selection_no)
    if wrong:
        message = f"Yanlış ürün seçildi ({pressed}); lütfen makinede {selection_no} numaralı ürüne basın."
    return {
        "id": row["id"],
        "productId": row["product_id"],
        "productName": data.get("productName") or row["product_id"],
        "optionKey": data.get("optionKey") or "",
        "variantLabel": data.get("optionLabel"),
        "selectionNo": selection_no,
        "unitIndex": int(row.get("unit_index") or 0),
        "amount": data.get("refundAmount"),
        "status": status,
        "message": message,
        "wrongSelection": wrong,
        "pressedSelectionNo": pressed if wrong else None,
        "refund": job_refund_view(data),
        "updatedAt": row.get("updated_at"),
    }


def admin_job_record(row: dict[str, Any]) -> dict[str, Any]:
    data = row.get("data") or {}
    record = kiosk_job_record(row)
    record.update({
        "orderNumber": row["order_number"],
        "machineId": row["machine_id"],
        "kioskId": data.get("kioskId"),
        "optionLabel": data.get("optionLabel"),
        "vendAmount": float(row["amount"]) if row.get("amount") is not None else None,
        "attempts": int(row.get("attempts") or 0),
        "mode": data.get("mode"),
        "detail": data.get("detail"),
        "failureStatus": data.get("failureStatus"),
        "needsManualRefund": bool((data.get("refund") or {}).get("needsManualRefund")) or row["status"] == REFUND_FAILED,
        "lateSuccess": bool(data.get("lateSuccessAt")),
        "createdAt": row.get("created_at"),
        "history": data.get("refundHistory") or [],
    })
    return record


# ---- okumalar ------------------------------------------------------------------------------

def fetch_machine(machine_id: str) -> dict[str, Any] | None:
    return MACHINES.get(machine_id)


def active_machine() -> dict[str, Any] | None:
    """Kioskun kullandigi otomat: tek aktif otomat (yoksa None: kiosk bugunku gibi calisir)."""
    return next((MACHINES[key] for key in sorted(MACHINES) if MACHINES[key].get("active")), None)


def machine_mappings(machine_id: str, *, active_only: bool = True) -> dict[str, dict[str, dict[str, Any]]]:
    """product_id -> option_key -> esleme satiri."""
    mapping: dict[str, dict[str, dict[str, Any]]] = {}
    for row in sorted(MAPPINGS, key=lambda item: (item["product_id"], item["option_key"])):
        if row["machine_id"] != machine_id or (active_only and not row["active"]):
            continue
        mapping.setdefault(row["product_id"], {})[row["option_key"]] = row
    return mapping


def kiosk_machine_context() -> dict[str, Any] | None:
    """Aktif otomat ve eslemeleri. Hic eslemesi olmayan otomat (panelde kurulum suruyor) kiosku degistirmez."""
    with state_lock():
        machine = active_machine()
        if not machine:
            return None
        mappings = machine_mappings(machine["id"])
        if not mappings:
            return None
        return {"machine": dict(machine), "mappings": mappings, "steps": machine_option_steps(machine)}


def kiosk_machine_summary(context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not context:
        return None
    machine = context["machine"]
    return {
        "id": machine["id"],
        "name": machine["name"],
        "mode": effective_mode(machine),
        "online": machine_online(machine),
    }


# ---- otomatli kiosk: yalnizca eslenmis urun/varyantlar ----------------------------------------

def product_variants(context: dict[str, Any], product_id: str) -> list[dict[str, Any]]:
    rows = context["mappings"].get(product_id) or {}
    return [
        {"optionKey": key, "selectionNo": int(row["selection_no"]), "price": row.get("price")}
        for key, row in sorted(rows.items())
    ]


def primary_selection(context: dict[str, Any] | None, product_id: str) -> int | None:
    """Standart (secimsiz, optionKey "") varyantin makine numarasi; yoksa null."""
    if not context:
        return None
    row = (context["mappings"].get(product_id) or {}).get("")
    return int(row["selection_no"]) if row else None


def mapped_options(context: dict[str, Any], product_id: str) -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    for key in (context["mappings"].get(product_id) or {}):
        try:
            for step_id, options in parse_option_key(key).items():
                used.setdefault(step_id, set()).update(options)
        except ValueError:
            continue
    return used


def filter_customization(product: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Yalnizca en az bir eslenmis varyantta gecen secenekler kalir; hic secenegi kalmayan adim kapatilir.
    Gecerli kombinasyonlar `machineVariants` ile birebir belirlenir (kiosk yalniz onlari sectirir)."""
    used = mapped_options(context, str(product["id"]))
    filtered: dict[str, Any] = {}
    for step_id, step in (product.get("customization") or {}).items():
        if not isinstance(step, dict) or not step.get("enabled"):
            filtered[step_id] = step
            continue
        step = dict(step)
        allowed = used.get(step_id, set())
        options = [dict(option) for option in step.get("options") or [] if str(option.get("id")) in allowed]
        step["options"] = options
        step["enabled"] = bool(options)
        filtered[step_id] = step
    return filtered


def apply_machine_catalog(catalog: dict[str, Any], context: dict[str, Any] | None) -> dict[str, Any]:
    """`/api/catalog` sonucu: otomatsiz kioskta degismez (machineSelection alani yok); otomatli kioskta yalnizca
    eslenmis urunler, secenek filtreleri, machineSelection ve machineVariants doner."""
    if not context:
        return catalog
    products = []
    for product in catalog.get("products") or []:
        product_id = str(product["id"])
        if not context["mappings"].get(product_id):
            continue
        product = {**product, "customization": filter_customization(product, context)}
        product["machineSelection"] = primary_selection(context, product_id)
        product["machineVariants"] = product_variants(context, product_id)
        products.append(product)
    counts: dict[str, int] = {}
    for product in products:
        counts[product["categoryId"]] = counts.get(product["categoryId"], 0) + 1
    catalog["products"] = products
    catalog["categories"] = [{**item, "productCount": counts[item["id"]]} for item in catalog.get("categories") or [] if counts.get(item["id"])]
    catalog["machineOnly"] = True
    catalog["machine"] = kiosk_machine_summary(context)
    return catalog


def resolve_machine_line(context: dict[str, Any], product: dict[str, Any], choices: dict[str, list[str]] | None,
                         sent_key: str | None = None) -> tuple[str, dict[str, Any]]:
    """(urun, secim) -> (option_key, esleme). Kioskun gonderdigi machineOptionKey secimle uyusmali; eslenmemisse 409."""
    key = option_key(choices)
    if sent_key is not None:
        try:
            sent = option_key(parse_option_key(sent_key))
        except ValueError:
            sent = None
        if sent != key:
            raise HTTPException(status_code=409, detail=KEY_MISMATCH_MESSAGE)
    rows = context["mappings"].get(str(product["id"])) or {}
    if not rows:
        raise HTTPException(status_code=409, detail=UNMAPPED_PRODUCT_MESSAGE)
    mapping = rows.get(key)
    if mapping is None:
        raise HTTPException(status_code=409, detail=UNMAPPED_VARIANT_MESSAGE)
    return key, mapping


def line_choices(line: Any) -> dict[str, list[str]]:
    selection = line.get("selection") if isinstance(line, dict) else getattr(line, "selection", None)
    if selection is None:
        return {}
    if isinstance(selection, dict):
        return dict(selection.get("choices") or {})
    return dict(getattr(selection, "choices", {}) or {})


def line_sent_key(line: Any) -> str | None:
    value = line.get("machineOptionKey") if isinstance(line, dict) else getattr(line, "machineOptionKey", None)
    return None if value is None else str(value)


def machine_line_price(context: dict[str, Any] | None, product: dict[str, Any], line: Any, *, strict: bool) -> tuple[str | None, float | None]:
    """Otomatli kioskta satirin varyant anahtari ve (eslemede tanimliysa) satis fiyati.

    strict=True (odeme): eslenmemis urun/varyant 409. strict=False (odenmis siparis): 409 verilmez; is olusurken
    eslemesiz kalem FAILED olur ve iade edilir.
    """
    if not context:
        return None, None
    try:
        key, mapping = resolve_machine_line(context, product, line_choices(line), line_sent_key(line))
    except HTTPException:
        if strict:
            raise
        return option_key(line_choices(line)), None
    return key, float(mapping["price"]) if mapping.get("price") is not None else None


def validate_machine_cart(lines: list[Any]) -> None:
    """Odeme baslamadan: otomatli kioskta sepetteki her urun/varyantin eslemesi olmali (yoksa 409; POS'a gidilmez)."""
    context = kiosk_machine_context()
    if not context:
        return
    products = {str(item["id"]): item for item in _core().products}
    for line in lines:
        product = products.get(str(line.get("productId") if isinstance(line, dict) else line.productId))
        if product:
            machine_line_price(context, product, line, strict=True)


# ---- vend islerinin olusturulmasi (siparis olusturulurken, STATE_LOCK altinda) -----------------

def create_vend_jobs(order_number: str, lines: list[dict[str, Any]], payment_reference: str, now: str,
                     context: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Siparisteki her urun ADEDI icin QUEUED is olusturur (otomatli kioskta tum urunler otomattan verilir).

    Odeme zaten alinmis oldugundan burada 409 verilmez: eslemesi (odeme sirasinda degismis) kalmayan
    kalem icin is dogrudan FAILED olusturulur ve otomatik iade edilir. Donus: (siparis ozeti, yeni isler).
    """
    if not context:
        return None, []
    machine = context["machine"]
    catalog = {str(item["id"]): item for item in _core().products}
    created: list[dict[str, Any]] = []
    failed = 0
    unit_index = 0
    for line_index, line in enumerate(lines):
        product = catalog.get(line["productId"]) or {"id": line["productId"], "name": line.get("name")}
        key = line.get("machineOptionKey")
        if key is None:
            key = option_key(line_choices(line))
        mapping = (context["mappings"].get(line["productId"]) or {}).get(key)
        paid = money(line.get("unitPrice"))
        for _ in range(int(line.get("quantity") or 1)):
            data = {
                "orderNumber": order_number,
                "kioskId": KIOSK_ID,
                "productName": line.get("name") or product.get("name") or line["productId"],
                "optionKey": key,
                "optionLabel": option_label(product, key) if key else None,
                "lineIndex": line_index,
                "refundAmount": float(paid),
                "paymentReference": payment_reference,
                "selection": line.get("selection"),
            }
            status = QUEUED
            selection_no = None
            message = None
            if mapping is None:
                status = "FAILED"
                message = UNMAPPED_VARIANT_MESSAGE
                data.update({"detail": "Esleme bulunamadi", "failureStatus": "FAILED"})
                failed += 1
            else:
                selection_no = int(mapping["selection_no"])
            job = {
                "id": f"vj-{uuid4().hex[:20]}", "order_number": order_number, "machine_id": machine["id"],
                "product_id": line["productId"], "selection_no": selection_no, "amount": float(paid),
                "unit_index": unit_index, "status": status, "message": message, "attempts": 0,
                "created_at": now, "updated_at": now, "data": data,
            }
            JOBS[job["id"]] = job
            created.append(job)
            unit_index += 1
    if not created:
        return None, []
    return {"machineId": machine["id"], "jobCount": len(created), "rejectedCount": failed}, created


def notify_machine(machine_id: str | None) -> None:
    if machine_id:
        mark_work_pending()


# ---- siparis vend durumu ---------------------------------------------------------------------

def compute_vend_status(rows: list[dict[str, Any]]) -> str:
    statuses = [row["status"] for row in rows]
    if not statuses:
        return VEND_ALL_DONE
    if any(status == REFUND_FAILED for status in statuses):
        if all(status in (SUCCESS, REFUNDED, REFUND_FAILED) for status in statuses):
            return VEND_NEEDS_ATTENTION
    if any(status not in (SUCCESS, REFUNDED, REFUND_FAILED) for status in statuses):
        return VEND_IN_PROGRESS
    if all(status == SUCCESS for status in statuses):
        return VEND_ALL_DONE
    if all(status == REFUNDED for status in statuses):
        return VEND_REFUNDED
    return VEND_PARTIAL_REFUNDED


def order_jobs(order_number: str) -> list[dict[str, Any]]:
    return sorted((job for job in JOBS.values() if job["order_number"] == order_number),
                  key=lambda job: (job["unit_index"], job["id"]))


def refresh_order_vend(order_number: str, *, save: bool = True) -> str | None:
    """Siparis kaydindaki vendStatus / iade ozetini isler uzerinden gunceller (STATE_LOCK altinda)."""
    core = _core()
    rows = order_jobs(order_number)
    order = core.orders.get(order_number)
    if not order or not rows:
        return None
    status = compute_vend_status(rows)
    refunded = sum(money((row.get("data") or {}).get("refundAmount")) for row in rows if row["status"] == REFUNDED)
    order.update({
        "vendStatus": status,
        "vendRefundedAmount": float(refunded),
        "needsManualRefund": any(row["status"] == REFUND_FAILED for row in rows),
        "vendUpdatedAt": utc_now_iso(),
    })
    if save:
        try:
            core.save_state()
        except Exception:  # noqa: BLE001 - siparis ozeti bir sonraki kayitta yazilir
            logger.exception("Siparis vend durumu kaydedilemedi")
    return status


def save_job(job: dict[str, Any], status: str, data: dict[str, Any], message: str | None, *, attempts: int | None = None) -> None:
    job.update({"status": status, "message": message, "data": data, "updated_at": utc_now_iso()})
    if attempts is not None:
        job["attempts"] = attempts


# ---- kopru (agent) kimlik dogrulama ---------------------------------------------------------------

def agent_machine(request: Request) -> dict[str, Any]:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Makine anahtarı gerekli.", headers={"WWW-Authenticate": "Bearer"})
    digest = token_hash(token)
    with state_lock():
        row = next((item for item in MACHINES.values() if item.get("token_hash") == digest), None)
    if not row:
        raise HTTPException(status_code=401, detail="Makine anahtarı geçersiz.", headers={"WWW-Authenticate": "Bearer"})
    if not row.get("active"):
        raise HTTPException(status_code=403, detail="Otomat pasif durumda.")
    return row


def touch_machine(machine_id: str) -> None:
    """Son gorulme bellekte her istekte, DB'de en fazla MACHINE_SEEN_WRITE_SECONDS'ta bir guncellenir."""
    now = time.monotonic()
    with state_lock():
        machine = MACHINES.get(machine_id)
        if not machine:
            return
        machine["last_seen_at"] = utc_now_iso()
        last = MACHINE_SEEN.get(machine_id)
        if last is not None and now - last < MACHINE_SEEN_WRITE_SECONDS:
            return
        MACHINE_SEEN[machine_id] = now
        persist_machine(machine, strict=False)


class AgentHello(BaseModel):
    agentVersion: str | None = Field(default=None, max_length=40)
    detectedMode: Literal["REMOTE", "CREDIT"] | None = None
    vmcInfo: dict[str, Any] | None = None


class AgentEvent(BaseModel):
    status: Literal["SENT", "WAITING_SELECTION", "DISPENSING", "SUCCESS", "FAILED"]
    detail: str | dict[str, Any] | None = None
    # Kredi modunda musterinin bastigi (eslesmeyen) urun numarasi; detail icinde de gelebilir.
    pressedSelectionNo: int | None = None

    def detail_text(self) -> str | None:
        if isinstance(self.detail, dict):
            text = self.detail.get("message") or self.detail.get("detail")
            return str(text)[:500] if text else dumps(self.detail)[:500]
        return self.detail[:500] if self.detail else None

    def pressed(self) -> int | None:
        value = self.pressedSelectionNo
        if value is None and isinstance(self.detail, dict):
            value = self.detail.get("pressedSelectionNo")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


class AgentHeartbeat(BaseModel):
    status: str | None = Field(default=None, max_length=40)
    vmcStatus: str | None = Field(default=None, max_length=80)


def fail_inflight_jobs(machine_id: str, reason: str) -> list[str]:
    """Kopru yeniden basladiysa yarim kalan isin sonucu bilinmez: FAILED yapilip iade edilir."""
    with state_lock():
        changed = [job for job in JOBS.values() if job["machine_id"] == machine_id and job["status"] in ACTIVE_STATUSES]
        for job in changed:
            data = dict(job.get("data") or {})
            data.update({"detail": reason, "failureStatus": "FAILED", "failedAt": utc_now_iso()})
            save_job(job, "FAILED", data, None)
        persist_jobs(changed)
        orders = list(dict.fromkeys(job["order_number"] for job in changed))
        for order_number in orders:
            refresh_order_vend(order_number)
    return orders


def record_hello(machine_id: str, payload: AgentHello) -> dict[str, Any]:
    with state_lock():
        row = MACHINES[machine_id]
        previous = row.get("vmc_info") if isinstance(row.get("vmc_info"), dict) else {}
        now = utc_now_iso()
        row.update({
            "agent_version": (payload.agentVersion or "")[:40] or None,
            "detected_mode": payload.detectedMode or row.get("detected_mode"),
            "vmc_info": {**previous, **dict(payload.vmcInfo or {}), "helloAt": now},
            "last_seen_at": now,
            "updated_at": now,
        })
        MACHINE_SEEN[machine_id] = time.monotonic()
        persist_machine(row, strict=False)
        return dict(row)


@router.post("/api/machine/agent/hello")
async def agent_hello(payload: AgentHello, machine: dict[str, Any] = Depends(agent_machine)):
    orders = fail_inflight_jobs(machine["id"], "Köprü yeniden başlatıldı; satış sonucu bilinmiyor.")
    if orders:
        mark_work_pending()
    for order_number in orders:
        schedule_order_refund(order_number)
    row = record_hello(machine["id"], payload)
    return {
        "machineId": row["id"],
        "name": row["name"],
        "mode": effective_mode(row),
        "configuredMode": row.get("mode") or "AUTO",
        "pollIntervalMs": POLL_INTERVAL_MS,
        "timeouts": {key: value for key, value in vend_timeouts().items() if key in ("REMOTE", "CREDIT")},
    }


def claim_next_job(machine_id: str) -> dict[str, Any] | None:
    """Makinede is yoksa en eski QUEUED isi SENT yapip dondurur (bir anda tek is)."""
    with state_lock():
        machine = MACHINES.get(machine_id)
        if not machine or not machine.get("active"):
            return None
        jobs = [job for job in JOBS.values() if job["machine_id"] == machine_id]
        if any(job["status"] in ACTIVE_STATUSES for job in jobs):
            return None
        queued = sorted((job for job in jobs if job["status"] == QUEUED),
                        key=lambda job: (job["created_at"], job["order_number"], job["unit_index"], job["id"]))
        if not queued:
            return None
        job = queued[0]
        mode = effective_mode(machine)
        timeout = int(vend_timeouts()[mode])
        data = dict(job.get("data") or {})
        payment_id = payment_id_for(job["id"])
        data.update({"mode": mode, "timeoutSec": timeout, "sentAt": utc_now_iso(), "paymentId": payment_id})
        save_job(job, "SENT", data, None, attempts=int(job.get("attempts") or 0) + 1)
        persist_jobs([job])
        refresh_order_vend(job["order_number"])
    amount = money(job["amount"])
    return {
        "id": job["id"],
        "orderNumber": job["order_number"],
        "productName": data.get("productName"),
        "optionKey": data.get("optionKey") or "",
        "selectionNo": int(job["selection_no"]),
        "amount": float(amount),
        "amountText": f"{amount:.2f}",
        "mode": mode,
        "timeoutSec": timeout,
        "paymentId": payment_id,
    }


@router.get("/api/machine/agent/jobs")
async def agent_jobs(wait: int = Query(default=25, ge=0, le=MAX_LONG_POLL_SECONDS), machine: dict[str, Any] = Depends(agent_machine)):
    """Uzun sorgu: is varsa hemen doner, yoksa `wait` saniye bekler ve 204 doner (kuyruk bellekte; DB'ye gidilmez)."""
    machine_id = machine["id"]
    deadline = time.monotonic() + wait
    touch_machine(machine_id)
    while True:
        job = claim_next_job(machine_id)
        if job:
            mark_work_pending()
            return {"job": job}
        now = time.monotonic()
        if now >= deadline:
            return Response(status_code=204)
        await asyncio.sleep(max(0.0, min(LONG_POLL_STEP_SECONDS, deadline - now)))


def apply_agent_event(machine_id: str, job_id: str, status: str, detail: str | None, pressed: int | None = None) -> dict[str, Any]:
    with state_lock():
        job = JOBS.get(job_id)
        if not job or job["machine_id"] != machine_id:
            raise HTTPException(status_code=404, detail="Vend işi bulunamadı.")
        current = job["status"]
        data = dict(job.get("data") or {})
        order_number = job["order_number"]
        if current == QUEUED:
            raise HTTPException(status_code=409, detail="İş henüz köprüye verilmedi.")
        if current not in ACTIVE_STATUSES:
            if status == SUCCESS and current != SUCCESS:
                # Zaman asimindan/iadeden sonra gelen basari: urun verilmis olabilir; panelde gorunur.
                data.update({"lateSuccessAt": utc_now_iso(), "lateSuccessDetail": detail})
                save_job(job, current, data, job.get("message"))
                persist_jobs([job])
                logger.warning("Vend isi %s %s durumundayken SUCCESS bildirildi", job_id, current)
            return {"accepted": False, "status": current, "orderNumber": order_number, "terminal": False}
        if status == "WAITING_SELECTION" and current == "WAITING_SELECTION":
            # Kredi modunda yanlis dugme: kiosk "yanlis urun" uyarisi gosterir; is beklemeye devam eder.
            data.update({"detail": detail, "pressedSelectionNo": pressed, "pressedAt": utc_now_iso() if pressed is not None else None})
            save_job(job, current, data, None)
            persist_jobs([job])
            return {"accepted": True, "status": current, "orderNumber": order_number, "terminal": False}
        if status in EVENT_RANK and EVENT_RANK[status] <= EVENT_RANK.get(current, 0):
            return {"accepted": status == current, "status": current, "orderNumber": order_number, "terminal": False}
        data["detail"] = detail
        data["pressedSelectionNo"] = pressed if status == "WAITING_SELECTION" else None
        events = list(data.get("events") or [])
        events.append({"status": status, "detail": detail, "pressedSelectionNo": pressed, "at": utc_now_iso()})
        data["events"] = events[-20:]
        terminal = status in (SUCCESS, "FAILED")
        if status == "FAILED":
            data.update({"failureStatus": "FAILED", "failedAt": utc_now_iso()})
        if status == SUCCESS:
            data["succeededAt"] = utc_now_iso()
        save_job(job, status, data, None)
        persist_jobs([job])
        vend_status = refresh_order_vend(order_number)
    return {"accepted": True, "status": status, "orderNumber": order_number, "terminal": terminal, "vendStatus": vend_status}


@router.post("/api/machine/agent/jobs/{job_id}/events")
async def agent_job_event(job_id: str, payload: AgentEvent, machine: dict[str, Any] = Depends(agent_machine)):
    result = apply_agent_event(machine["id"], job_id, payload.status, payload.detail_text(), payload.pressed())
    touch_machine(machine["id"])
    if result.get("terminal"):
        mark_work_pending()
        schedule_order_refund(result["orderNumber"])
    return {key: value for key, value in result.items() if key != "terminal"}


@router.post("/api/machine/agent/heartbeat")
async def agent_heartbeat(payload: AgentHeartbeat, machine: dict[str, Any] = Depends(agent_machine)):
    with state_lock():
        row = MACHINES[machine["id"]]
        info = dict(row.get("vmc_info") or {}) if isinstance(row.get("vmc_info"), dict) else {}
        info.update({"agentStatus": payload.status, "vmcStatus": payload.vmcStatus, "heartbeatAt": utc_now_iso()})
        row.update({"vmc_info": info, "last_seen_at": utc_now_iso()})
        MACHINE_SEEN[row["id"]] = time.monotonic()
        persist_machine(row, strict=False)
    return {"ok": True, "mode": effective_mode(row)}


# ---- kiosk: vend izleme ---------------------------------------------------------------------------

@router.get("/api/orders/{order_number}/vend")
def order_vend(order_number: str):
    core = _core()
    with state_lock():
        order = core.orders.get(order_number)
        if not order:
            raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
        rows = [dict(job) for job in order_jobs(order_number)]
        machine = MACHINES.get(rows[0]["machine_id"]) if rows else active_machine()
        machine = dict(machine) if machine else None
        order = dict(order)
    status = compute_vend_status(rows) if rows else None
    return {
        "orderNumber": order_number,
        # Kiosk sozlesmesi: is/iade surerken null; bitince ALL_DONE | PARTIAL_REFUNDED | REFUNDED | NEEDS_ATTENTION.
        "vendStatus": status if status != VEND_IN_PROGRESS else None,
        "machineId": machine["id"] if machine else None,
        "machineName": machine["name"] if machine else None,
        "mode": effective_mode(machine) if machine else None,
        "machineOnline": machine_online(machine),
        "refundedAmount": order.get("vendRefundedAmount", 0.0) if rows else 0.0,
        "needsManualRefund": bool(order.get("needsManualRefund")) if rows else False,
        "done": not rows or status != VEND_IN_PROGRESS,
        "jobs": [kiosk_job_record(row) for row in rows],
    }


# ---- zaman asimi taramasi ---------------------------------------------------------------------------

def expire_stale_jobs() -> tuple[list[str], bool]:
    """Suresi dolan isleri EXPIRED yapar; iade bekleyen siparis numaralarini ve bekleyen is olup olmadigini dondurur."""
    timeouts = vend_timeouts()
    orders: list[str] = []
    changed: list[dict[str, Any]] = []
    with state_lock():
        for job in list(JOBS.values()):
            if job["status"] not in ACTIVE_STATUSES:
                continue
            data = dict(job.get("data") or {})
            limit = float(data.get("timeoutSec") or timeouts[data.get("mode") or "REMOTE"]) + timeouts["grace"]
            if age_seconds(data.get("sentAt") or job["updated_at"]) < limit:
                continue
            data.update({"detail": "Makineden sonuç alınamadı (süre doldu).", "failureStatus": "EXPIRED", "failedAt": utc_now_iso()})
            save_job(job, "EXPIRED", data, None)
            changed.append(job)
            orders.append(job["order_number"])
        by_machine: dict[str, list[dict[str, Any]]] = {}
        for job in sorted((job for job in JOBS.values() if job["status"] == QUEUED),
                          key=lambda job: (job["created_at"], job["order_number"], job["unit_index"])):
            by_machine.setdefault(job["machine_id"], []).append(job)
        for machine_id, rows in by_machine.items():
            machine = MACHINES.get(machine_id)
            machine_jobs = [job for job in JOBS.values() if job["machine_id"] == machine_id]
            usable = bool(machine and machine.get("active"))
            if usable and any(job["status"] in ACTIVE_STATUSES for job in machine_jobs):
                continue
            finished = [job["updated_at"] for job in machine_jobs if job["status"] not in (QUEUED, *ACTIVE_STATUSES)]
            eligible_since = max([rows[0]["created_at"], *finished])
            if usable and age_seconds(eligible_since) < timeouts["pickup"]:
                continue
            reason = "Otomat bulunamadı veya pasif." if not usable else "Köprü işi almadı (otomat çevrimdışı)."
            for job in rows:
                data = dict(job.get("data") or {})
                data.update({"detail": reason, "failureStatus": "EXPIRED", "failedAt": utc_now_iso()})
                save_job(job, "EXPIRED", data, None)
                changed.append(job)
                orders.append(job["order_number"])
        # Surec iadeyi gonderirken oldu: sonuc belirsiz, elle kontrol gerekir.
        for job in list(JOBS.values()):
            if job["status"] not in FAILURE_STATUSES:
                continue
            data = dict(job.get("data") or {})
            if data.get("refundState") != "SENDING":
                orders.append(job["order_number"])
                continue
            task = REFUND_TASKS.get(job["order_number"])
            if (task is not None and not task.done()) or age_seconds(data.get("refundStartedAt")) < REFUND_STALE_SECONDS:
                continue
            refund = dict(data.get("refund") or {})
            refund.update({"state": "FAILED", "needsManualRefund": True, "retryable": False, "submissionUnconfirmed": True,
                           "message": "İade sonucu doğrulanamadı; POS'tan kontrol edin."})
            data.update({"refundState": "FAILED", "refund": refund})
            save_job(job, REFUND_FAILED, data, None)
            changed.append(job)
            orders.append(job["order_number"])
        persist_jobs(changed)
        unique = list(dict.fromkeys(orders))
        for order_number in unique:
            refresh_order_vend(order_number, save=bool(changed))
        pending = has_pending_work()
    return unique, pending


async def vend_sweep_once() -> None:
    with state_lock():
        if not WORK_STATE["pending"]:
            return
        generation = WORK_STATE["generation"]
    orders, pending = expire_stale_jobs()
    for order_number in orders:
        schedule_order_refund(order_number)
    if not pending and not orders and not any(not task.done() for task in REFUND_TASKS.values()):
        with state_lock():
            if WORK_STATE["generation"] == generation:
                WORK_STATE["pending"] = False


async def run_vend_sweep() -> None:
    while True:
        try:
            await vend_sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - tarama durmamali
            logger.exception("Vend zaman asimi taramasi hata verdi")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


async def start_background() -> None:
    # Yeniden baslatmada bekleyen isler (zaman asimi / iade) varsa tarama hemen devreye girer.
    with state_lock():
        pending = has_pending_work()
    if pending:
        mark_work_pending()
    BACKGROUND_TASKS.append(asyncio.create_task(run_vend_sweep()))


async def stop_background() -> None:
    tasks = [task for task in [*BACKGROUND_TASKS, *REFUND_TASKS.values()] if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    BACKGROUND_TASKS.clear()
    REFUND_TASKS.clear()
    REFUND_RERUN.clear()
    with state_lock():
        MACHINE_SEEN.clear()
        WORK_STATE["pending"] = False


# ---- otomatik iade -------------------------------------------------------------------------------

def schedule_order_refund(order_number: str | None) -> None:
    if not order_number:
        return
    task = REFUND_TASKS.get(order_number)
    if task is not None and not task.done():
        REFUND_RERUN.add(order_number)
        return
    task = asyncio.create_task(run_order_refund(order_number))
    REFUND_TASKS[order_number] = task
    task.add_done_callback(lambda done: REFUND_TASKS.pop(order_number, None) if REFUND_TASKS.get(order_number) is done else None)


async def run_order_refund(order_number: str) -> None:
    try:
        while True:
            REFUND_RERUN.discard(order_number)
            batch = claim_refund_batch(order_number)
            if batch is None:
                if order_number in REFUND_RERUN:
                    continue
                return
            try:
                result = await perform_refund(batch)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - beklenmeyen hata: iade belirsiz, elle kontrol
                logger.exception("Vend iadesi beklenmeyen hata ile bitti (%s)", order_number)
                result = refund_failure(f"İade sırasında hata: {type(error).__name__}", manual=True, retryable=False,
                                        unconfirmed=True)
            finalize_refund_batch(batch, result)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("Vend iade gorevi hata verdi (%s)", order_number)


def find_payment(reference: str | None) -> dict[str, Any] | None:
    if not reference:
        return None
    return next(
        (item for item in _core().pos_payments.values() if reference in (item.get("externalId"), item.get("transactionId"))),
        None,
    )


def claim_refund_batch(order_number: str) -> dict[str, Any] | None:
    """Siparisin tum isleri bittiyse (kuyrukta/makinede is yoksa) iadesi baslamamis basarisiz isleri tek parti olarak ayirir."""
    core = _core()
    with state_lock():
        rows = order_jobs(order_number)
        if not rows or any(row["status"] in (QUEUED, *ACTIVE_STATUSES) for row in rows):
            return None
        pending = [row for row in rows if row["status"] in FAILURE_STATUSES and not (row.get("data") or {}).get("refundState")]
        if not pending:
            return None
        order = core.orders.get(order_number) or {}
        reference = order.get("payment_reference") or (pending[0].get("data") or {}).get("paymentReference")
        payment = deepcopy(find_payment(reference))
        already = sum(
            money((row.get("data") or {}).get("refundAmount")) for row in rows
            if row["status"] == REFUNDED or (row.get("data") or {}).get("refundState") == "SENDING"
        )
        batch_id = f"rf-{uuid4().hex[:20]}"
        now = utc_now_iso()
        jobs = []
        for row in pending:
            data = dict(row.get("data") or {})
            data.update({"refundState": "SENDING", "refundBatchId": batch_id, "refundStartedAt": now,
                         "failureStatus": data.get("failureStatus") or row["status"]})
            data["refund"] = {"state": "PENDING", "batchId": batch_id}
            save_job(row, row["status"], data, None)
            jobs.append({
                "id": row["id"], "productId": row["product_id"], "productName": data.get("productName"),
                "refundAmount": money(data.get("refundAmount")), "status": row["status"],
            })
        persist_jobs(pending)
    amount = sum((job["refundAmount"] for job in jobs), Decimal("0"))
    payment_amount = money((payment or {}).get("amount"))
    return {
        "batchId": batch_id,
        "orderNumber": order_number,
        "jobs": jobs,
        "amount": amount,
        "payment": payment,
        "full": already == 0 and payment is not None and abs(amount - payment_amount) <= Decimal("0.01"),
        "printHeader": getattr(core, "CASPRO_PRINT_HEADER", "MAGIC COFFEE"),
    }


def refund_failure(message: str, *, manual: bool = True, retryable: bool = True, unconfirmed: bool = False,
                   **extra: Any) -> dict[str, Any]:
    return {"ok": False, "message": message, "needsManualRefund": manual, "retryable": retryable,
            "submissionUnconfirmed": unconfirmed, **extra}


def mark_refund_plan(batch: dict[str, Any], plan: dict[str, Any]) -> None:
    """Gonderimden once iade turunu ise yazar (kiosk "kartinizi okutun" gosterebilsin)."""
    with state_lock():
        changed = []
        for item in batch["jobs"]:
            job = JOBS.get(item["id"])
            if not job:
                continue
            data = dict(job.get("data") or {})
            data["refund"] = {**(data.get("refund") or {}), **plan, "state": "PENDING"}
            save_job(job, job["status"], data, None)
            changed.append(job)
        persist_jobs(changed)


def finalize_refund_batch(batch: dict[str, Any], result: dict[str, Any]) -> None:
    core = _core()
    now = utc_now_iso()
    status = REFUNDED if result.get("ok") else REFUND_FAILED
    with state_lock():
        changed = []
        for item in batch["jobs"]:
            job = JOBS.get(item["id"])
            if not job:
                continue
            data = dict(job.get("data") or {})
            refund = {
                **(data.get("refund") or {}),
                **{key: value for key, value in result.items() if key != "ok"},
                "state": "DONE" if result.get("ok") else "FAILED",
                "batchId": batch["batchId"],
                "amount": float(batch["amount"]),
                "finishedAt": now,
            }
            if result.get("ok"):
                refund.update({"needsManualRefund": False, "retryable": False})
            data.update({"refundState": refund["state"], "refund": refund})
            save_job(job, status, data, None)
            changed.append(job)
        persist_jobs(changed)
        order = core.orders.get(batch["orderNumber"])
        if order is not None:
            refunds = list(order.get("vendRefunds") or [])
            refunds.append({
                "batchId": batch["batchId"], "ok": bool(result.get("ok")), "amount": float(batch["amount"]),
                "provider": result.get("provider"), "type": result.get("type"), "transactionId": result.get("transactionId"),
                "message": result.get("message"), "jobIds": [item["id"] for item in batch["jobs"]], "at": now,
            })
            order["vendRefunds"] = refunds
        refresh_order_vend(batch["orderNumber"])
    log = logger.info if result.get("ok") else logger.warning
    log("Vend iadesi %s: siparis=%s tutar=%s sonuc=%s", batch["batchId"], batch["orderNumber"], batch["amount"], status)


async def perform_refund(batch: dict[str, Any]) -> dict[str, Any]:
    payment = batch.get("payment")
    if not payment:
        return refund_failure("Ödeme kaydı bulunamadı; iade elle yapılmalı.", retryable=False)
    if payment.get("status") not in ("COMPLETED", "PAID", "SUCCESS", "SUCCEEDED"):
        return refund_failure("Ödeme tamamlanmış görünmüyor; iade elle kontrol edilmeli.", retryable=False)
    if batch["amount"] <= 0:
        return refund_failure("İade tutarı geçersiz.", retryable=False)
    if str(payment.get("provider") or "").upper() != "CASPRO":
        # Pavo gateway odemelerinde Magic Coffee otomatik iade gonderemez.
        return refund_failure("Bu ödeme Castles POS dışında alındı; iade POS'tan elle yapılmalı.", retryable=False,
                              provider=str(payment.get("provider") or "PAVO"))
    return await refund_caspro(batch, payment)


def caspro_same_day(created_at: Any) -> bool:
    parsed = parse_iso(created_at)
    if parsed is None:
        return False
    return parsed.astimezone(TR_TZ).date() == datetime.now(TR_TZ).date()


async def refund_caspro(batch: dict[str, Any], payment: dict[str, Any]) -> dict[str, Any]:
    settings = caspro_settings()
    if not settings:
        return refund_failure("Castles POS yapılandırılmamış; iade gönderilemedi.")
    serial = payment.get("terminalSerial") or ""
    bank_reference = str(payment.get("bankReferenceNumber") or "").strip()
    if batch["full"] and bank_reference and caspro_same_day(payment.get("createdAt")):
        transaction_type = "Void"
    elif bank_reference:
        transaction_type = "Refund"
    else:
        transaction_type = "Refundunmatched"
    ext_transaction_id = caspro.new_transaction_id()
    plan = {
        "provider": "CASPRO", "type": transaction_type, "transactionId": ext_transaction_id,
        "amount": float(batch["amount"]), "needsCard": transaction_type != "Void",
        "originalTransactionId": payment.get("transactionId"),
    }
    mark_refund_plan(batch, plan)
    print_lines = [batch["printHeader"], f"Siparis No: {batch['orderNumber']}", "Otomat iadesi"]
    payload = caspro.build_reversal(
        transaction_type, serial, caspro.to_kurus(batch["amount"]), ext_transaction_id,
        bank_reference or None, print_lines,
    )
    started = time.monotonic()
    result = None
    try:
        response = await caspro.send_command(settings, payload, settings.transaction_timeout)
        result = caspro.interpret_transaction_response(response)
    except CasProDeviceOffline:
        return refund_failure(caspro.DEVICE_OFFLINE_MESSAGE.replace("ödeme", "iade"), **plan)
    except CasProUnavailable:
        result = None
    while result is None and time.monotonic() - started < settings.result_timeout:
        try:
            response = await caspro.send_command(settings, caspro.build_query(serial, ext_transaction_id), caspro.COMMAND_TIMEOUT_SECONDS)
            result = caspro.interpret_query_response(response)
        except caspro.CasProError:
            result = None
        if result is None:
            await asyncio.sleep(CASPRO_REFUND_QUERY_INTERVAL_SECONDS)
    if result is None:
        return refund_failure("Castles iade sonucu alınamadı; cihazdaki iade fişini kontrol edin.", retryable=False,
                              unconfirmed=True, **plan)
    status, message, details = result
    clean = {key: value for key, value in (details or {}).items() if value is not None}
    if status == "COMPLETED":
        return {"ok": True, "message": "İade onaylandı.", **plan, **clean}
    return refund_failure(message or "Castles iadeyi reddetti.", **plan, **clean)


# ---- panel ---------------------------------------------------------------------------------------

class MachineCreate(BaseModel):
    id: str | None = Field(default=None, max_length=32)
    name: str = Field(min_length=2, max_length=120)
    kioskId: str | None = Field(default=None, max_length=32)
    mode: Literal["AUTO", "REMOTE", "CREDIT"] = "AUTO"
    active: bool = True
    vendingOptionSteps: list[str] | None = Field(default=None, max_length=20)

    @field_validator("id", "name", "kioskId", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class MachineUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    kioskId: str | None = Field(default=None, max_length=32)
    mode: Literal["AUTO", "REMOTE", "CREDIT"] | None = None
    active: bool | None = None
    vendingOptionSteps: list[str] | None = Field(default=None, max_length=20)

    @field_validator("name", "kioskId", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class MachineProductItem(BaseModel):
    productId: str = Field(min_length=1, max_length=120)
    optionKey: str = Field(default="", max_length=400)
    selectionNo: int = Field(ge=0, le=SELECTION_MAX)
    price: float | None = Field(default=None, ge=0, le=100_000)
    active: bool = True


def mapping_items(payload: Any) -> list[MachineProductItem]:
    """Govde: `{products: [...]}`, `{items: [...]}` veya dogrudan dizi."""
    if isinstance(payload, dict):
        payload = payload.get("products", payload.get("items", payload.get("mappings")))
    if not isinstance(payload, list):
        raise HTTPException(status_code=422, detail="Eşleme listesi bekleniyor: {products: [...]}")
    if len(payload) > 2000:
        raise HTTPException(status_code=422, detail="Eşleme listesi çok uzun.")
    try:
        return [MachineProductItem.model_validate(item) for item in payload]
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors(include_url=False, include_context=False)) from error


def clean_option_steps(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    cleaned = []
    for value in values:
        value = str(value or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", value):
            raise HTTPException(status_code=400, detail=f"Geçersiz seçenek adımı: {value}")
        cleaned.append(value)
    return list(dict.fromkeys(cleaned))


def clean_kiosk_id(value: str | None) -> str | None:
    return (value or "").strip().lower() or None


def other_active(machine_id: str | None) -> bool:
    return any(row.get("active") and row["id"] != machine_id for row in MACHINES.values())


def require_machine(machine_id: str) -> dict[str, Any]:
    row = MACHINES.get(machine_id)
    if not row:
        raise HTTPException(status_code=404, detail="Otomat bulunamadı")
    return row


@router.get("/api/admin/machines")
def admin_list_machines():
    with state_lock():
        return [machine_record(MACHINES[key]) for key in sorted(MACHINES)]


@router.post("/api/admin/machines", status_code=201)
def admin_create_machine(payload: MachineCreate):
    with state_lock():
        if payload.active and other_active(None):
            raise HTTPException(status_code=409, detail=ACTIVE_TAKEN)
        if payload.id:
            machine_id = payload.id.lower()
            if not re.fullmatch(r"[a-z0-9_-]{1,32}", machine_id):
                raise HTTPException(status_code=400, detail="Otomat kimliği yalnızca harf, rakam, - ve _ içerebilir.")
            if machine_id in MACHINES:
                raise HTTPException(status_code=409, detail="Bu kimlikle bir otomat zaten var.")
        else:
            numbers = [int(match.group(1)) for key in MACHINES if (match := MACHINE_ID_PATTERN.fullmatch(key))]
            next_number = max(numbers) + 1 if numbers else 1
            machine_id = f"m{next_number:02d}"
            while machine_id in MACHINES:
                next_number += 1
                machine_id = f"m{next_number:02d}"
        now = utc_now_iso()
        row = {
            "id": machine_id, "name": payload.name, "kiosk_id": clean_kiosk_id(payload.kioskId) or KIOSK_ID,
            "mode": payload.mode, "detected_mode": None, "token_hash": None, "active": payload.active,
            "last_seen_at": None, "agent_version": None, "vmc_info": None,
            "option_steps": clean_option_steps(payload.vendingOptionSteps), "created_at": now, "updated_at": now,
        }
        MACHINES[machine_id] = row
        try:
            persist_machine(row)
        except HTTPException:
            MACHINES.pop(machine_id, None)
            raise
        return machine_record(row)


@router.put("/api/admin/machines/{machine_id}")
def admin_update_machine(machine_id: str, payload: MachineUpdate):
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="Değişiklik bulunamadı")
    with state_lock():
        row = require_machine(machine_id)
        if values.get("active") and other_active(machine_id):
            raise HTTPException(status_code=409, detail=ACTIVE_TAKEN)
        updates: dict[str, Any] = {}
        if values.get("name"):
            updates["name"] = values["name"]
        if "kioskId" in values:
            updates["kiosk_id"] = clean_kiosk_id(values["kioskId"])
        if values.get("mode"):
            updates["mode"] = values["mode"]
        if values.get("active") is not None:
            updates["active"] = bool(values["active"])
        if "vendingOptionSteps" in values:
            updates["option_steps"] = clean_option_steps(values["vendingOptionSteps"])
        if updates:
            previous = dict(row)
            row.update({**updates, "updated_at": utc_now_iso()})
            try:
                persist_machine(row)
            except HTTPException:
                row.clear()
                row.update(previous)
                raise
        return machine_record(row)


@router.delete("/api/admin/machines/{machine_id}", status_code=204)
def admin_delete_machine(machine_id: str):
    with state_lock():
        require_machine(machine_id)
        busy = (QUEUED, *ACTIVE_STATUSES, *FAILURE_STATUSES)
        if any(job["machine_id"] == machine_id and job["status"] in busy for job in JOBS.values()):
            raise HTTPException(status_code=409, detail="Otomatta bekleyen veya iadesi süren iş var; önce tamamlanmasını bekleyin.")
        persist_machine_delete(machine_id)
        MACHINES.pop(machine_id, None)
        MAPPINGS[:] = [row for row in MAPPINGS if row["machine_id"] != machine_id]
        MACHINE_SEEN.pop(machine_id, None)


@router.post("/api/admin/machines/{machine_id}/token")
def admin_machine_token(machine_id: str):
    """Yeni kopru anahtari uretir; duz metin YALNIZ bu yanitta doner (saklanan yalnizca SHA-256 ozetidir)."""
    token = new_machine_token()
    with state_lock():
        row = require_machine(machine_id)
        previous = row.get("token_hash")
        row.update({"token_hash": token_hash(token), "updated_at": utc_now_iso()})
        try:
            persist_machine(row)
        except HTTPException:
            row["token_hash"] = previous
            raise
    return {"machineId": machine_id, "token": token}


def product_map() -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in _core().products}


def mapping_record(row: dict[str, Any], product: dict[str, Any] | None) -> dict[str, Any]:
    key = row.get("option_key") or ""
    return {
        "productId": row["product_id"],
        "productName": (product or {}).get("name"),
        "sectionId": (product or {}).get("sectionId"),
        "categoryId": (product or {}).get("categoryId"),
        "productPrice": (product or {}).get("price"),
        "optionKey": key,
        "optionLabel": option_label(product, key) if product else key,
        "selectionNo": int(row["selection_no"]),
        "price": row.get("price"),
        "active": bool(row.get("active")),
    }


def list_mappings(machine_id: str) -> list[dict[str, Any]]:
    products = product_map()
    rows = sorted((row for row in MAPPINGS if row["machine_id"] == machine_id), key=lambda row: (row["product_id"], row["option_key"]))
    return [mapping_record(row, products.get(row["product_id"])) for row in rows]


@router.get("/api/admin/machines/{machine_id}/products")
def admin_machine_products(machine_id: str):
    with state_lock():
        require_machine(machine_id)
        return list_mappings(machine_id)


@router.put("/api/admin/machines/{machine_id}/products")
def admin_set_machine_products(machine_id: str, payload: Any = Body(...)):
    """Esleme listesini toptan degistirir: `{products: [{productId, optionKey, selectionNo, price, active}]}` (dizi de olur)."""
    items = mapping_items(payload)
    with state_lock():
        machine = require_machine(machine_id)
        products = product_map()
        seen: set[tuple[str, str]] = set()
        rows = []
        for item in items:
            product = products.get(item.productId)
            if not product:
                raise HTTPException(status_code=400, detail=f"Ürün bulunamadı: {item.productId}")
            key = canonical_option_key(item.optionKey, product)
            if (item.productId, key) in seen:
                raise HTTPException(status_code=400, detail=f"{product.get('name')} ({key or 'standart'}) iki kez eşlenmiş.")
            seen.add((item.productId, key))
            rows.append({
                "machine_id": machine_id, "product_id": item.productId, "option_key": key,
                "selection_no": item.selectionNo, "price": float(money(item.price)) if item.price is not None else None,
                "active": item.active,
            })
        previous = list(MAPPINGS)
        MAPPINGS[:] = [row for row in MAPPINGS if row["machine_id"] != machine_id] + rows
        try:
            persist_mappings(machine_id)
        except HTTPException:
            MAPPINGS[:] = previous
            raise
        machine["updated_at"] = utc_now_iso()
        persist_machine(machine, strict=False)
        return list_mappings(machine_id)


@router.get("/api/admin/machines/{machine_id}/products/options")
def admin_machine_product_options(machine_id: str, product_id: str = Query(alias="productId", min_length=1)):
    """Urunun secenek kombinasyonlari (eslenecek varyantlar): `{options: [{optionKey, label, price, mapped, selectionNo}]}`.
    `price` kombinasyonun katalog fiyatidir; eslemedeki fiyat `mappedPrice`."""
    with state_lock():
        machine = require_machine(machine_id)
        product = product_map().get(product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Ürün bulunamadı")
        product = deepcopy(product)
        current = machine_mappings(machine_id, active_only=False).get(product_id) or {}
        steps = machine_option_steps(machine)
    combos = option_combinations(product, steps)
    return {
        "productId": product_id,
        "productName": product.get("name"),
        "truncated": len(combos) >= MAX_OPTION_COMBINATIONS,
        "options": [
            {
                **combo,
                "label": option_label(product, combo["optionKey"]),
                "mapped": combo["optionKey"] in current,
                "selectionNo": int(current[combo["optionKey"]]["selection_no"]) if combo["optionKey"] in current else None,
                "mappedPrice": current[combo["optionKey"]].get("price") if combo["optionKey"] in current else None,
            }
            for combo in combos
        ],
    }


@router.get("/api/admin/vend-jobs")
def admin_vend_jobs(
    machine: str | None = Query(default=None),
    status: str | None = Query(default=None),
    order: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
):
    statuses = {item.strip().upper() for item in (status or "").split(",") if item.strip()}
    with state_lock():
        rows = [
            dict(job) for job in JOBS.values()
            if (not machine or not machine.strip() or job["machine_id"] == machine.strip())
            and (not statuses or job["status"] in statuses)
            and (not order or not order.strip() or job["order_number"] == order.strip())
        ]
    rows.sort(key=lambda job: (job["created_at"], job["unit_index"]), reverse=True)
    return [admin_job_record(row) for row in rows[:limit]]


def reset_job_for_refund(job_id: str, force: bool) -> str:
    with state_lock():
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Vend işi bulunamadı.")
        if job["status"] != REFUND_FAILED:
            raise HTTPException(status_code=409, detail="Yalnızca iadesi başarısız işler tekrar denenebilir.")
        data = dict(job.get("data") or {})
        refund = data.get("refund") or {}
        if refund.get("submissionUnconfirmed") and not force:
            raise HTTPException(
                status_code=409,
                detail="Önceki iadenin sonucu belirsiz; POS'tan kontrol edip yine de denemek için force=true gönderin.",
            )
        history = list(data.get("refundHistory") or [])
        history.append(refund)
        data.update({"refundHistory": history, "refundState": None, "refund": None, "refundRetriedAt": utc_now_iso()})
        save_job(job, data.get("failureStatus") or "FAILED", data, None)
        persist_jobs([job])
        refresh_order_vend(job["order_number"])
        return job["order_number"]


@router.post("/api/admin/vend-jobs/{job_id}/retry-refund")
async def admin_retry_refund(job_id: str, force: bool = Query(default=False)):
    order_number = reset_job_for_refund(job_id, force)
    mark_work_pending()
    schedule_order_refund(order_number)
    with state_lock():
        return admin_job_record(dict(JOBS[job_id]))


def dashboard_summary() -> dict[str, Any]:
    with state_lock():
        return {
            "attentionCount": sum(1 for job in JOBS.values() if job["status"] == REFUND_FAILED),
            "machineCount": len(MACHINES),
            "onlineMachineCount": sum(1 for row in MACHINES.values() if row.get("active") and machine_online(row)),
        }
