import os
import threading
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import httpx

from support import (
    SERIAL_12,
    SERIAL_16,
    PlatformTestCase,
    api,
    approved_sale,
    caspro,
    db,
    delayed,
    http_timeout,
    not_found_query,
)


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

    def test_kiosk_print_name_is_ascii(self):
        self.assertEqual(api.kiosk_print_name("Kiosk 12 · Ayakkabı & Çanta"), "Kiosk 12 - Ayakkabi & Canta")


class CasProFlowTests(PlatformTestCase):
    def setUp(self):
        super().setUp()
        self.assign_pos("k01", SERIAL_16)

    def test_approved_sale_completes_and_order_uses_reserved_number(self):
        self.cloud.handlers["Transaction"] = delayed(0.8, approved_sale)
        started = self.client.post("/api/pos/payments", json=self.payment_payload(), headers=self.kiosk())
        self.assertEqual(started.status_code, 201, started.text)
        body = started.json()
        self.assertEqual(body["status"], "PROCESSING")
        transaction_id = body["id"]
        self.assertRegex(transaction_id, r"^cp-[0-9a-f]{24}$")
        self.assertEqual(body["paymentReference"], transaction_id)

        sales = self.cloud.of("Transaction")
        self.assertEqual(len(sales), 1)
        payload, headers, url = sales[0]
        self.assertEqual(url, "https://caspro.example.test/api/send-command")
        self.assertEqual(headers.get("x-api-key"), "test-caspro-key")
        self.assertEqual(payload["transactionType"], "Sale")
        self.assertEqual(payload["DeviceSerialNumber"], SERIAL_12)
        self.assertEqual(payload["amount"], 9200)
        self.assertEqual(payload["printLine"], ["Kiosk 01 - Kahve", "Siparis No: KHV-0001"])
        self.assertEqual(payload["installment"], 1)
        self.assertEqual(payload["extTransactionId"], transaction_id)

        # Ayni clientRequestId: yeni satis gonderilmez.
        again = self.client.post("/api/pos/payments", json=self.payment_payload(), headers=self.kiosk())
        self.assertEqual(again.json()["id"], transaction_id)
        self.assertEqual(len(self.cloud.of("Transaction")), 1)

        paid = self.wait_for_status(transaction_id, "COMPLETED")
        self.assertIsNone(paid["message"])
        record = self.payment_record("caspro-request-0001")
        self.assertEqual(record["rrn"], "000000123456")
        self.assertEqual(record["authCode"], "123456")
        self.assertEqual(record["maskedPan"], "4111 **** **** 1111")
        self.assertEqual(record["kioskId"], "k01")
        self.assertNotIn("slipData", record)

        before = self.product("latte")["stockQuantity"]
        order = self.client.post("/api/orders", json=self.order_payload(paid), headers=self.kiosk())
        self.assertEqual(order.status_code, 201, order.text)
        self.assertEqual(order.json()["number"], "KHV-0001")
        self.assertEqual(order.json()["kiosk_id"], "k01")
        self.assertEqual(order.json()["sectionIds"], ["kahve"])
        self.assertEqual(self.product("latte")["stockQuantity"], before - 1)
        repeated = self.client.post("/api/orders", json=self.order_payload(paid), headers=self.kiosk())
        self.assertEqual(repeated.json()["number"], "KHV-0001")
        other_request = self.client.post("/api/orders", json=self.order_payload(paid, "caspro-order-0002"), headers=self.kiosk())
        self.assertEqual(other_request.json()["number"], "KHV-0001")
        self.assertEqual(self.count("mk_orders"), 1)
        self.assertEqual(self.product("latte")["stockQuantity"], before - 1)
        self.assertEqual(self.count("mk_stock_movements", "WHERE product_id = %s", ("latte",)), 1)

        fetched = self.client.get("/api/orders/KHV-0001", headers=self.kiosk())
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(self.client.get("/api/orders/KHV-0001", headers=self.kiosk("k02")).status_code, 404)
        receipt_one = self.client.post("/api/orders/KHV-0001/receipt", json={"status": "printed", "printAttemptId": "receipt-attempt-1"}, headers=self.kiosk())
        receipt_two = self.client.post("/api/orders/KHV-0001/receipt", json={"status": "printed", "printAttemptId": "receipt-attempt-2"}, headers=self.kiosk())
        self.assertFalse(receipt_one.json()["alreadyRecorded"])
        self.assertTrue(receipt_two.json()["alreadyRecorded"])

    def test_cross_section_cart_creates_single_order(self):
        self.assign_pos("k03", "136250609470")
        lines = [
            {"productId": "latte", "quantity": 2},
            {"productId": "pizza-margarita", "quantity": 1, "selection": {"choices": {"sides": ["medium"], "drinks": ["olive"]}}},
            {"productId": "giyim-basic-tisort", "quantity": 1, "selection": {"choices": {"sides": ["l"]}}},
        ]
        total = 92 * 2 + (249 + 60 + 20) + 399
        missing_size = self.client.post("/api/pos/payments", headers=self.kiosk("k03"), json=self.payment_payload(
            "cross-request-00", total, [*lines[:2], {"productId": "giyim-basic-tisort", "quantity": 1}],
        ))
        self.assertEqual(missing_size.status_code, 400)
        self.assertIn("Beden Seçimi zorunlu", missing_size.json()["detail"])

        stock_before = {pid: self.product(pid)["stockQuantity"] for pid in ("latte", "pizza-margarita", "giyim-basic-tisort")}
        paid = self.paid_payment("cross-request-01", "k03", total, lines)
        self.assertEqual(self.cloud.of("Transaction")[0][0]["printLine"], ["Kiosk 03 - Pizza", "Siparis No: PZA-0001"])
        order = self.client.post("/api/orders", headers=self.kiosk("k03"), json=self.order_payload(paid, "cross-order-01", total, lines))
        self.assertEqual(order.status_code, 201, order.text)
        body = order.json()
        self.assertEqual(body["number"], "PZA-0001")
        self.assertEqual(body["section_ids"], ["kahve", "pizza", "giyim"])
        self.assertEqual(body["item_count"], 4)
        self.assertEqual(self.product("latte")["stockQuantity"], stock_before["latte"] - 2)
        self.assertEqual(self.product("pizza-margarita")["stockQuantity"], stock_before["pizza-margarita"] - 1)
        self.assertEqual(self.product("giyim-basic-tisort")["stockQuantity"], stock_before["giyim-basic-tisort"] - 1)

        pizza_orders = self.client.get("/api/admin/orders?section=pizza").json()
        self.assertEqual([item["number"] for item in pizza_orders], ["PZA-0001"])
        self.assertEqual(self.client.get("/api/admin/orders?section=burger").json(), [])
        self.assertEqual(len(self.client.get("/api/admin/orders?kiosk=k03").json()), 1)
        self.assertEqual(self.client.get("/api/admin/orders?kiosk=k01").json(), [])
        dashboard = self.client.get("/api/admin/dashboard?section=pizza").json()
        self.assertEqual(dashboard["stats"]["today_order_count"], 1)
        self.assertEqual(dashboard["stats"]["today_revenue"], 329.0)
        today = datetime.now().date().isoformat()
        report = self.client.get(f"/api/admin/reports?start={today}&end={today}&kiosk=k03").json()
        self.assertEqual(report["summary"]["order_count"], 1)
        self.assertEqual(report["summary"]["revenue"], float(total))
        self.assertEqual({row["sectionId"] for row in report["sections"]}, {"kahve", "pizza", "giyim"})

    def test_order_numbers_use_kiosk_prefix_and_counter(self):
        self.assign_pos("k02", "136250609471")
        first = self.paid_payment("number-request-1", "k01")
        second = self.paid_payment("number-request-2", "k01")
        burger = self.paid_payment("number-request-3", "k02")
        self.assertEqual(first["orderNumber"], "KHV-0001")
        self.assertEqual(second["orderNumber"], "KHV-0002")
        self.assertEqual(burger["orderNumber"], "BRG-0001")
        kiosks = {row["id"]: row for row in self.client.get("/api/admin/kiosks").json()}
        self.assertEqual(kiosks["k01"]["orderSeq"], 2)

        # Payment of another kiosk cannot be used or polled.
        self.assertEqual(self.client.get(f"/api/pos/payments/{burger['id']}", headers=self.kiosk("k01")).status_code, 404)
        foreign = self.client.post("/api/orders", headers=self.kiosk("k01"), json=self.order_payload(burger, "foreign-order-01"))
        self.assertEqual(foreign.status_code, 409)

    def test_concurrent_number_reservation_is_unique(self):
        numbers: list[str] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def worker():
            try:
                for _ in range(5):
                    with db.connect(write=True) as conn:
                        number = api.reserve_order_number(conn, "k07")
                    with lock:
                        numbers.append(number)
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(numbers), 40)
        self.assertEqual(len(set(numbers)), 40)
        self.assertEqual(sorted(numbers), [f"DND-{index:04d}" for index in range(1, 41)])

    def test_concurrent_orders_get_unique_numbers_and_exact_stock(self):
        payments = [self.paid_payment(f"parallel-request-{index:02d}") for index in range(6)]
        before = self.product("latte")["stockQuantity"]
        results: list = []
        lock = threading.Lock()

        def worker(index, payment):
            response = self.client.post(
                "/api/orders", headers=self.kiosk(), json=self.order_payload(payment, f"parallel-order-{index:02d}"),
            )
            with lock:
                results.append(response)

        threads = [threading.Thread(target=worker, args=(index, payment)) for index, payment in enumerate(payments)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertTrue(all(response.status_code == 201 for response in results), [r.text for r in results])
        numbers = sorted(response.json()["number"] for response in results)
        self.assertEqual(numbers, [f"KHV-{index:04d}" for index in range(1, 7)])
        self.assertEqual(self.product("latte")["stockQuantity"], before - 6)

    def test_out_of_stock_blocks_payment(self):
        response = self.client.post("/api/admin/stock/latte/adjust", json={"mode": "set", "quantity": 1})
        self.assertEqual(response.json()["stockQuantity"], 1)
        too_many = self.client.post("/api/pos/payments", headers=self.kiosk(), json=self.payment_payload(
            "stock-request-01", 184.0, [{"productId": "latte", "quantity": 2}],
        ))
        self.assertEqual(too_many.status_code, 201)
        paid = self.wait_for_status(too_many.json()["id"], "COMPLETED")
        order = self.client.post("/api/orders", headers=self.kiosk(), json=self.order_payload(
            paid, "stock-order-01", 184.0, [{"productId": "latte", "quantity": 2}],
        ))
        self.assertEqual(order.status_code, 409)
        self.assertIn("yeterli stok yok", order.json()["detail"])
        self.assertEqual(self.product("latte")["stockQuantity"], 1)

    def test_declined_51_uses_appendix_message_and_blocks_order(self):
        self.cloud.handlers["Transaction"] = lambda payload: {"responseCode": "51", "message": "", "rrn": "000000123457"}
        started = self.client.post("/api/pos/payments", json=self.payment_payload(), headers=self.kiosk())
        self.assertEqual(started.status_code, 201)
        failed = self.wait_for_status(started.json()["id"], "FAILED")
        self.assertEqual(failed["message"], "Yetersiz bakiye")
        response = self.client.post("/api/orders", json=self.order_payload(started.json()), headers=self.kiosk())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.count("mk_orders"), 0)
        calls_before = len(self.cloud.calls)
        self.client.get(f"/api/pos/payments/{started.json()['id']}", headers=self.kiosk())
        self.assertEqual(len(self.cloud.calls), calls_before)

    def test_offline_device_marks_payment_failed_immediately(self):
        self.cloud.handlers["Transaction"] = lambda payload: httpx.Response(
            404, json={"detail": f"Device {SERIAL_12} not connected"},
        )
        response = self.client.post("/api/pos/payments", json=self.payment_payload("caspro-offline-01"), headers=self.kiosk())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["status"], "FAILED")
        self.assertEqual(response.json()["message"], "Castles POS cihazı çevrimdışı; ödeme gönderilemedi.")
        self.assertEqual(self.payment_record("caspro-offline-01")["status"], "FAILED")
        self.assertEqual(len(self.cloud.of("QueryTransaction")), 0)

    def test_http_timeout_recovers_with_query_transaction(self):
        self.cloud.handlers["Transaction"] = http_timeout
        query_results = iter([
            not_found_query,
            lambda payload: {
                "transactionStatus": "APPROVED", "responseCode": "00", "rrn": "000000999", "approvalCode": "A9",
                "cardNumber": "4111********1111", "dateTime": "2026-10-13T08:01:00Z",
            },
        ])
        self.cloud.handlers["QueryTransaction"] = lambda payload: next(query_results)(payload)
        started = self.client.post("/api/pos/payments", json=self.payment_payload(), headers=self.kiosk())
        self.assertIn(started.json()["status"], {"PROCESSING", "COMPLETED"})
        self.wait_for_status(started.json()["id"], "COMPLETED")
        queries = self.cloud.of("QueryTransaction")
        self.assertEqual(len(queries), 2)
        self.assertEqual(queries[0][0]["DeviceSerialNumber"], SERIAL_12)
        record = self.payment_record("caspro-request-0001")
        self.assertEqual(record["authCode"], "A9")
        self.assertEqual(len(self.cloud.of("CancelTransaction")), 0)

    def test_no_result_sends_cancel_and_fails(self):
        self.cloud.handlers["Transaction"] = http_timeout
        self.cloud.handlers["QueryTransaction"] = not_found_query
        self.cloud.handlers["CancelTransaction"] = lambda payload: {"status": True}
        with patch.dict(os.environ, {"CASPRO_RESULT_TIMEOUT_SECONDS": "1"}):
            started = self.client.post("/api/pos/payments", json=self.payment_payload(), headers=self.kiosk())
            self.assertEqual(started.json()["status"], "PROCESSING")
            failed = self.wait_for_status(started.json()["id"], "FAILED")
        self.assertEqual(failed["message"], "Kart okutma süresi doldu.")
        self.assertEqual(len(self.cloud.of("CancelTransaction")), 1)

    def test_kiosk_cancel_endpoint_marks_cancelled_and_releases_number(self):
        self.cloud.handlers["Transaction"] = delayed(3.0, {"responseCode": "98", "message": "Islem iptal edildi", "rrn": ""})
        self.cloud.handlers["CancelTransaction"] = lambda payload: {"status": True}
        started = self.client.post("/api/pos/payments", json=self.payment_payload(), headers=self.kiosk())
        transaction_id = started.json()["id"]
        self.assertEqual(started.json()["status"], "PROCESSING")

        self.assertEqual(self.client.post(f"/api/pos/payments/{transaction_id}/cancel").status_code, 403)
        self.assertEqual(self.client.post(f"/api/pos/payments/{transaction_id}/cancel", headers=self.kiosk("k02")).status_code, 404)

        cancelled = self.client.post(f"/api/pos/payments/{transaction_id}/cancel", headers=self.kiosk())
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json(), {"status": "CANCELLED", "message": "Ödeme POS cihazında iptal edildi."})
        record = self.payment_record("caspro-request-0001")
        self.assertIsNone(record["reservedOrderNumber"])
        self.assertEqual(record["releasedOrderNumber"], "KHV-0001")
        self.assertEqual(self.client.get(f"/api/pos/payments/{transaction_id}", headers=self.kiosk()).json()["status"], "CANCELLED")
        self.assertEqual(self.client.post(f"/api/pos/payments/{transaction_id}/cancel", headers=self.kiosk()).json()["status"], "CANCELLED")
        self.assertEqual(len(self.cloud.of("CancelTransaction")), 1)
        self.assertEqual(self.client.post("/api/orders", json=self.order_payload(started.json()), headers=self.kiosk()).status_code, 409)
        self.assertEqual(self.client.post("/api/pos/payments/cp-000000000000000000000000/cancel", headers=self.kiosk()).status_code, 404)

    def test_cancel_not_acknowledged_keeps_processing(self):
        self.cloud.handlers["Transaction"] = delayed(3.0, {"responseCode": "98", "message": "Islem iptal edildi", "rrn": ""})
        self.cloud.handlers["CancelTransaction"] = lambda payload: {"status": False}
        started = self.client.post("/api/pos/payments", json=self.payment_payload(), headers=self.kiosk())
        response = self.client.post(f"/api/pos/payments/{started.json()['id']}/cancel", headers=self.kiosk())
        self.assertEqual(response.json()["status"], "PROCESSING")
        self.assertEqual(self.payment_record("caspro-request-0001")["status"], "PROCESSING")

    def test_poll_queries_cloud_when_background_task_is_missing(self):
        self.cloud.handlers["QueryTransaction"] = lambda payload: {
            "transactionStatus": "APPROVED", "responseCode": "00", "rrn": "R1", "approvalCode": "A1",
        }

        def insert(request_id, transaction_id, created_at):
            record = {
                "clientRequestId": request_id, "externalId": transaction_id, "transactionId": transaction_id,
                "status": "PROCESSING", "message": None, "amount": 92.0, "paymentMethod": "card", "cartFingerprint": "x",
                "provider": "CASPRO", "kioskId": "k01", "reservedOrderNumber": None, "terminalSerial": SERIAL_12,
                "createdAt": created_at, "updatedAt": created_at,
            }
            with db.connect(write=True) as conn:
                conn.execute(
                    "INSERT INTO mk_pos_payments (transaction_id, kiosk_id, client_request_id, status, created_at, updated_at, data) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (transaction_id, "k01", request_id, "PROCESSING", created_at, created_at, api.dumps(record)),
                )

        now = datetime.now(timezone.utc)
        insert("restored-01", "cp-0123456789abcdef01234567", (now - timedelta(seconds=15)).isoformat())
        insert("restored-02", "cp-0123456789abcdef01234568", now.isoformat())
        insert("restored-03", "cp-0123456789abcdef01234569", (now - timedelta(seconds=400)).isoformat())

        self.assertEqual(self.client.get("/api/pos/payments/cp-0123456789abcdef01234568", headers=self.kiosk()).json()["status"], "PROCESSING")
        self.assertEqual(len(self.cloud.calls), 0)
        polled = self.client.get("/api/pos/payments/cp-0123456789abcdef01234567", headers=self.kiosk())
        self.assertEqual(polled.json()["status"], "COMPLETED")
        self.assertEqual(len(self.cloud.of("QueryTransaction")), 1)
        self.assertEqual(self.payment_record("restored-01")["authCode"], "A1")
        expired = self.client.get("/api/pos/payments/cp-0123456789abcdef01234569", headers=self.kiosk())
        self.assertEqual(expired.json()["status"], "FAILED")
        self.assertEqual(expired.json()["message"], "Kart okutma süresi doldu.")
        self.assertEqual(len(self.cloud.of("QueryTransaction")), 1)

    def test_amount_mismatch_returns_409_without_cloud_call(self):
        response = self.client.post("/api/pos/payments", json=self.payment_payload(amount=10.0), headers=self.kiosk())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.cloud.calls, [])
        self.assertEqual(self.count("mk_pos_payments"), 0)

    def test_pos_not_assigned_returns_409(self):
        response = self.client.post("/api/pos/payments", json=self.payment_payload("no-pos-request"), headers=self.kiosk("k02"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "Bu kioska POS cihazı atanmamış.")
        self.assertEqual(self.cloud.calls, [])
        config = self.client.get("/api/kiosk/config", headers=self.kiosk("k02")).json()
        self.assertFalse(config["kiosk"]["posConfigured"])

    def test_terminal_serial_from_body_is_ignored(self):
        payload = {**self.payment_payload("ignored-serial-01"), "terminalSerial": "999999999999"}
        response = self.client.post("/api/pos/payments", json=payload, headers=self.kiosk())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.cloud.of("Transaction")[0][0]["DeviceSerialNumber"], SERIAL_12)

    def test_inactive_castles_device_blocks_payment(self):
        self.client.post("/api/admin/castles/devices", json={"serial": SERIAL_16, "name": "Kasa 1", "active": False})
        response = self.client.post("/api/pos/payments", json=self.payment_payload("inactive-device"), headers=self.kiosk())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.cloud.calls, [])

    def test_kiosk_identity_is_required(self):
        self.assertEqual(self.client.post("/api/pos/payments", json=self.payment_payload()).status_code, 403)
        self.assertEqual(self.client.post("/api/pos/payments", json=self.payment_payload(), headers=self.kiosk("k99")).status_code, 403)
        self.client.put("/api/admin/kiosks/k01", json={"active": False})
        self.assertEqual(self.client.post("/api/pos/payments", json=self.payment_payload(), headers=self.kiosk()).status_code, 403)
        self.assertEqual(self.cloud.calls, [])

    def test_caspro_not_configured_returns_503(self):
        with patch.dict(os.environ, {"CASPRO_API_URL": ""}):
            response = self.client.post("/api/pos/payments", json=self.payment_payload(), headers=self.kiosk())
            health = self.client.get("/health").json()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(health["payment"], {"status": "disabled", "provider": "CASPRO"})


if __name__ == "__main__":
    unittest.main()
