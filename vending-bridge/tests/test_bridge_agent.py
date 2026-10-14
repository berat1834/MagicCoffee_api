"""Ajan: sahte API (httpx.MockTransport) + simulator ile hello / long-poll / olay / yeniden deneme."""
import json
import tempfile
import threading
import time
from pathlib import Path

import httpx

from vending_bridge.agent import Agent, ApiClient, ApiError
from vending_bridge.config import BridgeConfig, load_config
from vending_bridge.detect import Detection
from vending_bridge.qibixx import QibixxPort
from vending_bridge.simulator import SimulatedQibixx

TOKEN = "mkv_test-token"


class FakeApi:
    def __init__(self, jobs=None, mode="REMOTE"):
        self.jobs = list(jobs or [])
        self.mode = mode
        self.events = []
        self.hellos = []
        self.heartbeats = []
        self.fail_events = 0
        self.lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "yetkisiz"})
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        with self.lock:
            if path == "/api/machine/agent/hello":
                self.hellos.append(body)
                return httpx.Response(200, json={"machineId": "m01", "mode": self.mode, "pollIntervalMs": 1000})
            if path == "/api/machine/agent/jobs":
                if self.jobs:
                    return httpx.Response(200, json={"job": self.jobs.pop(0)})
                return httpx.Response(204)
            if path.startswith("/api/machine/agent/jobs/") and path.endswith("/events"):
                if body.get("status") in ("SUCCESS", "FAILED") and self.fail_events:
                    self.fail_events -= 1
                    return httpx.Response(503, json={"detail": "mesgul"})
                self.events.append((path.split("/")[-2], body["status"], body.get("detail")))
                return httpx.Response(200, json={"accepted": True, "status": body["status"]})
            if path == "/api/machine/agent/heartbeat":
                self.heartbeats.append(body)
                return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)


def make_agent(fake, sim, **config):
    client = httpx.Client(transport=httpx.MockTransport(fake), base_url="https://api.test")
    cfg = BridgeConfig(api_url="https://api.test", machine_token=TOKEN, poll_wait=0, heartbeat_interval=0, ready_timeout=2, **config)
    port = QibixxPort(sim)
    agent = Agent(ApiClient("https://api.test", TOKEN, client=client), port, cfg, sleep=lambda _: None,
                  detector=lambda p: Detection(mode=sim.mode, feature_level=3 if sim.mode == "REMOTE" else 1))
    return agent, port


def job(job_id="vj-1", selection=7, mode="REMOTE", timeout=3):
    return {"id": job_id, "selectionNo": selection, "amount": 92.0, "amountText": "92.00", "mode": mode,
            "timeoutSec": timeout, "paymentId": "0A0B0C0D"}


def test_config_from_toml_and_env():
    path = Path(tempfile.mkdtemp(prefix="vb-test-")) / "config.toml"
    path.write_text('[bridge]\napi_url = "https://x.test/"\nserial_port = "/dev/ttyACM0"\nmode = "credit"\npoll_wait = 10\n', encoding="utf-8")
    config = load_config(str(path), env={"MACHINE_TOKEN": "abc", "SIMULATOR": "1"})
    assert config.api_url == "https://x.test"
    assert config.serial_port == "/dev/ttyACM0"
    assert config.mode == "CREDIT" and config.forced_mode == "CREDIT"
    assert config.poll_wait == 10
    assert config.machine_token == "abc"
    assert config.simulator is True


def test_hello_then_remote_job_success():
    fake = FakeApi(jobs=[job()])
    agent, port = make_agent(fake, SimulatedQibixx(mode="REMOTE"))
    try:
        agent.detect()
        agent.hello()
        assert agent.run_once() is True
        assert agent.run_once() is False
    finally:
        port.close()
    assert fake.hellos[0]["detectedMode"] == "REMOTE"
    assert fake.hellos[0]["vmcInfo"]["featureLevel"] == 3
    assert [status for _, status, _ in fake.events] == ["SENT", "DISPENSING", "SUCCESS"]


def test_credit_job_reports_waiting_selection_then_success():
    fake = FakeApi(jobs=[job(mode="CREDIT")], mode="CREDIT")
    agent, port = make_agent(fake, SimulatedQibixx(mode="CREDIT", customer_selection=7))
    try:
        agent.hello()
        agent.run_once()
    finally:
        port.close()
    assert [status for _, status, _ in fake.events] == ["WAITING_SELECTION", "DISPENSING", "SUCCESS"]


def test_machine_failure_is_reported_as_failed():
    fake = FakeApi(jobs=[job()])
    agent, port = make_agent(fake, SimulatedQibixx(mode="REMOTE", outcomes=["fail"]))
    try:
        agent.hello()
        agent.run_once()
    finally:
        port.close()
    assert fake.events[-1][1] == "FAILED"
    assert "veremedi" in fake.events[-1][2]


def test_final_event_is_retried_until_accepted():
    fake = FakeApi(jobs=[job()])
    fake.fail_events = 2
    agent, port = make_agent(fake, SimulatedQibixx(mode="REMOTE"))
    try:
        agent.hello()
        agent.run_once()
    finally:
        port.close()
    assert fake.events[-1][1] == "SUCCESS"


def test_wrong_token_raises_and_run_forever_backs_off():
    fake = FakeApi()
    client = httpx.Client(transport=httpx.MockTransport(fake), base_url="https://api.test")
    api = ApiClient("https://api.test", "wrong", client=client)
    try:
        api.next_job(0)
    except ApiError as error:
        assert error.status_code == 401
    else:  # pragma: no cover
        raise AssertionError("401 bekleniyordu")


def test_run_forever_processes_jobs_and_stops():
    fake = FakeApi(jobs=[job("vj-a"), job("vj-b")])
    agent, port = make_agent(fake, SimulatedQibixx(mode="REMOTE", outcomes=["success", "deny"]))
    thread = threading.Thread(target=agent.run_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while agent.processed < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    agent.stop()
    thread.join(2)
    port.close()
    finals = [(job_id, status) for job_id, status, _ in fake.events if status in ("SUCCESS", "FAILED")]
    assert finals == [("vj-a", "SUCCESS"), ("vj-b", "FAILED")]
