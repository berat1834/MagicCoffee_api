"""API ajani: hello -> uzun sorguyla is al -> makinede sat -> sonucu bildir; arada heartbeat.

API uclari (Authorization: Bearer <makine anahtari>):
- POST /api/machine/agent/hello {agentVersion, detectedMode, vmcInfo} -> {machineId, mode, pollIntervalMs}
- GET  /api/machine/agent/jobs?wait=25 -> 200 {job} | 204
- POST /api/machine/agent/jobs/{id}/events {status, detail}
- POST /api/machine/agent/heartbeat {status, vmcStatus}
Ag hatalarinda ustel bekleme (1, 2, 4 ... 60 sn) ile yeniden denenir. Nihai sonuc (SUCCESS/FAILED) API'ye
ulasana kadar tekrar gonderilir; ulasmazsa API zaman asimiyla isi kapatip iade eder.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import httpx

from . import __version__
from .config import BridgeConfig
from .detect import Detection, detect_mode
from .qibixx import QibixxPort
from .vend import VendJob, VendResult, VendRunner

logger = logging.getLogger("vending_bridge.agent")

MAX_BACKOFF_SECONDS = 60.0
FINAL_EVENT_RETRY_SECONDS = 300.0


class ApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    """Ince HTTP istemcisi. Testlerde `client` olarak FastAPI TestClient ya da MockTransport'lu httpx.Client verilir."""

    def __init__(self, base_url: str, token: str, *, client: httpx.Client | None = None, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0))

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "User-Agent": f"magicpay-vending-bridge/{__version__}"}

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}" if self.base_url else path

    def _request(self, method: str, path: str, *, json: Any = None, params: dict | None = None, timeout: float | None = None) -> httpx.Response:
        try:
            response = self.client.request(method, self._url(path), json=json, params=params, headers=self.headers,
                                           timeout=timeout or self.timeout)
        except httpx.HTTPError as error:
            raise ApiError(f"{method} {path}: {type(error).__name__}") from error
        if response.status_code in (401, 403):
            raise ApiError(f"{method} {path}: yetkisiz ({response.status_code}); makine anahtarını kontrol edin.", response.status_code)
        if response.status_code >= 400:
            raise ApiError(f"{method} {path}: HTTP {response.status_code} {response.text[:200]}", response.status_code)
        return response

    def hello(self, detected_mode: str | None, vmc_info: dict[str, Any] | None) -> dict[str, Any]:
        return self._request("POST", "/api/machine/agent/hello", json={
            "agentVersion": __version__, "detectedMode": detected_mode, "vmcInfo": vmc_info or {},
        }).json()

    def next_job(self, wait: int) -> dict[str, Any] | None:
        response = self._request("GET", "/api/machine/agent/jobs", params={"wait": wait}, timeout=wait + 15.0)
        if response.status_code == 204 or not response.content:
            return None
        return response.json().get("job")

    def post_event(self, job_id: str, status: str, detail: "str | dict[str, Any] | None" = None) -> dict[str, Any]:
        return self._request("POST", f"/api/machine/agent/jobs/{job_id}/events", json={"status": status, "detail": detail}).json()

    def heartbeat(self, status: str, vmc_status: str | None) -> dict[str, Any]:
        return self._request("POST", "/api/machine/agent/heartbeat", json={"status": status, "vmcStatus": vmc_status}).json()


class Agent:
    def __init__(self, api: ApiClient, port: QibixxPort, config: BridgeConfig, *,
                 sleep: Callable[[float], None] = time.sleep, detector: Callable[[QibixxPort], Detection] | None = None):
        self.api = api
        self.port = port
        self.config = config
        self.sleep = sleep
        self.detector = detector
        self.stop_event = threading.Event()
        self.busy = False
        self.machine_id: str | None = None
        self.mode: str | None = None
        self.detection: Detection | None = None
        self.processed = 0
        self.last_result: VendResult | None = None
        self._heartbeat_thread: threading.Thread | None = None

    # ---- acilis ----
    def detect(self) -> Detection:
        if self.config.forced_mode:
            detection = Detection(mode=self.config.forced_mode)
            self.port.send("C,1")
        elif self.detector is not None:
            detection = self.detector(self.port)
        else:
            detection = detect_mode(self.port, timeout=self.config.detect_timeout)
        self.detection = detection
        return detection

    def hello(self) -> dict[str, Any]:
        detection = self.detection or self.detect()
        info = detection.as_vmc_info()
        info["forcedMode"] = self.config.forced_mode
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                result = self.api.hello(detection.mode, info)
            except ApiError as error:
                logger.warning("hello basarisiz: %s", error)
                self._wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue
            self.machine_id = result.get("machineId")
            self.mode = result.get("mode")
            logger.info("API'ye baglanildi: makine=%s mod=%s", self.machine_id, self.mode)
            return result
        return {}

    def _wait(self, seconds: float) -> None:
        if self.stop_event.wait(0) or seconds <= 0:
            return
        if self.sleep is time.sleep:
            self.stop_event.wait(seconds)
        else:
            self.sleep(seconds)

    # ---- heartbeat ----
    def _heartbeat_loop(self) -> None:
        while not self.stop_event.wait(self.config.heartbeat_interval):
            try:
                self.api.heartbeat("BUSY" if self.busy else "IDLE", self.port.state)
            except ApiError as error:
                logger.debug("heartbeat gonderilemedi: %s", error)

    def start_heartbeat(self) -> None:
        if self._heartbeat_thread is None and self.config.heartbeat_interval > 0:
            self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True)
            self._heartbeat_thread.start()

    # ---- is dongusu ----
    def report_final(self, job_id: str, result: VendResult) -> bool:
        deadline = time.monotonic() + FINAL_EVENT_RETRY_SECONDS
        backoff = 1.0
        while True:
            try:
                self.api.post_event(job_id, result.status, result.detail)
                return True
            except ApiError as error:
                if error.status_code in (404, 409):
                    logger.warning("Sonuc kabul edilmedi (%s): %s", job_id, error)
                    return False
                logger.warning("Sonuc gonderilemedi, tekrar denenecek: %s", error)
            if time.monotonic() >= deadline or self.stop_event.is_set():
                return False
            self._wait(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    def process(self, raw_job: dict[str, Any]) -> VendResult:
        job = VendJob.from_api(raw_job, forced_mode=self.config.forced_mode)
        logger.info("Is %s: urun %s, tutar %s, mod %s", job.id, job.selection_no, job.amount, job.mode)
        self.busy = True
        try:
            runner = VendRunner(self.port, lambda status, detail: self.api.post_event(job.id, status, detail),
                                ready_timeout=self.config.ready_timeout)
            try:
                result = runner.run(job)
            except Exception as error:  # noqa: BLE001 - seri port hatasi: is basarisiz sayilir (iade edilir)
                logger.exception("Satis sirasinda hata")
                result = VendResult("FAILED", f"Köprü hatası: {type(error).__name__}")
            self.report_final(job.id, result)
            self.last_result = result
            self.processed += 1
            logger.info("Is %s sonucu: %s (%s)", job.id, result.status, result.detail)
            return result
        finally:
            self.busy = False

    def run_once(self) -> bool:
        job = self.api.next_job(self.config.poll_wait)
        if not job:
            return False
        self.process(job)
        return True

    def run_forever(self) -> None:
        if not self.machine_id:
            self.hello()
        self.start_heartbeat()
        backoff = 1.0
        while not self.stop_event.is_set():
            try:
                self.run_once()
                backoff = 1.0
            except ApiError as error:
                logger.warning("Is alinamadi: %s", error)
                if error.status_code in (401, 403):
                    self._wait(MAX_BACKOFF_SECONDS)
                else:
                    self._wait(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    def stop(self) -> None:
        self.stop_event.set()
