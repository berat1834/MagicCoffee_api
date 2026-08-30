from __future__ import annotations

from datetime import datetime, timezone
from itertools import count
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .catalog import CATALOG


class OrderSelection(BaseModel):
    ingredients: list[str] = Field(default_factory=list)
    fries: str | None = None
    drink: str | None = None


class OrderLine(BaseModel):
    productId: str
    name: str
    quantity: int = Field(ge=1, le=99)
    unitPrice: float = Field(ge=0)
    selection: OrderSelection | None = None


class OrderCreate(BaseModel):
    fulfillment: Literal["restaurant", "package"]
    paymentMethod: Literal["card", "meal-card"]
    total: float = Field(ge=0)
    lines: list[OrderLine] = Field(min_length=1)


app = FastAPI(title="Magic Burger Kiosk API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

order_numbers = count(101)
orders: dict[str, dict] = {}


@app.get("/health")
def health():
    return {"status": "ok", "service": "magic-burger-api"}


@app.get("/api/catalog")
def get_catalog():
    return CATALOG


@app.post("/api/orders", status_code=201)
def create_order(payload: OrderCreate):
    number = f"MB-{next(order_numbers):03d}"
    order = {
        "number": number,
        "status": "preparing",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        **payload.model_dump(),
    }
    orders[number] = order
    return order


@app.get("/api/orders/{order_number}")
def get_order(order_number: str):
    if order_number not in orders:
        raise HTTPException(status_code=404, detail="Sipariş bulunamadı")
    return orders[order_number]

