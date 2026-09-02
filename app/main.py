from __future__ import annotations

import base64
import json
import re
from copy import deepcopy
from datetime import date, datetime, timezone
from itertools import count
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .catalog import CATEGORIES, PRODUCTS

app = FastAPI(title="Magic Coffee Kiosk API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5370",
        "http://127.0.0.1:5370",
        "http://localhost:5371",
        "http://127.0.0.1:5371",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads" / "products"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR.parent)), name="uploads")

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "store.json"


def load_state() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text(encoding="utf-8-sig"))
        return data.get("categories", []), data.get("products", []), data.get("orders", {}), data.get("stockMovements", [])
    return deepcopy(CATEGORIES), deepcopy(PRODUCTS), {}, []


categories, products, orders, stock_movements = load_state()
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
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps({
        "categories": categories,
        "products": products,
        "orders": orders,
        "stockMovements": stock_movements,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


class OrderSelection(BaseModel):
    choices: dict[str, list[str]] = Field(default_factory=dict)


class OrderLine(BaseModel):
    productId: str
    name: str = ""
    quantity: int = Field(ge=1, le=99)
    unitPrice: float = 0
    selection: OrderSelection | None = None


class OrderCreate(BaseModel):
    fulfillment: Literal["restaurant", "package"]
    paymentMethod: Literal["card", "meal-card"]
    total: float = 0
    lines: list[OrderLine] = Field(min_length=1)


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


def serialize_category(category: dict[str, Any]) -> dict[str, Any]:
    return {**category, "productCount": category_product_count(category["id"])}


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
    active_steps = [step_id for step_id, step in customization.items() if step.get("enabled")]
    if len(active_steps) <= max_steps:
        return customization

    required_steps = [step_id for step_id in active_steps if customization[step_id].get("required")]
    is_cold = customization.get("ice", {}).get("enabled", False)
    preferred_order = [
        "size",
        "ice" if is_cold else "temperature",
        "milk",
        "syrup",
        "sugar",
        "shot",
        "cream",
        "pairing",
    ]
    kept: list[str] = []
    for step_id in [*required_steps, *preferred_order, *active_steps]:
        if step_id in active_steps and step_id not in kept:
            kept.append(step_id)
        if len(kept) == max_steps:
            break

    for step_id in active_steps:
        if step_id not in kept:
            customization[step_id]["enabled"] = False
    return customization


def serialize_product(product: dict[str, Any]) -> dict[str, Any]:
    available, reason = product_available(product)
    category = next((item for item in categories if item["id"] == product["categoryId"]), None)
    return {
        **product,
        "customization": limit_customization_steps(product),
        "categoryName": category["name"] if category else "",
        "available": available,
        "unavailableReason": reason,
    }


def active_catalog():
    visible_categories = [serialize_category(item) for item in sorted(categories, key=lambda item: item.get("position", 0)) if item.get("active", True)]
    visible_ids = {item["id"] for item in visible_categories}
    visible_products = [
        serialize_product(item)
        for item in sorted(products, key=lambda item: item.get("position", 0))
        if item["categoryId"] in visible_ids and is_kiosk_visible(item)
    ]
    return {"brand": {"name": "Magic Coffee", "currency": "TL", "version": "1.0.0"}, "categories": visible_categories, "products": visible_products}


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
    return {"status": "ok", "service": "magic-coffee-api", "database": "in-memory"}


@app.get("/api/catalog")
def get_catalog():
    return active_catalog()


@app.post("/api/orders", status_code=201)
def create_order(payload: OrderCreate):
    calculated_total = 0.0
    order_lines = []
    requested_quantities: dict[str, int] = {}
    for line in payload.lines:
        product = find_product(line.productId)
        available, reason = product_available(product)
        if not available:
            raise HTTPException(status_code=409, detail=reason)
        validate_required_steps(product, line.selection)
        unit_price = float(product["price"]) + selected_price_delta(product, line.selection)
        calculated_total += unit_price * line.quantity
        requested_quantities[product["id"]] = requested_quantities.get(product["id"], 0) + line.quantity
        order_lines.append({
            "productId": product["id"],
            "name": product["name"],
            "quantity": line.quantity,
            "unitPrice": unit_price,
            "selection": line.selection.model_dump() if line.selection else None,
        })
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
        "total": round(calculated_total, 2),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "item_count": sum(line["quantity"] for line in order_lines),
        "lines": order_lines,
    }
    orders[number] = order
    save_state()
    return order


@app.get("/api/orders/{order_number}")
def get_order(order_number: str):
    if order_number not in orders:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    return orders[order_number]


@app.get("/api/admin/categories")
def admin_categories():
    return [serialize_category(item) for item in sorted(categories, key=lambda item: item.get("position", 0))]


@app.post("/api/admin/categories", status_code=201)
def create_category(payload: dict[str, Any]):
    name = payload.get("name", "Yeni Kategori")
    category_id = slugify(payload.get("id") or name)
    category = {"id": category_id, "name": name, "eyebrow": payload.get("eyebrow", "Coffee menüsü"), "position": len(categories), "active": payload.get("active", True)}
    categories.append(category)
    save_state()
    return serialize_category(category)


@app.put("/api/admin/categories/{category_id}")
def update_category(category_id: str, payload: dict[str, Any]):
    category = next((item for item in categories if item["id"] == category_id), None)
    if not category:
        raise HTTPException(status_code=404, detail="Kategori bulunamadı")
    category.update({key: value for key, value in payload.items() if key in {"name", "eyebrow", "position", "active"}})
    save_state()
    return serialize_category(category)


@app.delete("/api/admin/categories/{category_id}", status_code=204)
def delete_category(category_id: str):
    if category_product_count(category_id):
        raise HTTPException(status_code=400, detail="Ürün içeren kategori silinemez")
    categories[:] = [item for item in categories if item["id"] != category_id]
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
def create_product(payload: dict[str, Any]):
    product_id = slugify(payload.get("id") or payload.get("name", "urun"))
    product = {**payload, "id": product_id, "position": len(products)}
    products.append(product)
    save_state()
    return serialize_product(product)


@app.put("/api/admin/products/{product_id}")
def update_product(product_id: str, payload: dict[str, Any]):
    product = find_product(product_id)
    product.update({key: value for key, value in payload.items() if key != "id"})
    save_state()
    return serialize_product(product)


@app.delete("/api/admin/products/{product_id}", status_code=204)
def delete_product(product_id: str):
    products[:] = [item for item in products if item["id"] != product_id]
    save_state()


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
