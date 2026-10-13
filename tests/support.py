"""Platform testleri icin ortak altyapi: gecici SQLite veritabani ve sahte CasPro bulutu."""
import asyncio
import inspect
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

os.environ["DATABASE_URL"] = ""
os.environ.pop("ALLOW_LOCAL_FILE_STORE", None)

from fastapi.testclient import TestClient  # noqa: E402

import app.main as api  # noqa: E402
from app import caspro  # noqa: E402
from app.db import db  # noqa: E402

SERIAL_16 = "0000136250609467"
SERIAL_12 = "136250609467"
SERIAL_B = "136250609468"
CASPRO_ENV = {
    "DATABASE_URL": "",
    "CASPRO_API_URL": "https://caspro.example.test",
    "CASPRO_API_KEY": "test-caspro-key",
    "CASPRO_TERMINAL_SERIAL": "",
    "CASPRO_TRANSACTION_TIMEOUT_SECONDS": "5",
    "CASPRO_RESULT_TIMEOUT_SECONDS": "60",
    "DB_POOL_MAX_SIZE": "5",
    "DB_POOL_TIMEOUT": "2",
    "GOOGLE_TRANSLATE_API_KEY": "",
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


class PlatformTestCase(unittest.TestCase):
    """Her test kendi gecici SQLite dosyasiyla, migrasyonlar calismis halde baslar."""

    extra_env: dict = {}

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mk-test-")
        self.cloud = FakeCasProCloud()
        self.cloud.handlers["Transaction"] = approved_sale

        def client_factory(timeout):
            return httpx.AsyncClient(transport=httpx.MockTransport(self.cloud), timeout=timeout)

        env = {**CASPRO_ENV, "MK_SQLITE_PATH": os.path.join(self.tmpdir, "platform.db"), **self.extra_env}
        self.patches = [
            patch.dict(os.environ, env),
            patch.object(caspro, "_client_factory", new=client_factory),
            patch.object(api, "CASPRO_RECOVERY_INTERVAL_SECONDS", 0.05),
            patch.object(api, "CASPRO_OFFLINE_GRACE_SECONDS", 0.4),
        ]
        for item in self.patches:
            item.start()
        api.PAYMENT_ATTEMPTS.clear()
        api.KIOSK_SEEN.clear()
        api.CASPRO_TASKS.clear()
        self.prepare_database()
        # Arka plan satis gorevlerinin yasamasi icin istemci baglam olarak acilir (kalici event loop, lifespan).
        self.client = TestClient(api.app)
        self.client.__enter__()

    def prepare_database(self):
        """Alt siniflar migrasyondan once eski tablolari olusturmak icin kullanir."""

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        api.CASPRO_TASKS.clear()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- yardimcilar ----
    @staticmethod
    def kiosk(kiosk_id="k01"):
        return {"X-Kiosk-Id": kiosk_id}

    def assign_pos(self, kiosk_id="k01", serial=SERIAL_16):
        response = self.client.put(f"/api/admin/kiosks/{kiosk_id}", json={"posSerial": serial})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def payment_payload(request_id="caspro-request-0001", amount=92.0, lines=None):
        return {
            "clientRequestId": request_id,
            "paymentMethod": "card",
            "amount": amount,
            "lines": lines or [{"productId": "latte", "name": "ignored", "quantity": 1, "unitPrice": 1}],
        }

    @staticmethod
    def order_payload(payment, request_id="caspro-order-0001", total=92.0, lines=None):
        return {
            "clientRequestId": request_id,
            "fulfillment": "package",
            "paymentMethod": "card",
            "total": total,
            "paymentReference": payment["paymentReference"],
            "posTransactionId": payment["id"],
            "language": "tr",
            "lines": lines or [{"productId": "latte", "name": "ignored", "quantity": 1, "unitPrice": 1}],
        }

    def wait_for_status(self, transaction_id, expected, kiosk_id="k01", timeout=5.0):
        deadline = time.monotonic() + timeout
        body = None
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/pos/payments/{transaction_id}", headers=self.kiosk(kiosk_id))
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            if body["status"] == expected:
                return body
            time.sleep(0.05)
        self.fail(f"{transaction_id} beklenen duruma gelmedi: {expected}, son: {body}")

    def paid_payment(self, request_id, kiosk_id="k01", amount=92.0, lines=None):
        started = self.client.post(
            "/api/pos/payments", json=self.payment_payload(request_id, amount, lines), headers=self.kiosk(kiosk_id),
        )
        self.assertEqual(started.status_code, 201, started.text)
        return self.wait_for_status(started.json()["id"], "COMPLETED", kiosk_id)

    @staticmethod
    def product(product_id):
        with db.connect() as conn:
            return api.find_product(conn, product_id)

    @staticmethod
    def payment_record(client_request_id):
        with db.connect() as conn:
            return api.load_payment_by_request(conn, client_request_id)

    @staticmethod
    def count(table, where="", params=()):
        with db.connect() as conn:
            return conn.execute(f"SELECT COUNT(*) AS n FROM {table} {where}", params).fetchone()["n"]
