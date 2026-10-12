from __future__ import annotations

import base64
import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from itertools import count
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
import psycopg
from psycopg.rows import dict_row

from .catalog import CATEGORIES, PRODUCTS
from .translation import (
    localized_text,
    remove_entity_translations,
    sync_catalog_translations,
    translation_status,
)

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

app = FastAPI(title="Magic Coffee Kiosk API", version="1.0.0")
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

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads" / "products"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR.parent)), name="uploads")

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "store.json"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DATABASE_ALLOWED_HOST = os.getenv("DATABASE_ALLOWED_HOST", "").strip().lower().rstrip(".")
ALLOW_LOCAL_FILE_STORE = os.getenv("ALLOW_LOCAL_FILE_STORE", "").lower() in {"1", "true", "yes"}
STORE_KEY = "magic-coffee"
STATE_LOCK = RLock()
SUCCESSFUL_PAYMENT_STATUSES = {"COMPLETED", "PAID", "SUCCESS", "SUCCEEDED"}
FAILED_PAYMENT_STATUSES = {"FAILED", "ERROR", "CANCELLED", "CANCELED", "DECLINED"}
PAVO_PROVIDER = "PAVO_CLOUD"
ALLOWED_PAVO_GATEWAY_HOSTS = frozenset({"kebo-api-dev.magicpay.ai"})
PAVO_JWT_CACHE: dict[str, Any] = {"token": None, "expires_at": 0.0, "context": None}
PAVO_JWT_LOCK = asyncio.Lock()
PAYMENT_RATE_LIMIT = 20
PAYMENT_RATE_WINDOW_SECONDS = 60
PAYMENT_ATTEMPTS: dict[str, deque[float]] = defaultdict(deque)

ISOLATION_MARKER = "full" + "moon"
FORBIDDEN_DATABASE_MARKERS = frozenset({ISOLATION_MARKER, "kasadb_dev"})


def validate_database_url(database_url: str) -> None:
    if not database_url:
        return
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.username or parsed.path in {"", "/"}:
        raise RuntimeError("DATABASE_URL must identify a dedicated MagicCoffee PostgreSQL database and user.")
    identity = f"{parsed.hostname}/{parsed.username}{parsed.path}".lower()
    if any(marker in identity for marker in FORBIDDEN_DATABASE_MARKERS):
        raise RuntimeError("DATABASE_URL points to a forbidden external project.")
    if DATABASE_ALLOWED_HOST and parsed.hostname.lower().rstrip(".") != DATABASE_ALLOWED_HOST:
        raise RuntimeError("DATABASE_URL does not point to the approved MagicCoffee database host.")


validate_database_url(DATABASE_URL)


StateTuple = tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, str],
    dict[str, dict[str, Any]],
]


def default_state() -> StateTuple:
    return deepcopy(CATEGORIES), deepcopy(PRODUCTS), {}, [], {}, {}, {}


def connect_db():
    if not DATABASE_URL:
        return None
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def state_from_data(data: dict[str, Any]) -> StateTuple:
    return (
        data.get("categories", []),
        data.get("products", []),
        data.get("orders", {}),
        data.get("stockMovements", []),
        data.get("posPayments", {}),
        data.get("orderRequests", {}),
        data.get("translations", {}),
    )


def load_database_state() -> StateTuple | None:
    with connect_db() as conn:
        if conn is None:
            return None
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                data JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pos_gateway_config (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                base_url TEXT NOT NULL,
                allowed_host TEXT NOT NULL,
                terminal_serial TEXT NOT NULL,
                branch_id INTEGER NOT NULL,
                source_fingerprint TEXT NOT NULL,
                provider_type TEXT NOT NULL,
                service_email TEXT NOT NULL,
                service_password TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS castles_devices (
                serial TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        row = conn.execute("SELECT data FROM app_state WHERE key = %s", (STORE_KEY,)).fetchone()
        if not row:
            categories, products, orders, stock_movements, pos_payments, order_requests, translations = default_state()
            conn.execute(
                "INSERT INTO app_state (key, data) VALUES (%s, %s::jsonb)",
                (STORE_KEY, json.dumps({
                    "categories": categories,
                    "products": products,
                    "orders": orders,
                    "stockMovements": stock_movements,
                    "posPayments": pos_payments,
                    "orderRequests": order_requests,
                    "translations": translations,
                }, ensure_ascii=False)),
            )
            return categories, products, orders, stock_movements, pos_payments, order_requests, translations
        return state_from_data(row["data"])


def load_state() -> StateTuple:
    if DATABASE_URL:
        database_state = load_database_state()
        if database_state is not None:
            return database_state
    if not ALLOW_LOCAL_FILE_STORE:
        raise RuntimeError("DATABASE_URL is required. Set the Aiven PostgreSQL connection string before starting the API.")
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text(encoding="utf-8-sig"))
        return state_from_data(data)
    return default_state()


categories, products, orders, stock_movements, pos_payments, order_requests, translations = load_state()
stock_ids = count(1)


def load_local_castles_devices() -> list[dict[str, Any]]:
    # PostgreSQL modunda Castles cihazlari castles_devices tablosunda tutulur;
    # yalnizca yerel dosya modunda JSON durumundaki "castlesDevices" listesi kullanilir.
    if DATABASE_URL or not DATA_FILE.exists():
        return []
    data = json.loads(DATA_FILE.read_text(encoding="utf-8-sig"))
    devices = data.get("castlesDevices", [])
    return devices if isinstance(devices, list) else []


castles_devices: list[dict[str, Any]] = load_local_castles_devices()


def next_order_seed() -> int:
    numbers = []
    # Castles odemeleri siparis numarasini odeme baslarken ayirir; yeniden baslatmada
    # ayrilmis numaralar tekrar kullanilmasin diye onlar da hesaba katilir.
    reserved_numbers = [
        payment.get("reservedOrderNumber")
        for payment in pos_payments.values()
        if isinstance(payment, dict)
    ]
    for order_number in [*orders, *reserved_numbers]:
        match = re.fullmatch(r"MC-(\d+)", order_number or "")
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=300) + 1


order_numbers = count(next_order_seed())


def save_state():
    state = {
        "categories": categories,
        "products": products,
        "orders": orders,
        "stockMovements": stock_movements,
        "posPayments": pos_payments,
        "orderRequests": order_requests,
        "translations": translations,
    }
    if not DATABASE_URL:
        state["castlesDevices"] = castles_devices
    with STATE_LOCK:
        if DATABASE_URL:
            with connect_db() as conn:
                if conn is not None:
                    conn.execute(
                        """
                        INSERT INTO app_state (key, data, updated_at)
                        VALUES (%s, %s::jsonb, now())
                        ON CONFLICT (key) DO UPDATE
                        SET data = EXCLUDED.data, updated_at = now()
                        """,
                        (STORE_KEY, json.dumps(state, ensure_ascii=False)),
                    )
                    return
        if not ALLOW_LOCAL_FILE_STORE:
            raise RuntimeError("DATABASE_URL is required. Refusing to save to local JSON store.")
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


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
    fulfillment: Literal["restaurant", "package"]
    paymentMethod: Literal["card", "meal-card"]
    total: float = Field(gt=0, le=100_000)
    lines: list[OrderLine] = Field(min_length=1)
    # Castles islem kimlikleri "mb-<journal_id>" bicimindedir ve 8 karakterden kisa olabilir.
    paymentReference: str = Field(min_length=4, max_length=160)
    posTransactionId: str = Field(min_length=4, max_length=160)
    language: Literal["tr", "en"] = "tr"


class PosPaymentCreate(BaseModel):
    clientRequestId: str = Field(min_length=8, max_length=120)
    paymentMethod: Literal["card", "meal-card"]
    amount: float = Field(gt=0, le=100_000)
    lines: list[OrderLine] = Field(min_length=1)
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


class PosDeviceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    providerType: Literal["PAVO_CLOUD"] = "PAVO_CLOUD"
    serialNumber: str | None = Field(default=None, max_length=120)
    ipAddress: str | None = Field(default=None, max_length=80)
    port: int | None = Field(default=None, ge=1, le=65535)
    status: Literal["ACTIVE", "PASSIVE", "MAINTENANCE"] = "PASSIVE"
    isDefault: bool = False


class PosDeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    providerType: Literal["PAVO_CLOUD"] | None = None
    serialNumber: str | None = Field(default=None, max_length=120)
    ipAddress: str | None = Field(default=None, max_length=80)
    port: int | None = Field(default=None, ge=1, le=65535)
    status: Literal["ACTIVE", "PASSIVE", "MAINTENANCE"] | None = None
    isDefault: bool | None = None


class PosPairCheck(BaseModel):
    pairingId: int = Field(gt=0)


class PosPairStart(BaseModel):
    fingerprint: str = Field(min_length=1, max_length=120)


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


def slugify(value: str) -> str:
    value = value.lower()
    table = str.maketrans("çğıöşü", "cgiosu")
    value = value.translate(table)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or f"item-{int(datetime.now().timestamp())}"


def category_product_count(category_id: str) -> int:
    return sum(1 for item in products if item["categoryId"] == category_id)


def serialize_category(category: dict[str, Any], language: str = "tr") -> dict[str, Any]:
    record = {**category, "productCount": category_product_count(category["id"])}
    if language != "tr":
        category_id = str(category["id"])
        record["name"] = localized_text(translations, "category", category_id, "name", str(category.get("name") or ""), language)
        record["eyebrow"] = localized_text(translations, "category", category_id, "eyebrow", str(category.get("eyebrow") or ""), language)
    return record


def find_product(product_id: str) -> dict[str, Any]:
    product = next((item for item in products if item["id"] == product_id), None)
    if not product:
        raise HTTPException(status_code=404, detail="Ürün bulunamadı")
    return product


def product_available(product: dict[str, Any]) -> tuple[bool, str | None]:
    if not product.get("active", True) or not product.get("stockSellable", True):
        return False, "Bu ürün şu anda mevcut değil"
    if product.get("stockTrackingEnabled") and int(product.get("stockQuantity") or 0) <= 0:
        return False, "Bu ürün şu anda mevcut değil"
    return True, None


def parse_created_at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def is_kiosk_visible(product: dict[str, Any]) -> bool:
    category = next((item for item in categories if item["id"] == product["categoryId"]), None)
    return bool(category and category.get("active", True) and product.get("active", True) and product_available(product)[0])


def limit_customization_steps(product: dict[str, Any], max_steps: int = 3) -> dict[str, Any]:
    customization = deepcopy(product.get("customization") or {})
    for step_id in list(customization.keys()):
        if not customization[step_id].get("enabled"):
            customization[step_id]["enabled"] = False
    return customization


def localized_customization(product: dict[str, Any], language: str) -> dict[str, Any]:
    customization = limit_customization_steps(product)
    if language == "tr":
        return customization
    product_id = str(product["id"])
    for step_id, step in customization.items():
        title = str(step.get("title") or "")
        step["title"] = localized_text(
            translations, "product", product_id, f"customization.{step_id}.title", title, language,
        )
        for option in step.get("options") or []:
            option_id = str(option.get("id") or "")
            name = str(option.get("name") or "")
            option["name"] = localized_text(
                translations,
                "product",
                product_id,
                f"customization.{step_id}.options.{option_id}.name",
                name,
                language,
            )
    return customization


def serialize_product(product: dict[str, Any], language: str = "tr") -> dict[str, Any]:
    available, reason = product_available(product)
    category = next((item for item in categories if item["id"] == product["categoryId"]), None)
    record = {
        **product,
        "customization": localized_customization(product, language),
        "categoryName": serialize_category(category, language)["name"] if category else "",
        "available": available,
        "unavailableReason": reason,
    }
    if language != "tr":
        product_id = str(product["id"])
        record["name"] = localized_text(translations, "product", product_id, "name", str(product.get("name") or ""), language)
        record["description"] = localized_text(
            translations, "product", product_id, "description", str(product.get("description") or ""), language,
        )
        if reason:
            record["unavailableReason"] = "This product is currently unavailable"
    return record


def active_catalog(language: str = "tr"):
    visible_categories = [serialize_category(item, language) for item in sorted(categories, key=lambda item: item.get("position", 0)) if item.get("active", True)]
    visible_ids = {item["id"] for item in visible_categories}
    visible_products = [
        serialize_product(item, language)
        for item in sorted(products, key=lambda item: item.get("position", 0))
        if item["categoryId"] in visible_ids and is_kiosk_visible(item)
    ]
    return {
        "brand": {"name": "Magic Coffee", "currency": "TL", "version": "2.0.0"},
        "language": language,
        "categories": visible_categories,
        "products": visible_products,
    }


def sync_translations(entity_type: str | None = None, entity_id: str | None = None):
    with STATE_LOCK:
        result = sync_catalog_translations(categories, products, translations, entity_type, entity_id)
        save_state()
        return result


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


def validate_required_steps(product: dict[str, Any], selection: OrderSelection | None):
    if not product.get("customizable", False):
        return
    choices = selection.choices if selection else {}
    for step_id, step in product.get("customization", {}).items():
        if not step.get("enabled") or not step.get("required"):
            continue
        count_selected = len(choices.get(step_id, []))
        if count_selected < int(step.get("minSelect") or 1):
            raise HTTPException(status_code=400, detail=f"{product['name']} için {step['title']} zorunlu")
        if count_selected > int(step.get("maxSelect") or count_selected):
            raise HTTPException(status_code=400, detail=f"{product['name']} için çok fazla seçim yapıldı")


def validate_customization_selection(product: dict[str, Any], selection: OrderSelection | None):
    if not product.get("customizable", False):
        return
    choices = selection.choices if selection else {}
    for step_id, step in product.get("customization", {}).items():
        if not step.get("enabled"):
            continue
        option_ids = choices.get(step_id, [])
        if len(set(option_ids)) != len(option_ids):
            raise HTTPException(status_code=400, detail=f"{product['name']} icin tekrarlanan secim yapildi")
        enabled_options = {option["id"]: option for option in step.get("options", []) if option.get("enabled", True)}
        for option_id in option_ids:
            option = enabled_options.get(option_id)
            if not option:
                raise HTTPException(status_code=400, detail=f"{product['name']} icin gecersiz secim")
            if option.get("available") is False:
                raise HTTPException(status_code=400, detail="Secilen ozellestirme stokta yok")
        count_selected = len(option_ids)
        min_select = int(step.get("minSelect") or (1 if step.get("required") else 0))
        max_select = 1 if step_id == "shot" else int(step.get("maxSelect") or count_selected or 1)
        if count_selected < min_select:
            raise HTTPException(status_code=400, detail=f"{product['name']} icin {step['title']} zorunlu")
        if count_selected > max_select:
            raise HTTPException(status_code=400, detail=f"{product['name']} icin cok fazla secim yapildi")


def resolve_order_lines(lines: list[OrderLine]) -> tuple[float, list[dict[str, Any]], dict[str, int]]:
    calculated_total = 0.0
    resolved: list[dict[str, Any]] = []
    requested_quantities: dict[str, int] = defaultdict(int)
    for line in lines:
        product = find_product(line.productId)
        available, reason = product_available(product)
        if not available:
            raise HTTPException(status_code=409, detail=reason)
        validate_customization_selection(product, line.selection)
        unit_price = round(float(product["price"]) + selected_price_delta(product, line.selection), 2)
        calculated_total += unit_price * line.quantity
        requested_quantities[product["id"]] += line.quantity
        resolved.append({
            "productId": product["id"],
            "name": product["name"],
            "quantity": line.quantity,
            "unitPrice": unit_price,
            "selection": line.selection.model_dump() if line.selection else None,
        })
    return round(calculated_total, 2), resolved, dict(requested_quantities)


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
    }


def record_stock(product: dict[str, Any], before: int | None, after: int | None, quantity: int, note: str):
    stock_movements.insert(0, {
        "id": next(stock_ids),
        "product_id": product["id"],
        "product_name": product["name"],
        "movement_type": "order" if quantity < 0 else "manual",
        "quantity": quantity,
        "before_quantity": before,
        "after_quantity": after,
        "note": note,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/health")
def health():
    if DATABASE_URL:
        with connect_db() as conn:
            conn.execute("SELECT 1").fetchone()
    magicboss = magicboss_settings()
    if magicboss:
        return {
            "status": "ok",
            "service": "magic-coffee-api",
            "database": "postgresql" if DATABASE_URL else "local-file",
            "payment": {
                "status": "ready",
                "provider": MAGICBOSS_PROVIDER,
                "defaultSerial": magicboss.terminal_serial or None,
            },
            "translation": translation_status(translations),
        }
    try:
        pavo_gateway_settings()
        payment_status = "ready"
    except HTTPException:
        payment_status = "disabled"
    return {
        "status": "ok",
        "service": "magic-coffee-api",
        "database": "postgresql" if DATABASE_URL else "local-file",
        "payment": {"status": payment_status, "provider": PAVO_PROVIDER},
        "translation": translation_status(translations),
    }


@app.get("/api/catalog")
def get_catalog(language: Literal["tr", "en"] = Query(default="tr", alias="lang")):
    if language == "en":
        sync_translations()
    return active_catalog(language)


def pavo_gateway_settings() -> tuple[str, int, str, str, tuple[str, str]]:
    env_names = {
        "base_url": "PAVO_GATEWAY_BASE_URL",
        "allowed_host": "PAVO_GATEWAY_ALLOWED_HOST",
        "branch_id": "PAVO_BRANCH_ID",
        "terminal_serial": "PAVO_TERMINAL_SERIAL",
        "source_fingerprint": "PAVO_SOURCE_FINGERPRINT",
        "provider_type": "PAVO_PROVIDER_TYPE",
        "service_email": "PAVO_GATEWAY_SERVICE_EMAIL",
        "service_password": "PAVO_GATEWAY_SERVICE_PASSWORD",
    }
    env_values = {key: os.getenv(name, "").strip() for key, name in env_names.items()}
    values = dict(env_values)
    if DATABASE_URL:
        try:
            with connect_db() as conn:
                stored = conn.execute(
                    """
                    SELECT base_url, allowed_host, branch_id, terminal_serial,
                           source_fingerprint, provider_type, service_email, service_password
                    FROM pos_gateway_config
                    WHERE id = 1
                    """
                ).fetchone()
        except Exception as error:
            raise HTTPException(status_code=503, detail="POS odeme yapilandirmasi okunamadi") from error
        if stored and all(key in stored for key in env_names):
            values = {key: str(stored[key]).strip() for key in env_names}

    base_url = values["base_url"].rstrip("/")
    allowed_host = values["allowed_host"].lower().rstrip(".")
    raw_branch_id = values["branch_id"]
    terminal_serial = values["terminal_serial"]
    source_fingerprint = values["source_fingerprint"]
    provider_type = values["provider_type"]
    service_email = values["service_email"].lower()
    service_password = values["service_password"]
    if not all((base_url, allowed_host, raw_branch_id, terminal_serial, source_fingerprint, provider_type, service_email, service_password)):
        raise HTTPException(status_code=503, detail="POS odeme baglantisi yapilandirilmamis")

    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/api"
        or hostname not in ALLOWED_PAVO_GATEWAY_HOSTS
        or allowed_host not in ALLOWED_PAVO_GATEWAY_HOSTS
        or hostname != allowed_host
    ):
        raise HTTPException(status_code=503, detail="POS gateway ayari gecersiz")
    forbidden_host = ("full" + "moon") + "-api.magicpay.ai"
    if hostname == forbidden_host or hostname.endswith(f".{forbidden_host}") or ISOLATION_MARKER in hostname:
        raise HTTPException(status_code=503, detail="POS gateway hostuna izin verilmiyor")

    try:
        branch_id = int(raw_branch_id)
    except ValueError as error:
        raise HTTPException(status_code=503, detail="POS sube ayari gecersiz") from error
    if (
        branch_id != 2
        or terminal_serial != "PAV960000010"
        or source_fingerprint != "TEST"
        or provider_type != PAVO_PROVIDER
        or len(service_password) < 12
    ):
        raise HTTPException(status_code=503, detail="POS odeme baglantisi ayarlari gecersiz")
    return base_url, branch_id, terminal_serial, source_fingerprint, (service_email, service_password)


def clear_pavo_jwt_cache() -> None:
    PAVO_JWT_CACHE.update(token=None, expires_at=0.0, context=None)


def enforce_payment_rate_limit(request: Request) -> None:
    client_id = request.client.host if request.client else "unknown"
    now = time.monotonic()
    attempts = PAYMENT_ATTEMPTS[client_id]
    while attempts and now - attempts[0] >= PAYMENT_RATE_WINDOW_SECONDS:
        attempts.popleft()
    if len(attempts) >= PAYMENT_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Cok fazla odeme denemesi yapildi. Lutfen birazdan tekrar deneyin")
    attempts.append(now)


def require_kiosk_identity(request: Request, source_fingerprint: str) -> None:
    supplied_fingerprint = request.headers.get("X-MagicCoffee-Kiosk-Fingerprint", "").strip()
    if not supplied_fingerprint or not hmac.compare_digest(supplied_fingerprint, source_fingerprint):
        raise HTTPException(status_code=401, detail="Kiosk kimligi dogrulanamadi")


def expected_kiosk_fingerprint() -> str:
    """Kiosk kimligini Pavo ile ayni kaynaktan okur, Pavo host/sube/seri dogrulamasi yapmaz."""
    fingerprint = ""
    if DATABASE_URL:
        try:
            with connect_db() as conn:
                stored = conn.execute(
                    "SELECT source_fingerprint FROM pos_gateway_config WHERE id = 1"
                ).fetchone()
        except Exception as error:
            raise HTTPException(status_code=503, detail="Kiosk kimlik yapilandirmasi okunamadi") from error
        if stored and stored.get("source_fingerprint"):
            fingerprint = str(stored["source_fingerprint"]).strip()
    if not fingerprint:
        fingerprint = os.getenv("PAVO_SOURCE_FINGERPRINT", "").strip()
    if not fingerprint:
        raise HTTPException(status_code=503, detail="Kiosk kimligi yapilandirilmamis")
    return fingerprint


@dataclass(frozen=True)
class MagicBossSettings:
    base_url: str
    api_key: str
    terminal_serial: str


MAGICBOSS_PROVIDER = "MAGICBOSS"
MAGICBOSS_TRANSACTION_PREFIX = "mb-"
MAGICBOSS_TRANSACTION_PATTERN = re.compile(r"mb-\d{1,18}")
MAGICBOSS_STATUS_MAP = {
    "APPROVED": "COMPLETED",
    "DECLINED": "FAILED",
    "ERROR": "FAILED",
    "TIMEOUT": "FAILED",
    "CANCELLED": "CANCELLED",
}
MAGICBOSS_DEFAULT_MESSAGES = {
    "DECLINED": "Ödeme POS cihazında reddedildi.",
    "ERROR": "Castles POS cihazında ödeme hatası oluştu.",
    "TIMEOUT": "Ödeme POS cihazında zaman aşımına uğradı.",
    "CANCELLED": "Ödeme POS cihazında iptal edildi.",
}


def magicboss_settings() -> MagicBossSettings | None:
    """MAGICBOSS_API_URL tanimliysa odemeler Pavo yerine dogrudan Castles'a gider."""
    base_url = os.getenv("MAGICBOSS_API_URL", "").strip().rstrip("/")
    if not base_url:
        return None
    # Seri numarasi istege baglidir: kiosk kendi cihazini panelde tanimli listeden secer,
    # env'deki seri yalnizca secim yapilmamis kiosklar icin varsayilandir.
    return MagicBossSettings(
        base_url=base_url,
        api_key=os.getenv("MAGICBOSS_API_KEY", "").strip(),
        terminal_serial=os.getenv("MAGICBOSS_TERMINAL_SERIAL", "").strip(),
    )


async def magicboss_request(settings: MagicBossSettings, method: str, path: str, **kwargs) -> dict:
    headers = {"Accept": "application/json"}
    if settings.api_key:
        headers["X-API-Key"] = settings.api_key
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.request(method, f"{settings.base_url}{path}", headers=headers, **kwargs)
    except httpx.HTTPError as error:
        raise HTTPException(status_code=503, detail="Castles POS servisine ulaşılamadı") from error
    try:
        body = response.json() if response.content else {}
    except ValueError:
        # Proxy/HTML hata sayfalari JSON degildir; mesaji kisaltip tasi.
        body = {"detail": response.text[:200]}
    if response.status_code >= 400:
        detail = body.get("detail") if isinstance(body, dict) else None
        raise HTTPException(status_code=502, detail=f"Castles POS isteği başarısız: {detail or response.status_code}")
    return body if isinstance(body, dict) else {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def castles_device_record(row: dict[str, Any]) -> dict[str, Any]:
    def as_text(value: Any) -> Any:
        return value.isoformat() if isinstance(value, datetime) else value

    return {
        "serial": row["serial"],
        "name": row["name"],
        "active": bool(row["active"]),
        "createdAt": as_text(row.get("created_at")),
        "updatedAt": as_text(row.get("updated_at")),
    }


def castles_db_error() -> HTTPException:
    return HTTPException(status_code=503, detail="Castles cihaz listesine erisilemedi")


def list_castles_devices(*, only_active: bool = False) -> list[dict[str, Any]]:
    if DATABASE_URL:
        query = "SELECT serial, name, active, created_at, updated_at FROM castles_devices"
        if only_active:
            query += " WHERE active"
        query += " ORDER BY name, serial"
        try:
            with connect_db() as conn:
                rows = conn.execute(query).fetchall()
        except psycopg.Error as error:
            raise castles_db_error() from error
        return [castles_device_record(row) for row in rows]
    with STATE_LOCK:
        rows = [deepcopy(item) for item in castles_devices if item.get("active") or not only_active]
    return [castles_device_record(row) for row in sorted(rows, key=lambda item: (item["name"], item["serial"]))]


def find_castles_device(serial: str) -> dict[str, Any] | None:
    if DATABASE_URL:
        try:
            with connect_db() as conn:
                row = conn.execute(
                    "SELECT serial, name, active, created_at, updated_at FROM castles_devices WHERE serial = %s",
                    (serial,),
                ).fetchone()
        except psycopg.Error as error:
            raise castles_db_error() from error
        return castles_device_record(row) if row else None
    with STATE_LOCK:
        row = next((item for item in castles_devices if item.get("serial") == serial), None)
        return castles_device_record(row) if row else None


def resolve_castles_serial(settings: MagicBossSettings, requested_serial: str | None) -> str:
    serial = (requested_serial or "").strip()
    if serial:
        device = find_castles_device(serial)
        if not device or not device["active"]:
            raise HTTPException(status_code=400, detail="Seçilen Castles cihazı tanımlı değil")
        return serial
    if settings.terminal_serial:
        return settings.terminal_serial
    raise HTTPException(status_code=503, detail="Castles POS cihazı seçilmedi")


@app.get("/api/admin/castles/devices")
def admin_list_castles_devices():
    return list_castles_devices()


@app.post("/api/admin/castles/devices", status_code=201)
def admin_create_castles_device(payload: CastlesDeviceCreate):
    duplicate = HTTPException(status_code=409, detail="Bu seri numarasıyla kayıtlı bir Castles cihazı zaten var")
    if DATABASE_URL:
        try:
            with connect_db() as conn:
                row = conn.execute(
                    """
                    INSERT INTO castles_devices (serial, name, active)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (serial) DO NOTHING
                    RETURNING serial, name, active, created_at, updated_at
                    """,
                    (payload.serial, payload.name, payload.active),
                ).fetchone()
        except psycopg.Error as error:
            raise castles_db_error() from error
        if not row:
            raise duplicate
        return castles_device_record(row)
    with STATE_LOCK:
        if any(item.get("serial") == payload.serial for item in castles_devices):
            raise duplicate
        now = utc_now_iso()
        row = {"serial": payload.serial, "name": payload.name, "active": payload.active, "created_at": now, "updated_at": now}
        castles_devices.append(row)
        save_state()
        return castles_device_record(row)


@app.put("/api/admin/castles/devices/{serial}")
def admin_update_castles_device(serial: str, payload: CastlesDeviceUpdate):
    values = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None}
    if not values:
        raise HTTPException(status_code=400, detail="Değişiklik bulunamadı")
    not_found = HTTPException(status_code=404, detail="Castles cihazı bulunamadı")
    if DATABASE_URL:
        assignments = [f"{key} = %s" for key in values]
        parameters: list[Any] = [*values.values(), serial]
        try:
            with connect_db() as conn:
                row = conn.execute(
                    f"UPDATE castles_devices SET {', '.join(assignments)}, updated_at = now() "
                    "WHERE serial = %s RETURNING serial, name, active, created_at, updated_at",
                    parameters,
                ).fetchone()
        except psycopg.Error as error:
            raise castles_db_error() from error
        if not row:
            raise not_found
        return castles_device_record(row)
    with STATE_LOCK:
        row = next((item for item in castles_devices if item.get("serial") == serial), None)
        if not row:
            raise not_found
        row.update(values)
        row["updated_at"] = utc_now_iso()
        save_state()
        return castles_device_record(row)


@app.delete("/api/admin/castles/devices/{serial}", status_code=204)
def admin_delete_castles_device(serial: str):
    not_found = HTTPException(status_code=404, detail="Castles cihazı bulunamadı")
    if DATABASE_URL:
        try:
            with connect_db() as conn:
                result = conn.execute("DELETE FROM castles_devices WHERE serial = %s", (serial,))
        except psycopg.Error as error:
            raise castles_db_error() from error
        if result.rowcount == 0:
            raise not_found
        return
    with STATE_LOCK:
        remaining = [item for item in castles_devices if item.get("serial") != serial]
        if len(remaining) == len(castles_devices):
            raise not_found
        castles_devices[:] = remaining
        save_state()


@app.get("/api/pos/castles/devices")
def list_active_castles_devices():
    return [{"serial": item["serial"], "name": item["name"]} for item in list_castles_devices(only_active=True)]


def find_pos_payment(transaction_id: str) -> dict[str, Any] | None:
    with STATE_LOCK:
        return next(
            (
                item for item in pos_payments.values()
                if item.get("transactionId") == transaction_id or item.get("externalId") == transaction_id
            ),
            None,
        )


async def start_magicboss_payment(settings: MagicBossSettings, payload: PosPaymentCreate, request: Request) -> dict:
    require_kiosk_identity(request, expected_kiosk_fingerprint())
    calculated_total, resolved_lines, _ = resolve_order_lines(payload.lines)
    if abs(calculated_total - round(payload.amount, 2)) > 0.01:
        raise HTTPException(status_code=409, detail="Odeme tutari guncel sepet toplamiyla uyusmuyor")
    enforce_payment_rate_limit(request)
    fingerprint = order_fingerprint(payload.paymentMethod, resolved_lines)

    def existing_response() -> dict | None:
        existing = pos_payments.get(payload.clientRequestId)
        if not existing:
            return None
        if existing.get("cartFingerprint") != fingerprint:
            raise HTTPException(status_code=409, detail="Odeme istek anahtari farkli bir sepet icin kullanilmis")
        return payment_response(existing)

    with STATE_LOCK:
        cached = existing_response()
    if cached:
        return cached
    terminal_serial = resolve_castles_serial(settings, payload.terminalSerial)
    with STATE_LOCK:
        cached = existing_response()
        if cached:
            return cached
        # Siparis numarasi odeme onayindan once ayrilir; POS fisine basilir ve
        # odeme tamamlaninca siparis ayni numarayla olusturulur.
        reserved_order_number = f"MC-{next(order_numbers):03d}"
        now = utc_now_iso()
        record = {
            "clientRequestId": payload.clientRequestId,
            "externalId": None,
            "transactionId": None,
            "status": "STARTING",
            "message": None,
            "amount": calculated_total,
            "paymentMethod": payload.paymentMethod,
            "cartFingerprint": fingerprint,
            "provider": MAGICBOSS_PROVIDER,
            "reservedOrderNumber": reserved_order_number,
            "terminalSerial": terminal_serial,
            "createdAt": now,
            "updatedAt": now,
        }
        pos_payments[payload.clientRequestId] = record
        save_state()

    try:
        result = await magicboss_request(
            settings,
            "POST",
            "/number",
            json={
                "number": calculated_total,
                "serial_number": terminal_serial,
                "brand": "MAGICCOFFEE",
                "order_no": reserved_order_number,
            },
        )
    except HTTPException as error:
        with STATE_LOCK:
            record.update({"status": "FAILED", "message": str(error.detail), "updatedAt": utc_now_iso()})
            save_state()
        raise
    journal_id = result.get("journal_id")
    # MagicBoss cihaz cevrimdisiyken de "success" doner; tutar ancak sent_to >= 1 ise cihazdadir.
    try:
        sent_to = int(result.get("sent_to") or 0)
    except (TypeError, ValueError):
        sent_to = 0
    accepted = bool(journal_id) and str(result.get("status", "")).lower() == "success" and sent_to >= 1
    with STATE_LOCK:
        if accepted:
            transaction_id = f"{MAGICBOSS_TRANSACTION_PREFIX}{journal_id}"
            record.update({
                "transactionId": transaction_id,
                "externalId": transaction_id,
                "providerTransactionId": str(journal_id),
                "status": "PROCESSING",
                "message": None,
                "updatedAt": utc_now_iso(),
            })
        else:
            record.update({
                "status": "FAILED",
                "message": "Castles POS cihazı çevrimdışı; ödeme gönderilemedi.",
                "updatedAt": utc_now_iso(),
            })
        save_state()
    return payment_response(record)


async def poll_magicboss_payment(transaction_id: str, request: Request) -> dict:
    require_kiosk_identity(request, expected_kiosk_fingerprint())
    record = find_pos_payment(transaction_id)
    if not record:
        raise HTTPException(status_code=404, detail="POS islemi bulunamadi")
    if record.get("status") in SUCCESSFUL_PAYMENT_STATUSES | FAILED_PAYMENT_STATUSES:
        return payment_response(record)
    journal_id = record.get("providerTransactionId")
    if not journal_id:
        raise HTTPException(status_code=409, detail="POS islemi henuz baslatiliyor")
    settings = magicboss_settings()
    if not settings:
        raise HTTPException(status_code=503, detail="Castles POS yapılandırılmamış")
    # Castles islem sonucunu journal kaydina yazar; kart okutulana kadar durum INITIATED kalir.
    # Gecici ag/HTTP hatalari odemeyi dusurmez: tutar cihazda yasiyor olabilir.
    try:
        result = await magicboss_request(settings, "GET", f"/journal/{journal_id}")
    except HTTPException as error:
        return {
            **payment_response(record),
            "status": "PROCESSING",
            "message": f"Castles cihaz yanıtı bekleniyor ({error.detail}).",
        }
    device_status = str(result.get("status", "")).upper()
    status = MAGICBOSS_STATUS_MAP.get(device_status, "PROCESSING")
    message = None
    if status != "PROCESSING":
        message = result.get("declineReason") or MAGICBOSS_DEFAULT_MESSAGES.get(device_status)
    with STATE_LOCK:
        if record.get("status") != status or record.get("message") != message:
            record.update({"status": status, "message": message, "updatedAt": utc_now_iso()})
            save_state()
    return payment_response(record)


async def pavo_gateway_login(
    client: httpx.AsyncClient,
    base_url: str,
    service_email: str,
    service_password: str,
    *,
    force: bool = False,
) -> str:
    context = hashlib.sha256(f"{base_url}\0{service_email}\0{service_password}".encode()).hexdigest()
    if not force and PAVO_JWT_CACHE["context"] == context and time.monotonic() < PAVO_JWT_CACHE["expires_at"]:
        return str(PAVO_JWT_CACHE["token"])
    async with PAVO_JWT_LOCK:
        if not force and PAVO_JWT_CACHE["context"] == context and time.monotonic() < PAVO_JWT_CACHE["expires_at"]:
            return str(PAVO_JWT_CACHE["token"])
        response = await client.request(
            "POST",
            f"{base_url}/auth/login",
            json={"email": service_email, "password": service_password},
        )
        if response.is_error:
            clear_pavo_jwt_cache()
            raise HTTPException(status_code=503, detail="POS odeme servisi kimlik dogrulamasi basarisiz")
        try:
            body = response.json()
            token = str(body.get("access_token") or "")
            expires_in = int(body.get("expires_in") or 1800)
        except (TypeError, ValueError) as error:
            clear_pavo_jwt_cache()
            raise HTTPException(status_code=502, detail="POS odeme servisinden gecersiz kimlik yaniti alindi") from error
        if not token:
            clear_pavo_jwt_cache()
            raise HTTPException(status_code=502, detail="POS odeme servisinden kimlik bilgisi alinamadi")
        PAVO_JWT_CACHE.update(
            token=token,
            expires_at=time.monotonic() + max(30, expires_in - 60),
            context=context,
        )
        return token


async def pavo_gateway_request(method: str, path: str, payload: dict | None = None) -> dict:
    base_url, branch_id, terminal_serial, source_fingerprint, service_credentials = pavo_gateway_settings()
    service_email, service_password = service_credentials
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            token = await pavo_gateway_login(client, base_url, service_email, service_password)
            headers = {
                "Authorization": f"Bearer {token}",
                "X-MagicCoffee-Branch-ID": str(branch_id),
                "X-MagicCoffee-Terminal-Serial": terminal_serial,
                "X-MagicCoffee-Source-Fingerprint": source_fingerprint,
                "X-MagicCoffee-Provider": PAVO_PROVIDER,
            }
            response = await client.request(method, f"{base_url}{path}", json=payload, headers=headers)
            if response.status_code == 401:
                clear_pavo_jwt_cache()
                token = await pavo_gateway_login(client, base_url, service_email, service_password, force=True)
                headers["Authorization"] = f"Bearer {token}"
                response = await client.request(method, f"{base_url}{path}", json=payload, headers=headers)
    except httpx.RequestError as error:
        raise HTTPException(status_code=502, detail="POS odeme servisine ulasilamadi") from error
    if response.status_code == 204:
        return {}
    try:
        body = response.json()
    except ValueError as error:
        raise HTTPException(status_code=502, detail="POS odeme servisinden gecersiz yanit alindi") from error
    if response.is_error:
        raise HTTPException(status_code=502, detail="POS odeme istegi basarisiz oldu")
    return body


def pos_device_record(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(device.get("id", "")),
        "name": device.get("name") or "POS Terminali",
        "providerType": device.get("provider_type") or PAVO_PROVIDER,
        "serialNumber": device.get("serial_number"),
        "ipAddress": device.get("ip_address"),
        "port": device.get("port"),
        "status": device.get("status") or "PASSIVE",
        "isDefault": bool(device.get("is_default")),
        "cloudSourceFingerprint": device.get("cloud_source_fingerprint"),
        "cloudPairingId": device.get("cloud_pairing_id"),
        "paired": bool(
            device.get("cloud_source_fingerprint")
            and device.get("cloud_pairing_id")
            and device.get("status") == "ACTIVE"
        ),
    }


async def list_pavo_devices(*, only_active: bool = False) -> list[dict[str, Any]]:
    _, branch_id, _, _, _ = pavo_gateway_settings()
    result = await pavo_gateway_request(
        "GET", f"/pavo/devices/{branch_id}?only_active={'true' if only_active else 'false'}",
    )
    return result if isinstance(result, list) else []


async def resolve_pavo_device() -> dict[str, Any]:
    _, branch_id, configured_serial, source_fingerprint, _ = pavo_gateway_settings()
    devices = await list_pavo_devices(only_active=True)
    matches = [
        item for item in devices
        if item.get("serial_number") == configured_serial
        and item.get("branch_id") == branch_id
    ]
    if len(matches) != 1:
        raise HTTPException(status_code=503, detail="Yapilandirilmis POS terminali benzersiz olarak bulunamadi")
    device = matches[0]
    if not (
        device.get("status") == "ACTIVE"
        and device.get("provider_type") == PAVO_PROVIDER
        and device.get("cloud_source_fingerprint") == source_fingerprint
        and device.get("cloud_pairing_id")
    ):
        raise HTTPException(
            status_code=503,
            detail="Yapilandirilmis POS terminali aktif veya eslesmis degil",
        )
    return device


def pos_device_gateway_payload(payload: PosDeviceCreate | PosDeviceUpdate, *, include_branch: bool) -> dict[str, Any]:
    data = payload.model_dump(exclude_none=True)
    field_map = {
        "providerType": "provider_type",
        "serialNumber": "serial_number",
        "ipAddress": "ip_address",
        "isDefault": "is_default",
    }
    result = {field_map.get(key, key): value for key, value in data.items()}
    if include_branch:
        _, branch_id, _, _, _ = pavo_gateway_settings()
        result["branch_id"] = branch_id
    return result


async def find_pavo_device(device_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-fA-F-]{32,36}", device_id):
        raise HTTPException(status_code=400, detail="Gecersiz terminal kimligi")
    _, branch_id, configured_serial, _, _ = pavo_gateway_settings()
    device = next(
        (
            item for item in await list_pavo_devices()
            if str(item.get("id")) == device_id
            and item.get("branch_id") == branch_id
            and item.get("serial_number") == configured_serial
        ),
        None,
    )
    if not device:
        raise HTTPException(status_code=404, detail="POS terminali bulunamadi")
    return device


@app.get("/api/admin/pos/devices")
async def admin_list_pos_devices():
    _, branch_id, configured_serial, _, _ = pavo_gateway_settings()
    return [
        pos_device_record(device)
        for device in await list_pavo_devices()
        if device.get("branch_id") == branch_id and device.get("serial_number") == configured_serial
    ]


@app.post("/api/admin/pos/devices/refresh-status")
async def admin_refresh_pos_device_status():
    raise HTTPException(status_code=405, detail="MagicCoffee terminal kaydi salt okunurdur")


@app.post("/api/admin/pos/devices", status_code=201)
async def admin_create_pos_device(payload: PosDeviceCreate):
    raise HTTPException(status_code=405, detail="MagicCoffee terminal kaydi salt okunurdur")


@app.put("/api/admin/pos/devices/{device_id}")
async def admin_update_pos_device(device_id: str, payload: PosDeviceUpdate):
    raise HTTPException(status_code=405, detail="MagicCoffee terminal kaydi salt okunurdur")


@app.delete("/api/admin/pos/devices/{device_id}", status_code=204)
async def admin_delete_pos_device(device_id: str):
    raise HTTPException(status_code=405, detail="MagicCoffee terminal kaydi salt okunurdur")


@app.post("/api/admin/pos/devices/{device_id}/pair")
async def admin_pair_pos_device(device_id: str, payload: PosPairStart):
    raise HTTPException(status_code=405, detail="MagicCoffee terminal eslestirmesi salt okunurdur")


@app.post("/api/admin/pos/devices/{device_id}/pair/check")
async def admin_check_pos_pairing(device_id: str, payload: PosPairCheck):
    raise HTTPException(status_code=405, detail="MagicCoffee terminal eslestirmesi salt okunurdur")


@app.get("/api/pos/device")
async def get_pos_device():
    magicboss = magicboss_settings()
    if magicboss:
        return {
            "name": "Castles POS",
            "provider": MAGICBOSS_PROVIDER,
            "serialNumber": magicboss.terminal_serial or None,
            "defaultSerial": magicboss.terminal_serial or None,
            "status": "ACTIVE",
        }
    device = await resolve_pavo_device()
    return {
        "name": device.get("name") or "Pavo N96",
        "provider": device.get("provider_type"),
        "serialNumber": device.get("serial_number"),
        "status": device.get("status"),
    }


@app.post("/api/pos/payments", status_code=201)
async def start_pos_payment(payload: PosPaymentCreate, request: Request):
    magicboss = magicboss_settings()
    if magicboss:
        return await start_magicboss_payment(magicboss, payload, request)
    _, branch_id, _, source_fingerprint, _ = pavo_gateway_settings()
    require_kiosk_identity(request, source_fingerprint)
    calculated_total, resolved_lines, _ = resolve_order_lines(payload.lines)
    if abs(calculated_total - round(payload.amount, 2)) > 0.01:
        raise HTTPException(status_code=409, detail="Odeme tutari guncel sepet toplamiyla uyusmuyor")
    enforce_payment_rate_limit(request)
    device = await resolve_pavo_device()
    terminal_serial = device.get("serial_number")
    fingerprint = order_fingerprint(payload.paymentMethod, resolved_lines)
    with STATE_LOCK:
        existing = pos_payments.get(payload.clientRequestId)
        if existing:
            if existing.get("cartFingerprint") != fingerprint:
                raise HTTPException(status_code=409, detail="Odeme istek anahtari farkli bir sepet icin kullanilmis")
            return payment_response(existing)
        external_id = f"MAGICCOFFEE-{uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "clientRequestId": payload.clientRequestId,
            "transactionId": None,
            "externalId": external_id,
            "status": "STARTING",
            "message": None,
            "amount": calculated_total,
            "paymentMethod": payload.paymentMethod,
            "cartFingerprint": fingerprint,
            "createdAt": now,
            "updatedAt": now,
        }
        pos_payments[payload.clientRequestId] = record
        save_state()

    gateway_payload = {
        "terminal_serial": terminal_serial,
        "amount": calculated_total,
        "external_id": record["externalId"],
        "branch_id": branch_id,
        "provider_type": PAVO_PROVIDER,
        "source_fingerprint": source_fingerprint,
        "payment_method": "meal_card" if payload.paymentMethod == "meal-card" else "card",
        "sale_items": [
            {
                "description": line["name"],
                "amount": line["unitPrice"],
                "quantity": line["quantity"],
                "vat_rate": 10,
            }
            for line in resolved_lines
        ],
    }
    try:
        result = await pavo_gateway_request("POST", "/pavo/payment", gateway_payload)
    except HTTPException as error:
        with STATE_LOCK:
            record.update({
                "status": "ERROR",
                "message": str(error.detail),
                "updatedAt": datetime.now(timezone.utc).isoformat(),
            })
            save_state()
        raise
    with STATE_LOCK:
        record.update({
            "transactionId": result.get("id") or record["externalId"],
            "status": str(result.get("status") or "PROCESSING").upper(),
            "message": result.get("error_message"),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        save_state()
    return payment_response(record)


@app.get("/api/pos/payments/{transaction_id}")
async def poll_pos_payment(transaction_id: str, request: Request):
    if MAGICBOSS_TRANSACTION_PATTERN.fullmatch(transaction_id):
        return await poll_magicboss_payment(transaction_id, request)
    castles_record = find_pos_payment(transaction_id)
    if castles_record and castles_record.get("provider") == MAGICBOSS_PROVIDER:
        return await poll_magicboss_payment(transaction_id, request)
    _, _, _, source_fingerprint, _ = pavo_gateway_settings()
    require_kiosk_identity(request, source_fingerprint)
    with STATE_LOCK:
        record = next(
            (
                item for item in pos_payments.values()
                if item.get("transactionId") == transaction_id or item.get("externalId") == transaction_id
            ),
            None,
        )
    if not record:
        raise HTTPException(status_code=404, detail="POS islemi bulunamadi")
    if record.get("status") in SUCCESSFUL_PAYMENT_STATUSES | FAILED_PAYMENT_STATUSES:
        return payment_response(record)
    gateway_id = record.get("transactionId")
    if not gateway_id or gateway_id == record.get("externalId"):
        raise HTTPException(status_code=409, detail="POS islemi henuz baslatiliyor")
    result = await pavo_gateway_request("GET", f"/pavo/cloud/poll/{gateway_id}")
    with STATE_LOCK:
        record.update({
            "status": str(result.get("status") or "PROCESSING").upper(),
            "message": result.get("message") or result.get("error_message"),
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
        save_state()
    return payment_response(record)


@app.post("/api/orders", status_code=201)
def create_order(payload: OrderCreate):
    with STATE_LOCK:
        existing_number = order_requests.get(payload.clientRequestId)
        if existing_number and existing_number in orders:
            return orders[existing_number]

        calculated_total, order_lines, requested_quantities = resolve_order_lines(payload.lines)
        if abs(calculated_total - round(payload.total, 2)) > 0.01:
            raise HTTPException(status_code=409, detail="Sipariş tutarı güncel sepet toplamıyla uyuşmuyor")

        payment = next(
            (item for item in pos_payments.values() if item.get("externalId") == payload.paymentReference),
            None,
        )
        if not payment:
            raise HTTPException(status_code=409, detail="Doğrulanmış POS ödemesi bulunamadı")
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
        existing_payment_order = payment.get("orderNumber")
        if existing_payment_order and existing_payment_order in orders:
            order_requests[payload.clientRequestId] = existing_payment_order
            save_state()
            return orders[existing_payment_order]

        for product_id, quantity in requested_quantities.items():
            product = find_product(product_id)
            if product.get("stockTrackingEnabled") and quantity > int(product.get("stockQuantity") or 0):
                raise HTTPException(status_code=409, detail=f"{product['name']} için yeterli stok yok")
        for line in order_lines:
            product = find_product(line["productId"])
            if product.get("stockTrackingEnabled"):
                before = int(product.get("stockQuantity") or 0)
                after = before - line["quantity"]
                if after < 0:
                    raise HTTPException(status_code=409, detail="Bu ürün şu anda mevcut değil")
                product["stockQuantity"] = after
                record_stock(product, before, after, -line["quantity"], "Sipariş sonrası stok düşümü")

        reserved_number = payment.get("reservedOrderNumber")
        if reserved_number and reserved_number not in orders:
            number = reserved_number
        else:
            number = f"MC-{next(order_numbers):03d}"
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
            "created_at": datetime.now(timezone.utc).isoformat(),
            "item_count": sum(line["quantity"] for line in order_lines),
            "lines": order_lines,
        }
        orders[number] = order
        order_requests[payload.clientRequestId] = number
        payment["orderNumber"] = number
        save_state()
        return order


@app.get("/api/orders/{order_number}")
def get_order(order_number: str):
    if order_number not in orders:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    return orders[order_number]


@app.post("/api/orders/{order_number}/receipt")
def update_receipt_status(order_number: str, payload: ReceiptStatusUpdate):
    with STATE_LOCK:
        order = orders.get(order_number)
        if not order:
            raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
        existing_attempt = order.get("receipt_print_attempt_id")
        if existing_attempt and existing_attempt != payload.printAttemptId:
            return {
                "status": order.get("receipt_status", "pending"),
                "alreadyRecorded": True,
            }
        order["receipt_status"] = payload.status
        order["receipt_print_attempt_id"] = payload.printAttemptId
        order["receipt_device_id"] = payload.deviceId
        order["receipt_updated_at"] = datetime.now(timezone.utc).isoformat()
        save_state()
        return {"status": payload.status, "alreadyRecorded": False}


@app.get("/api/admin/categories")
def admin_categories():
    return [serialize_category(item) for item in sorted(categories, key=lambda item: item.get("position", 0))]


@app.post("/api/admin/categories", status_code=201)
def create_category(payload: dict[str, Any], background_tasks: BackgroundTasks):
    name = payload.get("name", "Yeni Kategori")
    category_id = slugify(payload.get("id") or name)
    category = {"id": category_id, "name": name, "eyebrow": payload.get("eyebrow", "Coffee menüsü"), "position": len(categories), "active": payload.get("active", True)}
    categories.append(category)
    save_state()
    background_tasks.add_task(sync_translations, "category", category_id)
    return serialize_category(category)


@app.put("/api/admin/categories/{category_id}")
def update_category(category_id: str, payload: dict[str, Any], background_tasks: BackgroundTasks):
    category = next((item for item in categories if item["id"] == category_id), None)
    if not category:
        raise HTTPException(status_code=404, detail="Kategori bulunamadı")
    category.update({key: value for key, value in payload.items() if key in {"name", "eyebrow", "position", "active"}})
    save_state()
    background_tasks.add_task(sync_translations, "category", category_id)
    return serialize_category(category)


@app.delete("/api/admin/categories/{category_id}", status_code=204)
def delete_category(category_id: str, delete_products: bool = Query(False, alias="deleteProducts")):
    category = next((item for item in categories if item["id"] == category_id), None)
    if not category:
        raise HTTPException(status_code=404, detail="Kategori bulunamadi")
    product_count = category_product_count(category_id)
    if product_count and not delete_products:
        raise HTTPException(status_code=400, detail="Ürün içeren kategori silinemez")
    if delete_products:
        deleted_product_ids = [item["id"] for item in products if item["categoryId"] == category_id]
        products[:] = [item for item in products if item["categoryId"] != category_id]
        for product_id in deleted_product_ids:
            remove_entity_translations(translations, "product", product_id)
    categories[:] = [item for item in categories if item["id"] != category_id]
    remove_entity_translations(translations, "category", category_id)
    for index, item in enumerate(sorted(categories, key=lambda item: item.get("position", 0))):
        item["position"] = index
    save_state()


@app.post("/api/admin/categories/reorder")
def reorder_categories(payload: dict[str, list[str]]):
    order = payload.get("ids", [])
    for category in categories:
        if category["id"] in order:
            category["position"] = order.index(category["id"])
    save_state()
    return admin_categories()


@app.get("/api/admin/products")
def admin_products():
    return [serialize_product(item) for item in sorted(products, key=lambda item: item.get("position", 0))]


@app.post("/api/admin/products", status_code=201)
def create_product(payload: dict[str, Any], background_tasks: BackgroundTasks):
    product_id = slugify(payload.get("id") or payload.get("name", "urun"))
    product = {**payload, "id": product_id, "position": len(products)}
    products.append(product)
    save_state()
    background_tasks.add_task(sync_translations, "product", product_id)
    return serialize_product(product)


@app.put("/api/admin/products/{product_id}")
def update_product(product_id: str, payload: dict[str, Any], background_tasks: BackgroundTasks):
    product = find_product(product_id)
    product.update({key: value for key, value in payload.items() if key != "id"})
    save_state()
    background_tasks.add_task(sync_translations, "product", product_id)
    return serialize_product(product)


@app.delete("/api/admin/products/{product_id}", status_code=204)
def delete_product(product_id: str):
    products[:] = [item for item in products if item["id"] != product_id]
    remove_entity_translations(translations, "product", product_id)
    save_state()


@app.get("/api/admin/translations/status")
def admin_translation_status():
    return translation_status(translations)


@app.post("/api/admin/translations/sync")
def admin_sync_translations():
    return sync_translations()


@app.get("/api/admin/stock")
def stock():
    return admin_products()


@app.post("/api/admin/stock/{product_id}/adjust")
def adjust_stock(product_id: str, payload: StockAdjust):
    product = find_product(product_id)
    before = int(product.get("stockQuantity") or 0)
    if payload.mode == "set":
        after = payload.quantity
    elif payload.mode == "add":
        after = before + payload.quantity
    else:
        after = max(0, before - payload.quantity)
    product["stockTrackingEnabled"] = True
    product["stockQuantity"] = after
    record_stock(product, before, after, after - before, payload.note or "Panel stok işlemi")
    save_state()
    return serialize_product(product)


@app.get("/api/admin/stock/movements")
def movements():
    return stock_movements


@app.post("/api/admin/uploads/product-image", status_code=201)
def upload_product_image(payload: UploadPayload):
    match = re.match(r"data:(image/(?:png|jpeg|webp));base64,(.+)", payload.dataUrl)
    if not match:
        raise HTTPException(status_code=400, detail="Geçerli bir görsel seçin")
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[match.group(1)]
    filename = f"{slugify(Path(payload.filename).stem)}-{int(datetime.now().timestamp())}.{ext}"
    (UPLOAD_DIR / filename).write_bytes(base64.b64decode(match.group(2)))
    return {"path": f"/uploads/products/{filename}"}


@app.get("/api/admin/orders")
def admin_orders():
    return sorted(orders.values(), key=lambda item: parse_created_at(item["created_at"]), reverse=True)


@app.get("/api/admin/dashboard")
def dashboard():
    today_key = date.today()
    order_list = sorted(orders.values(), key=lambda item: parse_created_at(item["created_at"]), reverse=True)
    today_orders = [order for order in order_list if parse_created_at(order["created_at"]).date() == today_key]
    product_rows = report_rows(list(orders.values()))[:5]
    active_categories = [item for item in categories if item.get("active", True)]
    return {
        "stats": {
            "product_count": len(products),
            "active_product_count": sum(1 for item in products if is_kiosk_visible(item)),
            "category_count": len(active_categories),
            "low_stock_count": sum(1 for item in products if item.get("stockTrackingEnabled") and int(item.get("stockQuantity") or 0) <= int(item.get("criticalStock") or 0)),
            "today_order_count": len(today_orders),
            "today_revenue": sum(order["total"] for order in today_orders),
        },
        "recentOrders": order_list[:8],
        "topProducts": [{"name": row["name"], "quantity": row["quantity"], "revenue": row["revenue"]} for row in product_rows],
    }


def report_rows(order_list: list[dict[str, Any]]):
    rows: dict[str, dict[str, Any]] = {}
    for order in order_list:
        for line in order["lines"]:
            product = next((item for item in products if item["id"] == line["productId"]), None)
            category = next((item for item in categories if item["id"] == product["categoryId"]), {}) if product else {}
            product_id = product["id"] if product else line["productId"]
            product_name = product["name"] if product else line.get("name", product_id)
            row = rows.setdefault(product_id, {"product_id": product_id, "name": product_name, "category": category.get("name", ""), "quantity": 0, "revenue": 0.0})
            row["quantity"] += line["quantity"]
            row["revenue"] += line["unitPrice"] * line["quantity"]
    return sorted(rows.values(), key=lambda item: (item["quantity"], item["revenue"]), reverse=True)


@app.get("/api/admin/reports")
def reports(start: str = Query(...), end: str = Query(...)):
    selected = [order for order in orders.values() if start <= parse_created_at(order["created_at"]).date().isoformat() <= end]
    daily: dict[str, dict[str, Any]] = {}
    for order in selected:
        day = parse_created_at(order["created_at"]).date().isoformat()
        row = daily.setdefault(day, {"day": day, "order_count": 0, "revenue": 0.0})
        row["order_count"] += 1
        row["revenue"] += order["total"]
    revenue = sum(order["total"] for order in selected)
    return {
        "start": start,
        "end": end,
        "summary": {"order_count": len(selected), "revenue": revenue, "average_order": revenue / len(selected) if selected else 0},
        "products": report_rows(selected),
        "daily": sorted(daily.values(), key=lambda item: item["day"]),
    }
