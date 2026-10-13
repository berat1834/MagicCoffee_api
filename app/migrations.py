"""Idempotent sema olusturma ve tohumlama.

Yalnizca `mk_` onekli tablolara yazar. Canli Magic Coffee'nin kullandigi `app_state`,
`castles_devices` ve `pos_gateway_config` tablolari yalnizca OKUNUR.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import Conn, Database
from .seed_catalog import SECTIONS, build_section_catalog, kiosk_rows

logger = logging.getLogger("migrations")

SCHEMA_VERSION = 2
COFFEE_SECTION_ID = "kahve"
LEGACY_STORE_KEY = "magic-coffee"
DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "store.json"
# PostgreSQL'de ayni anda baslayan podlarin migrasyonu tek tek calistirmasi icin.
MIGRATION_LOCK_ID = 726_451_017


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def schema_statements(conn: Conn) -> list[str]:
    serial_pk = "BIGSERIAL PRIMARY KEY" if conn.is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return [
        "CREATE TABLE IF NOT EXISTS mk_schema_version (version INTEGER NOT NULL)",
        """
        CREATE TABLE IF NOT EXISTS mk_sections (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            name_en TEXT NOT NULL DEFAULT '',
            tagline TEXT NOT NULL DEFAULT '',
            tagline_en TEXT NOT NULL DEFAULT '',
            icon TEXT NOT NULL DEFAULT 'Store',
            accent TEXT NOT NULL DEFAULT '#684cf0',
            order_prefix TEXT NOT NULL UNIQUE,
            position INTEGER NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS mk_categories (
            id TEXT PRIMARY KEY,
            section_id TEXT NOT NULL REFERENCES mk_sections(id),
            name TEXT NOT NULL,
            eyebrow TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """,
        "CREATE INDEX IF NOT EXISTS mk_categories_section_idx ON mk_categories (section_id)",
        """
        CREATE TABLE IF NOT EXISTS mk_products (
            id TEXT PRIMARY KEY,
            section_id TEXT NOT NULL,
            category_id TEXT NOT NULL REFERENCES mk_categories(id),
            position INTEGER NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            data TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS mk_products_section_idx ON mk_products (section_id)",
        "CREATE INDEX IF NOT EXISTS mk_products_category_idx ON mk_products (category_id)",
        """
        CREATE TABLE IF NOT EXISTS mk_kiosks (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            featured_section_id TEXT NOT NULL REFERENCES mk_sections(id),
            pos_serial TEXT UNIQUE,
            order_seq INTEGER NOT NULL DEFAULT 0,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            last_seen_at TEXT,
            app_version TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS mk_castles_devices (
            serial TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS mk_orders (
            number TEXT PRIMARY KEY,
            kiosk_id TEXT NOT NULL,
            section_ids TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL,
            total NUMERIC(12, 2) NOT NULL,
            created_at TEXT NOT NULL,
            data TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS mk_orders_created_idx ON mk_orders (created_at)",
        "CREATE INDEX IF NOT EXISTS mk_orders_kiosk_idx ON mk_orders (kiosk_id, created_at)",
        """
        CREATE TABLE IF NOT EXISTS mk_pos_payments (
            transaction_id TEXT PRIMARY KEY,
            kiosk_id TEXT NOT NULL,
            client_request_id TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reserved_order_number TEXT,
            data TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS mk_pos_payments_order_idx ON mk_pos_payments (reserved_order_number)",
        """
        CREATE TABLE IF NOT EXISTS mk_order_requests (
            client_request_id TEXT PRIMARY KEY,
            order_number TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS mk_stock_movements (
            id {serial_pk},
            product_id TEXT NOT NULL,
            kiosk_id TEXT,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS mk_stock_movements_product_idx ON mk_stock_movements (product_id)",
        """
        CREATE TABLE IF NOT EXISTS mk_translations (
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            field TEXT NOT NULL,
            language TEXT NOT NULL,
            value TEXT NOT NULL DEFAULT '',
            source_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ready',
            last_attempt_at TEXT,
            PRIMARY KEY (entity_type, entity_id, field, language)
        )
        """,
    ]


def create_schema(conn: Conn) -> None:
    for statement in schema_statements(conn):
        conn.execute(statement)


def current_version(conn: Conn) -> int:
    row = conn.execute("SELECT MAX(version) AS version FROM mk_schema_version").fetchone()
    return int(row["version"]) if row and row["version"] is not None else 0


# ---- tohumlama -----------------------------------------------------------------

def seed_sections(conn: Conn) -> None:
    if conn.execute("SELECT COUNT(*) AS n FROM mk_sections").fetchone()["n"]:
        return
    for section in SECTIONS:
        conn.execute(
            """
            INSERT INTO mk_sections (id, name, name_en, tagline, tagline_en, icon, accent, order_prefix, position, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                section["id"], section["name"], section["name_en"], section["tagline"], section["tagline_en"],
                section["icon"], section["accent"], section["order_prefix"], section["position"], True,
            ),
        )


def seed_kiosks(conn: Conn) -> None:
    if conn.execute("SELECT COUNT(*) AS n FROM mk_kiosks").fetchone()["n"]:
        return
    for kiosk in kiosk_rows():
        conn.execute(
            "INSERT INTO mk_kiosks (id, name, featured_section_id, pos_serial, order_seq, active) VALUES (%s, %s, %s, NULL, 0, %s)",
            (kiosk["id"], kiosk["name"], kiosk["featured_section_id"], True),
        )


def _as_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8")
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def legacy_coffee_state(conn: Conn) -> tuple[dict[str, Any] | None, str]:
    """Canli Magic Coffee durumunu (salt okunur) veya repo tohumunu dondurur."""
    if conn.table_exists("app_state"):
        row = conn.execute("SELECT data FROM app_state WHERE key = %s", (LEGACY_STORE_KEY,)).fetchone()
        data = _as_dict(row["data"]) if row else None
        if data and data.get("products"):
            return data, "app_state"
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8-sig")), "store.json"
    return None, "none"


def upsert_translation(conn: Conn, entity_type: str, entity_id: str, field: str, source: str, value: str) -> None:
    if not source or not value:
        return
    conn.execute(
        """
        INSERT INTO mk_translations (entity_type, entity_id, field, language, value, source_hash, status, last_attempt_at)
        VALUES (%s, %s, %s, 'en', %s, %s, 'ready', %s)
        ON CONFLICT (entity_type, entity_id, field, language) DO NOTHING
        """,
        (entity_type, entity_id, field, value, source_hash(source), utc_now_iso()),
    )


def import_coffee_section(conn: Conn) -> int:
    """Kahve alt kategori ve urunlerini mevcut Magic Coffee menusunden tasir."""
    existing = conn.execute("SELECT COUNT(*) AS n FROM mk_categories WHERE section_id = %s", (COFFEE_SECTION_ID,)).fetchone()["n"]
    if existing:
        return 0
    state, origin = legacy_coffee_state(conn)
    if not state:
        logger.warning("Kahve menusu icin kaynak bulunamadi")
        return 0
    # Diger kategorilerin tohum kimlikleri de ayrilir; cakisan kahve kimligi "kahve-" oneki alir.
    seed_categories, seed_products, _ = build_section_catalog()
    used_categories = {row["id"] for row in conn.execute("SELECT id FROM mk_categories").fetchall()}
    used_categories |= {item["id"] for item in seed_categories}
    used_products = {row["id"] for row in conn.execute("SELECT id FROM mk_products").fetchall()}
    used_products |= {item["id"] for item in seed_products}
    category_map: dict[str, str] = {}
    categories = sorted(state.get("categories") or [], key=lambda item: item.get("position", 0))
    for position, category in enumerate(categories):
        original_id = str(category.get("id") or "").strip()
        if not original_id:
            continue
        new_id = original_id if original_id not in used_categories else f"{COFFEE_SECTION_ID}-{original_id}"
        used_categories.add(new_id)
        category_map[original_id] = new_id
        conn.execute(
            "INSERT INTO mk_categories (id, section_id, name, eyebrow, position, active) VALUES (%s, %s, %s, %s, %s, %s)",
            (new_id, COFFEE_SECTION_ID, str(category.get("name") or new_id), str(category.get("eyebrow") or ""),
             position, bool(category.get("active", True))),
        )
    imported = 0
    product_map: dict[str, str] = {}
    products = sorted(state.get("products") or [], key=lambda item: item.get("position", 0))
    for position, product in enumerate(products):
        original_id = str(product.get("id") or "").strip()
        category_id = category_map.get(str(product.get("categoryId") or ""))
        if not original_id or not category_id:
            continue
        new_id = original_id if original_id not in used_products else f"{COFFEE_SECTION_ID}-{original_id}"
        used_products.add(new_id)
        product_map[original_id] = new_id
        data = {**product, "id": new_id, "categoryId": category_id, "sectionId": COFFEE_SECTION_ID, "position": position}
        conn.execute(
            "INSERT INTO mk_products (id, section_id, category_id, position, active, data) VALUES (%s, %s, %s, %s, %s, %s)",
            (new_id, COFFEE_SECTION_ID, category_id, position, bool(product.get("active", True)),
             json.dumps(data, ensure_ascii=False)),
        )
        imported += 1
    # Hazir Ingilizce ceviriler de (varsa) tasinir.
    for record in (state.get("translations") or {}).values():
        if not isinstance(record, dict) or record.get("status") != "ready" or record.get("language") != "en":
            continue
        entity_type = record.get("entityType")
        mapping = category_map if entity_type == "category" else product_map if entity_type == "product" else {}
        entity_id = mapping.get(str(record.get("entityId") or ""))
        if not entity_id:
            continue
        upsert_translation(conn, entity_type, entity_id, str(record.get("field") or ""),
                           str(record.get("sourceText") or ""), str(record.get("translatedText") or ""))
    logger.info("Kahve menusu %s kaynagindan tasindi: %s urun", origin, imported)
    return imported


def _max_positions(conn: Conn, table: str) -> dict[str, int]:
    rows = conn.execute(f"SELECT section_id, MAX(position) AS position FROM {table} GROUP BY section_id").fetchall()
    return {row["section_id"]: int(row["position"]) for row in rows if row["position"] is not None}


def seed_missing_catalog(conn: Conn) -> dict[str, int]:
    """Kahve disindaki kategorilerin eksik alt kategori/urun/cevirilerini kimlige gore ekler.

    Var olan satirlara (panelden duzenlenmis olabilir) dokunmaz (ON CONFLICT DO NOTHING); silinmis
    ana kategorilere ve kahve kategorisine urun eklemez. Ilk kurulumda (v1) ve v2 yukseltmesinde calisir.
    """
    categories, products, translations = build_section_catalog()
    sections = {row["id"] for row in conn.execute("SELECT id FROM mk_sections").fetchall()}
    category_offsets = _max_positions(conn, "mk_categories")
    product_offsets = _max_positions(conn, "mk_products")
    existing_categories = {row["id"] for row in conn.execute("SELECT id FROM mk_categories").fetchall()}
    existing_products = {row["id"] for row in conn.execute("SELECT id FROM mk_products").fetchall()}
    inserted = {"categories": 0, "products": 0}
    for category in categories:
        section_id = category["section_id"]
        if section_id == COFFEE_SECTION_ID or section_id not in sections or category["id"] in existing_categories:
            continue
        offset = category_offsets[section_id] + 1 if section_id in category_offsets else 0
        result = conn.execute(
            "INSERT INTO mk_categories (id, section_id, name, eyebrow, position, active) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (category["id"], section_id, category["name"], category["eyebrow"], offset + category["position"], True),
        )
        if result.rowcount:
            existing_categories.add(category["id"])
            inserted["categories"] += 1
    new_products: set[str] = set()
    for product in products:
        section_id = product["section_id"]
        if section_id == COFFEE_SECTION_ID or product["id"] in existing_products or product["category_id"] not in existing_categories:
            continue
        offset = product_offsets[section_id] + 1 if section_id in product_offsets else 0
        data = {**product["data"], "position": offset + product["position"]}
        result = conn.execute(
            "INSERT INTO mk_products (id, section_id, category_id, position, active, data) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (product["id"], section_id, product["category_id"], data["position"], True, json.dumps(data, ensure_ascii=False)),
        )
        if result.rowcount:
            new_products.add(product["id"])
            inserted["products"] += 1
    for entity_type, entity_id, field, source, value in translations:
        if entity_id in existing_categories or entity_id in existing_products or entity_id in new_products:
            upsert_translation(conn, entity_type, entity_id, field, source, value)
    return inserted


def copy_castles_devices(conn: Conn) -> int:
    if not conn.table_exists("castles_devices"):
        return 0
    rows = conn.execute("SELECT serial, name, active, created_at, updated_at FROM castles_devices").fetchall()
    copied = 0
    for row in rows:
        created = row.get("created_at")
        updated = row.get("updated_at")
        created_text = created.isoformat() if isinstance(created, datetime) else str(created or utc_now_iso())
        updated_text = updated.isoformat() if isinstance(updated, datetime) else str(updated or created_text)
        result = conn.execute(
            """
            INSERT INTO mk_castles_devices (serial, name, active, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (serial) DO NOTHING
            """,
            (row["serial"], row["name"], bool(row["active"]), created_text, updated_text),
        )
        copied += max(0, result.rowcount)
    return copied


def run_migrations(database: Database) -> None:
    with database.connect(write=True) as conn:
        if conn.is_postgres:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
        create_schema(conn)
        version = current_version(conn)
        if version >= SCHEMA_VERSION:
            return
        if version < 1:
            seed_sections(conn)
            seed_kiosks(conn)
            import_coffee_section(conn)
            copy_castles_devices(conn)
        if version < 2:
            # v2: genisletilmis katalog; yalnizca eksik alt kategori/urun/ceviriler eklenir.
            inserted = seed_missing_catalog(conn)
            logger.info("Katalog tohumu tamamlandi: %s", inserted)
        conn.execute("DELETE FROM mk_schema_version")
        conn.execute("INSERT INTO mk_schema_version (version) VALUES (%s)", (SCHEMA_VERSION,))
