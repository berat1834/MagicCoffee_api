import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

os.environ["DATABASE_URL"] = ""
os.environ["ALLOW_LOCAL_FILE_STORE"] = "true"

from fastapi.testclient import TestClient

import app.main as api


KIOSK_FINGERPRINT = "KIOSK-CASTLES-1"
CASTLES_ENV = {
    "DATABASE_URL": "",
    "ALLOW_LOCAL_FILE_STORE": "true",
    "MAGICBOSS_API_URL": "https://magicboss.example.test/api",
    "MAGICBOSS_API_KEY": "test-magicboss-key",
    "MAGICBOSS_TERMINAL_SERIAL": "CASTLES0001",
    # Castles yolu Pavo ayarlarina bagli olmamali; kiosk kimligi disindaki Pavo
    # degerleri bos birakilir ve parmak izi Pavo'nun "TEST" kuralina uymaz.
    "PAVO_GATEWAY_BASE_URL": "",
    "PAVO_GATEWAY_ALLOWED_HOST": "",
    "PAVO_BRANCH_ID": "",
    "PAVO_TERMINAL_SERIAL": "",
    "PAVO_SOURCE_FINGERPRINT": KIOSK_FINGERPRINT,
    "PAVO_PROVIDER_TYPE": "",
    "PAVO_GATEWAY_SERVICE_EMAIL": "",
    "PAVO_GATEWAY_SERVICE_PASSWORD": "",
}


class CastlesFlowTests(unittest.TestCase):
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
        api.castles_devices.clear()
        api.PAYMENT_ATTEMPTS.clear()
        api.order_numbers = iter(range(501, 600))

        self.calls = []
        self.number_response = {"status": "success", "sent_to": 1, "journal_id": 42}
        self.journal_response = {"status": "INITIATED"}
        self.journal_error = None

        async def fake_magicboss(settings, method, path, **kwargs):
            self.calls.append((method, path, kwargs.get("json")))
            if method == "POST" and path == "/number":
                return self.number_response
            if method == "GET" and path.startswith("/journal/"):
                if self.journal_error:
                    raise self.journal_error
                return self.journal_response
            raise AssertionError(f"Unexpected MagicBoss call: {method} {path}")

        async def forbidden_pavo(*args, **kwargs):
            raise AssertionError("Castles akisi Pavo gateway'ine gitmemeli")

        self.patches = [
            patch.dict(os.environ, CASTLES_ENV),
            patch.object(api, "magicboss_request", new=fake_magicboss),
            patch.object(api, "pavo_gateway_request", new=forbidden_pavo),
            patch.object(api, "save_state", new=lambda: None),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(api.app, headers={"X-MagicCoffee-Kiosk-Fingerprint": KIOSK_FINGERPRINT})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        api.castles_devices.clear()

    @staticmethod
    def payment_payload(request_id="castles-request-0001", amount=92.0, terminal_serial=None):
        payload = {
            "clientRequestId": request_id,
            "paymentMethod": "card",
            "amount": amount,
            "lines": [{"productId": "latte", "name": "ignored", "quantity": 1, "unitPrice": 1}],
        }
        if terminal_serial is not None:
            payload["terminalSerial"] = terminal_serial
        return payload

    def number_calls(self):
        return [call for call in self.calls if call[:2] == ("POST", "/number")]

    def test_start_poll_and_order_use_reserved_number(self):
        started = self.client.post("/api/pos/payments", json=self.payment_payload())
        self.assertEqual(started.status_code, 201)
        body = started.json()
        self.assertEqual(body["status"], "PROCESSING")
        self.assertEqual(body["id"], "mb-42")
        self.assertEqual(body["externalId"], "mb-42")
        self.assertEqual(body["paymentReference"], "mb-42")
        sent = self.number_calls()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][2], {
            "number": 92.0,
            "serial_number": "CASTLES0001",
            "brand": "MAGICCOFFEE",
            "order_no": "MC-501",
        })

        again = self.client.post("/api/pos/payments", json=self.payment_payload())
        self.assertEqual(again.json()["id"], "mb-42")
        self.assertEqual(len(self.number_calls()), 1)

        waiting = self.client.get("/api/pos/payments/mb-42")
        self.assertEqual(waiting.status_code, 200)
        self.assertEqual(waiting.json()["status"], "PROCESSING")
        self.assertEqual(self.calls[-1][:2], ("GET", "/journal/42"))

        self.journal_error = HTTPException(status_code=503, detail="Castles POS servisine ulaşılamadı")
        flaky = self.client.get("/api/pos/payments/mb-42")
        self.assertEqual(flaky.json()["status"], "PROCESSING")
        self.journal_error = None

        self.journal_response = {"status": "APPROVED"}
        paid = self.client.get("/api/pos/payments/mb-42")
        self.assertEqual(paid.json()["status"], "COMPLETED")

        # Ayni numara baska bir Pavo/Castles siparisine verilmemeli; sayac ilerlemis olmali.
        order_payload = {
            "clientRequestId": "castles-order-0001",
            "fulfillment": "restaurant",
            "paymentMethod": "card",
            "total": 92.0,
            "paymentReference": paid.json()["paymentReference"],
            "posTransactionId": paid.json()["id"],
            "language": "tr",
            "lines": [{"productId": "latte", "name": "ignored", "quantity": 1, "unitPrice": 1}],
        }
        order = self.client.post("/api/orders", json=order_payload)
        self.assertEqual(order.status_code, 201)
        self.assertEqual(order.json()["number"], "MC-501")
        self.assertEqual(api.products[0]["stockQuantity"], 4)
        repeated = self.client.post("/api/orders", json=order_payload)
        self.assertEqual(repeated.json()["number"], "MC-501")
        self.assertEqual(len(api.orders), 1)

    def test_next_order_seed_skips_reserved_numbers(self):
        self.client.post("/api/pos/payments", json=self.payment_payload())
        self.assertEqual(api.next_order_seed(), 502)

    def test_cancelled_payment_reports_device_message(self):
        self.client.post("/api/pos/payments", json=self.payment_payload())
        self.journal_response = {"status": "CANCELLED"}
        cancelled = self.client.get("/api/pos/payments/mb-42")
        self.assertEqual(cancelled.json()["status"], "CANCELLED")
        self.assertEqual(cancelled.json()["message"], "Ödeme POS cihazında iptal edildi.")

        calls_before = len(self.calls)
        cached = self.client.get("/api/pos/payments/mb-42")
        self.assertEqual(cached.json()["status"], "CANCELLED")
        self.assertEqual(len(self.calls), calls_before)

    def test_declined_payment_uses_decline_reason_and_blocks_order(self):
        started = self.client.post("/api/pos/payments", json=self.payment_payload())
        self.journal_response = {"status": "DECLINED", "declineReason": "Yetersiz bakiye"}
        failed = self.client.get("/api/pos/payments/mb-42")
        self.assertEqual(failed.json()["status"], "FAILED")
        self.assertEqual(failed.json()["message"], "Yetersiz bakiye")
        response = self.client.post("/api/orders", json={
            "clientRequestId": "castles-order-failed",
            "fulfillment": "package",
            "paymentMethod": "card",
            "total": 92.0,
            "paymentReference": started.json()["paymentReference"],
            "posTransactionId": "mb-42",
            "language": "tr",
            "lines": [{"productId": "latte", "quantity": 1}],
        })
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(api.orders), 0)

    def test_offline_device_marks_payment_failed(self):
        self.number_response = {"status": "success", "sent_to": 0, "journal_id": 43}
        response = self.client.post("/api/pos/payments", json=self.payment_payload("castles-offline-01"))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "FAILED")
        self.assertEqual(response.json()["message"], "Castles POS cihazı çevrimdışı; ödeme gönderilemedi.")
        self.assertIsNone(response.json()["id"])
        self.assertEqual(api.pos_payments["castles-offline-01"]["status"], "FAILED")

    def test_magicboss_http_error_marks_failed_and_propagates(self):
        async def failing(settings, method, path, **kwargs):
            raise HTTPException(status_code=502, detail="Castles POS isteği başarısız: 500")

        with patch.object(api, "magicboss_request", new=failing):
            response = self.client.post("/api/pos/payments", json=self.payment_payload("castles-http-err1"))
        self.assertEqual(response.status_code, 502)
        self.assertEqual(api.pos_payments["castles-http-err1"]["status"], "FAILED")

    def test_amount_mismatch_returns_409_without_device_call(self):
        response = self.client.post("/api/pos/payments", json=self.payment_payload(amount=10.0))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.calls, [])
        self.assertEqual(api.pos_payments, {})

    def test_unknown_terminal_serial_returns_400(self):
        response = self.client.post("/api/pos/payments", json=self.payment_payload(terminal_serial="UNKNOWN01"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Seçilen Castles cihazı tanımlı değil")
        self.assertEqual(self.calls, [])

    def test_selected_active_terminal_is_used(self):
        self.client.post("/api/admin/castles/devices", json={"serial": "CASTLES0002", "name": "Kasa 2"})
        response = self.client.post("/api/pos/payments", json=self.payment_payload(terminal_serial="CASTLES0002"))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.number_calls()[0][2]["serial_number"], "CASTLES0002")

    def test_missing_default_serial_returns_503(self):
        with patch.dict(os.environ, {"MAGICBOSS_TERMINAL_SERIAL": ""}):
            response = self.client.post("/api/pos/payments", json=self.payment_payload())
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.calls, [])

    def test_kiosk_identity_is_required(self):
        response = TestClient(api.app).post("/api/pos/payments", json=self.payment_payload())
        self.assertEqual(response.status_code, 401)
        wrong = TestClient(api.app, headers={"X-MagicCoffee-Kiosk-Fingerprint": "TEST"}).get("/api/pos/payments/mb-42")
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(self.calls, [])

    def test_device_crud(self):
        created = self.client.post("/api/admin/castles/devices", json={"serial": "CASTLES0002", "name": " Kasa 2 "})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["name"], "Kasa 2")
        self.assertTrue(created.json()["active"])

        duplicate = self.client.post("/api/admin/castles/devices", json={"serial": "CASTLES0002", "name": "Kopya"})
        self.assertEqual(duplicate.status_code, 409)
        invalid = self.client.post("/api/admin/castles/devices", json={"serial": "AB-1", "name": "Hatali"})
        self.assertEqual(invalid.status_code, 422)

        self.client.post("/api/admin/castles/devices", json={"serial": "CASTLES0003", "name": "Kasa 3"})
        updated = self.client.put("/api/admin/castles/devices/CASTLES0003", json={"active": False})
        self.assertEqual(updated.status_code, 200)
        self.assertFalse(updated.json()["active"])
        self.assertEqual(len(self.client.get("/api/admin/castles/devices").json()), 2)
        self.assertEqual(
            self.client.get("/api/pos/castles/devices").json(),
            [{"serial": "CASTLES0002", "name": "Kasa 2"}],
        )

        inactive = self.client.post("/api/pos/payments", json=self.payment_payload(terminal_serial="CASTLES0003"))
        self.assertEqual(inactive.status_code, 400)

        self.assertEqual(self.client.put("/api/admin/castles/devices/MISSING01", json={"name": "Yok"}).status_code, 404)
        self.assertEqual(self.client.delete("/api/admin/castles/devices/CASTLES0003").status_code, 204)
        self.assertEqual(self.client.delete("/api/admin/castles/devices/CASTLES0003").status_code, 404)
        self.assertEqual([item["serial"] for item in self.client.get("/api/admin/castles/devices").json()], ["CASTLES0002"])

    def test_device_and_health_report_castles_without_pavo(self):
        device = self.client.get("/api/pos/device")
        self.assertEqual(device.status_code, 200)
        self.assertEqual(device.json()["provider"], "MAGICBOSS")
        self.assertEqual(device.json()["defaultSerial"], "CASTLES0001")
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["payment"], {
            "status": "ready",
            "provider": "MAGICBOSS",
            "defaultSerial": "CASTLES0001",
        })


if __name__ == "__main__":
    unittest.main()
