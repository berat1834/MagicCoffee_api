import json
import unittest

from support import PlatformTestCase, api, db, delayed

from app import migrations
from app.seed_catalog import SECTIONS

SERIAL_A = "0000573234898609"
SERIAL_A12 = "573234898609"
SERIAL_B12 = "136250609499"
PIN = {"X-Kiosk-Admin-Pin": "1907"}


class KioskSetupTests(PlatformTestCase):
    def put_pos(self, kiosk_id, serial, pin="1907"):
        headers = {**self.kiosk(kiosk_id)}
        if pin is not None:
            headers["X-Kiosk-Admin-Pin"] = pin
        return self.client.put("/api/kiosk/pos", json={"posSerial": serial}, headers=headers)

    def kiosk_serial(self, kiosk_id):
        with db.connect() as conn:
            return conn.execute("SELECT pos_serial FROM mk_kiosks WHERE id = %s", (kiosk_id,)).fetchone()["pos_serial"]

    def test_profiles_never_leak_full_serial(self):
        self.assign_pos("k03", SERIAL_A)
        response = self.client.get("/api/kiosk/profiles")
        self.assertEqual(response.status_code, 200)
        profiles = response.json()
        self.assertEqual([item["id"] for item in profiles], [f"k{index:02d}" for index in range(1, 18)])
        k03 = profiles[2]
        self.assertEqual(set(k03), {"id", "name", "featuredSectionId", "active", "posConfigured", "posSerialLast4"})
        self.assertTrue(k03["posConfigured"])
        self.assertEqual(k03["posSerialLast4"], "8609")
        self.assertFalse(profiles[0]["posConfigured"])
        self.assertIsNone(profiles[0]["posSerialLast4"])
        self.assertNotIn(SERIAL_A12, response.text)
        self.assertNotIn("57323489", response.text)

    def test_put_pos_assigns_serial_and_creates_device(self):
        response = self.put_pos("k07", SERIAL_A)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["kiosk"]["id"], "k07")
        self.assertTrue(body["kiosk"]["posConfigured"])
        self.assertEqual(len(body["sections"]), 17)
        self.assertEqual(self.kiosk_serial("k07"), SERIAL_A12)
        devices = self.client.get("/api/admin/castles/devices").json()
        self.assertEqual(devices, [{**devices[0], "serial": SERIAL_A12, "name": "Kiosk 07 POS", "active": True, "assignedKioskId": "k07"}])

        # Ayni seri tekrar: cihaz cogaltilmaz. Null ile kaldirilir.
        self.assertEqual(self.put_pos("k07", SERIAL_A12).status_code, 200)
        self.assertEqual(len(self.client.get("/api/admin/castles/devices").json()), 1)
        removed = self.put_pos("k07", None)
        self.assertFalse(removed.json()["kiosk"]["posConfigured"])
        self.assertIsNone(self.kiosk_serial("k07"))

    def test_put_pos_reactivates_inactive_device(self):
        self.client.post("/api/admin/castles/devices", json={"serial": SERIAL_A, "name": "Depo cihazı", "active": False})
        self.assertEqual(self.put_pos("k02", SERIAL_A12).status_code, 200)
        devices = self.client.get("/api/admin/castles/devices").json()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["serial"], SERIAL_A)
        self.assertTrue(devices[0]["active"])
        self.assertEqual(devices[0]["name"], "Depo cihazı")
        self.assertEqual(devices[0]["assignedKioskId"], "k02")

    def test_wrong_pin_invalid_serial_and_conflict(self):
        missing = self.put_pos("k07", SERIAL_A, pin=None)
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(missing.json()["detail"], "PIN hatalı.")
        self.assertEqual(self.put_pos("k07", SERIAL_A, pin="0000").status_code, 403)
        self.assertIsNone(self.kiosk_serial("k07"))
        self.assertEqual(self.client.put("/api/kiosk/pos", json={"posSerial": SERIAL_A}, headers=PIN).status_code, 403)

        invalid = self.put_pos("k07", "12345")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["detail"], "POS seri numarası 12 haneli olmalı.")

        self.assertEqual(self.put_pos("k05", SERIAL_A).status_code, 200)
        conflict = self.put_pos("k07", SERIAL_A12)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["detail"], "Bu POS cihazı Kiosk 05 · Salata & Bowl kioskuna atanmış.")
        self.assertIsNone(self.kiosk_serial("k07"))

    def test_wrong_pin_rate_limit(self):
        for _ in range(5):
            self.assertEqual(self.put_pos("k07", SERIAL_A, pin="1111").status_code, 403)
        blocked = self.put_pos("k07", SERIAL_A)
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(blocked.json()["detail"], "Çok fazla deneme, lütfen 1 dakika bekleyin.")
        self.assertIsNone(self.kiosk_serial("k07"))
        # Baska kiosk ayni istemciden etkilenmez.
        self.assertEqual(self.put_pos("k08", SERIAL_A).status_code, 200)

    def test_payment_uses_serial_saved_on_kiosk_setup(self):
        self.cloud.handlers["Transaction"] = delayed(3.0, {"responseCode": "98", "message": "Iptal", "rrn": ""})
        self.cloud.handlers["CancelTransaction"] = lambda payload: {"status": True}
        self.assertEqual(self.put_pos("k07", SERIAL_A).status_code, 200)
        first = self.client.post("/api/pos/payments", json=self.payment_payload("serial-route-01"), headers=self.kiosk("k07"))
        self.assertEqual(first.status_code, 201, first.text)
        sales = self.cloud.of("Transaction")
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0][0]["DeviceSerialNumber"], SERIAL_A12)
        self.assertEqual(self.payment_record("serial-route-01")["terminalSerial"], SERIAL_A12)

        # Kurulum ekranindan seri degistirilir: bir sonraki odeme yeni cihaza gider.
        self.assertEqual(self.put_pos("k07", SERIAL_B12).status_code, 200)
        second = self.client.post("/api/pos/payments", json=self.payment_payload("serial-route-02"), headers=self.kiosk("k07"))
        self.assertEqual(second.status_code, 201, second.text)
        sales = self.cloud.of("Transaction")
        self.assertEqual(len(sales), 2)
        self.assertEqual(sales[1][0]["DeviceSerialNumber"], SERIAL_B12)
        self.assertEqual(self.payment_record("serial-route-02")["terminalSerial"], SERIAL_B12)

        # Ilk odemenin iptali odeme kaydindaki seriye (A) gider.
        cancelled = self.client.post(f"/api/pos/payments/{first.json()['id']}/cancel", headers=self.kiosk("k07"))
        self.assertEqual(cancelled.json()["status"], "CANCELLED")
        cancels = self.cloud.of("CancelTransaction")
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0][0]["DeviceSerialNumber"], SERIAL_A12)


class CatalogExpansionTests(PlatformTestCase):
    def test_each_non_coffee_section_has_at_least_22_products(self):
        for section in SECTIONS:
            if section["id"] == "kahve":
                continue
            body = self.client.get(f"/api/catalog?section={section['id']}").json()
            self.assertGreaterEqual(len(body["products"]), 22, section["id"])
            self.assertTrue(4 <= len(body["categories"]) <= 5, section["id"])
            popular = [item for item in body["products"] if item.get("popular")]
            self.assertTrue(1 <= len(popular) <= 4, section["id"])
            english = self.client.get(f"/api/catalog?section={section['id']}&lang=en").json()
            self.assertTrue(all(item["name"] for item in english["products"]))
        shoes = self.client.get("/api/catalog?section=ayakkabi").json()
        boot = next(item for item in shoes["products"] if item["id"] == "ayakkabi-chelsea-bot")
        self.assertTrue(boot["customization"]["sides"]["required"])
        self.assertEqual(boot["customization"]["sides"]["title"], "Numara Seçimi")
        tech = self.client.get("/api/catalog?section=elektronik&lang=en").json()
        watch = next(item for item in tech["products"] if item["id"] == "elektronik-akilli-saat")
        self.assertEqual(watch["name"], "Smartwatch")
        self.assertFalse(watch["customization"]["drinks"]["required"])
        self.assertEqual(watch["customization"]["drinks"]["title"], "Color")


class MigrationV2Tests(PlatformTestCase):
    def test_v2_adds_missing_rows_without_touching_existing(self):
        # v1 durumunu taklit et: v2 urunleri/alt kategorisi yok, bazi satirlar panelden duzenlenmis.
        with db.connect(write=True) as conn:
            conn.execute("DELETE FROM mk_products WHERE category_id = %s", ("burger-menuler",))
            conn.execute("DELETE FROM mk_categories WHERE id = %s", ("burger-menuler",))
            conn.execute("DELETE FROM mk_products WHERE id IN (%s, %s)", ("burger-bbq-burger", "giyim-polo-tisort"))
            conn.execute("DELETE FROM mk_translations WHERE entity_id = %s", ("burger-bbq-burger",))
            row = conn.execute("SELECT data FROM mk_products WHERE id = %s", ("pizza-margarita",)).fetchone()
            data = json.loads(row["data"])
            data.update({"name": "Panel Margarita", "price": 275.0, "stockQuantity": 3})
            conn.execute("UPDATE mk_products SET data = %s, position = %s WHERE id = %s", (json.dumps(data), 99, "pizza-margarita"))
            conn.execute("UPDATE mk_categories SET name = %s WHERE id = %s", ("Panel Klasikler", "pizza-klasik"))
            conn.execute("DELETE FROM mk_products WHERE id = %s", ("latte",))
            coffee_before = conn.execute("SELECT COUNT(*) AS n FROM mk_products WHERE section_id = %s", ("kahve",)).fetchone()["n"]
            conn.execute("DELETE FROM mk_schema_version")
            conn.execute("INSERT INTO mk_schema_version (version) VALUES (%s)", (1,))
            kiosks_before = conn.execute("SELECT * FROM mk_kiosks ORDER BY id").fetchall()

        migrations.run_migrations(db)

        with db.connect() as conn:
            version = conn.execute("SELECT MAX(version) AS v FROM mk_schema_version").fetchone()["v"]
            margarita = conn.execute("SELECT * FROM mk_products WHERE id = %s", ("pizza-margarita",)).fetchone()
            klasik = conn.execute("SELECT name FROM mk_categories WHERE id = %s", ("pizza-klasik",)).fetchone()
            menu = conn.execute("SELECT * FROM mk_categories WHERE id = %s", ("burger-menuler",)).fetchone()
            bbq = conn.execute("SELECT * FROM mk_products WHERE id = %s", ("burger-bbq-burger",)).fetchone()
            coffee_after = conn.execute("SELECT COUNT(*) AS n FROM mk_products WHERE section_id = %s", ("kahve",)).fetchone()["n"]
            latte = conn.execute("SELECT 1 AS found FROM mk_products WHERE id = %s", ("latte",)).fetchone()
            bbq_en = conn.execute(
                "SELECT value FROM mk_translations WHERE entity_id = %s AND field = %s", ("burger-bbq-burger", "name"),
            ).fetchone()
            kiosks_after = conn.execute("SELECT * FROM mk_kiosks ORDER BY id").fetchall()
        self.assertEqual(version, 2)
        self.assertEqual(json.loads(margarita["data"])["name"], "Panel Margarita")
        self.assertEqual(json.loads(margarita["data"])["price"], 275.0)
        self.assertEqual(json.loads(margarita["data"])["stockQuantity"], 3)
        self.assertEqual(margarita["position"], 99)
        self.assertEqual(klasik["name"], "Panel Klasikler")
        self.assertIsNotNone(menu)
        self.assertEqual(menu["section_id"], "burger")
        self.assertIsNotNone(bbq)
        self.assertEqual(bbq_en["value"], "BBQ Burger")
        self.assertEqual(self.count("mk_products", "WHERE category_id = %s", ("burger-menuler",)), 3)
        self.assertEqual(self.count("mk_products", "WHERE id = %s", ("giyim-polo-tisort",)), 1)
        self.assertEqual(coffee_after, coffee_before)
        self.assertIsNone(latte)
        self.assertEqual(kiosks_before, kiosks_after)

        counts = {table: self.count(table) for table in ("mk_categories", "mk_products", "mk_translations")}
        with db.connect(write=True) as conn:
            conn.execute("DELETE FROM mk_schema_version")
            conn.execute("INSERT INTO mk_schema_version (version) VALUES (%s)", (1,))
        migrations.run_migrations(db)
        migrations.run_migrations(db)
        self.assertEqual(counts, {table: self.count(table) for table in counts})


if __name__ == "__main__":
    unittest.main()
