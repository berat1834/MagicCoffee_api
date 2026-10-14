"""Kahve otomati (MDB kopru) uctan uca testleri - Magic Coffee.

Gercek kopru ajani (vending-bridge/vending_bridge) Qibixx simulatoruyle TestClient uzerinden API'ye baglanir;
CasPro sahte bulutu (httpx.MockTransport) satis ve iadeleri (Void/Refund) dogrular.
"""
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["DATABASE_URL"] = ""
os.environ["ALLOW_LOCAL_FILE_STORE"] = "true"

import httpx
from fastapi.testclient import TestClient

import app.main as api
from app import caspro, vending

from test_caspro_flow import FakeCasProCloud, approved_sale

ROOT = Path(__file__).resolve().parent.parent
BRIDGE_ROOT = ROOT / "vending-bridge"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from vending_bridge.agent import Agent, ApiClient  # noqa: E402
from vending_bridge.config import BridgeConfig  # noqa: E402
from vending_bridge.detect import Detection  # noqa: E402
from vending_bridge.qibixx import QibixxPort  # noqa: E402
from vending_bridge.simulator import SimulatedQibixx  # noqa: E402

KIOSK_FINGERPRINT = "TEST"
POS_SERIAL = "115253215740"
ENV = {
    "DATABASE_URL": "",
    "ALLOW_LOCAL_FILE_STORE": "true",
    "CASPRO_API_URL": "https://caspro.example.test",
    "CASPRO_API_KEY": "test-caspro-key",
    "CASPRO_TERMINAL_SERIAL": POS_SERIAL,
    "CASPRO_TRANSACTION_TIMEOUT_SECONDS": "5",
    "CASPRO_RESULT_TIMEOUT_SECONDS": "3",
    "PAVO_SOURCE_FINGERPRINT": KIOSK_FINGERPRINT,
    "VEND_REMOTE_TIMEOUT_SECONDS": "3",
    "VEND_CREDIT_TIMEOUT_SECONDS": "3",
    "VEND_PICKUP_TIMEOUT_SECONDS": "1",
    "VEND_REPORT_GRACE_SECONDS": "1",
}
LATTE = [{"productId": "latte", "quantity": 1}]


def option(option_id, name, price=0):
    return {"id": option_id, "name": name, "priceDelta": price, "enabled": True, "available": True}


def catalog_products():
    return [
        {
            "id": "latte", "categoryId": "espresso", "name": "Caffe Latte", "price": 92.0, "active": True,
            "customizable": True, "position": 0, "stockTrackingEnabled": False,
            "customization": {
                "size": {"enabled": True, "title": "Boyut", "required": False, "minSelect": 0, "maxSelect": 1,
                         "options": [option("small", "Küçük"), option("medium", "Orta", 10), option("large", "Büyük", 35)]},
                "milk": {"enabled": True, "title": "Süt", "required": False, "minSelect": 0, "maxSelect": 1,
                         "options": [option("whole-milk", "Tam yağlı süt"), option("oat", "Yulaf sütü", 18)]},
                "syrup": {"enabled": False, "title": "Şurup", "required": False, "minSelect": 0, "maxSelect": 1, "options": []},
            },
        },
        {"id": "americano", "categoryId": "espresso", "name": "Americano", "price": 76.0, "active": True,
         "customizable": False, "position": 1, "customization": {}},
        {"id": "cookie", "categoryId": "desserts", "name": "Cookie", "price": 45.0, "active": True,
         "customizable": False, "position": 2, "customization": {}},
    ]


def declined_reversal(payload):
    if payload.get("transactionType") in ("Void", "Refund", "Refundunmatched"):
        return {"responseCode": "05", "message": "IADE REDDEDILDI", "rrn": "000000999999", "extTransactionId": payload["extTransactionId"]}
    return approved_sale(payload)


class VendingTestCase(unittest.TestCase):
    def setUp(self):
        api.categories[:] = [
            {"id": "espresso", "name": "Espresso", "eyebrow": "", "position": 0, "active": True},
            {"id": "desserts", "name": "Tatlılar", "eyebrow": "", "position": 1, "active": True},
        ]
        api.products[:] = catalog_products()
        api.orders.clear()
        api.stock_movements.clear()
        api.pos_payments.clear()
        api.order_requests.clear()
        api.translations.clear()
        api.castles_devices.clear()
        api.PAYMENT_ATTEMPTS.clear()
        api.CASPRO_TASKS.clear()
        api.order_numbers = iter(range(701, 800))
        vending.reset_state()

        self.cloud = FakeCasProCloud()
        self.cloud.handlers["Transaction"] = approved_sale

        def client_factory(timeout):
            return httpx.AsyncClient(transport=httpx.MockTransport(self.cloud), timeout=timeout)

        self.patches = [
            patch.dict(os.environ, ENV),
            patch.object(caspro, "_client_factory", new=client_factory),
            patch.object(api, "save_state", new=lambda: None),
            patch.object(api, "CASPRO_OFFLINE_GRACE_SECONDS", 0.2),
            patch.object(vending, "SWEEP_INTERVAL_SECONDS", 0.1),
            patch.object(vending, "LONG_POLL_STEP_SECONDS", 0.02),
            patch.object(vending, "CASPRO_REFUND_QUERY_INTERVAL_SECONDS", 0.05),
        ]
        for item in self.patches:
            item.start()
        self.agents = []
        self.client = TestClient(api.app, headers={"X-MagicCoffee-Kiosk-Fingerprint": KIOSK_FINGERPRINT})
        self.client.__enter__()

    def tearDown(self):
        for agent, _, _ in self.agents:
            agent.stop()
        for _, thread, port in self.agents:
            thread.join(5)
            port.close()
        self.client.__exit__(None, None, None)
        for item in reversed(self.patches):
            item.stop()
        vending.reset_state()
        api.castles_devices.clear()
        api.CASPRO_TASKS.clear()

    # ---- yardimcilar ----
    def create_machine(self, mode="REMOTE", mappings=(("latte", "", 7),), **extra):
        response = self.client.post("/api/admin/machines", json={"name": "Lobi Otomati", "mode": mode, **extra})
        self.assertEqual(response.status_code, 201, response.text)
        machine_id = response.json()["id"]
        items = [{"productId": pid, "optionKey": key, "selectionNo": sel} for pid, key, sel in mappings]
        mapped = self.client.put(f"/api/admin/machines/{machine_id}/products", json={"items": items})
        self.assertEqual(mapped.status_code, 200, mapped.text)
        token = self.client.post(f"/api/admin/machines/{machine_id}/token").json()["token"]
        return machine_id, token

    def start_bridge(self, token, sim):
        config = BridgeConfig(api_url="", machine_token=token, poll_wait=1, heartbeat_interval=0, ready_timeout=2)
        port = QibixxPort(sim)
        agent = Agent(ApiClient("", token, client=self.client), port, config,
                      detector=lambda _: Detection(mode=sim.mode, feature_level=3 if sim.mode == "REMOTE" else 1))
        agent.detect()
        thread = threading.Thread(target=agent.run_forever, daemon=True)
        thread.start()
        self.agents.append((agent, thread, port))
        return agent

    @staticmethod
    def agent_headers(token):
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def payment_payload(request_id, amount, lines):
        return {"clientRequestId": request_id, "paymentMethod": "card", "amount": amount, "lines": lines}

    def wait_payment(self, transaction_id, expected="COMPLETED", timeout=5.0):
        deadline = time.monotonic() + timeout
        body = None
        while time.monotonic() < deadline:
            body = self.client.get(f"/api/pos/payments/{transaction_id}").json()
            if body["status"] == expected:
                return body
            time.sleep(0.05)
        self.fail(f"Odeme {expected} olmadi: {body}")

    def buy(self, request_id, lines=LATTE, total=92.0):
        started = self.client.post("/api/pos/payments", json=self.payment_payload(f"{request_id}-pay", total, lines))
        self.assertEqual(started.status_code, 201, started.text)
        paid = self.wait_payment(started.json()["id"])
        order = self.client.post("/api/orders", json={
            "clientRequestId": f"{request_id}-order", "fulfillment": "restaurant", "paymentMethod": "card",
            "total": total, "paymentReference": paid["paymentReference"], "posTransactionId": paid["id"],
            "language": "tr", "lines": lines,
        })
        self.assertEqual(order.status_code, 201, order.text)
        return order.json()

    def vend(self, number):
        response = self.client.get(f"/api/orders/{number}/vend")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait_vend(self, number, expected, timeout=10.0):
        deadline = time.monotonic() + timeout
        body = None
        while time.monotonic() < deadline:
            body = self.vend(number)
            if body["vendStatus"] == expected:
                return body
            time.sleep(0.05)
        self.fail(f"{number} vend durumu {expected} olmadi; son: {body}")

    def reversals(self):
        return [call[0] for call in self.cloud.of("Transaction") if call[0]["transactionType"] != "Sale"]


class VendingFlowTests(VendingTestCase):
    def test_remote_success_dispenses_exact_selection(self):
        machine_id, token = self.create_machine()
        sim = SimulatedQibixx(mode="REMOTE")
        self.start_bridge(token, sim)
        order = self.buy("remote-ok")
        self.assertEqual(order["vendStatus"], "IN_PROGRESS")
        self.assertEqual(order["vend"], {"machineId": machine_id, "jobCount": 1, "rejectedCount": 0})
        self.assertEqual(order["lines"][0]["machineOptionKey"], "")
        body = self.wait_vend(order["number"], "ALL_DONE")
        job = body["jobs"][0]
        self.assertEqual((job["status"], job["selectionNo"], job["productName"]), ("SUCCESS", 7, "Caffe Latte"))
        self.assertEqual(job["message"], "Afiyet olsun!")
        self.assertEqual((body["mode"], body["machineOnline"], body["done"]), ("REMOTE", True, True))
        selection = [cmd for cmd in sim.commands if cmd.startswith("C,SEL,")][0]
        self.assertRegex(selection, r"^C,SEL,92\.00,[0-9A-F]{8},7$")
        self.assertIn("C,VEND,92.00", sim.commands)
        self.assertEqual(self.reversals(), [])
        # Odeme tek POS'a (115253215740) gitti.
        sale = self.cloud.of("Transaction")[0][0]
        self.assertEqual((sale["transactionType"], sale["DeviceSerialNumber"]), ("Sale", POS_SERIAL))
        self.assertEqual(self.client.get(f"/api/orders/{order['number']}").json()["vendStatus"], "ALL_DONE")
        machine = self.client.get("/api/admin/machines").json()[0]
        self.assertEqual((machine["agentVersion"], machine["detectedMode"], machine["online"]), ("1.0.0", "REMOTE", True))

    def test_credit_wrong_button_is_shown_to_kiosk(self):
        _, token = self.create_machine(mode="CREDIT")
        order = self.buy("credit-wrong")
        headers = self.agent_headers(token)
        job = self.client.get("/api/machine/agent/jobs?wait=2", headers=headers).json()["job"]
        self.assertEqual(job["mode"], "CREDIT")
        self.client.post(f"/api/machine/agent/jobs/{job['id']}/events", json={"status": "WAITING_SELECTION", "detail": "C,START"}, headers=headers)
        waiting = self.vend(order["number"])["jobs"][0]
        self.assertEqual((waiting["status"], waiting["wrongSelection"]), ("WAITING_SELECTION", False))
        self.assertEqual(waiting["message"], "Lütfen makinede 7 numaralı ürüne basın.")
        self.client.post(f"/api/machine/agent/jobs/{job['id']}/events", headers=headers, json={
            "status": "WAITING_SELECTION", "detail": {"message": "Yanlis urun", "pressedSelectionNo": 3},
        })
        wrong = self.vend(order["number"])["jobs"][0]
        self.assertEqual((wrong["wrongSelection"], wrong["pressedSelectionNo"], wrong["selectionNo"]), (True, 3, 7))
        self.client.post(f"/api/machine/agent/jobs/{job['id']}/events", json={"status": "DISPENSING"}, headers=headers)
        self.client.post(f"/api/machine/agent/jobs/{job['id']}/events", json={"status": "SUCCESS"}, headers=headers)
        done = self.vend(order["number"])
        self.assertEqual((done["vendStatus"], done["jobs"][0]["wrongSelection"]), ("ALL_DONE", False))

    def test_credit_bridge_success(self):
        _, token = self.create_machine(mode="CREDIT")
        sim = SimulatedQibixx(mode="CREDIT", customer_selection=7, wrong_selection=3, outcomes=["wrong_button"])
        self.start_bridge(token, sim)
        order = self.buy("credit-ok")
        body = self.wait_vend(order["number"], "ALL_DONE")
        self.assertEqual(body["mode"], "CREDIT")
        self.assertIn("C,START,92.00", sim.commands)
        self.assertEqual(self.reversals(), [])

    def test_machine_failure_voids_whole_order(self):
        _, token = self.create_machine()
        self.start_bridge(token, SimulatedQibixx(mode="REMOTE", outcomes=["fail"]))
        order = self.buy("remote-fail")
        body = self.wait_vend(order["number"], "REFUNDED")
        job = body["jobs"][0]
        self.assertEqual(job["status"], "REFUNDED")
        self.assertEqual(job["message"], "Ürün hazırlanamadı; 92,00 TL kartınıza iade edildi.")
        self.assertEqual((job["refund"]["type"], job["refund"]["needsCard"]), ("Void", False))
        self.assertEqual(body["refundedAmount"], 92.0)
        (void,) = self.reversals()
        self.assertEqual((void["transactionType"], void["amount"], void["originalBankReferenceCode"]), ("Void", 9200, "BR-0001"))
        self.assertEqual(void["DeviceSerialNumber"], POS_SERIAL)
        self.assertEqual(void["printLine"][0], "MAGIC COFFEE")
        self.assertIn("Otomat iadesi", void["printLine"])
        stored = api.orders[order["number"]]
        self.assertEqual((stored["vendStatus"], stored["vendRefunds"][0]["ok"]), ("REFUNDED", True))

    def test_partial_failure_refunds_only_failed_unit(self):
        _, token = self.create_machine()
        self.start_bridge(token, SimulatedQibixx(mode="REMOTE", outcomes=["success", "fail"]))
        order = self.buy("partial", [{"productId": "latte", "quantity": 2}], 184.0)
        self.assertEqual(order["vend"]["jobCount"], 2)
        body = self.wait_vend(order["number"], "PARTIAL_REFUNDED")
        self.assertEqual([job["status"] for job in body["jobs"]], ["SUCCESS", "REFUNDED"])
        (refund,) = self.reversals()
        self.assertEqual((refund["transactionType"], refund["amount"], refund["originalBankReferenceCode"]), ("Refund", 9200, "BR-0001"))
        self.assertTrue(body["jobs"][1]["refund"]["needsCard"])

    def test_pickup_timeout_expires_and_refunds(self):
        self.create_machine()  # kopru calismiyor
        order = self.buy("pickup-timeout")
        body = self.wait_vend(order["number"], "REFUNDED")
        jobs = self.client.get("/api/admin/vend-jobs?status=REFUNDED").json()
        self.assertEqual(jobs[0]["failureStatus"], "EXPIRED")
        self.assertFalse(body["machineOnline"])
        self.assertEqual(len(self.reversals()), 1)

    def test_inflight_timeout_and_late_success(self):
        _, token = self.create_machine()
        headers = self.agent_headers(token)
        order = self.buy("inflight-timeout")
        polled = self.client.get("/api/machine/agent/jobs?wait=2", headers=headers)
        self.assertEqual(polled.status_code, 200, polled.text)
        job = polled.json()["job"]
        self.assertEqual((job["selectionNo"], job["amountText"], job["mode"], job["timeoutSec"]), (7, "92.00", "REMOTE", 3))
        self.assertRegex(job["paymentId"], r"^[0-9A-F]{8}$")
        # Is makinedeyken ikinci is verilmez (bir anda tek is).
        self.buy("inflight-second")
        self.assertEqual(self.client.get("/api/machine/agent/jobs?wait=0", headers=headers).status_code, 204)
        self.client.post(f"/api/machine/agent/jobs/{job['id']}/events", json={"status": "DISPENSING"}, headers=headers)
        self.wait_vend(order["number"], "REFUNDED", timeout=12)
        late = self.client.post(f"/api/machine/agent/jobs/{job['id']}/events", json={"status": "SUCCESS"}, headers=headers)
        self.assertEqual(late.json()["accepted"], False)
        record = self.client.get(f"/api/admin/vend-jobs?order={order['number']}").json()[0]
        self.assertEqual((record["status"], record["failureStatus"], record["lateSuccess"]), ("REFUNDED", "EXPIRED", True))

    def test_refund_failure_needs_attention_and_retry(self):
        _, token = self.create_machine()
        self.cloud.handlers["Transaction"] = declined_reversal
        self.start_bridge(token, SimulatedQibixx(mode="REMOTE", outcomes=["fail"]))
        order = self.buy("refund-fail")
        body = self.wait_vend(order["number"], "NEEDS_ATTENTION")
        job = body["jobs"][0]
        self.assertEqual(job["status"], "REFUND_FAILED")
        self.assertEqual(job["message"], "İade yapılamadı; lütfen görevliye başvurun.")
        self.assertTrue(body["needsManualRefund"])
        self.assertEqual(self.client.get("/api/admin/dashboard").json()["vending"]["attentionCount"], 1)
        self.assertEqual(self.client.get("/api/admin/machines").json()[0]["attentionCount"], 1)
        failed = self.client.get("/api/admin/vend-jobs?status=REFUND_FAILED").json()
        self.assertEqual(failed[0]["refund"]["message"], "IADE REDDEDILDI")
        self.cloud.handlers["Transaction"] = approved_sale
        retried = self.client.post(f"/api/admin/vend-jobs/{job['id']}/retry-refund")
        self.assertEqual(retried.status_code, 200, retried.text)
        self.wait_vend(order["number"], "REFUNDED")
        self.assertEqual(len(self.reversals()), 2)
        self.assertEqual(self.client.get("/api/admin/dashboard").json()["vending"]["attentionCount"], 0)
        self.assertEqual(self.client.post(f"/api/admin/vend-jobs/{job['id']}/retry-refund").status_code, 409)

    def test_bridge_restart_fails_inflight_job_and_refunds(self):
        _, token = self.create_machine()
        order = self.buy("restart")
        job = self.client.get("/api/machine/agent/jobs?wait=2", headers=self.agent_headers(token)).json()["job"]
        hello = self.client.post("/api/machine/agent/hello", json={"agentVersion": "1.0.0", "detectedMode": "REMOTE"},
                                 headers=self.agent_headers(token))
        self.assertEqual(hello.json()["mode"], "REMOTE")
        self.wait_vend(order["number"], "REFUNDED")
        self.assertEqual(self.client.get(f"/api/admin/vend-jobs?order={order['number']}").json()[0]["id"], job["id"])

    def test_mapping_removed_after_payment_fails_job_and_refunds(self):
        machine_id, _ = self.create_machine(mappings=(("latte", "", 7), ("americano", "", 3)))
        started = self.client.post("/api/pos/payments", json=self.payment_payload("removed-pay", 76.0, [{"productId": "americano", "quantity": 1}]))
        paid = self.wait_payment(started.json()["id"])
        self.client.put(f"/api/admin/machines/{machine_id}/products", json={"items": [{"productId": "latte", "optionKey": "", "selectionNo": 7}]})
        order = self.client.post("/api/orders", json={
            "clientRequestId": "removed-order", "fulfillment": "restaurant", "paymentMethod": "card", "total": 76.0,
            "paymentReference": paid["paymentReference"], "posTransactionId": paid["id"], "language": "tr",
            "lines": [{"productId": "americano", "quantity": 1}],
        })
        self.assertEqual(order.status_code, 201, order.text)
        self.assertEqual(order.json()["vend"]["rejectedCount"], 1)
        self.wait_vend(order.json()["number"], "REFUNDED")


class VendingRulesTests(VendingTestCase):
    def test_without_machine_catalog_and_orders_unchanged(self):
        catalog = self.client.get("/api/catalog").json()
        self.assertEqual(sorted(item["id"] for item in catalog["products"]), ["americano", "cookie", "latte"])
        self.assertTrue(all("machineSelection" not in item for item in catalog["products"]))
        self.assertNotIn("machineOnly", catalog)
        order = self.buy("plain", [{"productId": "cookie", "quantity": 1}], 45.0)
        self.assertNotIn("vendStatus", order)
        self.assertNotIn("vend", order)
        body = self.vend(order["number"])
        self.assertEqual((body["jobs"], body["vendStatus"], body["done"]), ([], None, True))
        self.assertEqual(vending.JOBS, {})
        self.assertEqual(self.client.get("/api/orders/MC-999/vend").status_code, 404)

    def test_machine_without_mappings_or_inactive_does_not_change_kiosk(self):
        created = self.client.post("/api/admin/machines", json={"name": "Kurulumda"})
        self.assertEqual(created.status_code, 201)
        self.assertNotIn("machineOnly", self.client.get("/api/catalog").json())
        self.client.put("/api/admin/machines/m01/products", json=[{"productId": "latte", "optionKey": "", "selectionNo": 7}])
        self.assertTrue(self.client.get("/api/catalog").json()["machineOnly"])
        self.client.put("/api/admin/machines/m01", json={"active": False})
        catalog = self.client.get("/api/catalog").json()
        self.assertNotIn("machineOnly", catalog)
        self.assertEqual(len(catalog["products"]), 3)

    def test_machine_kiosk_sells_only_mapped_coffee(self):
        self.create_machine(mappings=(("latte", "", 7), ("latte", "size=large", 8), ("americano", "", 3)))
        catalog = self.client.get("/api/catalog").json()
        self.assertTrue(catalog["machineOnly"])
        self.assertEqual(catalog["machine"]["mode"], "REMOTE")
        self.assertEqual([item["id"] for item in catalog["categories"]], ["espresso"])
        self.assertEqual(catalog["categories"][0]["productCount"], 2)
        self.assertEqual(sorted(item["id"] for item in catalog["products"]), ["americano", "latte"])
        latte = next(item for item in catalog["products"] if item["id"] == "latte")
        self.assertEqual(latte["machineSelection"], 7)
        self.assertEqual(latte["machineVariants"], [
            {"optionKey": "", "selectionNo": 7, "price": None}, {"optionKey": "size=large", "selectionNo": 8, "price": None},
        ])
        self.assertEqual([item["id"] for item in latte["customization"]["size"]["options"]], ["large"])
        self.assertFalse(latte["customization"]["milk"]["enabled"])  # eslenmemis secenekler gizlenir

        unmapped = self.client.post("/api/pos/payments", json=self.payment_payload("unmapped-product", 45.0, [{"productId": "cookie", "quantity": 1}]))
        self.assertEqual((unmapped.status_code, unmapped.json()["detail"]), (409, vending.UNMAPPED_PRODUCT_MESSAGE))
        medium = [{"productId": "latte", "quantity": 1, "selection": {"choices": {"size": ["medium"]}}}]
        variant = self.client.post("/api/pos/payments", json=self.payment_payload("unmapped-variant", 102.0, medium))
        self.assertEqual((variant.status_code, variant.json()["detail"]), (409, vending.UNMAPPED_VARIANT_MESSAGE))
        mismatch = self.client.post("/api/pos/payments", json=self.payment_payload("key-mismatch", 92.0, [
            {"productId": "latte", "quantity": 1, "machineOptionKey": "size=large"},
        ]))
        self.assertEqual((mismatch.status_code, mismatch.json()["detail"]), (409, vending.KEY_MISMATCH_MESSAGE))
        self.assertEqual(self.cloud.of("Transaction"), [])  # hic odeme alinmadi

    def test_variant_selection_is_sent_to_machine(self):
        _, token = self.create_machine(mappings=(("latte", "", 7), ("latte", "size=large", 8)))
        sim = SimulatedQibixx(mode="REMOTE")
        self.start_bridge(token, sim)
        large = [{"productId": "latte", "quantity": 1, "machineOptionKey": "size=large", "selection": {"choices": {"size": ["large"]}}}]
        order = self.buy("variant", large, 92 + 35)
        body = self.wait_vend(order["number"], "ALL_DONE")
        self.assertEqual((body["jobs"][0]["selectionNo"], body["jobs"][0]["optionKey"], body["jobs"][0]["variantLabel"]), (8, "size=large", "Büyük"))
        self.assertTrue(any(cmd.startswith("C,SEL,127.00,") and cmd.endswith(",8") for cmd in sim.commands))

    def test_mapping_price_is_the_sale_price(self):
        machine_id, token = self.create_machine()
        self.client.put(f"/api/admin/machines/{machine_id}/products", json={"items": [
            {"productId": "latte", "optionKey": "", "selectionNo": 7, "price": 90.0},
        ]})
        catalog = self.client.get("/api/catalog").json()
        self.assertEqual(catalog["products"][0]["machineVariants"], [{"optionKey": "", "selectionNo": 7, "price": 90.0}])
        stale = self.client.post("/api/pos/payments", json=self.payment_payload("stale-price", 92.0, LATTE))
        self.assertEqual(stale.status_code, 409)
        sim = SimulatedQibixx(mode="REMOTE", outcomes=["fail"])
        self.start_bridge(token, sim)
        order = self.buy("mapping-price", total=90.0)
        self.assertEqual(order["lines"][0]["unitPrice"], 90.0)
        body = self.wait_vend(order["number"], "REFUNDED")
        self.assertTrue(any(cmd.startswith("C,SEL,90.00,") for cmd in sim.commands))
        self.assertEqual(body["jobs"][0]["amount"], 90.0)
        self.assertEqual(self.reversals()[0]["amount"], 9000)

    def test_token_auth_and_long_poll(self):
        machine_id, token = self.create_machine()
        self.assertEqual(self.client.get("/api/machine/agent/jobs?wait=0").status_code, 401)
        self.assertEqual(self.client.get("/api/machine/agent/jobs?wait=0", headers=self.agent_headers("mkv_wrong")).status_code, 401)
        started = time.monotonic()
        empty = self.client.get("/api/machine/agent/jobs?wait=1", headers=self.agent_headers(token))
        self.assertEqual(empty.status_code, 204)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)
        beat = self.client.post("/api/machine/agent/heartbeat", json={"status": "IDLE", "vmcStatus": "ENABLED"}, headers=self.agent_headers(token))
        self.assertEqual(beat.status_code, 200)
        machine = self.client.get("/api/admin/machines").json()[0]
        self.assertEqual((machine["vmcStatus"], machine["agentStatus"], machine["hasToken"]), ("ENABLED", "IDLE", True))
        self.assertNotIn(token, str(machine))
        self.assertEqual(vending.MACHINES[machine_id]["token_hash"], vending.token_hash(token))
        self.assertNotIn(token, str(vending.export_state()))
        new_token = self.client.post(f"/api/admin/machines/{machine_id}/token").json()["token"]
        self.assertEqual(self.client.get("/api/machine/agent/jobs?wait=0", headers=self.agent_headers(token)).status_code, 401)
        self.client.put(f"/api/admin/machines/{machine_id}", json={"active": False})
        self.assertEqual(self.client.get("/api/machine/agent/jobs?wait=0", headers=self.agent_headers(new_token)).status_code, 403)

    def test_long_poll_wakes_up_when_job_is_queued(self):
        _, token = self.create_machine()
        result = {}

        def poll():
            result["response"] = self.client.get("/api/machine/agent/jobs?wait=5", headers=self.agent_headers(token))

        thread = threading.Thread(target=poll)
        thread.start()
        time.sleep(0.3)
        order = self.buy("wake")
        thread.join(8)
        self.assertEqual(result["response"].status_code, 200)
        self.assertEqual(result["response"].json()["job"]["orderNumber"], order["number"])

    def test_admin_machine_crud_and_options(self):
        response = self.client.post("/api/admin/machines", json={"name": "Lobi Otomati"})
        self.assertEqual(response.status_code, 201)
        machine = response.json()
        self.assertEqual((machine["id"], machine["mode"], machine["effectiveMode"], machine["hasToken"]), ("m01", "AUTO", "REMOTE", False))
        self.assertEqual((machine["kioskId"], machine["kioskName"]), (vending.KIOSK_ID, vending.KIOSK_NAME))
        # Kioskta tek aktif otomat kullanilir.
        self.assertEqual(self.client.post("/api/admin/machines", json={"name": "Ikinci"}).status_code, 409)
        second = self.client.post("/api/admin/machines", json={"name": "Ikinci", "active": False}).json()
        self.assertEqual(second["id"], "m02")
        self.assertEqual(self.client.put("/api/admin/machines/m02", json={"active": True}).status_code, 409)
        credit = self.client.put("/api/admin/machines/m01", json={"mode": "CREDIT"}).json()
        self.assertEqual(credit["effectiveMode"], "CREDIT")
        self.assertIsNotNone(credit["modeWarning"])

        options = self.client.get("/api/admin/machines/m01/products/options?productId=latte").json()["options"]
        keys = [item["optionKey"] for item in options]
        self.assertEqual(len(keys), 12)  # milk (yok + 2) x size (yok + 3)
        self.assertEqual(keys[0], "")
        self.assertIn("milk=oat|size=large", keys)
        large = next(item for item in options if item["optionKey"] == "size=large")
        self.assertEqual((large["label"], large["price"]), ("Büyük", 127.0))
        self.client.put("/api/admin/machines/m01", json={"vendingOptionSteps": ["size"]})
        limited = self.client.get("/api/admin/machines/m01/products/options?productId=latte").json()["options"]
        self.assertEqual([item["optionKey"] for item in limited], ["", "size=large", "size=medium", "size=small"])
        bad = self.client.put("/api/admin/machines/m01/products", json={"products": [{"productId": "latte", "optionKey": "size=huge", "selectionNo": 1}]})
        self.assertEqual(bad.status_code, 400)
        old_format = self.client.put("/api/admin/machines/m01/products", json=[{"productId": "latte", "optionKey": "size:large", "selectionNo": 1}])
        self.assertEqual(old_format.status_code, 400)
        canonical = self.client.put("/api/admin/machines/m01/products", json=[
            {"productId": "latte", "optionKey": "size=large|milk=oat", "selectionNo": 9},
        ])
        self.assertEqual(canonical.status_code, 200, canonical.text)
        self.assertEqual(canonical.json()[0]["optionKey"], "milk=oat|size=large")
        duplicate = self.client.put("/api/admin/machines/m01/products", json={"items": [
            {"productId": "latte", "optionKey": "", "selectionNo": 1}, {"productId": "latte", "optionKey": "", "selectionNo": 2},
        ]})
        self.assertEqual(duplicate.status_code, 400)
        saved = self.client.put("/api/admin/machines/m01/products", json={"products": [
            {"productId": "latte", "optionKey": "size=large", "selectionNo": 8, "price": 120},
            {"productId": "latte", "optionKey": "", "selectionNo": 7},
        ]}).json()
        self.assertEqual([(item["optionKey"], item["selectionNo"], item["price"]) for item in saved], [("", 7, None), ("size=large", 8, 120.0)])
        self.assertEqual(saved[1]["optionLabel"], "Büyük")
        listed = self.client.get("/api/admin/machines/m01/products/options?productId=latte").json()
        self.assertTrue(next(item for item in listed["options"] if item["optionKey"] == "size=large")["mapped"])
        self.assertEqual(self.client.get("/api/admin/machines/m01/products").json()[0]["productName"], "Caffe Latte")
        custom = self.client.post("/api/admin/machines", json={"id": "lobi-1", "name": "Lobi 1", "active": False})
        self.assertEqual((custom.status_code, custom.json()["id"]), (201, "lobi-1"))
        self.assertEqual(self.client.post("/api/admin/machines", json={"id": "lobi-1", "name": "Lobi 1", "active": False}).status_code, 409)
        self.assertEqual(self.client.delete("/api/admin/machines/lobi-1").status_code, 204)
        self.assertEqual(self.client.delete("/api/admin/machines/m02").status_code, 204)
        self.assertEqual(self.client.delete("/api/admin/machines/m02").status_code, 404)
        self.assertEqual([item["id"] for item in self.client.get("/api/admin/machines").json()], ["m01"])

    def test_delete_with_pending_job_is_refused(self):
        machine_id, _ = self.create_machine()
        self.buy("pending-delete")
        self.assertEqual(self.client.delete(f"/api/admin/machines/{machine_id}").status_code, 409)

    def test_state_round_trip_for_local_store(self):
        machine_id, _ = self.create_machine()
        self.buy("round-trip")
        exported = vending.export_state()
        jobs = len(vending.JOBS)
        vending.reset_state()
        vending.load_from_data(exported)
        self.assertEqual(set(vending.MACHINES), {machine_id})
        self.assertEqual(len(vending.JOBS), jobs)
        self.assertEqual(vending.machine_mappings(machine_id)["latte"][""]["selection_no"], 7)


class FakeConn:
    def __init__(self, rows=None):
        self.statements = []
        self.rows = rows or {}

    def execute(self, query, params=None):
        self.statements.append((" ".join(query.split()), params))
        conn = self

        class Result:
            def fetchone(self_inner):
                return {"serial": params[0]} if "RETURNING serial" in query else None

            def fetchall(self_inner):
                for table, rows in conn.rows.items():
                    if f"FROM {table}" in query:
                        return rows
                return []

        return Result()


class DeploymentAndStartupTests(unittest.TestCase):
    def test_default_castles_device_is_registered_idempotently(self):
        with patch.dict(os.environ, ENV):
            api.castles_devices.clear()
            self.assertTrue(api.ensure_default_castles_device())
            self.assertFalse(api.ensure_default_castles_device())
            self.assertEqual([(item["serial"], item["name"], item["active"]) for item in api.castles_devices],
                             [(POS_SERIAL, "Magic Coffee POS", True)])
            conn = FakeConn()
            with patch.object(api, "DATABASE_URL", "postgresql://u@h/db"):
                self.assertTrue(api.ensure_default_castles_device(conn))
            (query, params), = conn.statements
            self.assertIn("INSERT INTO castles_devices", query)
            self.assertIn("ON CONFLICT (serial) DO NOTHING", query)
            self.assertEqual(params, (POS_SERIAL, "Magic Coffee POS"))
        api.castles_devices.clear()

    def test_database_schema_is_idempotent_and_loaded(self):
        conn = FakeConn(rows={
            "coffee_machines": [{"id": "m01", "name": "Otomat", "kiosk_id": "magiccoffee", "mode": "AUTO", "active": True,
                                 "token_hash": "x", "vmc_info": {"vmcStatus": "ENABLED"}, "option_steps": None,
                                 "created_at": "2026-10-14T00:00:00+00:00", "updated_at": "2026-10-14T00:00:00+00:00"}],
            "coffee_machine_products": [{"machine_id": "m01", "product_id": "latte", "option_key": "", "selection_no": 7,
                                         "price": None, "active": True}],
        })
        try:
            vending.load_from_database(conn)
            creates = [query for query, _ in conn.statements if query.startswith("CREATE")]
            self.assertTrue(all("IF NOT EXISTS" in query for query in creates))
            self.assertTrue(any("coffee_vend_jobs" in query for query in creates))
            self.assertEqual(vending.MACHINES["m01"]["vmc_info"], {"vmcStatus": "ENABLED"})
            self.assertEqual(vending.machine_mappings("m01")["latte"][""]["selection_no"], 7)
        finally:
            vending.reset_state()

    def test_health_reports_the_single_pos(self):
        with patch.dict(os.environ, ENV):
            body = TestClient(api.app).get("/health").json()
        self.assertEqual(body["payment"]["defaultSerial"], POS_SERIAL)

    def test_deployment_points_to_single_pos_and_exposes_agent(self):
        deployment = (ROOT / "k8s" / "prod" / "deployment.yaml").read_text(encoding="utf-8")
        self.assertIn('- name: CASPRO_TERMINAL_SERIAL\n              value: "115253215740"', deployment.replace("\r\n", "\n"))
        ingress = (ROOT / "k8s" / "prod" / "ingress.yaml").read_text(encoding="utf-8")
        self.assertIn("path: /api/machine/agent", ingress)
        self.assertNotIn("path: /api/admin", ingress)


if __name__ == "__main__":
    unittest.main()
