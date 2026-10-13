from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import caspro
from .caspro import CasProDeviceOffline, CasProError, CasProSettings, CasProUnavailable, caspro_settings, normalize_caspro_serial
from .db import BUSY_MESSAGE, INTEGRITY_ERRORS, Conn, DatabaseBusy, db
from .db import validate_database_url as _validate_database_url
from .migrations import run_migrations, source_hash, utc_now_iso
from .translation import TARGET_LANGUAGE, provider_configured, sync_catalog_translations

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

logger = logging.getLogger("magickiosk")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATABASE_ALLOWED_HOST = os.getenv("DATABASE_ALLOWED_HOST", "").strip().lower().rstrip(".")


def validate_database_url(database_url: str) -> None:
    _validate_database_url(database_url, DATABASE_ALLOWED_HOST)


validate_database_url(DATABASE_URL)

SUCCESSFUL_PAYMENT_STATUSES = {"COMPLETED", "PAID", "SUCCESS", "SUCCEEDED"}
FAILED_PAYMENT_STATUSES = {"FAILED", "ERROR", "CANCELLED", "CANCELED", "DECLINED"}
PAYMENT_RATE_LIMIT = 20
PAYMENT_RATE_WINDOW_SECONDS = 60
PAYMENT_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)
BRAND = {"name": "MagicPay Kiosk", "currency": "TL", "version": "3.0.0"}
KIOSK_HEADER = "X-Kiosk-Id"
KIOSK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# Kiosk last_seen_at en fazla bu sıklıkta yazılır.
KIOSK_SEEN_INTERVAL_SECONDS = 30.0
KIOSK_ONLINE_SECONDS = 120
KIOSK_SEEN: dict[str, float] = {}
KIOSK_SEEN_LOCK = threading.Lock()

CASPRO_PROVIDER = "CASPRO"
CASPRO_TRANSACTION_PATTERN = caspro.TRANSACTION_PATTERN
# Kurtarma dongusunde QueryTransaction araligi ve poll tarafinin sorgu esigi (saniye).
CASPRO_RECOVERY_INTERVAL_SECONDS = 3.0
CASPRO_POLL_QUERY_GRACE_SECONDS = 10.0
# Baslat istegi, bulutun aninda dondugu "cihaz bagli degil" (404) yanitini yakalamak icin
# arka plan gorevini en fazla bu kadar bekler; sonra PROCESSING ile doner.
CASPRO_OFFLINE_GRACE_SECONDS = 1.5
# Surec icinde yasayan satis gorevleri; poll tarafi gorev kaybolduysa QueryTransaction ile sonucu arar.
CASPRO_TASKS: dict[str, asyncio.Task] = {}


async def cancel_caspro_tasks() -> None:
    tasks = [task for task in CASPRO_TASKS.values() if not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    CASPRO_TASKS.clear()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await asyncio.to_thread(db.open)
    try:
        await asyncio.to_thread(run_migrations, db)
        yield
    finally:
        await cancel_caspro_tasks()
        await asyncio.to_thread(db.close)


app = FastAPI(title="MagicPay Kiosk API", version=BRAND["version"], lifespan=lifespan)
configured_origins = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "http://localhost:5370,http://127.0.0.1:5370,http://localhost:5371,http://127.0.0.1:5371",
    ).split(",")
    if origin.strip()
]
NATIVE_APP_ORIGINS = [
    "capacitor://localhost",
    "ionic://localhost",
    "http://localhost",
    "https://localhost",
]
ALLOWED_ORIGINS = list(dict.fromkeys([*configured_origins, *NATIVE_APP_ORIGINS]))
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DatabaseBusy)
async def database_busy_handler(_: Request, __: DatabaseBusy):
    return JSONResponse(status_code=503, content={"detail": BUSY_MESSAGE}, headers={"Retry-After": "2"})


UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads" / "products"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR.parent)), name="uploads")


# ---- modeller ----------------------------------------------------------------------

class OrderSelection(BaseModel):
    choices: dict[str, list[str]] = Field(default_factory=dict)


class OrderLine(BaseModel):
    productId: str
    name: str = ""
    quantity: int = Field(ge=1, le=99)
    unitPrice: float = 0
    selection: OrderSelection | None = None


class OrderCreate(BaseModel):
    clientRequestId: str = Field(min_length=8, max_length=120)
    fulfillment: Literal["restaurant", "package"] = "package"
    paymentMethod: Literal["card", "meal-card"]
    total: float = Field(gt=0, le=100_000)
    lines: list[OrderLine] = Field(min_length=1)
    # Castles (CasPro) islem kimlikleri "cp-<24 hex>" bicimindedir.
    paymentReference: str = Field(min_length=4, max_length=160)
    posTransactionId: str = Field(min_length=4, max_length=160)
    language: Literal["tr", "en"] = "tr"


class PosPaymentCreate(BaseModel):
    clientRequestId: str = Field(min_length=8, max_length=120)
    paymentMethod: Literal["card", "meal-card"]
    amount: float = Field(gt=0, le=100_000)
    lines: list[OrderLine] = Field(min_length=1)
    # Platformda yok sayilir: POS seri numarasi kiosk profilinden gelir.
    terminalSerial: str | None = Field(default=None, max_length=64)


class CastlesDeviceCreate(BaseModel):
    serial: str = Field(min_length=4, max_length=64, pattern=r"^[A-Za-z0-9]+$")
    name: str = Field(min_length=2, max_length=120)
    active: bool = True

    @field_validator("serial", "name", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class CastlesDeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    active: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class KioskUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    featuredSectionId: str | None = Field(default=None, min_length=1, max_length=64)
    posSerial: str | None = Field(default=None, max_length=64)
    active: bool | None = None

    @field_validator("name", "featuredSectionId", "posSerial", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class SectionCreate(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    nameEn: str = Field(default="", max_length=120)
    tagline: str = Field(default="", max_length=200)
    taglineEn: str = Field(default="", max_length=200)
    icon: str = Field(default="Store", max_length=64)
    accent: str = Field(default="#684cf0", pattern=r"^#[0-9A-Fa-f]{6}$")
    orderPrefix: str = Field(pattern=r"^[A-Za-z0-9]{2,6}$")
    active: bool = True

    @field_validator("name", "nameEn", "tagline", "taglineEn", "icon", "orderPrefix", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class SectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    nameEn: str | None = Field(default=None, max_length=120)
    tagline: str | None = Field(default=None, max_length=200)
    taglineEn: str | None = Field(default=None, max_length=200)
    icon: str | None = Field(default=None, max_length=64)
    accent: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    orderPrefix: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{2,6}$")
    position: int | None = Field(default=None, ge=0)
    active: bool | None = None

    @field_validator("name", "nameEn", "tagline", "taglineEn", "icon", "orderPrefix", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class ReceiptStatusUpdate(BaseModel):
    status: Literal["printed", "failed", "skipped"]
    printAttemptId: str = Field(min_length=8, max_length=120)
    deviceId: str | None = Field(default=None, max_length=160)


class UploadPayload(BaseModel):
    filename: str
    dataUrl: str


class StockAdjust(BaseModel):
    mode: Literal["set", "add", "remove"]
    quantity: int = Field(gt=0)
    note: str = ""


# ---- yardimcilar ---------------------------------------------------------------------

def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return {}
    return json.loads(value)


def slugify(value: str) -> str:
    value = value.lower()
    table = str.maketrans("çğıöşüâîû", "cgiosuaiu")
    value = value.translate(table)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or f"item-{int(datetime.now().timestamp())}"


def clean_filter(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def parse_created_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def local_day_start_utc(day: date) -> str:
    return datetime.combine(day, dt_time.min).astimezone().astimezone(timezone.utc).isoformat()


def placeholders(values: list[Any] | tuple[Any, ...] | set[Any]) -> str:
    return ", ".join(["%s"] * len(values))


def unique_id(conn: Conn, table: str, base: str) -> str:
    candidate = base
    suffix = 2
    while conn.execute(f"SELECT 1 AS found FROM {table} WHERE id = %s", (candidate,)).fetchone():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


# ---- satir donusturuculer -------------------------------------------------------------

def section_record(row: dict[str, Any], *, product_count: int = 0, category_count: int = 0) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "nameEn": row.get("name_en") or "",
        "tagline": row.get("tagline") or "",
        "taglineEn": row.get("tagline_en") or "",
        "icon": row.get("icon") or "Store",
        "accent": row.get("accent") or "#684cf0",
        "orderPrefix": row["order_prefix"],
        "position": int(row.get("position") or 0),
        "active": bool(row.get("active")),
        "productCount": product_count,
        "categoryCount": category_count,
    }


def localized_section(row: dict[str, Any], language: str, product_count: int) -> dict[str, Any]:
    record = section_record(row, product_count=product_count)
    if language == "en":
        record["name"] = record["nameEn"] or record["name"]
        record["tagline"] = record["taglineEn"] or record["tagline"]
    return record


def category_row_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "sectionId": row["section_id"],
        "name": row["name"],
        "eyebrow": row.get("eyebrow") or "",
        "position": int(row.get("position") or 0),
        "active": bool(row.get("active")),
    }


def product_row_record(row: dict[str, Any]) -> dict[str, Any]:
    data = loads(row.get("data"))
    return {
        **data,
        "id": row["id"],
        "sectionId": row["section_id"],
        "categoryId": row["category_id"],
        "position": int(row.get("position") or 0),
        "active": bool(row.get("active")),
    }


# ---- depo okumalari -------------------------------------------------------------------

def fetch_sections(conn: Conn, *, active_only: bool = False) -> list[dict[str, Any]]:
    query = "SELECT * FROM mk_sections"
    params: tuple = ()
    if active_only:
        query += " WHERE active = %s"
        params = (True,)
    return conn.execute(query + " ORDER BY position, id", params).fetchall()


def fetch_section(conn: Conn, section_id: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM mk_sections WHERE id = %s", (section_id,)).fetchone()


def fetch_categories(conn: Conn, section_id: str | None = None) -> list[dict[str, Any]]:
    if section_id:
        rows = conn.execute("SELECT * FROM mk_categories WHERE section_id = %s ORDER BY position, id", (section_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM mk_categories ORDER BY section_id, position, id").fetchall()
    return [category_row_record(row) for row in rows]


def fetch_category(conn: Conn, category_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM mk_categories WHERE id = %s", (category_id,)).fetchone()
    return category_row_record(row) if row else None


def fetch_products(conn: Conn, section_id: str | None = None) -> list[dict[str, Any]]:
    if section_id:
        rows = conn.execute("SELECT * FROM mk_products WHERE section_id = %s ORDER BY position, id", (section_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM mk_products ORDER BY section_id, position, id").fetchall()
    return [product_row_record(row) for row in rows]


def fetch_products_by_ids(conn: Conn, product_ids: list[str], *, for_update: bool = False) -> dict[str, dict[str, Any]]:
    ids = sorted(set(product_ids))
    if not ids:
        return {}
    query = f"SELECT * FROM mk_products WHERE id IN ({placeholders(ids)}) ORDER BY id"
    if for_update:
        query += " FOR UPDATE"
    return {row["id"]: product_row_record(row) for row in conn.execute(query, ids).fetchall()}


def find_product(conn: Conn, product_id: str) -> dict[str, Any]:
    product = fetch_products_by_ids(conn, [product_id]).get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    return product


def save_product(conn: Conn, product: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE mk_products SET section_id = %s, category_id = %s, position = %s, active = %s, data = %s WHERE id = %s",
        (
            product["sectionId"], product["categoryId"], int(product.get("position") or 0),
            bool(product.get("active", True)), dumps(product), product["id"],
        ),
    )


# ---- ceviri ---------------------------------------------------------------------------

def translation_key(entity_type: str, entity_id: str, field: str, language: str = TARGET_LANGUAGE) -> str:
    return "|".join((entity_type, entity_id, field, language))


def fetch_translation_records(conn: Conn, language: str = TARGET_LANGUAGE) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT entity_type, entity_id, field, language, value, source_hash, status, last_attempt_at "
        "FROM mk_translations WHERE language = %s",
        (language,),
    ).fetchall()
    return {
        translation_key(row["entity_type"], row["entity_id"], row["field"], row["language"]): {
            "entityType": row["entity_type"],
            "entityId": row["entity_id"],
            "field": row["field"],
            "language": row["language"],
            "sourceHash": row["source_hash"],
            "translatedText": row["value"] or "",
            "status": row["status"] or ("ready" if row["value"] else "pending"),
            "lastAttemptAt": row["last_attempt_at"],
        }
        for row in rows
    }


def localized_text(records: dict[str, dict[str, Any]], entity_type: str, entity_id: str, field: str, source: str, language: str) -> str:
    if language == "tr" or not source:
        return source
    record = records.get(translation_key(entity_type, entity_id, field, language))
    if not record or record.get("status") != "ready" or record.get("sourceHash") != source_hash(source):
        return source
    return str(record.get("translatedText") or source)


def translation_status_from_db(conn: Conn) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM mk_translations WHERE language = %s GROUP BY status", (TARGET_LANGUAGE,),
    ).fetchall()
    counts = {row["status"] or "pending": int(row["n"]) for row in rows}
    return {
        "language": TARGET_LANGUAGE,
        "provider": "google",
        "configured": provider_configured(),
        "ready": counts.get("ready", 0),
        "pending": counts.get("pending", 0),
        "errors": counts.get("error", 0),
    }


def sync_translations(entity_type: str | None = None, entity_id: str | None = None, section_id: str | None = None):
    """Eksik/eskimis Ingilizce cevirileri tamamlar. Ceviri servisi cagrisi sirasinda
    veritabani baglantisi tutulmaz."""
    with db.connect() as conn:
        categories = fetch_categories(conn, section_id)
        products = fetch_products(conn, section_id)
        records = fetch_translation_records(conn)
    before = deepcopy(records)
    result = sync_catalog_translations(categories, products, records, entity_type, entity_id)
    changed = [(key, record) for key, record in records.items() if before.get(key) != record]
    if changed:
        with db.connect(write=True) as conn:
            for _, record in changed:
                conn.execute(
                    """
                    INSERT INTO mk_translations (entity_type, entity_id, field, language, value, source_hash, status, last_attempt_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (entity_type, entity_id, field, language) DO UPDATE
                    SET value = excluded.value, source_hash = excluded.source_hash,
                        status = excluded.status, last_attempt_at = excluded.last_attempt_at
                    """,
                    (
                        record["entityType"], record["entityId"], record["field"], record.get("language") or TARGET_LANGUAGE,
                        record.get("translatedText") or "", record["sourceHash"], record.get("status") or "pending",
                        record.get("lastAttemptAt"),
                    ),
                )
    return result


def remove_entity_translations(conn: Conn, entity_type: str, entity_id: str) -> None:
    conn.execute("DELETE FROM mk_translations WHERE entity_type = %s AND entity_id = %s", (entity_type, entity_id))


# ---- katalog serilestirme ------------------------------------------------------------

def product_available(product: dict[str, Any]) -> tuple[bool, str | None]:
    if not product.get("active", True) or not product.get("stockSellable", True):
        return False, "Bu ürün şu anda mevcut değil"
    if product.get("stockTrackingEnabled") and int(product.get("stockQuantity") or 0) <= 0:
        return False, "Bu ürün şu anda mevcut değil"
    return True, None


def limit_customization_steps(product: dict[str, Any]) -> dict[str, Any]:
    customization = deepcopy(product.get("customization") or {})
    for step_id in list(customization.keys()):
        if not customization[step_id].get("enabled"):
            customization[step_id]["enabled"] = False
    return customization


def localized_customization(product: dict[str, Any], language: str, records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    customization = limit_customization_steps(product)
    if language == "tr":
        return customization
    product_id = str(product["id"])
    for step_id, step in customization.items():
        step["title"] = localized_text(
            records, "product", product_id, f"customization.{step_id}.title", str(step.get("title") or ""), language,
        )
        for option in step.get("options") or []:
            option_id = str(option.get("id") or "")
            option["name"] = localized_text(
                records, "product", product_id, f"customization.{step_id}.options.{option_id}.name",
                str(option.get("name") or ""), language,
            )
    return customization


def serialize_category(category: dict[str, Any], product_count: int, language: str = "tr",
                       records: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    record = {**category, "productCount": product_count}
    if language != "tr":
        records = records or {}
        record["name"] = localized_text(records, "category", category["id"], "name", str(category.get("name") or ""), language)
        record["eyebrow"] = localized_text(records, "category", category["id"], "eyebrow", str(category.get("eyebrow") or ""), language)
    return record


def serialize_product(product: dict[str, Any], category_names: dict[str, str], language: str = "tr",
                      records: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    records = records or {}
    available, reason = product_available(product)
    record = {
        **product,
        "customization": localized_customization(product, language, records),
        "categoryName": category_names.get(product["categoryId"], ""),
        "available": available,
        "unavailableReason": reason,
    }
    if language != "tr":
        product_id = str(product["id"])
        record["name"] = localized_text(records, "product", product_id, "name", str(product.get("name") or ""), language)
        record["description"] = localized_text(
            records, "product", product_id, "description", str(product.get("description") or ""), language,
        )
        if reason:
            record["unavailableReason"] = "This product is currently unavailable"
    return record


def visible_products(sections: list[dict[str, Any]], categories: list[dict[str, Any]], products: list[dict[str, Any]]):
    active_sections = {row["id"] for row in sections if row.get("active")}
    active_categories = {item["id"] for item in categories if item.get("active") and item["sectionId"] in active_sections}
    return [
        item for item in products
        if item["categoryId"] in active_categories and item["sectionId"] in active_sections and product_available(item)[0]
    ], active_categories


def active_catalog(language: str = "tr", section_id: str | None = None) -> dict[str, Any]:
    with db.connect() as conn:
        sections = fetch_sections(conn)
        section = None
        if section_id:
            section = next((row for row in sections if row["id"] == section_id), None)
            if not section or not section.get("active"):
                raise HTTPException(status_code=404, detail="Kategori bulunamadı")
        categories = fetch_categories(conn, section_id)
        products = fetch_products(conn, section_id)
        records = fetch_translation_records(conn) if language != "tr" else {}
    shown, active_category_ids = visible_products(sections, categories, products)
    counts: dict[str, int] = defaultdict(int)
    for item in shown:
        counts[item["categoryId"]] += 1
    serialized_categories = [
        serialize_category(item, counts[item["id"]], language, records)
        for item in categories if item["id"] in active_category_ids
    ]
    category_names = {item["id"]: item["name"] for item in serialized_categories}
    return {
        "brand": BRAND,
        "language": language,
        "section": localized_section(section, language, len(shown)) if section else None,
        "categories": serialized_categories,
        "products": [serialize_product(item, category_names, language, records) for item in shown],
    }


# ---- kiosk kimligi -----------------------------------------------------------------

KIOSK_SELECT = """
    SELECT k.id, k.name, k.featured_section_id, k.pos_serial, k.order_seq, k.active, k.last_seen_at, k.app_version,
           s.order_prefix, s.name AS section_name
    FROM mk_kiosks k
    LEFT JOIN mk_sections s ON s.id = k.featured_section_id
"""


def fetch_kiosk(conn: Conn, kiosk_id: str) -> dict[str, Any] | None:
    return conn.execute(KIOSK_SELECT + " WHERE k.id = %s", (kiosk_id,)).fetchone()


def touch_kiosk(kiosk_id: str, app_version: str | None) -> None:
    now = time.monotonic()
    with KIOSK_SEEN_LOCK:
        last = KIOSK_SEEN.get(kiosk_id)
        if last is not None and now - last < KIOSK_SEEN_INTERVAL_SECONDS:
            return
        KIOSK_SEEN[kiosk_id] = now
    try:
        with db.connect(write=True) as conn:
            if app_version:
                conn.execute(
                    "UPDATE mk_kiosks SET last_seen_at = %s, app_version = %s WHERE id = %s",
                    (utc_now_iso(), app_version[:40], kiosk_id),
                )
            else:
                conn.execute("UPDATE mk_kiosks SET last_seen_at = %s WHERE id = %s", (utc_now_iso(), kiosk_id))
    except DatabaseBusy:
        # Son gorulme bilgisi kritik degil; havuz doluysa istegi bozmaz.
        with KIOSK_SEEN_LOCK:
            KIOSK_SEEN.pop(kiosk_id, None)


def resolve_kiosk(request: Request, *, required: bool) -> dict[str, Any] | None:
    kiosk_id = request.headers.get(KIOSK_HEADER, "").strip().lower()
    if not kiosk_id:
        if required:
            raise HTTPException(status_code=403, detail="Kiosk kimliği doğrulanamadı")
        return None
    if not KIOSK_ID_PATTERN.fullmatch(kiosk_id):
        raise HTTPException(status_code=403, detail="Kiosk kimliği doğrulanamadı")
    with db.connect() as conn:
        kiosk = fetch_kiosk(conn, kiosk_id)
    if not kiosk:
        raise HTTPException(status_code=403, detail="Kiosk tanımlı değil")
    if not kiosk.get("active"):
        raise HTTPException(status_code=403, detail="Kiosk pasif durumda")
    touch_kiosk(kiosk_id, request.headers.get("X-App-Version", "").strip() or None)
    return kiosk


def current_kiosk(request: Request) -> dict[str, Any]:
    return resolve_kiosk(request, required=True)  # type: ignore[return-value]


def optional_kiosk(request: Request) -> dict[str, Any] | None:
    return resolve_kiosk(request, required=False)


# ---- saglik ---------------------------------------------------------------------------

@app.get("/health")
def health():
    try:
        with db.connect() as conn:
            conn.execute("SELECT 1 AS online").fetchone()
            translation = translation_status_from_db(conn)
    except DatabaseBusy:
        raise
    except Exception as error:  # noqa: BLE001 - saglik ucu hatayi 503 olarak bildirir
        logger.warning("Saglik kontrolu veritabanina ulasamadi: %s", type(error).__name__)
        raise HTTPException(status_code=503, detail="Veritabanına erişilemedi") from error
    caspro_config = caspro_settings()
    return {
        "status": "ok",
        "service": "magickiosk-api",
        "database": "postgresql" if db.dialect == "postgres" else "sqlite",
        "pool": db.stats(),
        "payment": {"status": "ready" if caspro_config else "disabled", "provider": CASPRO_PROVIDER},
        "translation": translation,
    }


# ---- kiosk uclari -----------------------------------------------------------------------

@app.get("/api/kiosk/config")
def kiosk_config(
    language: Literal["tr", "en"] = Query(default="tr", alias="lang"),
    kiosk: dict[str, Any] = Depends(current_kiosk),
):
    return build_kiosk_config(kiosk, language)


def build_kiosk_config(kiosk: dict[str, Any], language: str = "tr") -> dict[str, Any]:
    with db.connect() as conn:
        sections = fetch_sections(conn)
        categories = fetch_categories(conn)
        products = fetch_products(conn)
    shown, _ = visible_products(sections, categories, products)
    counts: dict[str, int] = defaultdict(int)
    for item in shown:
        counts[item["sectionId"]] += 1
    section_list = []
    for row in sections:
        if not row.get("active") or not counts[row["id"]]:
            continue
        record = localized_section(row, language, counts[row["id"]])
        section_list.append({key: record[key] for key in ("id", "name", "tagline", "icon", "accent", "productCount", "position", "orderPrefix")})
    return {
        "kiosk": {
            "id": kiosk["id"],
            "name": kiosk["name"],
            "featuredSectionId": kiosk["featured_section_id"],
            "orderPrefix": kiosk.get("order_prefix") or "",
            "posConfigured": bool(kiosk.get("pos_serial")),
        },
        "brand": {"name": BRAND["name"], "currency": BRAND["currency"]},
        "language": language,
        "sections": section_list,
    }


# ---- kiosk kurulum ekrani: POS seri numarasi -------------------------------------------

KIOSK_PIN_HEADER = "X-Kiosk-Admin-Pin"
PIN_RATE_LIMIT = 5
PIN_RATE_WINDOW_SECONDS = 60
PIN_FAILURES: dict[str, deque[float]] = defaultdict(deque)
PIN_FAILURES_LOCK = threading.Lock()


class KioskPosUpdate(BaseModel):
    posSerial: str | None = Field(default=None, max_length=64)


def kiosk_admin_pin() -> str:
    return os.getenv("KIOSK_ADMIN_PIN", "").strip() or "1907"


def kiosk_number_label(kiosk_id: str) -> str:
    digits = re.sub(r"\D", "", kiosk_id)
    return f"Kiosk {digits.zfill(2)} POS" if digits else f"{kiosk_id} POS"


def check_kiosk_pin(request: Request, kiosk_id: str) -> None:
    client = request.client.host if request.client else "unknown"
    key = f"{client}|{kiosk_id}"
    now = time.monotonic()
    with PIN_FAILURES_LOCK:
        failures = PIN_FAILURES[key]
        while failures and now - failures[0] >= PIN_RATE_WINDOW_SECONDS:
            failures.popleft()
        if len(failures) >= PIN_RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Çok fazla deneme, lütfen 1 dakika bekleyin.")
        supplied = request.headers.get(KIOSK_PIN_HEADER, "").strip()
        if not supplied or not hmac.compare_digest(supplied.encode("utf-8"), kiosk_admin_pin().encode("utf-8")):
            failures.append(now)
            raise HTTPException(status_code=403, detail="PIN hatalı.")


@app.get("/api/kiosk/profiles")
def kiosk_profiles():
    """Kurulum ekrani icin kiosk listesi; seri numarasinin yalnizca son 4 hanesi doner."""
    with db.connect() as conn:
        rows = conn.execute(KIOSK_SELECT + " ORDER BY k.id").fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "featuredSectionId": row["featured_section_id"],
            "active": bool(row.get("active")),
            "posConfigured": bool(row.get("pos_serial")),
            "posSerialLast4": (row.get("pos_serial") or "")[-4:] or None,
        }
        for row in rows
    ]


@app.put("/api/kiosk/pos")
def kiosk_set_pos(payload: KioskPosUpdate, request: Request, kiosk: dict[str, Any] = Depends(current_kiosk)):
    kiosk_id = kiosk["id"]
    check_kiosk_pin(request, kiosk_id)
    serial = None
    if payload.posSerial and payload.posSerial.strip():
        serial = normalize_caspro_serial(payload.posSerial)
        if len(serial) != 12:
            raise HTTPException(status_code=400, detail="POS seri numarası 12 haneli olmalı.")
    try:
        with db.connect(write=True) as conn:
            if serial:
                other = conn.execute(
                    "SELECT id, name FROM mk_kiosks WHERE pos_serial = %s AND id <> %s", (serial, kiosk_id),
                ).fetchone()
                if other:
                    raise HTTPException(status_code=409, detail=f"Bu POS cihazı {other['name']} kioskuna atanmış.")
                device = find_castles_device_by_serial(conn, serial)
                now = utc_now_iso()
                if device is None:
                    conn.execute(
                        "INSERT INTO mk_castles_devices (serial, name, active, created_at, updated_at) VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT (serial) DO NOTHING",
                        (serial, kiosk_number_label(kiosk_id), True, now, now),
                    )
                elif not device.get("active"):
                    conn.execute(
                        "UPDATE mk_castles_devices SET active = %s, updated_at = %s WHERE serial = %s",
                        (True, now, device["serial"]),
                    )
            conn.execute("UPDATE mk_kiosks SET pos_serial = %s WHERE id = %s", (serial, kiosk_id))
            updated = fetch_kiosk(conn, kiosk_id)
    except INTEGRITY_ERRORS as error:
        raise HTTPException(status_code=409, detail="Bu POS cihazı başka bir kioska atanmış.") from error
    logger.info("Kiosk %s POS seri numarasi kurulum ekranindan %s", kiosk_id, "atandi" if serial else "kaldirildi")
    return build_kiosk_config(updated)


@app.get("/api/catalog")
def get_catalog(
    language: Literal["tr", "en"] = Query(default="tr", alias="lang"),
    section: str | None = Query(default=None),
    _: dict[str, Any] | None = Depends(optional_kiosk),
):
    section_id = clean_filter(section)
    if language == "en":
        sync_translations(section_id=section_id)
    return active_catalog(language, section_id)


# ---- siparis ve odeme mantigi ------------------------------------------------------------

def selected_price_delta(product: dict[str, Any], selection: OrderSelection | None) -> float:
    if not selection:
        return 0
    total = 0.0
    for step_id, option_ids in selection.choices.items():
        step = product.get("customization", {}).get(step_id)
        if not step or not step.get("enabled", False):
            continue
        enabled_options = {option["id"]: option for option in step.get("options", []) if option.get("enabled", True)}
        for option_id in option_ids:
            option = enabled_options.get(option_id)
            if not option:
                raise HTTPException(status_code=400, detail=f"{product['name']} için geçersiz seçim")
            if option.get("available") is False:
                raise HTTPException(status_code=400, detail="Seçilen özelleştirme stokta yok")
            total += float(option.get("priceDelta") or 0)
    return total


def validate_customization_selection(product: dict[str, Any], selection: OrderSelection | None):
    if not product.get("customizable", False):
        return
    choices = selection.choices if selection else {}
    for step_id, step in product.get("customization", {}).items():
        if not step.get("enabled"):
            continue
        option_ids = choices.get(step_id, [])
        if len(set(option_ids)) != len(option_ids):
            raise HTTPException(status_code=400, detail=f"{product['name']} için tekrarlanan seçim yapıldı")
        enabled_options = {option["id"]: option for option in step.get("options", []) if option.get("enabled", True)}
        for option_id in option_ids:
            option = enabled_options.get(option_id)
            if not option:
                raise HTTPException(status_code=400, detail=f"{product['name']} için geçersiz seçim")
            if option.get("available") is False:
                raise HTTPException(status_code=400, detail="Seçilen özelleştirme stokta yok")
        count_selected = len(option_ids)
        min_select = int(step.get("minSelect") or (1 if step.get("required") else 0))
        max_select = 1 if step_id == "shot" else int(step.get("maxSelect") or count_selected or 1)
        if count_selected < min_select:
            raise HTTPException(status_code=400, detail=f"{product['name']} için {step['title']} zorunlu")
        if count_selected > max_select:
            raise HTTPException(status_code=400, detail=f"{product['name']} için çok fazla seçim yapıldı")


def resolve_order_lines(conn: Conn, lines: list[OrderLine], *, for_update: bool = False):
    catalog = fetch_products_by_ids(conn, [line.productId for line in lines], for_update=for_update)
    calculated_total = 0.0
    resolved: list[dict[str, Any]] = []
    requested_quantities: dict[str, int] = defaultdict(int)
    for line in lines:
        product = catalog.get(line.productId)
        if not product:
            raise HTTPException(status_code=404, detail="Ürün bulunamadı")
        available, reason = product_available(product)
        if not available:
            raise HTTPException(status_code=409, detail=reason)
        validate_customization_selection(product, line.selection)
        unit_price = round(float(product["price"]) + selected_price_delta(product, line.selection), 2)
        calculated_total += unit_price * line.quantity
        requested_quantities[product["id"]] += line.quantity
        resolved.append({
            "productId": product["id"],
            "sectionId": product["sectionId"],
            "name": product["name"],
            "quantity": line.quantity,
            "unitPrice": unit_price,
            "selection": line.selection.model_dump() if line.selection else None,
        })
    return round(calculated_total, 2), resolved, dict(requested_quantities), catalog


def order_fingerprint(payment_method: str, lines: list[dict[str, Any]]) -> str:
    normalized = {
        "paymentMethod": payment_method,
        "lines": sorted(
            [
                {
                    "productId": line["productId"],
                    "quantity": line["quantity"],
                    "unitPrice": round(float(line["unitPrice"]), 2),
                    "selection": line.get("selection") or {"choices": {}},
                }
                for line in lines
            ],
            key=lambda item: (
                item["productId"],
                json.dumps(item["selection"], ensure_ascii=False, sort_keys=True),
            ),
        ),
    }
    encoded = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def payment_response(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": record.get("transactionId"),
        "status": record.get("status", "PROCESSING"),
        "message": record.get("message"),
        "externalId": record.get("externalId"),
        "paymentReference": record.get("externalId"),
        "orderNumber": record.get("reservedOrderNumber") or record.get("orderNumber"),
    }


def record_stock(conn: Conn, product: dict[str, Any], before: int | None, after: int | None, quantity: int,
                 note: str, kiosk_id: str | None) -> None:
    now = utc_now_iso()
    conn.execute(
        "INSERT INTO mk_stock_movements (product_id, kiosk_id, data, created_at) VALUES (%s, %s, %s, %s)",
        (
            product["id"], kiosk_id,
            dumps({
                "product_id": product["id"],
                "product_name": product["name"],
                "section_id": product.get("sectionId"),
                "movement_type": "order" if quantity < 0 else "manual",
                "quantity": quantity,
                "before_quantity": before,
                "after_quantity": after,
                "note": note,
                "created_at": now,
            }),
            now,
        ),
    )


def reserve_order_number(conn: Conn, kiosk_id: str) -> str:
    """Kiosk sayacini atomik arttirir ve `<onek>-<NNNN>` dondurur. Cagiran yazma
    transaction'i icinde olmalidir (SQLite: BEGIN IMMEDIATE, PostgreSQL: satir kilidi)."""
    kiosk = fetch_kiosk(conn, kiosk_id)
    if not kiosk:
        raise HTTPException(status_code=403, detail="Kiosk tanımlı değil")
    prefix = kiosk.get("order_prefix") or "MK"
    for _ in range(1000):
        row = conn.execute(
            "UPDATE mk_kiosks SET order_seq = order_seq + 1 WHERE id = %s RETURNING order_seq", (kiosk_id,),
        ).fetchone()
        number = f"{prefix}-{int(row['order_seq']):04d}"
        # Ayni vitrin kategorisini paylasan kiosklar ayni oneki kullanabilir; cakisma atlanir.
        taken = conn.execute("SELECT 1 AS found FROM mk_orders WHERE number = %s", (number,)).fetchone() or conn.execute(
            "SELECT 1 AS found FROM mk_pos_payments WHERE reserved_order_number = %s", (number,),
        ).fetchone()
        if not taken:
            return number
    raise HTTPException(status_code=503, detail="Sipariş numarası ayrılamadı")


def load_payment(conn: Conn, transaction_id: str, *, for_update: bool = False) -> dict[str, Any] | None:
    query = "SELECT data FROM mk_pos_payments WHERE transaction_id = %s"
    if for_update:
        query += " FOR UPDATE"
    row = conn.execute(query, (transaction_id,)).fetchone()
    return loads(row["data"]) if row else None


def load_payment_by_request(conn: Conn, client_request_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT data FROM mk_pos_payments WHERE client_request_id = %s", (client_request_id,)).fetchone()
    return loads(row["data"]) if row else None


def save_payment(conn: Conn, record: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE mk_pos_payments SET status = %s, updated_at = %s, data = %s WHERE transaction_id = %s",
        (record.get("status") or "PROCESSING", record.get("updatedAt") or utc_now_iso(), dumps(record), record["transactionId"]),
    )


def get_payment(transaction_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        return load_payment(conn, transaction_id)


def enforce_payment_rate_limit(key: str) -> None:
    now = time.monotonic()
    attempts = PAYMENT_ATTEMPTS[key]
    while attempts and now - attempts[0] >= PAYMENT_RATE_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= PAYMENT_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Çok fazla ödeme denemesi yapıldı. Lütfen birazdan tekrar deneyin")
    attempts.append(now)


def kiosk_print_name(name: str) -> str:
    text = re.sub(r"\s*[·•|]\s*", " - ", name or "")
    return caspro.ascii_print_line(re.sub(r"\s+", " ", text)) or "MAGICPAY KIOSK"


def caspro_record_age_seconds(record: dict[str, Any]) -> float:
    try:
        created = datetime.fromisoformat(str(record.get("createdAt")))
    except (TypeError, ValueError):
        return 0.0
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - created).total_seconds())


def finalize_caspro_payment(transaction_id: str, status: str, message: str | None, details: dict[str, Any] | None = None) -> bool:
    """Nihai sonucu kayda yazar. Yalnizca PROCESSING kayit guncellenir; onaylanan satis (para cekildi)
    kullanici iptalinin ustune de yazilir."""
    with db.connect(write=True) as conn:
        record = load_payment(conn, transaction_id, for_update=True)
        if not record:
            return False
        current = record.get("status")
        if current == status and status != "COMPLETED":
            return False
        if current in SUCCESSFUL_PAYMENT_STATUSES:
            return False
        if current != "PROCESSING" and status != "COMPLETED":
            return False
        record.update({"status": status, "message": message, "updatedAt": utc_now_iso()})
        if details:
            record.update({key: value for key, value in details.items() if value is not None})
        save_payment(conn, record)
    return True


async def apply_caspro_result(transaction_id: str, result: tuple[str, str | None, dict[str, Any]] | None) -> bool:
    if not result:
        return False
    status, message, details = result
    return await asyncio.to_thread(finalize_caspro_payment, transaction_id, status, message, details)


async def caspro_cancel_quietly(settings: CasProSettings, terminal_serial: str) -> bool:
    try:
        response = await caspro.send_command(settings, caspro.build_cancel(terminal_serial), caspro.COMMAND_TIMEOUT_SECONDS)
    except CasProError:
        return False
    return bool(response.get("status"))


async def query_caspro_result(settings: CasProSettings, record: dict[str, Any]) -> bool:
    """Tek QueryTransaction denemesi; nihai sonuc geldiyse kayda yazar ve True doner."""
    try:
        response = await caspro.send_command(
            settings,
            caspro.build_query(record["terminalSerial"], record["externalId"]),
            caspro.COMMAND_TIMEOUT_SECONDS,
        )
    except CasProError:
        return False
    return await apply_caspro_result(record["transactionId"], caspro.interpret_query_response(response))


async def recover_caspro_payment(settings: CasProSettings, transaction_id: str) -> None:
    """Transaction yaniti alinamadiysa CASPRO_RESULT_TIMEOUT_SECONDS boyunca 3 s araliklarla
    QueryTransaction dener; sonuc gelmezse CancelTransaction gonderip kaydi FAILED yapar."""
    while True:
        record = await asyncio.to_thread(get_payment, transaction_id)
        if not record or record.get("status") != "PROCESSING":
            return
        if caspro_record_age_seconds(record) >= settings.result_timeout:
            await caspro_cancel_quietly(settings, record["terminalSerial"])
            await asyncio.to_thread(finalize_caspro_payment, transaction_id, "FAILED", caspro.TIMEOUT_MESSAGE)
            return
        if await query_caspro_result(settings, record):
            return
        await asyncio.sleep(CASPRO_RECOVERY_INTERVAL_SECONDS)


async def run_caspro_sale(transaction_id: str) -> None:
    settings = caspro_settings()
    try:
        record = await asyncio.to_thread(get_payment, transaction_id)
        if not record or not settings:
            return
        amount_kurus = caspro.to_kurus(Decimal(str(record.get("amount") or 0)))
        payload = caspro.build_sale(
            record["terminalSerial"],
            amount_kurus,
            record["externalId"],
            [record.get("printHeader") or "MAGICPAY KIOSK", f"Siparis No: {record.get('reservedOrderNumber') or ''}"],
        )
        try:
            response = await caspro.send_command(settings, payload, settings.transaction_timeout)
        except CasProDeviceOffline:
            await asyncio.to_thread(finalize_caspro_payment, transaction_id, "FAILED", caspro.DEVICE_OFFLINE_MESSAGE)
            return
        except CasProUnavailable:
            # Istek cihaza ulasmis olabilir; sonucu QueryTransaction ile ara.
            await recover_caspro_payment(settings, transaction_id)
            return
        if not await apply_caspro_result(transaction_id, caspro.interpret_transaction_response(response)):
            await recover_caspro_payment(settings, transaction_id)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - arka plan gorevi sessizce olmemeli
        logging.getLogger("caspro").exception("CasPro satis gorevi beklenmeyen hata ile bitti")
        try:
            await asyncio.to_thread(finalize_caspro_payment, transaction_id, "FAILED", "Castles POS cihazında ödeme hatası oluştu.")
        except Exception:  # noqa: BLE001
            logging.getLogger("caspro").exception("CasPro hata sonucu kaydedilemedi")


def spawn_caspro_sale(transaction_id: str) -> asyncio.Task:
    task = asyncio.create_task(run_caspro_sale(transaction_id))
    CASPRO_TASKS[transaction_id] = task
    task.add_done_callback(lambda done: CASPRO_TASKS.pop(transaction_id, None) if CASPRO_TASKS.get(transaction_id) is done else None)
    return task


def find_castles_device_by_serial(conn: Conn, normalized_serial: str) -> dict[str, Any] | None:
    for row in conn.execute("SELECT serial, name, active FROM mk_castles_devices").fetchall():
        if normalize_caspro_serial(row["serial"]) == normalized_serial:
            return row
    return None


async def start_caspro_payment(settings: CasProSettings, payload: PosPaymentCreate, kiosk: dict[str, Any]) -> dict:
    kiosk_id = kiosk["id"]

    def prepare():
        with db.connect() as conn:
            total, lines, _, _ = resolve_order_lines(conn, payload.lines)
            existing = load_payment_by_request(conn, payload.clientRequestId)
            current = fetch_kiosk(conn, kiosk_id)
            device = None
            serial = normalize_caspro_serial((current or {}).get("pos_serial"))
            if serial:
                device = find_castles_device_by_serial(conn, serial)
        return total, lines, existing, current, serial, device

    calculated_total, resolved_lines, existing, current_kiosk_row, terminal_serial, device = await asyncio.to_thread(prepare)
    if abs(calculated_total - round(payload.amount, 2)) > 0.01:
        raise HTTPException(status_code=409, detail="Ödeme tutarı güncel sepet toplamıyla uyuşmuyor")
    enforce_payment_rate_limit(kiosk_id)
    fingerprint = order_fingerprint(payload.paymentMethod, resolved_lines)

    def cached_response(record: dict[str, Any] | None) -> dict | None:
        if not record:
            return None
        if record.get("kioskId") != kiosk_id or record.get("cartFingerprint") != fingerprint:
            raise HTTPException(status_code=409, detail="Ödeme istek anahtarı farklı bir sepet için kullanılmış")
        return payment_response(record)

    cached = cached_response(existing)
    if cached:
        return cached
    if not terminal_serial:
        raise HTTPException(status_code=409, detail="Bu kioska POS cihazı atanmamış.")
    if len(terminal_serial) != 12:
        raise HTTPException(status_code=409, detail="Bu kioska atanan POS seri numarası geçersiz.")
    if device is not None and not device.get("active"):
        raise HTTPException(status_code=409, detail="Bu kioska atanan Castles cihazı pasif durumda.")
    print_header = kiosk_print_name((current_kiosk_row or kiosk).get("name") or "")

    def create():
        with db.connect(write=True) as conn:
            again = load_payment_by_request(conn, payload.clientRequestId)
            if again:
                return again, False
            # Siparis numarasi odeme onayindan once ayrilir; POS fisine basilir ve
            # odeme tamamlaninca siparis ayni numarayla olusturulur.
            reserved_order_number = reserve_order_number(conn, kiosk_id)
            transaction_id = caspro.new_transaction_id()
            now = utc_now_iso()
            record = {
                "clientRequestId": payload.clientRequestId,
                "externalId": transaction_id,
                "transactionId": transaction_id,
                "status": "PROCESSING",
                "message": None,
                "amount": calculated_total,
                "paymentMethod": payload.paymentMethod,
                "cartFingerprint": fingerprint,
                "provider": CASPRO_PROVIDER,
                "kioskId": kiosk_id,
                "printHeader": print_header,
                "reservedOrderNumber": reserved_order_number,
                "terminalSerial": terminal_serial,
                "createdAt": now,
                "updatedAt": now,
            }
            conn.execute(
                """
                INSERT INTO mk_pos_payments (transaction_id, kiosk_id, client_request_id, status, created_at, updated_at,
                                             reserved_order_number, data)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (transaction_id, kiosk_id, payload.clientRequestId, "PROCESSING", now, now, reserved_order_number, dumps(record)),
            )
            return record, True

    try:
        record, created = await asyncio.to_thread(create)
    except INTEGRITY_ERRORS:
        # Ayni clientRequestId ile eszamanli istek: kaydedilmis olani dondur.
        record = await asyncio.to_thread(lambda: _payment_by_request(payload.clientRequestId))
        created = False
        if not record:
            raise
    if not created:
        return cached_response(record)  # type: ignore[return-value]

    # Transaction/Sale banka yaniti gelene kadar surebilir; arka planda calisir.
    task = spawn_caspro_sale(record["transactionId"])
    # Cihaz bagli degilse bulut aninda 404 doner; bunu yakalayip FAILED ile donmek icin kisa sure beklenir.
    await asyncio.wait({task}, timeout=CASPRO_OFFLINE_GRACE_SECONDS)
    latest = await asyncio.to_thread(get_payment, record["transactionId"])
    return payment_response(latest or record)


def _payment_by_request(client_request_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        return load_payment_by_request(conn, client_request_id)


def kiosk_payment(transaction_id: str, kiosk: dict[str, Any]) -> dict[str, Any]:
    record = get_payment(transaction_id) if CASPRO_TRANSACTION_PATTERN.fullmatch(transaction_id) else None
    if not record or record.get("kioskId") != kiosk["id"] or record.get("provider") != CASPRO_PROVIDER:
        raise HTTPException(status_code=404, detail="POS işlemi bulunamadı")
    return record


@app.get("/api/pos/device")
def get_pos_device(kiosk: dict[str, Any] = Depends(current_kiosk)):
    serial = kiosk.get("pos_serial")
    return {
        "name": "Castles POS",
        "provider": CASPRO_PROVIDER,
        "serialNumber": serial,
        "status": "ACTIVE" if serial else "UNASSIGNED",
        "posConfigured": bool(serial),
    }


@app.post("/api/pos/payments", status_code=201)
async def start_pos_payment(payload: PosPaymentCreate, kiosk: dict[str, Any] = Depends(current_kiosk)):
    settings = caspro_settings()
    if not settings:
        raise HTTPException(status_code=503, detail="Castles POS yapılandırılmamış")
    return await start_caspro_payment(settings, payload, kiosk)


@app.get("/api/pos/payments/{transaction_id}")
async def poll_pos_payment(transaction_id: str, kiosk: dict[str, Any] = Depends(current_kiosk)):
    record = await asyncio.to_thread(kiosk_payment, transaction_id, kiosk)
    if record.get("status") in SUCCESSFUL_PAYMENT_STATUSES | FAILED_PAYMENT_STATUSES:
        return payment_response(record)
    settings = caspro_settings()
    if not settings:
        raise HTTPException(status_code=503, detail="Castles POS yapılandırılmamış")
    task = CASPRO_TASKS.get(record["externalId"])
    if task is not None and not task.done():
        return payment_response(record)
    # Arka plan gorevi yok (surec yeniden basladi): sonucu buluttan sor.
    age = caspro_record_age_seconds(record)
    if age >= settings.result_timeout:
        await asyncio.to_thread(finalize_caspro_payment, record["transactionId"], "FAILED", caspro.TIMEOUT_MESSAGE)
    elif age >= CASPRO_POLL_QUERY_GRACE_SECONDS:
        await query_caspro_result(settings, record)
    latest = await asyncio.to_thread(get_payment, record["transactionId"])
    return payment_response(latest or record)


def mark_payment_cancelled(transaction_id: str) -> dict[str, Any] | None:
    with db.connect(write=True) as conn:
        record = load_payment(conn, transaction_id, for_update=True)
        if record and record.get("status") == "PROCESSING":
            record.update({
                "status": "CANCELLED",
                "message": caspro.CANCELLED_MESSAGE,
                "releasedOrderNumber": record.get("reservedOrderNumber"),
                "reservedOrderNumber": None,
                "updatedAt": utc_now_iso(),
            })
            save_payment(conn, record)
        return record


@app.post("/api/pos/payments/{transaction_id}/cancel")
async def cancel_pos_payment(transaction_id: str, kiosk: dict[str, Any] = Depends(current_kiosk)):
    record = await asyncio.to_thread(kiosk_payment, transaction_id, kiosk)
    settings = caspro_settings()
    if not settings:
        raise HTTPException(status_code=503, detail="Castles POS yapılandırılmamış")
    if record.get("status") == "PROCESSING":
        # Onaylanmis satisi geri almaz; yalnizca bekleyen islemi kapatip cihazi bosa dondurur.
        # Cihaz iptali onaylamazsa kart hala bekleniyor olabilir: kayit PROCESSING kalir,
        # sonucu Transaction yaniti belirler (musteri kart okutursa odeme kaybolmaz).
        accepted = await caspro_cancel_quietly(settings, record["terminalSerial"])
        if not accepted:
            return {"status": "PROCESSING", "message": "Cihaz iptali onaylamadı; POS'ta kart bekleniyor."}
        record = await asyncio.to_thread(mark_payment_cancelled, transaction_id) or record
    return {"status": record.get("status"), "message": record.get("message")}


def order_record(row: dict[str, Any]) -> dict[str, Any]:
    data = loads(row["data"])
    section_ids = loads(row.get("section_ids")) or []
    return {
        **data,
        "number": row["number"],
        "status": row.get("status") or data.get("status"),
        "total": float(row["total"]) if row.get("total") is not None else data.get("total"),
        "created_at": row.get("created_at") or data.get("created_at"),
        "kiosk_id": row.get("kiosk_id"),
        "kioskId": row.get("kiosk_id"),
        "section_ids": section_ids,
        "sectionIds": section_ids,
    }


def load_order(conn: Conn, number: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM mk_orders WHERE number = %s", (number,)).fetchone()
    return order_record(row) if row else None


def create_order_transaction(payload: OrderCreate, kiosk_id: str) -> dict[str, Any]:
    with db.connect(write=True) as conn:
        request_row = conn.execute(
            "SELECT order_number FROM mk_order_requests WHERE client_request_id = %s", (payload.clientRequestId,),
        ).fetchone()
        if request_row:
            existing = load_order(conn, request_row["order_number"])
            if existing:
                return existing

        calculated_total, order_lines, requested_quantities, catalog = resolve_order_lines(conn, payload.lines, for_update=True)
        if abs(calculated_total - round(payload.total, 2)) > 0.01:
            raise HTTPException(status_code=409, detail="Sipariş tutarı güncel sepet toplamıyla uyuşmuyor")

        payment = load_payment(conn, payload.paymentReference, for_update=True)
        if not payment or payment.get("externalId") != payload.paymentReference:
            raise HTTPException(status_code=409, detail="Doğrulanmış POS ödemesi bulunamadı")
        if payment.get("kioskId") != kiosk_id:
            raise HTTPException(status_code=409, detail="POS ödemesi farklı bir kioska ait")
        if payment.get("status") not in SUCCESSFUL_PAYMENT_STATUSES:
            raise HTTPException(status_code=409, detail="POS ödemesi başarıyla tamamlanmadı")
        if payload.posTransactionId not in {payment.get("transactionId"), payment.get("externalId")}:
            raise HTTPException(status_code=409, detail="POS işlem referansı eşleşmiyor")
        if payment.get("paymentMethod") != payload.paymentMethod:
            raise HTTPException(status_code=409, detail="Ödeme yöntemi eşleşmiyor")
        if abs(float(payment.get("amount") or 0) - calculated_total) > 0.01:
            raise HTTPException(status_code=409, detail="POS ödeme tutarı siparişle eşleşmiyor")
        if payment.get("cartFingerprint") != order_fingerprint(payload.paymentMethod, order_lines):
            raise HTTPException(status_code=409, detail="POS ödemesi farklı bir sepete ait")
        now = utc_now_iso()
        existing_payment_order = payment.get("orderNumber")
        if existing_payment_order:
            existing = load_order(conn, existing_payment_order)
            if existing:
                conn.execute(
                    "INSERT INTO mk_order_requests (client_request_id, order_number, created_at) VALUES (%s, %s, %s) "
                    "ON CONFLICT (client_request_id) DO NOTHING",
                    (payload.clientRequestId, existing_payment_order, now),
                )
                return existing

        for product_id, quantity in requested_quantities.items():
            product = catalog[product_id]
            if product.get("stockTrackingEnabled") and quantity > int(product.get("stockQuantity") or 0):
                raise HTTPException(status_code=409, detail=f"{product['name']} için yeterli stok yok")
        for product_id, quantity in requested_quantities.items():
            product = catalog[product_id]
            if not product.get("stockTrackingEnabled"):
                continue
            before = int(product.get("stockQuantity") or 0)
            after = before - quantity
            product["stockQuantity"] = after
            save_product(conn, product)
            record_stock(conn, product, before, after, -quantity, "Sipariş sonrası stok düşümü", kiosk_id)

        reserved_number = payment.get("reservedOrderNumber")
        taken = reserved_number and conn.execute("SELECT 1 AS found FROM mk_orders WHERE number = %s", (reserved_number,)).fetchone()
        number = reserved_number if reserved_number and not taken else reserve_order_number(conn, kiosk_id)
        section_ids = list(dict.fromkeys(line["sectionId"] for line in order_lines))
        order = {
            "number": number,
            "status": "preparing",
            "fulfillment": payload.fulfillment,
            "payment_method": payload.paymentMethod,
            "payment_reference": payload.paymentReference,
            "pos_transaction_id": payload.posTransactionId,
            "language": payload.language,
            "receipt_status": "pending",
            "receipt_print_attempt_id": None,
            "total": calculated_total,
            "created_at": now,
            "item_count": sum(line["quantity"] for line in order_lines),
            "lines": order_lines,
            "kiosk_id": kiosk_id,
            "kioskId": kiosk_id,
            "section_ids": section_ids,
            "sectionIds": section_ids,
        }
        conn.execute(
            "INSERT INTO mk_orders (number, kiosk_id, section_ids, status, total, created_at, data) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (number, kiosk_id, dumps(section_ids), "preparing", calculated_total, now, dumps(order)),
        )
        conn.execute(
            "INSERT INTO mk_order_requests (client_request_id, order_number, created_at) VALUES (%s, %s, %s)",
            (payload.clientRequestId, number, now),
        )
        payment["orderNumber"] = number
        payment["updatedAt"] = now
        save_payment(conn, payment)
        return order


@app.post("/api/orders", status_code=201)
def create_order(payload: OrderCreate, kiosk: dict[str, Any] = Depends(current_kiosk)):
    try:
        return create_order_transaction(payload, kiosk["id"])
    except INTEGRITY_ERRORS as error:
        # Eszamanli ayni istek: digeri siparisi olusturduysa onu dondur.
        with db.connect() as conn:
            row = conn.execute(
                "SELECT order_number FROM mk_order_requests WHERE client_request_id = %s", (payload.clientRequestId,),
            ).fetchone()
            existing = load_order(conn, row["order_number"]) if row else None
        if existing:
            return existing
        raise HTTPException(status_code=409, detail="Sipariş eşzamanlı bir istekle çakıştı, lütfen tekrar deneyin") from error


def kiosk_order(conn: Conn, order_number: str, kiosk_id: str) -> dict[str, Any]:
    order = load_order(conn, order_number)
    if not order or order.get("kiosk_id") != kiosk_id:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    return order


@app.get("/api/orders/{order_number}")
def get_order(order_number: str, kiosk: dict[str, Any] = Depends(current_kiosk)):
    with db.connect() as conn:
        return kiosk_order(conn, order_number, kiosk["id"])


@app.post("/api/orders/{order_number}/receipt")
def update_receipt_status(order_number: str, payload: ReceiptStatusUpdate, kiosk: dict[str, Any] = Depends(current_kiosk)):
    with db.connect(write=True) as conn:
        row = conn.execute("SELECT * FROM mk_orders WHERE number = %s FOR UPDATE", (order_number,)).fetchone()
        order = order_record(row) if row else None
        if not order or order.get("kiosk_id") != kiosk["id"]:
            raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
        existing_attempt = order.get("receipt_print_attempt_id")
        if existing_attempt and existing_attempt != payload.printAttemptId:
            return {"status": order.get("receipt_status", "pending"), "alreadyRecorded": True}
        data = loads(row["data"])
        data.update({
            "receipt_status": payload.status,
            "receipt_print_attempt_id": payload.printAttemptId,
            "receipt_device_id": payload.deviceId,
            "receipt_updated_at": utc_now_iso(),
        })
        conn.execute("UPDATE mk_orders SET data = %s WHERE number = %s", (dumps(data), order_number))
        return {"status": payload.status, "alreadyRecorded": False}


# ---- panel: kiosklar --------------------------------------------------------------------

def today_order_summary(conn: Conn) -> dict[str, dict[str, float]]:
    rows = conn.execute(
        "SELECT kiosk_id, COUNT(*) AS n, COALESCE(SUM(total), 0) AS revenue FROM mk_orders WHERE created_at >= %s GROUP BY kiosk_id",
        (local_day_start_utc(date.today()),),
    ).fetchall()
    return {row["kiosk_id"]: {"count": int(row["n"]), "revenue": float(row["revenue"] or 0)} for row in rows}


def is_online(last_seen_at: str | None) -> bool:
    if not last_seen_at:
        return False
    try:
        seen = datetime.fromisoformat(last_seen_at)
    except ValueError:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - seen).total_seconds() <= KIOSK_ONLINE_SECONDS


def kiosk_admin_record(row: dict[str, Any], summary: dict[str, dict[str, float]]) -> dict[str, Any]:
    today = summary.get(row["id"], {"count": 0, "revenue": 0.0})
    return {
        "id": row["id"],
        "name": row["name"],
        "featuredSectionId": row["featured_section_id"],
        "posSerial": row.get("pos_serial"),
        "active": bool(row.get("active")),
        "lastSeenAt": row.get("last_seen_at"),
        "online": is_online(row.get("last_seen_at")),
        "orderPrefix": row.get("order_prefix") or "",
        "orderSeq": int(row.get("order_seq") or 0),
        "appVersion": row.get("app_version"),
        "todayOrderCount": int(today["count"]),
        "todayRevenue": round(float(today["revenue"]), 2),
    }


@app.get("/api/admin/kiosks")
def admin_kiosks():
    with db.connect() as conn:
        rows = conn.execute(KIOSK_SELECT + " ORDER BY k.id").fetchall()
        summary = today_order_summary(conn)
    return [kiosk_admin_record(row, summary) for row in rows]


@app.put("/api/admin/kiosks/{kiosk_id}")
def admin_update_kiosk(kiosk_id: str, payload: KioskUpdate):
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=400, detail="Değişiklik bulunamadı")
    conflict = HTTPException(status_code=409, detail="Bu POS cihazı başka bir kioska atanmış.")
    try:
        with db.connect(write=True) as conn:
            kiosk = conn.execute("SELECT * FROM mk_kiosks WHERE id = %s FOR UPDATE", (kiosk_id,)).fetchone()
            if not kiosk:
                raise HTTPException(status_code=404, detail="Kiosk bulunamadı")
            assignments: list[str] = []
            params: list[Any] = []
            if "name" in values:
                if not values["name"]:
                    raise HTTPException(status_code=400, detail="Kiosk adı boş olamaz")
                assignments.append("name = %s")
                params.append(values["name"])
            if "featuredSectionId" in values:
                section_id = values["featuredSectionId"]
                if not section_id or not fetch_section(conn, section_id):
                    raise HTTPException(status_code=400, detail="Vitrin kategorisi bulunamadı")
                assignments.append("featured_section_id = %s")
                params.append(section_id)
            if "posSerial" in values:
                raw_serial = values["posSerial"]
                serial = None
                if raw_serial:
                    serial = normalize_caspro_serial(raw_serial)
                    if len(serial) != 12:
                        raise HTTPException(status_code=400, detail="POS seri numarası 12 haneli olmalıdır.")
                    other = conn.execute(
                        "SELECT id FROM mk_kiosks WHERE pos_serial = %s AND id <> %s", (serial, kiosk_id),
                    ).fetchone()
                    if other:
                        raise conflict
                assignments.append("pos_serial = %s")
                params.append(serial)
            if "active" in values:
                if values["active"] is None:
                    raise HTTPException(status_code=400, detail="Aktiflik bilgisi geçersiz")
                assignments.append("active = %s")
                params.append(bool(values["active"]))
            if assignments:
                conn.execute(f"UPDATE mk_kiosks SET {', '.join(assignments)} WHERE id = %s", (*params, kiosk_id))
            row = fetch_kiosk(conn, kiosk_id)
            summary = today_order_summary(conn)
    except INTEGRITY_ERRORS as error:
        raise conflict from error
    return kiosk_admin_record(row, summary)


# ---- panel: ana kategoriler --------------------------------------------------------------

def section_counts(conn: Conn) -> tuple[dict[str, int], dict[str, int]]:
    products = {row["section_id"]: int(row["n"]) for row in conn.execute(
        "SELECT section_id, COUNT(*) AS n FROM mk_products GROUP BY section_id").fetchall()}
    categories = {row["section_id"]: int(row["n"]) for row in conn.execute(
        "SELECT section_id, COUNT(*) AS n FROM mk_categories GROUP BY section_id").fetchall()}
    return products, categories


def admin_section_record(conn: Conn, section_id: str) -> dict[str, Any]:
    row = fetch_section(conn, section_id)
    if not row:
        raise HTTPException(status_code=404, detail="Ana kategori bulunamadı")
    products, categories = section_counts(conn)
    return section_record(row, product_count=products.get(section_id, 0), category_count=categories.get(section_id, 0))


@app.get("/api/admin/sections")
def admin_sections():
    with db.connect() as conn:
        rows = fetch_sections(conn)
        products, categories = section_counts(conn)
    return [
        section_record(row, product_count=products.get(row["id"], 0), category_count=categories.get(row["id"], 0))
        for row in rows
    ]


PREFIX_CONFLICT = "Bu sipariş öneki başka bir kategoride kullanılıyor."


@app.post("/api/admin/sections", status_code=201)
def admin_create_section(payload: SectionCreate):
    section_id = slugify(payload.id or payload.name)
    prefix = payload.orderPrefix.upper()
    try:
        with db.connect(write=True) as conn:
            if fetch_section(conn, section_id):
                raise HTTPException(status_code=409, detail="Bu kimlikle bir ana kategori zaten var.")
            if conn.execute("SELECT id FROM mk_sections WHERE order_prefix = %s", (prefix,)).fetchone():
                raise HTTPException(status_code=409, detail=PREFIX_CONFLICT)
            position = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS next FROM mk_sections").fetchone()["next"]
            conn.execute(
                """
                INSERT INTO mk_sections (id, name, name_en, tagline, tagline_en, icon, accent, order_prefix, position, active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (section_id, payload.name, payload.nameEn, payload.tagline, payload.taglineEn, payload.icon or "Store",
                 payload.accent, prefix, int(position), payload.active),
            )
            return admin_section_record(conn, section_id)
    except INTEGRITY_ERRORS as error:
        raise HTTPException(status_code=409, detail=PREFIX_CONFLICT) from error


@app.put("/api/admin/sections/{section_id}")
def admin_update_section(section_id: str, payload: SectionUpdate):
    values = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None}
    columns = {
        "name": "name", "nameEn": "name_en", "tagline": "tagline", "taglineEn": "tagline_en", "icon": "icon",
        "accent": "accent", "orderPrefix": "order_prefix", "position": "position", "active": "active",
    }
    if "orderPrefix" in values:
        values["orderPrefix"] = values["orderPrefix"].upper()
    try:
        with db.connect(write=True) as conn:
            if not fetch_section(conn, section_id):
                raise HTTPException(status_code=404, detail="Ana kategori bulunamadı")
            if "orderPrefix" in values and conn.execute(
                "SELECT id FROM mk_sections WHERE order_prefix = %s AND id <> %s", (values["orderPrefix"], section_id),
            ).fetchone():
                raise HTTPException(status_code=409, detail=PREFIX_CONFLICT)
            if values:
                assignments = ", ".join(f"{columns[key]} = %s" for key in values)
                conn.execute(f"UPDATE mk_sections SET {assignments} WHERE id = %s", (*values.values(), section_id))
            return admin_section_record(conn, section_id)
    except INTEGRITY_ERRORS as error:
        raise HTTPException(status_code=409, detail=PREFIX_CONFLICT) from error


@app.delete("/api/admin/sections/{section_id}", status_code=204)
def admin_delete_section(section_id: str):
    with db.connect(write=True) as conn:
        if not fetch_section(conn, section_id):
            raise HTTPException(status_code=404, detail="Ana kategori bulunamadı")
        if conn.execute("SELECT 1 AS found FROM mk_products WHERE section_id = %s", (section_id,)).fetchone():
            raise HTTPException(status_code=409, detail="Ürün içeren kategori silinemez.")
        if conn.execute("SELECT 1 AS found FROM mk_kiosks WHERE featured_section_id = %s", (section_id,)).fetchone():
            raise HTTPException(status_code=409, detail="Bu kategori bir kioskun vitrin kategorisi; önce kioskları güncelleyin.")
        for row in conn.execute("SELECT id FROM mk_categories WHERE section_id = %s", (section_id,)).fetchall():
            remove_entity_translations(conn, "category", row["id"])
        conn.execute("DELETE FROM mk_categories WHERE section_id = %s", (section_id,))
        conn.execute("DELETE FROM mk_sections WHERE id = %s", (section_id,))


@app.post("/api/admin/sections/reorder")
def admin_reorder_sections(payload: dict[str, list[str]]):
    order = payload.get("ids", [])
    with db.connect(write=True) as conn:
        for index, section_id in enumerate(order):
            conn.execute("UPDATE mk_sections SET position = %s WHERE id = %s", (index, section_id))
    return admin_sections()


# ---- panel: alt kategoriler ---------------------------------------------------------------

def category_product_counts(conn: Conn) -> dict[str, int]:
    return {row["category_id"]: int(row["n"]) for row in conn.execute(
        "SELECT category_id, COUNT(*) AS n FROM mk_products GROUP BY category_id").fetchall()}


@app.get("/api/admin/categories")
def admin_categories(section: str | None = Query(default=None)):
    with db.connect() as conn:
        categories = fetch_categories(conn, clean_filter(section))
        counts = category_product_counts(conn)
    return [serialize_category(item, counts.get(item["id"], 0)) for item in categories]


def require_section_id(conn: Conn, section_id: Any) -> str:
    section_id = str(section_id or "").strip()
    if not section_id:
        raise HTTPException(status_code=400, detail="Ana kategori seçimi zorunlu")
    if not fetch_section(conn, section_id):
        raise HTTPException(status_code=400, detail="Ana kategori bulunamadı")
    return section_id


@app.post("/api/admin/categories", status_code=201)
def create_category(payload: dict[str, Any], background_tasks: BackgroundTasks):
    name = str(payload.get("name") or "Yeni Kategori").strip()
    with db.connect(write=True) as conn:
        section_id = require_section_id(conn, payload.get("sectionId"))
        category_id = unique_id(conn, "mk_categories", slugify(str(payload.get("id") or f"{section_id}-{name}")))
        position = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM mk_categories WHERE section_id = %s", (section_id,),
        ).fetchone()["next"]
        conn.execute(
            "INSERT INTO mk_categories (id, section_id, name, eyebrow, position, active) VALUES (%s, %s, %s, %s, %s, %s)",
            (category_id, section_id, name, str(payload.get("eyebrow") or ""), int(position), bool(payload.get("active", True))),
        )
        category = fetch_category(conn, category_id)
    background_tasks.add_task(sync_translations, "category", category_id)
    return serialize_category(category, 0)


@app.put("/api/admin/categories/{category_id}")
def update_category(category_id: str, payload: dict[str, Any], background_tasks: BackgroundTasks):
    with db.connect(write=True) as conn:
        category = fetch_category(conn, category_id)
        if not category:
            raise HTTPException(status_code=404, detail="Kategori bulunamadı")
        updates: dict[str, Any] = {}
        for key in ("name", "eyebrow"):
            if key in payload and payload[key] is not None:
                updates[key] = str(payload[key])
        if "position" in payload and payload["position"] is not None:
            updates["position"] = int(payload["position"])
        if "active" in payload and payload["active"] is not None:
            updates["active"] = bool(payload["active"])
        if payload.get("sectionId") and payload["sectionId"] != category["sectionId"]:
            updates["section_id"] = require_section_id(conn, payload["sectionId"])
            for product in fetch_products(conn):
                if product["categoryId"] == category_id:
                    product["sectionId"] = updates["section_id"]
                    save_product(conn, product)
        if updates:
            assignments = ", ".join(f"{key} = %s" for key in updates)
            conn.execute(f"UPDATE mk_categories SET {assignments} WHERE id = %s", (*updates.values(), category_id))
        category = fetch_category(conn, category_id)
        count = category_product_counts(conn).get(category_id, 0)
    background_tasks.add_task(sync_translations, "category", category_id)
    return serialize_category(category, count)


@app.delete("/api/admin/categories/{category_id}", status_code=204)
def delete_category(category_id: str, delete_products: bool = Query(False, alias="deleteProducts")):
    with db.connect(write=True) as conn:
        category = fetch_category(conn, category_id)
        if not category:
            raise HTTPException(status_code=404, detail="Kategori bulunamadı")
        product_ids = [row["id"] for row in conn.execute(
            "SELECT id FROM mk_products WHERE category_id = %s", (category_id,)).fetchall()]
        if product_ids and not delete_products:
            raise HTTPException(status_code=400, detail="Ürün içeren kategori silinemez")
        for product_id in product_ids:
            remove_entity_translations(conn, "product", product_id)
        conn.execute("DELETE FROM mk_products WHERE category_id = %s", (category_id,))
        conn.execute("DELETE FROM mk_categories WHERE id = %s", (category_id,))
        remove_entity_translations(conn, "category", category_id)
        remaining = conn.execute(
            "SELECT id FROM mk_categories WHERE section_id = %s ORDER BY position, id", (category["sectionId"],),
        ).fetchall()
        for index, row in enumerate(remaining):
            conn.execute("UPDATE mk_categories SET position = %s WHERE id = %s", (index, row["id"]))


@app.post("/api/admin/categories/reorder")
def reorder_categories(payload: dict[str, list[str]]):
    order = payload.get("ids", [])
    with db.connect(write=True) as conn:
        for index, category_id in enumerate(order):
            conn.execute("UPDATE mk_categories SET position = %s WHERE id = %s", (index, category_id))
    return admin_categories(None)


# ---- panel: urunler ve stok -------------------------------------------------------------

def admin_product_list(section_id: str | None) -> list[dict[str, Any]]:
    with db.connect() as conn:
        categories = fetch_categories(conn)
        products = fetch_products(conn, section_id)
    names = {item["id"]: item["name"] for item in categories}
    return [serialize_product(item, names) for item in products]


def admin_product_record(conn: Conn, product_id: str) -> dict[str, Any]:
    product = find_product(conn, product_id)
    category = fetch_category(conn, product["categoryId"])
    return serialize_product(product, {product["categoryId"]: category["name"] if category else ""})


@app.get("/api/admin/products")
def admin_products(section: str | None = Query(default=None)):
    return admin_product_list(clean_filter(section))


PRODUCT_COLUMN_KEYS = {"id", "sectionId", "categoryId", "position", "active", "categoryName", "available", "unavailableReason"}


@app.post("/api/admin/products", status_code=201)
def create_product(payload: dict[str, Any], background_tasks: BackgroundTasks):
    with db.connect(write=True) as conn:
        section_id = require_section_id(conn, payload.get("sectionId"))
        category = fetch_category(conn, str(payload.get("categoryId") or ""))
        if not category:
            raise HTTPException(status_code=400, detail="Alt kategori seçimi zorunlu")
        if category["sectionId"] != section_id:
            raise HTTPException(status_code=400, detail="Alt kategori seçilen ana kategoriye ait değil")
        product_id = unique_id(conn, "mk_products", slugify(str(payload.get("id") or payload.get("name") or "urun")))
        position = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM mk_products WHERE section_id = %s", (section_id,),
        ).fetchone()["next"]
        data = {key: value for key, value in payload.items() if key not in PRODUCT_COLUMN_KEYS}
        product = {
            "kind": "simple",
            "customization": {},
            **data,
            "id": product_id,
            "sectionId": section_id,
            "categoryId": category["id"],
            "position": int(position),
            "active": bool(payload.get("active", True)),
        }
        conn.execute(
            "INSERT INTO mk_products (id, section_id, category_id, position, active, data) VALUES (%s, %s, %s, %s, %s, %s)",
            (product_id, section_id, category["id"], int(position), product["active"], dumps(product)),
        )
        result = admin_product_record(conn, product_id)
    background_tasks.add_task(sync_translations, "product", product_id)
    return result


@app.put("/api/admin/products/{product_id}")
def update_product(product_id: str, payload: dict[str, Any], background_tasks: BackgroundTasks):
    with db.connect(write=True) as conn:
        product = fetch_products_by_ids(conn, [product_id], for_update=True).get(product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Ürün bulunamadı")
        product.update({key: value for key, value in payload.items() if key not in PRODUCT_COLUMN_KEYS})
        if "position" in payload and payload["position"] is not None:
            product["position"] = int(payload["position"])
        if "active" in payload and payload["active"] is not None:
            product["active"] = bool(payload["active"])
        if payload.get("categoryId") and payload["categoryId"] != product["categoryId"]:
            category = fetch_category(conn, str(payload["categoryId"]))
            if not category:
                raise HTTPException(status_code=400, detail="Alt kategori bulunamadı")
            product["categoryId"] = category["id"]
            product["sectionId"] = category["sectionId"]
        if payload.get("sectionId") and payload["sectionId"] != product["sectionId"]:
            raise HTTPException(status_code=400, detail="Alt kategori seçilen ana kategoriye ait değil")
        save_product(conn, product)
        result = admin_product_record(conn, product_id)
    background_tasks.add_task(sync_translations, "product", product_id)
    return result


@app.delete("/api/admin/products/{product_id}", status_code=204)
def delete_product(product_id: str):
    with db.connect(write=True) as conn:
        conn.execute("DELETE FROM mk_products WHERE id = %s", (product_id,))
        remove_entity_translations(conn, "product", product_id)


@app.get("/api/admin/translations/status")
def admin_translation_status():
    with db.connect() as conn:
        return translation_status_from_db(conn)


@app.post("/api/admin/translations/sync")
def admin_sync_translations():
    return sync_translations()


@app.get("/api/admin/stock")
def stock(section: str | None = Query(default=None)):
    return admin_product_list(clean_filter(section))


@app.post("/api/admin/stock/{product_id}/adjust")
def adjust_stock(product_id: str, payload: StockAdjust):
    with db.connect(write=True) as conn:
        product = fetch_products_by_ids(conn, [product_id], for_update=True).get(product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Ürün bulunamadı")
        before = int(product.get("stockQuantity") or 0)
        if payload.mode == "set":
            after = payload.quantity
        elif payload.mode == "add":
            after = before + payload.quantity
        else:
            after = max(0, before - payload.quantity)
        product["stockTrackingEnabled"] = True
        product["stockQuantity"] = after
        save_product(conn, product)
        record_stock(conn, product, before, after, after - before, payload.note or "Panel stok işlemi", None)
        return admin_product_record(conn, product_id)


@app.get("/api/admin/stock/movements")
def movements(
    section: str | None = Query(default=None),
    kiosk: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
):
    query = "SELECT id, product_id, kiosk_id, data, created_at FROM mk_stock_movements"
    clauses: list[str] = []
    params: list[Any] = []
    if clean_filter(section):
        clauses.append("product_id IN (SELECT id FROM mk_products WHERE section_id = %s)")
        params.append(clean_filter(section))
    if clean_filter(kiosk):
        clauses.append("kiosk_id = %s")
        params.append(clean_filter(kiosk))
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id DESC LIMIT %s"
    params.append(limit)
    with db.connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [{**loads(row["data"]), "id": int(row["id"]), "kiosk_id": row.get("kiosk_id"), "created_at": row["created_at"]} for row in rows]


@app.post("/api/admin/uploads/product-image", status_code=201)
def upload_product_image(payload: UploadPayload):
    match = re.match(r"data:(image/(?:png|jpeg|webp));base64,(.+)", payload.dataUrl)
    if not match:
        raise HTTPException(status_code=400, detail="Geçerli bir görsel seçin")
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[match.group(1)]
    filename = f"{slugify(Path(payload.filename).stem)}-{int(datetime.now().timestamp())}.{ext}"
    (UPLOAD_DIR / filename).write_bytes(base64.b64decode(match.group(2)))
    return {"path": f"/uploads/products/{filename}"}


# ---- panel: Castles cihazlari -----------------------------------------------------------

def castles_device_record(row: dict[str, Any], assignments: dict[str, str]) -> dict[str, Any]:
    return {
        "serial": row["serial"],
        "name": row["name"],
        "active": bool(row["active"]),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "assignedKioskId": assignments.get(normalize_caspro_serial(row["serial"])),
    }


def kiosk_serial_assignments(conn: Conn) -> dict[str, str]:
    return {
        row["pos_serial"]: row["id"]
        for row in conn.execute("SELECT id, pos_serial FROM mk_kiosks WHERE pos_serial IS NOT NULL").fetchall()
    }


def castles_device(conn: Conn, serial: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT serial, name, active, created_at, updated_at FROM mk_castles_devices WHERE serial = %s", (serial,),
    ).fetchone()
    return castles_device_record(row, kiosk_serial_assignments(conn)) if row else None


@app.get("/api/admin/castles/devices")
def admin_list_castles_devices():
    with db.connect() as conn:
        rows = conn.execute("SELECT serial, name, active, created_at, updated_at FROM mk_castles_devices ORDER BY name, serial").fetchall()
        assignments = kiosk_serial_assignments(conn)
    return [castles_device_record(row, assignments) for row in rows]


@app.post("/api/admin/castles/devices", status_code=201)
def admin_create_castles_device(payload: CastlesDeviceCreate):
    duplicate = HTTPException(status_code=409, detail="Bu seri numarasıyla kayıtlı bir Castles cihazı zaten var")
    now = utc_now_iso()
    with db.connect(write=True) as conn:
        result = conn.execute(
            """
            INSERT INTO mk_castles_devices (serial, name, active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (serial) DO NOTHING
            """,
            (payload.serial, payload.name, payload.active, now, now),
        )
        if result.rowcount == 0:
            raise duplicate
        return castles_device(conn, payload.serial)


@app.put("/api/admin/castles/devices/{serial}")
def admin_update_castles_device(serial: str, payload: CastlesDeviceUpdate):
    values = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None}
    if not values:
        raise HTTPException(status_code=400, detail="Değişiklik bulunamadı")
    with db.connect(write=True) as conn:
        assignments = ", ".join(f"{key} = %s" for key in values)
        result = conn.execute(
            f"UPDATE mk_castles_devices SET {assignments}, updated_at = %s WHERE serial = %s",
            (*values.values(), utc_now_iso(), serial),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Castles cihazı bulunamadı")
        return castles_device(conn, serial)


@app.delete("/api/admin/castles/devices/{serial}", status_code=204)
def admin_delete_castles_device(serial: str):
    with db.connect(write=True) as conn:
        device = castles_device(conn, serial)
        if not device:
            raise HTTPException(status_code=404, detail="Castles cihazı bulunamadı")
        if device["assignedKioskId"]:
            raise HTTPException(status_code=409, detail="Bu cihaz bir kioska atanmış; önce kiosktan kaldırın.")
        conn.execute("DELETE FROM mk_castles_devices WHERE serial = %s", (serial,))


# ---- panel: siparisler, gosterge, raporlar ----------------------------------------------

def fetch_orders(conn: Conn, *, kiosk_id: str | None = None, section_id: str | None = None,
                 since: str | None = None, until: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
    query = "SELECT * FROM mk_orders"
    clauses: list[str] = []
    params: list[Any] = []
    if kiosk_id:
        clauses.append("kiosk_id = %s")
        params.append(kiosk_id)
    if section_id:
        clauses.append("section_ids LIKE %s")
        params.append(f'%"{section_id}"%')
    if since:
        clauses.append("created_at >= %s")
        params.append(since)
    if until:
        clauses.append("created_at < %s")
        params.append(until)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC"
    if limit:
        query += " LIMIT %s"
        params.append(limit)
    return [order_record(row) for row in conn.execute(query, params).fetchall()]


def order_amount(order: dict[str, Any], section_id: str | None = None) -> float:
    if not section_id:
        return float(order.get("total") or 0)
    return round(sum(
        float(line.get("unitPrice") or 0) * int(line.get("quantity") or 0)
        for line in order.get("lines", []) if line.get("sectionId") == section_id
    ), 2)


def report_rows(order_list: list[dict[str, Any]], section_id: str | None, products: dict[str, dict[str, Any]],
                categories: dict[str, dict[str, Any]]):
    rows: dict[str, dict[str, Any]] = {}
    for order in order_list:
        for line in order.get("lines", []):
            if section_id and line.get("sectionId") != section_id:
                continue
            product = products.get(line["productId"])
            category = categories.get(product["categoryId"], {}) if product else {}
            product_id = product["id"] if product else line["productId"]
            product_name = product["name"] if product else line.get("name", product_id)
            row = rows.setdefault(product_id, {
                "product_id": product_id, "name": product_name, "category": category.get("name", ""),
                "section_id": line.get("sectionId") or (product or {}).get("sectionId"), "quantity": 0, "revenue": 0.0,
            })
            row["quantity"] += int(line["quantity"])
            row["revenue"] += float(line["unitPrice"]) * int(line["quantity"])
    return sorted(rows.values(), key=lambda item: (item["quantity"], item["revenue"]), reverse=True)


def breakdowns(order_list: list[dict[str, Any]], section_id: str | None):
    kiosks: dict[str, dict[str, Any]] = {}
    sections: dict[str, dict[str, Any]] = {}
    for order in order_list:
        kiosk_row = kiosks.setdefault(order.get("kiosk_id") or "", {"kioskId": order.get("kiosk_id"), "orderCount": 0, "revenue": 0.0})
        kiosk_row["orderCount"] += 1
        kiosk_row["revenue"] += order_amount(order, section_id)
        per_section: dict[str, float] = defaultdict(float)
        for line in order.get("lines", []):
            per_section[line.get("sectionId") or ""] += float(line.get("unitPrice") or 0) * int(line.get("quantity") or 0)
        for line_section, amount in per_section.items():
            if section_id and line_section != section_id:
                continue
            section_row = sections.setdefault(line_section, {"sectionId": line_section, "orderCount": 0, "revenue": 0.0})
            section_row["orderCount"] += 1
            section_row["revenue"] += amount
    for row in [*kiosks.values(), *sections.values()]:
        row["revenue"] = round(row["revenue"], 2)
    return kiosks, sections


@app.get("/api/admin/orders")
def admin_orders(
    kiosk: str | None = Query(default=None),
    section: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=10000),
):
    with db.connect() as conn:
        return fetch_orders(conn, kiosk_id=clean_filter(kiosk), section_id=clean_filter(section), limit=limit)


@app.get("/api/admin/dashboard")
def dashboard(kiosk: str | None = Query(default=None), section: str | None = Query(default=None)):
    kiosk_id, section_id = clean_filter(kiosk), clean_filter(section)
    today_start = local_day_start_utc(date.today())
    with db.connect() as conn:
        sections = fetch_sections(conn)
        categories = fetch_categories(conn, section_id)
        products = fetch_products(conn, section_id)
        all_categories = {item["id"]: item for item in fetch_categories(conn)}
        today_orders = fetch_orders(conn, kiosk_id=kiosk_id, section_id=section_id, since=today_start)
        recent_orders = fetch_orders(conn, kiosk_id=kiosk_id, section_id=section_id, limit=8)
        month_orders = fetch_orders(
            conn, kiosk_id=kiosk_id, section_id=section_id,
            since=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        )
        kiosk_rows = conn.execute(KIOSK_SELECT + " ORDER BY k.id").fetchall()
        product_map = {item["id"]: item for item in fetch_products(conn)}
    shown, _ = visible_products(sections, categories, products)
    kiosk_today, section_today = breakdowns(today_orders, section_id)
    top = report_rows(month_orders, section_id, product_map, all_categories)[:5]
    return {
        "stats": {
            "product_count": len(products),
            "active_product_count": len(shown),
            "category_count": sum(1 for item in categories if item.get("active")),
            "low_stock_count": sum(
                1 for item in products
                if item.get("stockTrackingEnabled") and int(item.get("stockQuantity") or 0) <= int(item.get("criticalStock") or 0)
            ),
            "today_order_count": len(today_orders),
            "today_revenue": round(sum(order_amount(order, section_id) for order in today_orders), 2),
        },
        "recentOrders": recent_orders,
        "topProducts": [{"name": row["name"], "quantity": row["quantity"], "revenue": row["revenue"]} for row in top],
        "kiosks": [
            {
                "kioskId": row["id"],
                "name": row["name"],
                "orderPrefix": row.get("order_prefix") or "",
                "online": is_online(row.get("last_seen_at")),
                "posConfigured": bool(row.get("pos_serial")),
                "orderCount": kiosk_today.get(row["id"], {}).get("orderCount", 0),
                "revenue": kiosk_today.get(row["id"], {}).get("revenue", 0.0),
            }
            for row in kiosk_rows if not kiosk_id or row["id"] == kiosk_id
        ],
        "sections": [
            {
                "sectionId": row["id"],
                "name": row["name"],
                "orderCount": section_today.get(row["id"], {}).get("orderCount", 0),
                "revenue": section_today.get(row["id"], {}).get("revenue", 0.0),
            }
            for row in sections if not section_id or row["id"] == section_id
        ],
    }


@app.get("/api/admin/reports")
def reports(
    start: str = Query(...),
    end: str = Query(...),
    kiosk: str | None = Query(default=None),
    section: str | None = Query(default=None),
):
    kiosk_id, section_id = clean_filter(kiosk), clean_filter(section)
    try:
        start_day = date.fromisoformat(start)
        end_day = date.fromisoformat(end)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Geçersiz tarih aralığı") from error
    with db.connect() as conn:
        candidates = fetch_orders(
            conn, kiosk_id=kiosk_id, section_id=section_id,
            since=local_day_start_utc(start_day), until=local_day_start_utc(end_day + timedelta(days=1)),
        )
        product_map = {item["id"]: item for item in fetch_products(conn)}
        category_map = {item["id"]: item for item in fetch_categories(conn)}
    selected = [order for order in candidates if start <= parse_created_at(order["created_at"]).date().isoformat() <= end]
    daily: dict[str, dict[str, Any]] = {}
    for order in selected:
        day = parse_created_at(order["created_at"]).date().isoformat()
        row = daily.setdefault(day, {"day": day, "order_count": 0, "revenue": 0.0})
        row["order_count"] += 1
        row["revenue"] += order_amount(order, section_id)
    revenue = round(sum(order_amount(order, section_id) for order in selected), 2)
    kiosk_rows, section_rows = breakdowns(selected, section_id)
    return {
        "start": start,
        "end": end,
        "summary": {"order_count": len(selected), "revenue": revenue, "average_order": revenue / len(selected) if selected else 0},
        "products": report_rows(selected, section_id, product_map, category_map),
        "daily": sorted(daily.values(), key=lambda item: item["day"]),
        "kiosks": sorted(kiosk_rows.values(), key=lambda item: item["kioskId"] or ""),
        "sections": sorted(section_rows.values(), key=lambda item: item["sectionId"]),
    }
