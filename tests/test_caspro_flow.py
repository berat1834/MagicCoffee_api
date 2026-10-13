import asyncio
import inspect
import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import httpx

os.environ["DATABASE_URL"] = ""
os.environ["ALLOW_LOCAL_FILE_STORE"] = "true"

from fastapi.testclient import TestClient

import app.main as api
from app import caspro


KIOSK_FINGERPRINT = "KIOSK-CASTLES-1"
SERIAL_16 = "0000136250609467"
SERIAL_12 = "136250609467"
PAVO_PAYMENT_ID = "22222222-2222-4222-8222-222222222222"
CASPRO_ENV = {
    "DATABASE_URL": "",
    "ALLOW_LOCAL_FILE_STORE": "true",
    "CASPRO_API_URL": "https://caspro.example.test",
    "CASPRO_API_KEY": "test-caspro-key",
    "CASPRO_TERMINAL_SERIAL": SERIAL_16,
    "CASPRO_TRANSACTION_TIMEOUT_SECONDS": "5",
    "CASPRO_RESULT_TIMEOUT_SECONDS": "60",
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
PAVO_ENV = {
    "DATABASE_URL": "",
    "ALLOW_LOCAL_FILE_STORE": "true",
    "CASPRO_API_URL": "",
    "CASPRO_API_KEY": "",
    "CASPRO_TERMINAL_SERIAL": "",
    "PAVO_GATEWAY_BASE_URL": "https://kebo-api-dev.magicpay.ai/api",
    "PAVO_GATEWAY_ALLOWED_HOST": "kebo-api-dev.magicpay.ai",
    "PAVO_BRANCH_ID": "2",
    "PAVO_TERMINAL_SERIAL": "PAV960000010",
    "PAVO_SOURCE_FINGERPRINT": "TEST",
    "PAVO_PROVIDER_TYPE": "PAVO_CLOUD",
    "PAVO_GATEWAY_SERVICE_EMAIL": "magiccoffee-service@example.test",
    "PAVO_GATEWAY_SERVICE_PASSWORD": "test-service-password",
}


def approved_sale(payload):
    return {
        "responseCode": "00",
        "message": "ONAYLANDI",
        "authCode": "123456",
        "rrn": "000000123456",
        "bankReferenceNumber": "BR-0001",
        "maskedPan": "4111 **** **** 1111",
        "cardBrand": "VISA",
        "acquirerId": "0046",
        "batchNumber": "12",
        "stan": "000123",
        "approvalTimestamp": "2026-10-13T08:00:05Z",
        "extTransactionId": payload["extTransactionId"],
        "slipData": "RAW-SLIP-DATA",
    }


def delayed(seconds, result):
    async def handler(payload):
        await asyncio.sleep(seconds)
        return result(payload) if callable(result) else result

    return handler


def http_timeout(payload):
    return httpx.ReadTimeout("CasPro yanit vermedi")


def not_found_query(payload):
    return {"responseCode": "05", "message": "Transaction not found"}


class FakeCasProCloud:
    """httpx.MockTransport ile `/api/send-command` ucunu taklit eder."""

    def __init__(self):
        self.calls = []
        self.handlers = {}

    async def __call__(self, request):
        payload = json.loads(request.content)
        self.calls.append((payload, dict(request.headers), str(request.url)))
        handler = self.handlers.get(payload.get("messageType"))
        if handler is None:
            raise AssertionError(f"Beklenmeyen CasPro mesaji: {payload.get('messageType')}")
        result = handler(payload)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, Exception):
            raise result
        if isinstance(result, httpx.Response):
            return result
        return httpx.Response(200, json=result)

    def of(self, message_type):
        return [call for call in self.calls if call[0].get("messageType") == message_type]


class CasProHelperTests(unittest.TestCase):
    def test_normalize_serial_keeps_last_twelve_digits(self):
        self.assertEqual(caspro.normalize_caspro_serial(SERIAL_16), SERIAL_12)
        self.assertEqual(caspro.normalize_caspro_serial(SERIAL_12), SERIAL_12)
        self.assertEqual(caspro.normalize_caspro_serial(" 0000-1362-5060-9467 "), SERIAL_12)
        self.assertEqual(caspro.normalize_caspro_serial("CASTLES0001"), "0001")
        self.assertEqual(caspro.normalize_caspro_serial(None), "")

    def test_ascii_print_line_simplifies_turkish_and_caps_length(self):
        self.assertEqual(caspro.ascii_print_line("Sipariş No: MC-324 çğİöüŞĞÇÖÜı"), "Siparis No: MC-324 cgIouSGCOUi")
        self.assertEqual(len(caspro.ascii_print_line("x" * 80)), 40)
        self.assertEqual(caspro.ascii_print_line(None), "")

    def test_to_kurus_uses_decimal_half_up(self):
        self.assertEqual(caspro.to_kurus(Decimal("13.00")), 1300)
        self.assertEqual(caspro.to_kurus(Decimal("0.005")), 1)
        self.assertEqual(caspro.to_kurus(Decimal("1.115")), 112)
        self.assertEqual(caspro.to_kurus(92.0), 9200)
        self.assertEqual(caspro.to_kurus("19.99"), 1999)

    def test_decline_message_prefers_device_message_then_appendix(self):
        self.assertEqual(caspro.decline_message("51", ""), "Yetersiz bakiye")
        self.assertEqual(caspro.decline_message("51", "Limit yetersiz"), "Limit yetersiz")
        self.assertEqual(caspro.decline_message("XX", None), "Kart işlemi reddedildi (kod XX)")

    def test_builders_use_document_field_names(self):
        sale = caspro.build_sale(SERIAL_16, 1300, "cp-abc", ["MAGIC COFFEE", "Sipariş No: MC-324"], timestamp="2026-10-13T08:00:00Z")
        self.assertEqual(sale["messageType"], "Transaction")
        self.assertEqual(sale["transactionType"], "Sale")
        self.assertEqual(sale["DeviceSerialNumber"], SERIAL_12)
        self.assertEqual(sale["amount"], 1300)
        self.assertEqual(sale["currency"], "TRY")
        self.assertEqual(sale["printLine"], ["MAGIC COFFEE", "Siparis No: MC-324"])
        self.assertEqual(sale["installment"], 1)
        self.assertEqual(sale["originalBankReferenceCode"], "")
        self.assertTrue(sale["isPrintSlips"])
        self.assertNotIn("acquirerId", sale)
        self.assertEqual(len(sale["clientRequestId"]), 32)
        self.assertEqual(caspro.build_query(SERIAL_16, "cp-abc")["extTransactionId"], "cp-abc")
        self.assertEqual(caspro.build_status(SERIAL_16, "cp-abc")["transactionId"], "cp-abc")
        self.assertEqual(caspro.build_cancel(SERIAL_16)["messageType"], "CancelTransaction")
        self.assertEqual(caspro.build_pos_information(SERIAL_16)["timestamp"], "")
        printed = caspro.build_print(SERIAL_16, [{"text": "MAGIC COFFEE", "align": "CENTER", "fontSize": "LARGE"}, "Ödeme: NAKİT"])
        self.assertEqual(printed["lines"], [
            {"text": "MAGIC COFFEE", "align": "CENTER", "fontSize": "LARGE"},
            {"text": "Odeme: NAKIT", "align": "LEFT", "fontSize": "NORMAL"},
        ])


class CasProFlowTests(unittest.TestCase):
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
        api.CASPRO_TASKS.clear()
        api.order_numbers = iter(range(501, 600))

        self.cloud = FakeCasProCloud()
        self.cloud.handlers["Transaction"] = approved_sale

        def client_factory(timeout):
            return httpx.AsyncClient(transport=httpx.MockTransport(self.cloud), timeout=timeout)

        async def forbidden_pavo(*args, **kwargs):
            raise AssertionError("Castles akisi Pavo gateway'ine gitmemeli")

        self.patches = [
            patch.dict(os.environ, CASPRO_ENV),
            patch.object(caspro, "_client_factory", new=client_factory),
            patch.object(api, "pavo_gateway_request", new=forbidden_pavo),
            patch.object(api, "save_state", new=lambda: None),
            patch.object(api, "CASPRO_RECOVERY_INTERVAL_SECONDS", 0.05),
            patch.object(api, "CASPRO_OFFLINE_GRACE_SECONDS", 0.4),
        ]
        for item in self.patches:
            item.start()
        # Arka plan satis gorevlerinin yasamasi icin istemci baglam olarak acilir (kalici event loop).
        self.client = TestClient(api.app, headers={"X-MagicCoffee-Kiosk-Fingerprint": KIOSK_FINGERPRINT})
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        api.castles_devices.clear()
        api.CASPRO_TASKS.clear()

    @staticmethod
    def payment_payload(request_id="caspro-request-0001", amount=92.0, terminal_serial=None):
        payload = {
            "clientRequestId": request_id,
            "paymentMethod": "card",
            "amount": amount,
            "lines": [{"productId": "latte", "name": "ignored", "quantity": 1, "unitPrice": 1}],
        }
        if terminal_serial is not None:
            payload["terminalSerial"] = terminal_serial
        return payload

    @staticmethod
    def order_payload(payment, request_id="caspro-order-0001"):
        return {
            "clientRequestId": request_id,
            "fulfillment": "restaurant",
            "paymentMethod": "card",
            "total": 92.0,
            "paymentReference": payment["paymentReference"],
            "posTransactionId": payment["id"],
            "language": "tr",
            "lines": [{"productId": "latte", "name": "ignored", "quantity": 1, "unitPrice": 1}],
        }

    def wait_for_status(self, transaction_id, expected, timeout=5.0):
        deadline = time.monotonic() + timeout
        body = None
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/pos/payments/{transaction_id}")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            if body["status"] == expected:
                return body
            time.sleep(0.05)
        self.fail(f"{transaction_id} beklenen duruma gelmedi: {expected}, son: {body}")

    def test_approved_sale_completes_and_order_uses_reserved_number(self):
        self.cloud.handlers["Transaction"] = delayed(0.8, approved_sale)
        started = self.client.post("/api/pos/payments", json=self.payment_payload())
        self.assertEqual(started.status_code, 201)
        body = started.json()
        self.assertEqual(body["status"], "PROCESSING")
        transaction_id = body["id"]
        self.assertRegex(transaction_id, r"^cp-[0-9a-f]{24}$")
        self.assertEqual(body["externalId"], transaction_id)
        self.assertEqual(body["paymentReference"], transaction_id)

        sales = self.cloud.of("Transaction")
        self.assertEqual(len(sales), 1)
        payload, headers, url = sales[0]
        self.assertEqual(url, "https://caspro.example.test/api/send-command")
        self.assertEqual(headers.get("x-api-key"), "test-caspro-key")
        self.assertEqual(payload["transactionType"], "Sale")
        self.assertEqual(payload["DeviceSerialNumber"], SERIAL_12)
        self.assertEqual(payload["amount"], 9200)
        self.assertEqual(payload["currency"], "TRY")
        self.assertEqual(payload["printLine"], ["MAGIC COFFEE", "Siparis No: MC-501"])
        self.assertEqual(payload["extTransactionId"], transaction_id)
        self.assertTrue(payload["isPrintSlips"])
        self.assertEqual(payload["installment"], 1)
        self.assertRegex(payload["timestamp"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

        again = self.client.post("/api/pos/payments", json=self.payment_payload())
        self.assertEqual(again.json()["id"], transaction_id)
        self.assertEqual(len(self.cloud.of("Transaction")), 1)
        self.assertEqual(api.next_order_seed(), 502)

        paid = self.wait_for_status(transaction_id, "COMPLETED")
        self.assertIsNone(paid["message"])
        record = api.pos_payments["caspro-request-0001"]
        self.assertEqual(record["rrn"], "000000123456")
        self.assertEqual(record["authCode"], "123456")
        self.assertEqual(record["bankReferenceNumber"], "BR-0001")
        self.assertEqual(record["maskedPan"], "4111 **** **** 1111")
        self.assertEqual(record["cardBrand"], "VISA")
        self.assertEqual(record["acquirerId"], "0046")
        self.assertEqual(record["batchNumber"], "12")
        self.assertEqual(record["stan"], "000123")
        self.assertEqual(record["approvalTimestamp"], "2026-10-13T08:00:05Z")
        self.assertNotIn("slipData", record)
        self.assertEqual(len(self.cloud.of("QueryTransaction")), 0)

        order = self.client.post("/api/orders", json=self.order_payload(paid))
        self.assertEqual(order.status_code, 201)
        self.assertEqual(order.json()["number"], "MC-501")
        self.assertEqual(api.products[0]["stockQuantity"], 4)
        repeated = self.client.post("/api/orders", json=self.order_payload(paid))
        self.assertEqual(repeated.json()["number"], "MC-501")
        self.assertEqual(len(api.orders), 1)

    def test_declined_51_uses_appendix_message_and_blocks_order(self):
        self.cloud.handlers["Transaction"] = lambda payload: {"responseCode": "51", "message": "", "rrn": "000000123457"}
        started = self.client.post("/api/pos/payments", json=self.payment_payload())
        self.assertEqual(started.status_code, 201)
        failed = self.wait_for_status(started.json()["id"], "FAILED")
        self.assertEqual(failed["message"], "Yetersiz bakiye")
        response = self.client.post("/api/orders", json=self.order_payload(started.json()))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(api.orders), 0)
        # Kayit nihai; tekrar poll buluta gitmez.
        calls_before = len(self.cloud.calls)
        self.client.get(f"/api/pos/payments/{started.json()['id']}")
        self.assertEqual(len(self.cloud.calls), calls_before)

    def test_declined_with_device_message_keeps_device_message(self):
        self.cloud.handlers["Transaction"] = lambda payload: {"responseCode": "05", "message": "Kart reddedildi", "rrn": "1"}
        started = self.client.post("/api/pos/payments", json=self.payment_payload())
        failed = self.wait_for_status(started.json()["id"], "FAILED")
        self.assertEqual(failed["message"], "Kart reddedildi")

    def test_offline_device_marks_payment_failed_immediately(self):
        self.cloud.handlers["Transaction"] = lambda payload: httpx.Response(
            404, json={"detail": f"Device {SERIAL_12} not connected"},
        )
        response = self.client.post("/api/pos/payments", json=self.payment_payload("caspro-offline-01"))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "FAILED")
        self.assertEqual(response.json()["message"], "Castles POS cihazı çevrimdışı; ödeme gönderilemedi.")
        self.assertEqual(api.pos_payments["caspro-offline-01"]["status"], "FAILED")
        self.assertEqual(len(self.cloud.of("QueryTransaction")), 0)
        self.assertEqual(len(self.cloud.of("CancelTransaction")), 0)

    def test_http_timeout_recovers_with_query_transaction(self):
        self.cloud.handlers["Transaction"] = http_timeout
        query_results = iter([
            not_found_query,
            lambda payload: {
                "transactionStatus": "APPROVED",
                "responseCode": "00",
                "rrn": "000000999",
                "approvalCode": "A9",
                "bankReferenceNumber": "BR-9",
                "cardNumber": "4111********1111",
                "amount": 9200,
                "dateTime": "2026-10-13T08:01:00Z",
            },
        ])
        self.cloud.handlers["QueryTransaction"] = lambda payload: next(query_results)(payload)
        started = self.client.post("/api/pos/payments", json=self.payment_payload())
        self.assertIn(started.json()["status"], {"PROCESSING", "COMPLETED"})
        transaction_id = started.json()["id"]
        self.wait_for_status(transaction_id, "COMPLETED")
        queries = self.cloud.of("QueryTransaction")
        self.assertEqual(len(queries), 2)
        self.assertEqual(queries[0][0]["extTransactionId"], transaction_id)
        self.assertEqual(queries[0][0]["DeviceSerialNumber"], SERIAL_12)
        record = api.pos_payments["caspro-request-0001"]
        self.assertEqual(record["authCode"], "A9")
        self.assertEqual(record["maskedPan"], "4111********1111")
        self.assertEqual(record["approvalTimestamp"], "2026-10-13T08:01:00Z")
        self.assertEqual(len(self.cloud.of("CancelTransaction")), 0)

    def test_no_result_sends_cancel_and_fails(self):
        self.cloud.handlers["Transaction"] = http_timeout
        self.cloud.handlers["QueryTransaction"] = not_found_query
        self.cloud.handlers["CancelTransaction"] = lambda payload: {"status": True}
        with patch.dict(os.environ, {"CASPRO_RESULT_TIMEOUT_SECONDS": "1"}):
            started = self.client.post("/api/pos/payments", json=self.payment_payload())
            self.assertEqual(started.json()["status"], "PROCESSING")
            failed = self.wait_for_status(started.json()["id"], "FAILED")
        self.assertEqual(failed["message"], "Kart okutma süresi doldu.")
        self.assertGreaterEqual(len(self.cloud.of("QueryTransaction")), 1)
        cancels = self.cloud.of("CancelTransaction")
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0][0]["DeviceSerialNumber"], SERIAL_12)

    def test_kiosk_cancel_endpoint_marks_cancelled_and_releases_number(self):
        self.cloud.handlers["Transaction"] = delayed(3.0, {"responseCode": "98", "message": "Islem iptal edildi", "rrn": ""})
        self.cloud.handlers["CancelTransaction"] = lambda payload: {"status": True}
        started = self.client.post("/api/pos/payments", json=self.payment_payload())
        transaction_id = started.json()["id"]
        self.assertEqual(started.json()["status"], "PROCESSING")

        unauthenticated = TestClient(api.app).post(f"/api/pos/payments/{transaction_id}/cancel")
        self.assertEqual(unauthenticated.status_code, 401)

        cancelled = self.client.post(f"/api/pos/payments/{transaction_id}/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json(), {"status": "CANCELLED", "message": "Ödeme POS cihazında iptal edildi."})
        cancels = self.cloud.of("CancelTransaction")
        self.assertEqual(len(cancels), 1)
        self.assertEqual(cancels[0][0], {
            "messageType": "CancelTransaction",
            "clientRequestId": cancels[0][0]["clientRequestId"],
            "DeviceSerialNumber": SERIAL_12,
        })
        record = api.pos_payments["caspro-request-0001"]
        self.assertIsNone(record["reservedOrderNumber"])
        self.assertEqual(record["releasedOrderNumber"], "MC-501")

        polled = self.client.get(f"/api/pos/payments/{transaction_id}")
        self.assertEqual(polled.json()["status"], "CANCELLED")
        again = self.client.post(f"/api/pos/payments/{transaction_id}/cancel")
        self.assertEqual(again.json()["status"], "CANCELLED")
        self.assertEqual(len(self.cloud.of("CancelTransaction")), 1)
        order = self.client.post("/api/orders", json=self.order_payload(started.json()))
        self.assertEqual(order.status_code, 409)
        self.assertEqual(self.client.post("/api/pos/payments/cp-000000000000000000000000/cancel").status_code, 404)

    def test_poll_queries_cloud_when_background_task_is_missing(self):
        self.cloud.handlers["QueryTransaction"] = lambda payload: {
            "transactionStatus": "APPROVED", "responseCode": "00", "rrn": "R1", "approvalCode": "A1",
        }
        old = (datetime.now(timezone.utc) - timedelta(seconds=15)).isoformat()
        api.pos_payments["restored-01"] = {
            "clientRequestId": "restored-01",
            "externalId": "cp-0123456789abcdef01234567",
            "transactionId": "cp-0123456789abcdef01234567",
            "status": "PROCESSING",
            "message": None,
            "amount": 92.0,
            "paymentMethod": "card",
            "cartFingerprint": "x",
            "provider": "CASPRO",
            "reservedOrderNumber": "MC-499",
            "terminalSerial": SERIAL_12,
            "createdAt": old,
            "updatedAt": old,
        }
        fresh = {**api.pos_payments["restored-01"], "clientRequestId": "restored-02",
                 "externalId": "cp-0123456789abcdef01234568", "transactionId": "cp-0123456789abcdef01234568",
                 "createdAt": datetime.now(timezone.utc).isoformat()}
        api.pos_payments["restored-02"] = fresh
        expired = {**fresh, "clientRequestId": "restored-03",
                   "externalId": "cp-0123456789abcdef01234569", "transactionId": "cp-0123456789abcdef01234569",
                   "createdAt": (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()}
        api.pos_payments["restored-03"] = expired

        # Yeni kayit (< 10 s): buluta sorulmaz.
        self.assertEqual(self.client.get("/api/pos/payments/cp-0123456789abcdef01234568").json()["status"], "PROCESSING")
        self.assertEqual(len(self.cloud.calls), 0)
        # 15 s'lik kayit: tek QueryTransaction ile sonuc alinir.
        polled = self.client.get("/api/pos/payments/cp-0123456789abcdef01234567")
        self.assertEqual(polled.json()["status"], "COMPLETED")
        self.assertEqual(len(self.cloud.of("QueryTransaction")), 1)
        self.assertEqual(api.pos_payments["restored-01"]["authCode"], "A1")
        # Sure asilmis kayit: sorgusuz FAILED.
        expired_poll = self.client.get("/api/pos/payments/cp-0123456789abcdef01234569")
        self.assertEqual(expired_poll.json()["status"], "FAILED")
        self.assertEqual(expired_poll.json()["message"], "Kart okutma süresi doldu.")
        self.assertEqual(len(self.cloud.of("QueryTransaction")), 1)

    def test_amount_mismatch_returns_409_without_cloud_call(self):
        response = self.client.post("/api/pos/payments", json=self.payment_payload(amount=10.0))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.cloud.calls, [])
        self.assertEqual(api.pos_payments, {})

    def test_terminal_serial_selection_and_normalization(self):
        unknown = self.client.post("/api/pos/payments", json=self.payment_payload(terminal_serial="136250609999"))
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(unknown.json()["detail"], "Seçilen Castles cihazı tanımlı değil")

        self.client.post("/api/admin/castles/devices", json={"serial": "0000136250609468", "name": "Kasa 2"})
        selected = self.client.post("/api/pos/payments", json=self.payment_payload(terminal_serial="0000136250609468"))
        self.assertEqual(selected.status_code, 201)
        self.assertEqual(self.cloud.of("Transaction")[0][0]["DeviceSerialNumber"], "136250609468")
        self.assertEqual(api.pos_payments["caspro-request-0001"]["terminalSerial"], "136250609468")

        self.client.post("/api/admin/castles/devices", json={"serial": "CASTLES0001", "name": "Eski seri"})
        invalid = self.client.post("/api/pos/payments", json=self.payment_payload("caspro-request-0002", terminal_serial="CASTLES0001"))
        self.assertEqual(invalid.status_code, 400)

        with patch.dict(os.environ, {"CASPRO_TERMINAL_SERIAL": ""}):
            missing = self.client.post("/api/pos/payments", json=self.payment_payload("caspro-request-0003"))
        self.assertEqual(missing.status_code, 503)
        self.assertEqual(len(self.cloud.of("Transaction")), 1)

    def test_kiosk_identity_is_required(self):
        response = TestClient(api.app).post("/api/pos/payments", json=self.payment_payload())
        self.assertEqual(response.status_code, 401)
        wrong = TestClient(api.app, headers={"X-MagicCoffee-Kiosk-Fingerprint": "TEST"}).get(
            "/api/pos/payments/cp-0123456789abcdef01234567",
        )
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(self.cloud.calls, [])

    def test_device_crud(self):
        created = self.client.post("/api/admin/castles/devices", json={"serial": "0000136250609468", "name": " Kasa 2 "})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["name"], "Kasa 2")
        self.assertTrue(created.json()["active"])

        duplicate = self.client.post("/api/admin/castles/devices", json={"serial": "0000136250609468", "name": "Kopya"})
        self.assertEqual(duplicate.status_code, 409)
        invalid = self.client.post("/api/admin/castles/devices", json={"serial": "AB-1", "name": "Hatali"})
        self.assertEqual(invalid.status_code, 422)

        self.client.post("/api/admin/castles/devices", json={"serial": "136250609469", "name": "Kasa 3"})
        updated = self.client.put("/api/admin/castles/devices/136250609469", json={"active": False})
        self.assertEqual(updated.status_code, 200)
        self.assertFalse(updated.json()["active"])
        self.assertEqual(len(self.client.get("/api/admin/castles/devices").json()), 2)
        self.assertEqual(
            self.client.get("/api/pos/castles/devices").json(),
            [{"serial": "0000136250609468", "name": "Kasa 2"}],
        )

        inactive = self.client.post("/api/pos/payments", json=self.payment_payload(terminal_serial="136250609469"))
        self.assertEqual(inactive.status_code, 400)

        self.assertEqual(self.client.put("/api/admin/castles/devices/MISSING01", json={"name": "Yok"}).status_code, 404)
        self.assertEqual(self.client.delete("/api/admin/castles/devices/136250609469").status_code, 204)
        self.assertEqual(self.client.delete("/api/admin/castles/devices/136250609469").status_code, 404)
        self.assertEqual(
            [item["serial"] for item in self.client.get("/api/admin/castles/devices").json()],
            ["0000136250609468"],
        )

    def test_device_and_health_report_caspro_without_pavo(self):
        device = self.client.get("/api/pos/device")
        self.assertEqual(device.status_code, 200)
        self.assertEqual(device.json()["provider"], "CASPRO")
        self.assertEqual(device.json()["defaultSerial"], SERIAL_16)
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["payment"], {
            "status": "ready",
            "provider": "CASPRO",
            "defaultSerial": SERIAL_16,
        })


class PavoPathUnaffectedTests(unittest.TestCase):
    def setUp(self):
        api.categories[:] = [{"id": "coffee", "name": "Kahveler", "eyebrow": "Kahve menusu", "position": 0, "active": True}]
        api.products[:] = [{
            "id": "latte", "categoryId": "coffee", "name": "Caffe Latte", "description": "", "price": 92.0,
            "kind": "coffee", "active": True, "available": True, "stockTrackingEnabled": False, "stockQuantity": 0,
            "customization": {},
        }]
        api.pos_payments.clear()
        api.orders.clear()
        api.order_requests.clear()
        api.PAYMENT_ATTEMPTS.clear()
        self.gateway_calls = []
        self.cloud = FakeCasProCloud()

        async def fake_gateway(method, path, payload=None):
            self.gateway_calls.append((method, path, payload))
            if path.startswith("/pavo/devices/"):
                return [{
                    "id": "11111111-1111-4111-8111-111111111111",
                    "name": "Coffee POS",
                    "provider_type": "PAVO_CLOUD",
                    "branch_id": 2,
                    "serial_number": "PAV960000010",
                    "status": "ACTIVE",
                    "is_default": True,
                    "cloud_source_fingerprint": "TEST",
                    "cloud_pairing_id": "coffee-pair-1",
                }]
            if method == "POST" and path == "/pavo/payment":
                return {"id": PAVO_PAYMENT_ID, "status": "PROCESSING"}
            if method == "GET" and path == f"/pavo/cloud/poll/{PAVO_PAYMENT_ID}":
                return {"status": "COMPLETED"}
            raise AssertionError(f"Unexpected gateway call: {method} {path}")

        self.patches = [
            patch.dict(os.environ, PAVO_ENV),
            patch.object(caspro, "_client_factory", new=lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(self.cloud))),
            patch.object(api, "pavo_gateway_request", new=fake_gateway),
            patch.object(api, "save_state", new=lambda: None),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(api.app, headers={"X-MagicCoffee-Kiosk-Fingerprint": "TEST"})

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()

    def test_pavo_flow_runs_without_caspro(self):
        self.assertIsNone(api.caspro_settings())
        health = self.client.get("/health")
        self.assertEqual(health.json()["payment"], {"status": "ready", "provider": "PAVO_CLOUD"})
        started = self.client.post("/api/pos/payments", json={
            "clientRequestId": "pavo-request-0001",
            "paymentMethod": "card",
            "amount": 92.0,
            "lines": [{"productId": "latte", "quantity": 1}],
        })
        self.assertEqual(started.status_code, 201)
        self.assertEqual(started.json()["id"], PAVO_PAYMENT_ID)
        self.assertEqual(started.json()["status"], "PROCESSING")
        polled = self.client.get(f"/api/pos/payments/{PAVO_PAYMENT_ID}")
        self.assertEqual(polled.json()["status"], "COMPLETED")
        self.assertEqual(self.cloud.calls, [])
        self.assertEqual(api.CASPRO_TASKS, {})
        self.assertEqual(self.client.post(f"/api/pos/payments/{PAVO_PAYMENT_ID}/cancel").status_code, 404)


if __name__ == "__main__":
    unittest.main()
