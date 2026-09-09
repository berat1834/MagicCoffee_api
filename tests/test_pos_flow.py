import os
import unittest
from unittest.mock import patch

import httpx

os.environ["DATABASE_URL"] = ""
os.environ["ALLOW_LOCAL_FILE_STORE"] = "true"
os.environ["PAVO_GATEWAY_BASE_URL"] = "https://kebo-api-dev.magicpay.ai/api"
os.environ["PAVO_BRANCH_ID"] = "2"
os.environ["PAVO_TERMINAL_SERIAL"] = "PAV960000010"
os.environ["PAVO_SOURCE_FINGERPRINT"] = "test1"
os.environ["PAVO_PROVIDER_TYPE"] = "PAVO_UNICLOUD"
os.environ["PAVO_INTERNAL_GATEWAY_TOKEN"] = "test-magiccoffee-token-0000000000000001"

from fastapi.testclient import TestClient

import app.main as api


DEVICE_ID = "11111111-1111-4111-8111-111111111111"
PAYMENT_ID = "22222222-2222-4222-8222-222222222222"
PAVO_ENV_KEYS = (
    "PAVO_GATEWAY_BASE_URL",
    "PAVO_BRANCH_ID",
    "PAVO_TERMINAL_SERIAL",
    "PAVO_SOURCE_FINGERPRINT",
    "PAVO_PROVIDER_TYPE",
    "PAVO_INTERNAL_GATEWAY_TOKEN",
)


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
        self.include_configured_device = True
        self.device_branch_id = 2
        self.device_serial = "PAV960000010"
        self.device_provider = "PAVO_UNICLOUD"
        self.device_is_default = True
        self.device_fingerprint = "test1"
        self.pair_response_fingerprint = "test1"

        async def fake_gateway(method, path, payload=None):
            self.gateway_calls.append((method, path, payload))
            if path.startswith("/pavo/devices/"):
                devices = [{
                    "id": DEVICE_ID,
                    "name": "Coffee POS",
                    "provider_type": self.device_provider,
                    "branch_id": self.device_branch_id,
                    "serial_number": self.device_serial,
                    "status": "ACTIVE",
                    "is_default": self.device_is_default,
                    "cloud_source_fingerprint": self.device_fingerprint,
                    "cloud_pairing_id": "coffee-pair-1",
                }]
                if not self.include_configured_device:
                    devices = [{
                        **devices[0],
                        "id": "33333333-3333-4333-8333-333333333333",
                        "name": "Wrong fallback POS",
                        "serial_number": "OTHER-POS-999",
                        "is_default": True,
                    }]
                return devices
            if method == "POST" and path == "/pavo/payment":
                return {"id": PAYMENT_ID, "status": "PROCESSING"}
            if method == "POST" and path == "/pavo/device":
                self.include_configured_device = True
                return {"id": DEVICE_ID, **payload}
            if method == "PUT" and path == f"/pavo/device/{DEVICE_ID}":
                return {
                    "id": DEVICE_ID,
                    "branch_id": 2,
                    "serial_number": "PAV960000010",
                    **payload,
                }
            if method == "DELETE" and path == f"/pavo/device/{DEVICE_ID}":
                return {}
            if method == "GET" and path == f"/pavo/cloud/poll/{PAYMENT_ID}":
                return {"status": self.poll_status}
            if method == "POST" and path == "/pavo/cloud/pair":
                return {"Success": True, "Data": {"Id": 2, "PairingCode": "123456"}}
            if method == "POST" and path == "/pavo/cloud/pair/check":
                return {"Success": True, "Data": {
                    "IsApproved": True,
                    "IsActive": True,
                    "SourceFingerPrint": self.pair_response_fingerprint,
                    "TargetSerialNo": "PAV960000010",
                }}
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
        self.assertEqual(payment_posts[0][2]["branch_id"], 2)
        self.assertEqual(payment_posts[0][2]["terminal_serial"], "PAV960000010")
        self.assertEqual(payment_posts[0][2]["provider_type"], "PAVO_UNICLOUD")
        self.assertEqual(payment_posts[0][2]["source_fingerprint"], "test1")
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

    def test_payment_never_falls_back_to_another_active_terminal(self):
        self.include_configured_device = False
        response = self.client.post("/api/pos/payments", json=self.payment_payload("payment-request-wrong-terminal"))

        self.assertEqual(response.status_code, 503)
        self.assertNotIn(("POST", "/pavo/payment"), [call[:2] for call in self.gateway_calls])

    def test_missing_pavo_setting_returns_503_without_gateway_request(self):
        for key in PAVO_ENV_KEYS:
            with self.subTest(key=key), patch.dict(os.environ, {key: ""}):
                self.gateway_calls.clear()
                response = self.client.post("/api/pos/payments", json=self.payment_payload(f"missing-{key.lower()}"))
                self.assertEqual(response.status_code, 503)
                self.assertEqual(self.gateway_calls, [])

    def test_forbidden_gateway_host_returns_503_without_gateway_request(self):
        forbidden_host = ("full" + "moon") + "-api.magicpay.ai"
        with patch.dict(os.environ, {"PAVO_GATEWAY_BASE_URL": f"https://{forbidden_host}/api"}):
            response = self.client.post("/api/pos/payments", json=self.payment_payload("forbidden-gateway-host"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.gateway_calls, [])

    def test_invalid_pavo_setting_returns_503_without_gateway_request(self):
        invalid_settings = (
            ("PAVO_GATEWAY_BASE_URL", "http://kebo-api-dev.magicpay.ai/api"),
            ("PAVO_BRANCH_ID", "not-a-number"),
            ("PAVO_BRANCH_ID", "0"),
            ("PAVO_BRANCH_ID", "3"),
            ("PAVO_TERMINAL_SERIAL", "OTHER-POS-999"),
            ("PAVO_SOURCE_FINGERPRINT", "other-source"),
            ("PAVO_PROVIDER_TYPE", "PAVO_CLOUD"),
            ("PAVO_INTERNAL_GATEWAY_TOKEN", "too-short"),
        )
        for index, (key, value) in enumerate(invalid_settings):
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                self.gateway_calls.clear()
                response = self.client.post("/api/pos/payments", json=self.payment_payload(f"invalid-setting-{index}"))
                self.assertEqual(response.status_code, 503)
                self.assertEqual(self.gateway_calls, [])

    def test_gateway_host_must_exactly_match_allowlist_without_gateway_request(self):
        with patch.dict(os.environ, {"PAVO_GATEWAY_BASE_URL": "https://other.example/api"}):
            response = self.client.post("/api/pos/payments", json=self.payment_payload("wrong-allowed-host"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.gateway_calls, [])

    def test_wrong_device_identity_never_starts_payment(self):
        mismatches = (
            ("device_branch_id", 999),
            ("device_serial", "OTHER-POS-999"),
            ("device_provider", "PAVO_CLOUD"),
            ("device_is_default", False),
            ("device_fingerprint", "other-project-kiosk"),
        )
        for index, (attribute, value) in enumerate(mismatches):
            with self.subTest(attribute=attribute):
                original = getattr(self, attribute)
                setattr(self, attribute, value)
                self.gateway_calls.clear()
                response = self.client.post("/api/pos/payments", json=self.payment_payload(f"wrong-device-{index}"))
                setattr(self, attribute, original)
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(("POST", "/pavo/payment"), [call[:2] for call in self.gateway_calls])

    def test_pairing_preserves_fingerprint_and_returns_six_digit_code(self):
        fingerprint = "test1"
        paired = self.client.post(f"/api/admin/pos/devices/{DEVICE_ID}/pair", json={"fingerprint": fingerprint})
        self.assertEqual(paired.status_code, 200)
        self.assertEqual(paired.json()["pairingCode"], "123456")
        pair_call = next(call for call in self.gateway_calls if call[:2] == ("POST", "/pavo/cloud/pair"))
        self.assertEqual(pair_call[2]["source_fingerprint"], fingerprint)
        self.assertEqual(pair_call[2]["application_name"], "MagicCoffee")

        checked = self.client.post(f"/api/admin/pos/devices/{DEVICE_ID}/pair/check", json={"pairingId": 2})
        self.assertTrue(checked.json()["approved"])
        self.assertTrue(checked.json()["active"])

    def test_pairing_rejects_wrong_fingerprint_without_pair_request(self):
        response = self.client.post(
            f"/api/admin/pos/devices/{DEVICE_ID}/pair",
            json={"fingerprint": "other-project-kiosk"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertNotIn(("POST", "/pavo/cloud/pair"), [call[:2] for call in self.gateway_calls])

    def test_device_create_rejects_non_unicloud_provider_without_gateway_request(self):
        response = self.client.post("/api/admin/pos/devices", json={
            "name": "Yanlis POS",
            "providerType": "PAVO_CLOUD",
            "serialNumber": "PAV960000010",
        })

        self.assertEqual(response.status_code, 422)
        self.assertNotIn(("POST", "/pavo/device"), [call[:2] for call in self.gateway_calls])

    def test_pairing_check_rejects_gateway_fingerprint_mismatch(self):
        self.pair_response_fingerprint = "other-project-kiosk"
        response = self.client.post(f"/api/admin/pos/devices/{DEVICE_ID}/pair/check", json={"pairingId": 2})

        self.assertEqual(response.status_code, 503)
        self.assertNotIn(("POST", "/pavo/cloud/check-status/2"), [call[:2] for call in self.gateway_calls])

    def test_terminal_create_update_refresh_and_delete_contracts(self):
        created = self.client.post("/api/admin/pos/devices", json={
            "name": "Yeni Coffee POS",
            "providerType": "PAVO_UNICLOUD",
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
        self.assertTrue(all(product["kind"] == "coffee" for product in catalog.json()["products"]))

    def test_health_reports_magiccoffee_postgresql_online(self):
        class FakeConnection:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def execute(self, _query):
                return self

            def fetchone(self):
                return {"online": 1}

        with patch.object(api, "DATABASE_URL", "postgresql://magiccoffee_app:secret@coffee-db.example/magiccoffee"), patch.object(
            api, "connect_db", return_value=FakeConnection(),
        ):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["database"], "postgresql")
        self.assertEqual(response.json()["payment"], {"status": "ready", "provider": "PAVO_UNICLOUD"})

    def test_health_reports_payment_disabled_without_complete_configuration(self):
        with patch.dict(os.environ, {"PAVO_INTERNAL_GATEWAY_TOKEN": ""}):
            response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["payment"], {"status": "disabled", "provider": "PAVO_UNICLOUD"})

    def test_database_url_rejects_external_project_identity(self):
        forbidden_marker = "full" + "moon"
        with self.assertRaises(RuntimeError):
            api.validate_database_url(f"postgresql://magiccoffee:secret@{forbidden_marker}-db.example/coffee")


class PavoGatewayIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_coffee_gateway_receives_internal_token(self):
        captured = {}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

            async def request(self, method, url, **kwargs):
                captured.update(method=method, url=url, kwargs=kwargs)
                return httpx.Response(200, json={"success": True})

        with patch.object(api.httpx, "AsyncClient", return_value=FakeClient()):
            result = await api.pavo_gateway_request("POST", "/pavo/payment", {"amount": 1})

        self.assertTrue(result["success"])
        self.assertEqual(captured["url"], "https://kebo-api-dev.magicpay.ai/api/pavo/payment")
        self.assertEqual(
            captured["kwargs"]["headers"]["X-Internal-Gateway-Token"],
            "test-magiccoffee-token-0000000000000001",
        )
        self.assertEqual(captured["kwargs"]["headers"]["X-MagicCoffee-Branch-ID"], "2")
        self.assertEqual(captured["kwargs"]["headers"]["X-MagicCoffee-Terminal-Serial"], "PAV960000010")
        self.assertEqual(captured["kwargs"]["headers"]["X-MagicCoffee-Source-Fingerprint"], "test1")
        self.assertEqual(captured["kwargs"]["headers"]["X-MagicCoffee-Provider"], "PAVO_UNICLOUD")


if __name__ == "__main__":
    unittest.main()
