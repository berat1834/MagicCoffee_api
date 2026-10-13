"""Katalog fotograflarinin (uploads/products/catalog/<bolum>/sources.json) urunlere atanmasi."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import PlatformTestCase, db

from app import migrations


class CatalogImageTests(PlatformTestCase):
    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp()) / "catalog"
        pizza = self.root / "pizza"
        pizza.mkdir(parents=True)
        (pizza / "pizza-margarita.webp").write_bytes(b"RIFF0000WEBP")
        (pizza / "pizza-sucuklu.webp").write_bytes(b"RIFF0000WEBP")
        (pizza / "sources.json").write_text(json.dumps({
            "section": "pizza",
            "images": [
                {"productId": "pizza-margarita", "file": "pizza-margarita.webp", "license": "cc0"},
                {"productId": "sucuklu", "file": "pizza-sucuklu.webp", "license": "cc0"},
                # Dosyasi olmayan kayit ve kahve urunu atlanmali.
                {"productId": "pizza-yok", "file": "pizza-yok.webp", "license": "cc0"},
            ],
            "missing": [],
        }), encoding="utf-8")
        kahve = self.root / "kahve"
        kahve.mkdir()
        (kahve / "latte.webp").write_bytes(b"RIFF0000WEBP")
        (kahve / "sources.json").write_text(json.dumps({"section": "kahve", "images": [{"productId": "latte", "file": "latte.webp"}]}), encoding="utf-8")
        self.roots = patch.object(migrations, "CATALOG_IMAGE_ROOTS", (self.root,))
        self.roots.start()
        self.addCleanup(self.roots.stop)

    def product(self, product_id):
        with db.connect() as conn:
            row = conn.execute("SELECT data FROM mk_products WHERE id = %s", (product_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def set_image(self, product_id, image):
        data = self.product(product_id)
        data["image"] = image
        with db.connect(write=True) as conn:
            conn.execute("UPDATE mk_products SET data = %s WHERE id = %s", (json.dumps(data), product_id))

    def test_assigns_only_empty_images_and_skips_coffee(self):
        latte_before = self.product("latte")["image"]
        self.set_image("pizza-margarita", "")
        self.set_image("pizza-sucuklu", "/uploads/products/panelden.webp")

        migrations.run_migrations(db)

        self.assertEqual(self.product("pizza-margarita")["image"], "/uploads/products/catalog/pizza/pizza-margarita.webp")
        self.assertEqual(self.product("pizza-sucuklu")["image"], "/uploads/products/panelden.webp")
        self.assertEqual(self.product("latte")["image"], latte_before)

    def test_unprefixed_ids_map_to_section_prefixed_products(self):
        mapping = migrations.catalog_image_sources()
        self.assertEqual(mapping["pizza-sucuklu"], "/uploads/products/catalog/pizza/pizza-sucuklu.webp")
        self.assertNotIn("pizza-yok", mapping)

    def test_is_idempotent_and_served_in_catalog(self):
        self.set_image("pizza-margarita", "")
        migrations.run_migrations(db)
        first = self.product("pizza-margarita")
        migrations.run_migrations(db)
        self.assertEqual(self.product("pizza-margarita"), first)
        catalog = self.client.get("/api/catalog?section=pizza", headers={"X-Kiosk-Id": "k03"}).json()
        margarita = next(item for item in catalog["products"] if item["id"] == "pizza-margarita")
        self.assertEqual(margarita["image"], "/uploads/products/catalog/pizza/pizza-margarita.webp")

    def test_no_catalog_folder_is_noop(self):
        with patch.object(migrations, "CATALOG_IMAGE_ROOTS", (self.root / "yok",)):
            self.assertEqual(migrations.catalog_image_sources(), {})
            with db.connect(write=True) as conn:
                self.assertEqual(migrations.assign_catalog_images(conn), 0)


if __name__ == "__main__":
    unittest.main()
