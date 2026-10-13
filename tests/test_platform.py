import json
import unittest
from unittest.mock import patch

from support import SERIAL_12, SERIAL_16, PlatformTestCase, api, db

from app import migrations
from app.seed_catalog import SECTIONS


class KioskAndCatalogTests(PlatformTestCase):
    def test_kiosk_config_for_k05(self):
        response = self.client.get("/api/kiosk/config", headers=self.kiosk("k05"))
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["kiosk"], {
            "id": "k05", "name": "Kiosk 05 · Salata & Bowl", "featuredSectionId": "salata",
            "orderPrefix": "SLT", "posConfigured": False,
        })
        self.assertEqual(body["brand"], {"name": "MagicPay Kiosk", "currency": "TL"})
        self.assertEqual([item["id"] for item in body["sections"]], [item["id"] for item in SECTIONS])
        first = body["sections"][0]
        self.assertEqual(set(first) >= {"id", "name", "tagline", "icon", "accent", "productCount", "position"}, True)
        self.assertEqual(first["icon"], "Coffee")
        self.assertGreater(first["productCount"], 30)
        english = self.client.get("/api/kiosk/config?lang=en", headers=self.kiosk("k05")).json()
        self.assertEqual(english["sections"][4]["name"], "Salads & Bowls")

    def test_kiosk_header_rules_and_last_seen(self):
        self.assertEqual(self.client.get("/api/kiosk/config").status_code, 403)
        self.assertEqual(self.client.get("/api/kiosk/config", headers=self.kiosk("k42")).status_code, 403)
        self.assertEqual(self.client.get("/api/kiosk/config", headers=self.kiosk("bad id!")).status_code, 403)
        self.client.get("/api/kiosk/config", headers={"X-Kiosk-Id": "K02", "X-App-Version": "3.0.0"})
        kiosks = {row["id"]: row for row in self.client.get("/api/admin/kiosks").json()}
        self.assertTrue(kiosks["k02"]["online"])
        self.assertIsNotNone(kiosks["k02"]["lastSeenAt"])
        self.assertEqual(kiosks["k02"]["appVersion"], "3.0.0")
        self.assertFalse(kiosks["k03"]["online"])
        # 30 sn icinde ikinci istek DB'ye yazmaz.
        with patch.object(api.db, "connect", wraps=api.db.connect) as connect:
            self.client.get("/api/kiosk/config", headers=self.kiosk("k02"))
            write_calls = [call for call in connect.call_args_list if call.kwargs.get("write")]
        self.assertEqual(write_calls, [])

    def test_catalog_per_section_and_all(self):
        pizza = self.client.get("/api/catalog?section=pizza", headers=self.kiosk("k05"))
        self.assertEqual(pizza.status_code, 200)
        body = pizza.json()
        self.assertEqual(body["brand"]["name"], "MagicPay Kiosk")
        self.assertEqual(body["section"]["id"], "pizza")
        self.assertEqual(body["section"]["accent"], "#dc2626")
        self.assertTrue(body["products"])
        self.assertTrue(all(item["sectionId"] == "pizza" for item in body["products"]))
        self.assertTrue(all(item["sectionId"] == "pizza" for item in body["categories"]))
        margarita = next(item for item in body["products"] if item["id"] == "pizza-margarita")
        self.assertEqual(margarita["price"], 249.0)
        self.assertEqual(margarita["emoji"], "🍕")
        self.assertTrue(margarita["customization"]["sides"]["required"])

        english = self.client.get("/api/catalog?section=pizza&lang=en").json()
        self.assertEqual(next(item for item in english["products"] if item["id"] == "pizza-margarita")["name"], "Margherita Pizza")
        self.assertEqual(english["section"]["name"], "Pizza")
        giyim = self.client.get("/api/catalog?section=giyim").json()
        tshirt = next(item for item in giyim["products"] if item["id"] == "giyim-basic-tisort")
        self.assertEqual(tshirt["customization"]["sides"]["title"], "Beden Seçimi")
        self.assertEqual([option["name"] for option in tshirt["customization"]["sides"]["options"]], ["S", "M", "L", "XL"])

        everything = self.client.get("/api/catalog").json()
        self.assertIsNone(everything["section"])
        self.assertEqual({item["sectionId"] for item in everything["products"]}, {item["id"] for item in SECTIONS})
        self.assertEqual(self.client.get("/api/catalog?section=yok").status_code, 404)
        coffee = self.client.get("/api/catalog?section=kahve").json()
        self.assertEqual(len(coffee["categories"]), 10)
        self.assertTrue(any(item["id"] == "latte" and item["image"].endswith("latte.png") for item in coffee["products"]))

    def test_cors_preflight_allows_kiosk_header_from_native_origin(self):
        for origin in ("https://localhost", "http://localhost", "capacitor://localhost"):
            response = self.client.options("/api/kiosk/config", headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-kiosk-id,content-type",
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers["access-control-allow-origin"], origin)
            allowed = response.headers["access-control-allow-headers"].lower()
            self.assertIn("x-kiosk-id", allowed)
            self.assertIn("content-type", allowed)
            self.assertIn("POST", response.headers["access-control-allow-methods"])

    def test_health_reports_pool(self):
        body = self.client.get("/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["database"], "sqlite")
        self.assertEqual(body["pool"], {"size": 5, "available": 5, "waiting": 0})
        self.assertEqual(body["payment"]["provider"], "CASPRO")

    def test_pool_exhaustion_returns_503(self):
        slots = db._sqlite_slots
        held = 0
        with patch.object(db, "timeout", 0.1):
            while slots.acquire(blocking=False):
                held += 1
            try:
                self.assertEqual(self.client.get("/health").status_code, 503)
                response = self.client.get("/api/kiosk/config", headers=self.kiosk("k01"))
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["detail"], "Sunucu meşgul, lütfen tekrar deneyin.")
                self.assertEqual(self.client.get("/api/admin/kiosks").status_code, 503)
            finally:
                for _ in range(held):
                    slots.release()
        self.assertEqual(held, 5)
        self.assertEqual(self.client.get("/api/kiosk/config", headers=self.kiosk("k01")).status_code, 200)

    def test_postgres_pool_timeout_maps_to_busy(self):
        from psycopg_pool import PoolTimeout

        from app.db import Database, DatabaseBusy

        class FakePool:
            def getconn(self, timeout=None):
                raise PoolTimeout("dolu")

        fake = Database()
        fake.dialect = "postgres"
        fake.pool = FakePool()
        with self.assertRaises(DatabaseBusy):
            with fake.connect():
                pass

    def test_database_url_validation_kept(self):
        forbidden_marker = "full" + "moon"
        with self.assertRaises(RuntimeError):
            api.validate_database_url(f"postgresql://magiccoffee:secret@{forbidden_marker}-db.example/coffee")
        with self.assertRaises(RuntimeError):
            api.validate_database_url("postgresql://magiccoffee:secret@kebo-db.example/kasadb_dev")
        with patch.object(api, "DATABASE_ALLOWED_HOST", "coffee-db.example"):
            api.validate_database_url("postgresql://magiccoffee:secret@coffee-db.example/magiccoffee")
            with self.assertRaises(RuntimeError):
                api.validate_database_url("postgresql://magiccoffee:secret@other-db.example/magiccoffee")


class AdminTests(PlatformTestCase):
    def test_admin_kiosks_and_pos_assignment(self):
        kiosks = self.client.get("/api/admin/kiosks").json()
        self.assertEqual([row["id"] for row in kiosks], [f"k{index:02d}" for index in range(1, 18)])
        self.assertEqual(kiosks[16]["featuredSectionId"], "cicek")
        self.assertEqual(kiosks[16]["orderPrefix"], "CCK")
        self.assertEqual(kiosks[0]["todayOrderCount"], 0)

        assigned = self.client.put("/api/admin/kiosks/k01", json={"posSerial": SERIAL_16, "name": "Giriş Kiosku"})
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.json()["posSerial"], SERIAL_12)
        self.assertEqual(assigned.json()["name"], "Giriş Kiosku")
        conflict = self.client.put("/api/admin/kiosks/k02", json={"posSerial": SERIAL_12})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"], "Bu POS cihazı başka bir kioska atanmış.")
        self.assertEqual(self.client.put("/api/admin/kiosks/k02", json={"posSerial": "12345"}).status_code, 400)
        self.assertEqual(self.client.put("/api/admin/kiosks/k02", json={"featuredSectionId": "yok"}).status_code, 400)
        self.assertEqual(self.client.put("/api/admin/kiosks/k99", json={"active": False}).status_code, 404)

        moved = self.client.put("/api/admin/kiosks/k02", json={"featuredSectionId": "pizza"})
        self.assertEqual(moved.json()["orderPrefix"], "PZA")
        removed = self.client.put("/api/admin/kiosks/k01", json={"posSerial": None})
        self.assertIsNone(removed.json()["posSerial"])
        self.assertEqual(self.client.put("/api/admin/kiosks/k02", json={"posSerial": SERIAL_12}).status_code, 200)
        config = self.client.get("/api/kiosk/config", headers=self.kiosk("k02")).json()
        self.assertTrue(config["kiosk"]["posConfigured"])

    def test_admin_sections_crud(self):
        sections = self.client.get("/api/admin/sections").json()
        self.assertEqual(len(sections), 17)
        self.assertEqual(sections[0]["productCount"], 39)
        self.assertEqual(sections[0]["categoryCount"], 10)
        self.assertEqual(sections[1]["nameEn"], "Burgers")

        created = self.client.post("/api/admin/sections", json={
            "name": "Kuruyemiş", "nameEn": "Nuts", "tagline": "Taze kavrulmuş", "icon": "Nut", "accent": "#8b5cf6", "orderPrefix": "kry",
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["id"], "kuruyemis")
        self.assertEqual(created.json()["orderPrefix"], "KRY")
        self.assertEqual(created.json()["position"], 17)
        self.assertEqual(self.client.post("/api/admin/sections", json={"name": "Kuruyemiş", "orderPrefix": "ABC"}).status_code, 409)
        self.assertEqual(self.client.post("/api/admin/sections", json={"name": "Başka", "orderPrefix": "KHV"}).status_code, 409)
        self.assertEqual(self.client.put("/api/admin/sections/kuruyemis", json={"orderPrefix": "PZA"}).status_code, 409)
        updated = self.client.put("/api/admin/sections/kuruyemis", json={"tagline": "Çerez", "active": False})
        self.assertEqual(updated.json()["tagline"], "Çerez")
        self.assertFalse(updated.json()["active"])

        reordered = self.client.post("/api/admin/sections/reorder", json={"ids": ["kuruyemis", "kahve"]}).json()
        self.assertEqual(reordered[0]["id"], "kuruyemis")

        category = self.client.post("/api/admin/categories", json={"sectionId": "kuruyemis", "name": "Kavrulmuşlar"})
        self.assertEqual(category.status_code, 201, category.text)
        self.assertEqual(category.json()["sectionId"], "kuruyemis")
        product = self.client.post("/api/admin/products", json={
            "sectionId": "kuruyemis", "categoryId": category.json()["id"], "name": "Antep Fıstığı", "price": 199, "emoji": "🥜",
        })
        self.assertEqual(product.status_code, 201, product.text)
        self.assertEqual(product.json()["sectionId"], "kuruyemis")
        self.assertEqual(self.client.delete("/api/admin/sections/kuruyemis").status_code, 409)
        self.assertEqual(self.client.delete(f"/api/admin/products/{product.json()['id']}").status_code, 204)
        self.assertEqual(self.client.delete("/api/admin/sections/kuruyemis").status_code, 204)
        self.assertEqual(self.count("mk_categories", "WHERE section_id = %s", ("kuruyemis",)), 0)
        self.assertEqual(self.client.delete("/api/admin/sections/pizza").status_code, 409)

    def test_categories_and_products_require_section(self):
        self.assertEqual(self.client.post("/api/admin/categories", json={"name": "Sahipsiz"}).status_code, 400)
        self.assertEqual(self.client.post("/api/admin/products", json={"name": "Sahipsiz", "categoryId": "pizza-klasik", "price": 1}).status_code, 400)
        mismatch = self.client.post("/api/admin/products", json={"sectionId": "burger", "categoryId": "pizza-klasik", "name": "X", "price": 1})
        self.assertEqual(mismatch.status_code, 400)
        pizza_categories = self.client.get("/api/admin/categories?section=pizza").json()
        self.assertEqual([item["id"] for item in pizza_categories], ["pizza-klasik", "pizza-ozel", "pizza-yan-lezzetler"])
        self.assertTrue(all(item["productCount"] > 0 for item in pizza_categories))
        products = self.client.get("/api/admin/products?section=pizza").json()
        self.assertEqual(len(products), 8)
        updated = self.client.put("/api/admin/products/pizza-margarita", json={"price": 259, "categoryId": "pizza-ozel"})
        self.assertEqual(updated.json()["price"], 259)
        self.assertEqual(updated.json()["categoryId"], "pizza-ozel")
        self.assertEqual(updated.json()["sectionId"], "pizza")
        self.assertEqual(len(self.client.get("/api/admin/stock?section=pizza").json()), 8)
        adjusted = self.client.post("/api/admin/stock/pizza-margarita/adjust", json={"mode": "add", "quantity": 5, "note": "Teslimat"})
        self.assertEqual(adjusted.json()["stockQuantity"], 55)
        movements = self.client.get("/api/admin/stock/movements?section=pizza").json()
        self.assertEqual(movements[0]["product_id"], "pizza-margarita")
        self.assertEqual(movements[0]["after_quantity"], 55)
        self.assertEqual(self.client.get("/api/admin/stock/movements?section=burger").json(), [])

    def test_castles_devices_crud_and_assignment(self):
        created = self.client.post("/api/admin/castles/devices", json={"serial": SERIAL_16, "name": " Kasa 1 "})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["name"], "Kasa 1")
        self.assertIsNone(created.json()["assignedKioskId"])
        self.assertEqual(self.client.post("/api/admin/castles/devices", json={"serial": SERIAL_16, "name": "Kopya"}).status_code, 409)
        self.assertEqual(self.client.post("/api/admin/castles/devices", json={"serial": "AB-1", "name": "Hatali"}).status_code, 422)
        self.assign_pos("k04", SERIAL_16)
        devices = self.client.get("/api/admin/castles/devices").json()
        self.assertEqual(devices[0]["assignedKioskId"], "k04")
        self.assertEqual(self.client.delete(f"/api/admin/castles/devices/{SERIAL_16}").status_code, 409)
        updated = self.client.put(f"/api/admin/castles/devices/{SERIAL_16}", json={"active": False})
        self.assertFalse(updated.json()["active"])
        self.assertEqual(self.client.put("/api/admin/castles/devices/MISSING01", json={"name": "Yok"}).status_code, 404)
        self.client.put("/api/admin/kiosks/k04", json={"posSerial": None})
        self.assertEqual(self.client.delete(f"/api/admin/castles/devices/{SERIAL_16}").status_code, 204)
        self.assertEqual(self.client.delete(f"/api/admin/castles/devices/{SERIAL_16}").status_code, 404)

    def test_dashboard_contains_kiosk_and_section_rows(self):
        body = self.client.get("/api/admin/dashboard").json()
        self.assertEqual(len(body["kiosks"]), 17)
        self.assertEqual(len(body["sections"]), 17)
        self.assertEqual(body["stats"]["product_count"], 173)
        filtered = self.client.get("/api/admin/dashboard?kiosk=k03&section=pizza").json()
        self.assertEqual([row["kioskId"] for row in filtered["kiosks"]], ["k03"])
        self.assertEqual(filtered["stats"]["product_count"], 8)


class MigrationTests(PlatformTestCase):
    def test_seed_counts(self):
        self.assertEqual(self.count("mk_sections"), 17)
        self.assertEqual(self.count("mk_kiosks"), 17)
        self.assertEqual(self.count("mk_products", "WHERE section_id = %s", ("kahve",)), 39)
        self.assertEqual(self.count("mk_categories", "WHERE section_id = %s", ("kahve",)), 10)
        with db.connect() as conn:
            sections = conn.execute("SELECT id, order_prefix FROM mk_sections ORDER BY position").fetchall()
            kiosks = conn.execute("SELECT id, name, featured_section_id, pos_serial, order_seq FROM mk_kiosks ORDER BY id").fetchall()
            per_section = conn.execute("SELECT section_id, COUNT(*) AS n FROM mk_products GROUP BY section_id").fetchall()
            per_category = conn.execute("SELECT section_id, COUNT(*) AS n FROM mk_categories GROUP BY section_id").fetchall()
        self.assertEqual([row["id"] for row in sections], [item["id"] for item in SECTIONS])
        self.assertEqual(sections[2]["order_prefix"], "PZA")
        self.assertEqual(kiosks[0]["name"], "Kiosk 01 · Kahve")
        self.assertEqual([row["featured_section_id"] for row in kiosks], [item["id"] for item in SECTIONS])
        self.assertTrue(all(row["pos_serial"] is None and row["order_seq"] == 0 for row in kiosks))
        for row in per_section:
            if row["section_id"] != "kahve":
                self.assertTrue(6 <= row["n"] <= 10, row)
        for row in per_category:
            if row["section_id"] != "kahve":
                self.assertTrue(2 <= row["n"] <= 4, row)

    def test_migrations_are_idempotent(self):
        before = {table: self.count(table) for table in ("mk_sections", "mk_kiosks", "mk_categories", "mk_products", "mk_translations")}
        migrations.run_migrations(db)
        with db.connect(write=True) as conn:
            conn.execute("DELETE FROM mk_schema_version")
        migrations.run_migrations(db)
        after = {table: self.count(table) for table in before}
        self.assertEqual(before, after)


class LegacyImportTests(PlatformTestCase):
    """Eski app_state ve castles_devices tablolari varsa kahve menusu ve cihazlar oradan tasinir."""

    def prepare_database(self):
        import os
        import sqlite3

        legacy = {
            "categories": [{"id": "espresso", "name": "Espresso", "eyebrow": "Klasik", "position": 0, "active": True}],
            "products": [
                {"id": "latte", "categoryId": "espresso", "name": "Caffe Latte", "price": 95, "kind": "coffee", "active": True},
                {"id": "pizza-margarita", "categoryId": "espresso", "name": "Çakışan", "price": 10, "kind": "simple", "active": True},
            ],
            "translations": {
                "category|espresso|name|en": {
                    "entityType": "category", "entityId": "espresso", "field": "name", "language": "en",
                    "sourceText": "Espresso", "translatedText": "Espresso EN", "status": "ready",
                },
            },
            "orders": {"MC-1": {"number": "MC-1"}},
        }
        self.legacy_json = json.dumps(legacy, ensure_ascii=False)
        conn = sqlite3.connect(os.environ["MK_SQLITE_PATH"])
        conn.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, data TEXT NOT NULL)")
        conn.execute("INSERT INTO app_state (key, data) VALUES (?, ?)", ("magic-coffee", self.legacy_json))
        conn.execute("CREATE TABLE castles_devices (serial TEXT PRIMARY KEY, name TEXT, active BOOLEAN, created_at TEXT, updated_at TEXT)")
        conn.execute("INSERT INTO castles_devices VALUES (?, ?, ?, ?, ?)", (SERIAL_16, "Eski Kasa", 1, "2026-10-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00"))
        conn.commit()
        conn.close()

    def test_imports_from_app_state_read_only(self):
        self.assertEqual(self.count("mk_products", "WHERE section_id = %s", ("kahve",)), 2)
        with db.connect() as conn:
            latte = conn.execute("SELECT * FROM mk_products WHERE id = %s", ("latte",)).fetchone()
            renamed = conn.execute("SELECT * FROM mk_products WHERE id = %s", ("kahve-pizza-margarita",)).fetchone()
            data = conn.execute("SELECT data FROM app_state WHERE key = %s", ("magic-coffee",)).fetchone()["data"]
            device = conn.execute("SELECT * FROM mk_castles_devices").fetchall()
        self.assertEqual(json.loads(latte["data"])["price"], 95)
        self.assertEqual(renamed["section_id"], "kahve")  # tohum kimligiyle cakisan kahve urunu onek alir
        self.assertEqual(data, self.legacy_json)
        self.assertEqual([row["serial"] for row in device], [SERIAL_16])
        coffee = self.client.get("/api/catalog?section=kahve&lang=en").json()
        self.assertEqual(coffee["categories"][0]["name"], "Espresso EN")
        self.assertEqual(self.count("mk_orders"), 0)


if __name__ == "__main__":
    unittest.main()
