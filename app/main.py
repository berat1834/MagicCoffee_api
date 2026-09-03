from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone
from itertools import count
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
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
DATABASE_URL = os.getenv("DATABASE_URL")
ALLOW_LOCAL_FILE_STORE = os.getenv("ALLOW_LOCAL_FILE_STORE", "").lower() in {"1", "true", "yes"}
STORE_KEY = "magic-coffee"
STATE_LOCK = RLock()
SUCCESSFUL_PAYMENT_STATUSES = {"COMPLETED", "PAID", "SUCCESS", "SUCCEEDED"}
FAILED_PAYMENT_STATUSES = {"FAILED", "ERROR", "CANCELLED", "CANCELED", "DECLINED"}

if DATABASE_URL and "USER:PASSWORD@HOST:PORT/DBNAME" in DATABASE_URL:
    raise RuntimeError("Replace magicCoffee_api/.env DATABASE_URL with the real Aiven PostgreSQL connection string.")


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


def next_order_seed() -> int:
    numbers = []
    for order_number in orders:
        match = re.fullmatch(r"MC-(\d+)", order_number)
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
    paymentReference: str = Field(min_length=8, max_length=160)
    posTransactionId: str = Field(min_length=8, max_length=160)
    language: Literal["tr", "en"] = "tr"


class PosPaymentCreate(BaseModel):
    clientRequestId: str = Field(min_length=8, max_length=120)
    paymentMethod: Literal["card", "meal-card"]
    amount: float = Field(gt=0, le=100_000)
    lines: list[OrderLine] = Field(min_length=1)


class PosDeviceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    providerType: Literal["PAVO_CLOUD", "PAVO_REST", "MAGICBOSS"] = "PAVO_CLOUD"
    serialNumber: str | None = Field(default=None, max_length=120)
    ipAddress: str | None = Field(default=None, max_length=80)
    port: int | None = Field(default=None, ge=1, le=65535)
    status: Literal["ACTIVE", "PASSIVE", "MAINTENANCE"] = "PASSIVE"
    isDefault: bool = False


class PosDeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    providerType: Literal["PAVO_CLOUD", "PAVO_REST", "MAGICBOSS"] | None = None
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
    return {
        "status": "ok",
        "service": "magic-coffee-api",
        "database": "postgresql" if DATABASE_URL else "local-file",
        "translation": translation_status(translations),
    }


@app.get("/api/catalog")
def get_catalog(language: Literal["tr", "en"] = Query(default="tr", alias="lang")):
    if language == "en":
        sync_translations()
    return active_catalog(language)


def pavo_gateway_settings() -> tuple[str, str, int]:
    base_url = os.getenv("PAVO_GATEWAY_BASE_URL", "https://fullmoon-api.magicpay.ai/api").strip().rstrip("/")
    terminal_serial = os.getenv("PAVO_TERMINAL_SERIAL", "PAV960000010").strip()
    try:
        branch_id = int(os.getenv("PAVO_BRANCH_ID", "173"))
    except ValueError as error:
        raise HTTPException(status_code=503, detail="POS sube ayari gecersiz") from error
    if not base_url.startswith("https://") or not terminal_serial:
        raise HTTPException(status_code=503, detail="POS baglantisi yapilandirilmamis")
    return base_url, terminal_serial, branch_id


async def pavo_gateway_request(method: str, path: str, payload: dict | None = None) -> dict:
    base_url, _, _ = pavo_gateway_settings()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            response = await client.request(method, f"{base_url}{path}", json=payload)
    except httpx.RequestError as error:
        raise HTTPException(status_code=502, detail="POS odeme servisine ulasilamadi") from error
    if response.status_code == 204:
        return {}
    try:
        body = response.json()
    except ValueError as error:
        raise HTTPException(status_code=502, detail="POS odeme servisinden gecersiz yanit alindi") from error
    if response.is_error:
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("Message")
        raise HTTPException(status_code=502, detail=detail or "POS odeme istegi basarisiz oldu")
    return body


def pos_device_record(device: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(device.get("id", "")),
        "name": device.get("name") or "POS Terminali",
        "providerType": device.get("provider_type") or "PAVO_CLOUD",
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
    _, _, branch_id = pavo_gateway_settings()
    result = await pavo_gateway_request(
        "GET", f"/pavo/devices/{branch_id}?only_active={'true' if only_active else 'false'}",
    )
    return result if isinstance(result, list) else []


async def resolve_pavo_device() -> dict[str, Any]:
    _, configured_serial, _ = pavo_gateway_settings()
    devices = await list_pavo_devices(only_active=True)
    device = next((item for item in devices if item.get("is_default")), None)
    device = device or next((item for item in devices if item.get("serial_number") == configured_serial), None)
    device = device or next(iter(devices), None)
    if not device:
        raise HTTPException(status_code=404, detail="Odeme alabilecek aktif POS terminali bulunamadi")
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
        _, _, branch_id = pavo_gateway_settings()
        result["branch_id"] = branch_id
    return result


async def find_pavo_device(device_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-fA-F-]{32,36}", device_id):
        raise HTTPException(status_code=400, detail="Gecersiz terminal kimligi")
    device = next((item for item in await list_pavo_devices() if str(item.get("id")) == device_id), None)
    if not device:
        raise HTTPException(status_code=404, detail="POS terminali bulunamadi")
    return device


@app.get("/api/admin/pos/devices")
async def admin_list_pos_devices():
    return [pos_device_record(device) for device in await list_pavo_devices()]


@app.post("/api/admin/pos/devices/refresh-status")
async def admin_refresh_pos_device_status():
    _, _, branch_id = pavo_gateway_settings()
    result = await pavo_gateway_request("POST", f"/pavo/cloud/check-status/{branch_id}")
    return {"success": True, "result": result}


@app.post("/api/admin/pos/devices", status_code=201)
async def admin_create_pos_device(payload: PosDeviceCreate):
    if payload.providerType == "PAVO_CLOUD" and not payload.serialNumber:
        raise HTTPException(status_code=422, detail="Pavo Cloud icin terminal seri numarasi gerekli")
    result = await pavo_gateway_request(
        "POST", "/pavo/device", pos_device_gateway_payload(payload, include_branch=True),
    )
    return pos_device_record(result)


@app.put("/api/admin/pos/devices/{device_id}")
async def admin_update_pos_device(device_id: str, payload: PosDeviceUpdate):
    await find_pavo_device(device_id)
    result = await pavo_gateway_request(
        "PUT", f"/pavo/device/{device_id}", pos_device_gateway_payload(payload, include_branch=False),
    )
    return pos_device_record(result)


@app.delete("/api/admin/pos/devices/{device_id}", status_code=204)
async def admin_delete_pos_device(device_id: str):
    await find_pavo_device(device_id)
    await pavo_gateway_request("DELETE", f"/pavo/device/{device_id}")


@app.post("/api/admin/pos/devices/{device_id}/pair")
async def admin_pair_pos_device(device_id: str, payload: PosPairStart):
    device = await find_pavo_device(device_id)
    serial_number = device.get("serial_number")
    if device.get("provider_type") != "PAVO_CLOUD" or not serial_number:
        raise HTTPException(status_code=422, detail="Yalnizca seri numarasi tanimli Pavo Cloud cihazlari eslestirilebilir")
    if not payload.fingerprint.strip():
        raise HTTPException(status_code=422, detail="Parmak izi (fingerprint) gerekli")
    result = await pavo_gateway_request("POST", "/pavo/cloud/pair", {
        "source_fingerprint": payload.fingerprint,
        "target_serial_no": serial_number,
        "application_name": "MagicCoffee",
    })
    data = result.get("Data", {}) if isinstance(result, dict) else {}
    return {
        "success": bool(result.get("Success")),
        "pairingId": data.get("Id"),
        "pairingCode": data.get("PairingCode"),
        "message": result.get("Message") or "Eslestirme istegi terminale gonderildi",
    }


@app.post("/api/admin/pos/devices/{device_id}/pair/check")
async def admin_check_pos_pairing(device_id: str, payload: PosPairCheck):
    device = await find_pavo_device(device_id)
    result = await pavo_gateway_request("POST", "/pavo/cloud/pair/check", {
        "pairing_id": payload.pairingId,
        "target_serial_no": device.get("serial_number"),
    })
    data = result.get("Data", {}) if isinstance(result, dict) else {}
    if data.get("IsApproved"):
        try:
            _, _, branch_id = pavo_gateway_settings()
            await pavo_gateway_request("POST", f"/pavo/cloud/check-status/{branch_id}")
        except HTTPException:
            pass
    return {
        "approved": bool(data.get("IsApproved")),
        "active": bool(data.get("IsActive")),
        "message": result.get("Message"),
    }


@app.get("/api/pos/device")
async def get_pos_device():
    device = await resolve_pavo_device()
    return {
        "name": device.get("name") or "Pavo N96",
        "provider": device.get("provider_type"),
        "serialNumber": device.get("serial_number"),
        "status": device.get("status"),
    }


@app.post("/api/pos/payments", status_code=201)
async def start_pos_payment(payload: PosPaymentCreate):
    calculated_total, resolved_lines, _ = resolve_order_lines(payload.lines)
    if abs(calculated_total - round(payload.amount, 2)) > 0.01:
        raise HTTPException(status_code=409, detail="Odeme tutari guncel sepet toplamiyla uyusmuyor")
    _, _, branch_id = pavo_gateway_settings()
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
async def poll_pos_payment(transaction_id: str):
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
