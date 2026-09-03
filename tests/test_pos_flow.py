import os
import unittest
from unittest.mock import patch

os.environ["DATABASE_URL"] = ""
os.environ["ALLOW_LOCAL_FILE_STORE"] = "true"
os.environ["PAVO_GATEWAY_BASE_URL"] = "https://pos.invalid/api"
os.environ["PAVO_TERMINAL_SERIAL"] = "PAV960000010"
os.environ["PAVO_BRANCH_ID"] = "173"

from fastapi.testclient import TestClient

import app.main as api


DEVICE_ID = "11111111-1111-4111-8111-111111111111"
PAYMENT_ID = "22222222-2222-4222-8222-222222222222"


class PosOrderFlowTests(unittest.TestCase):
    def setUp(self):
        api.categories[:] = [{"id": "coffee", "name": "Kahveler", "eyebrow": "Kahve menusu", "position": 0, "active": True}]
        api.products[:] = [{
            "id": "latte",
            "categoryId": "coffee",
            "name": "Caffe Latte",
            "description": "Sutlu espresso",
            "price": 92.0,
            "kind": "coffee",
            "active": True,
            "available": True,
            "stockTrackingEnabled": True,
            "stockQuantity": 5,
            "customization": {},
        }]
        api.orders.clear()
        api.stock_movements.clear()
        api.pos_payments.clear()
        api.order_requests.clear()
        api.translations.clear()
        api.order_numbers = iter(range(401, 500))
        self.gateway_calls = []
        self.poll_status = "COMPLETED"

        async def fake_gateway(method, path, payload=None):
            self.gateway_calls.append((method, path, payload))
            if path.startswith("/pavo/devices/"):
                return [{
                    "id": DEVICE_ID,
                    "name": "Coffee POS",
                    "provider_type": "PAVO_CLOUD",
                    "serial_number": "PAV960000010",
                    "status": "ACTIVE",
                    "is_default": True,
                    "cloud_source_fingerprint": "coffee-test",
                    "cloud_pairing_id": "pair-1",
                }]
            if method == "POST" and path == "/pavo/payment":
                return {"id": PAYMENT_ID, "status": "PROCESSING"}
            if method == "POST" and path == "/pavo/device":
                return {"id": DEVICE_ID, **payload}
            if method == "PUT" and path == f"/pavo/device/{DEVICE_ID}":
                return {"id": DEVICE_ID, "serial_number": "PAV960000010", **payload}
            if method == "DELETE" and path == f"/pavo/device/{DEVICE_ID}":
                return {}
            if method == "GET" and path == f"/pavo/cloud/poll/{PAYMENT_ID}":
                return {"status": self.poll_status}
            if method == "POST" and path == "/pavo/cloud/pair":
                return {"Success": True, "Data": {"Id": 2, "PairingCode": "123456"}}
            if method == "POST" and path == "/pavo/cloud/pair/check":
                return {"Success": True, "Data": {"IsApproved": True, "IsActive": True}}
            if method == "POST" and path.startswith("/pavo/cloud/check-status/"):
                return {"success": True}
            raise AssertionError(f"Unexpected gateway call: {method} {path}")

        self.gateway_patch = patch.object(api, "pavo_gateway_request", new=fake_gateway)
        self.save_patch = patch.object(api, "save_state", new=lambda: None)
        self.gateway_patch.start()
        self.save_patch.start()
        self.client = TestClient(api.app)

    def tearDown(self):
        self.gateway_patch.stop()
        self.save_patch.stop()

    @staticmethod
    def payment_payload(request_id="payment-request-0001"):
        return {
            "clientRequestId": request_id,
            "paymentMethod": "card",
            "amount": 92.0,
            "lines": [{"productId": "latte", "name": "ignored", "quantity": 1, "unitPrice": 1}],
        }

    def test_payment_and_order_are_idempotent_and_stock_changes_once(self):
        first = self.client.post("/api/pos/payments", json=self.payment_payload())
        second = self.client.post("/api/pos/payments", json=self.payment_payload())
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        payment_posts = [call for call in self.gateway_calls if call[:2] == ("POST", "/pavo/payment")]
        self.assertEqual(len(payment_posts), 1)
        self.assertEqual(payment_posts[0][2]["amount"], 92.0)
        self.assertEqual(payment_posts[0][2]["sale_items"][0]["quantity"], 1)

        paid = self.client.get(f"/api/pos/payments/{PAYMENT_ID}")
        self.assertEqual(paid.json()["status"], "COMPLETED")
        reference = paid.json()["paymentReference"]
        order_payload = {
            "clientRequestId": "order-request-0001",
            "fulfillment": "restaurant",
            "paymentMethod": "card",
            "total": 92.0,
            "paymentReference": reference,
            "posTransactionId": PAYMENT_ID,
            "language": "tr",
            "lines": [{"productId": "latte", "name": "ignored", "quantity": 1, "unitPrice": 1}],
        }
        order_one = self.client.post("/api/orders", json=order_payload)
        order_two = self.client.post("/api/orders", json=order_payload)
        order_payload["clientRequestId"] = "order-request-0002"
        order_three = self.client.post("/api/orders", json=order_payload)
        self.assertEqual(order_one.status_code, 201)
        self.assertEqual(order_one.json()["number"], order_two.json()["number"])
        self.assertEqual(order_one.json()["number"], order_three.json()["number"])
        self.assertEqual(api.products[0]["stockQuantity"], 4)
        self.assertEqual(len(api.orders), 1)

        receipt_one = self.client.post(f"/api/orders/{order_one.json()['number']}/receipt", json={"status": "printed", "printAttemptId": "receipt-attempt-1"})
        receipt_two = self.client.post(f"/api/orders/{order_one.json()['number']}/receipt", json={"status": "printed", "printAttemptId": "receipt-attempt-2"})
        self.assertFalse(receipt_one.json()["alreadyRecorded"])
        self.assertTrue(receipt_two.json()["alreadyRecorded"])

    def test_failed_payment_cannot_create_order(self):
        self.poll_status = "DECLINED"
        started = self.client.post("/api/pos/payments", json=self.payment_payload("payment-request-failed"))
        self.assertEqual(started.status_code, 201)
        failed = self.client.get(f"/api/pos/payments/{PAYMENT_ID}")
        self.assertEqual(failed.json()["status"], "DECLINED")
        response = self.client.post("/api/orders", json={
            "clientRequestId": "order-request-failed",
            "fulfillment": "package",
            "paymentMethod": "card",
            "total": 92.0,
            "paymentReference": started.json()["paymentReference"],
            "posTransactionId": PAYMENT_ID,
            "language": "en",
            "lines": [{"productId": "latte", "quantity": 1}],
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(api.orders), 0)

    def test_pairing_preserves_fingerprint_and_returns_six_digit_code(self):
        fingerprint = "  Coffee Fingerprint / 01  "
        paired = self.client.post(f"/api/admin/pos/devices/{DEVICE_ID}/pair", json={"fingerprint": fingerprint})
        self.assertEqual(paired.status_code, 200)
        self.assertEqual(paired.json()["pairingCode"], "123456")
        pair_call = next(call for call in self.gateway_calls if call[:2] == ("POST", "/pavo/cloud/pair"))
        self.assertEqual(pair_call[2]["source_fingerprint"], fingerprint)
        self.assertEqual(pair_call[2]["application_name"], "MagicCoffee")

        checked = self.client.post(f"/api/admin/pos/devices/{DEVICE_ID}/pair/check", json={"pairingId": 2})
        self.assertTrue(checked.json()["approved"])
        self.assertTrue(checked.json()["active"])

    def test_terminal_create_update_refresh_and_delete_contracts(self):
        created = self.client.post("/api/admin/pos/devices", json={
            "name": "Yeni Coffee POS",
            "providerType": "PAVO_CLOUD",
            "serialNumber": "PAV960000010",
            "status": "PASSIVE",
            "isDefault": True,
        })
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["name"], "Yeni Coffee POS")
        self.assertTrue(created.json()["isDefault"])

        updated = self.client.put(f"/api/admin/pos/devices/{DEVICE_ID}", json={
            "name": "Guncel Coffee POS", "status": "MAINTENANCE",
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["status"], "MAINTENANCE")

        refreshed = self.client.post("/api/admin/pos/devices/refresh-status")
        self.assertEqual(refreshed.status_code, 200)
        deleted = self.client.delete(f"/api/admin/pos/devices/{DEVICE_ID}")
        self.assertEqual(deleted.status_code, 204)

    def test_english_catalog_uses_database_translation_records_with_turkish_fallback(self):
        api.categories[0]["name"] = "Filtre Kahveler"
        synced = self.client.post("/api/admin/translations/sync")
        self.assertEqual(synced.status_code, 200)
        catalog = self.client.get("/api/catalog?lang=en")
        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(catalog.json()["language"], "en")
        self.assertEqual(catalog.json()["categories"][0]["name"], "Filter Coffees")
        self.assertEqual(catalog.json()["products"][0]["name"], "Caffe Latte")


if __name__ == "__main__":
    unittest.main()
