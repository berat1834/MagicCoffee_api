"""Qibixx surucusu, REMOTE/CREDIT durum makineleri ve VMC tespiti (simulatorle, donanimsiz)."""
from decimal import Decimal

import pytest

from vending_bridge.detect import Detection, apply_sniff, detect_mode
from vending_bridge.qibixx import QibixxPort, format_amount, parse_item, parse_line
from vending_bridge.simulator import SimulatedQibixx
from vending_bridge.vend import VendJob, VendRunner


def make(mode="REMOTE", **kwargs):
    sim = SimulatedQibixx(mode=mode, **kwargs)
    port = QibixxPort(sim)
    reports = []
    runner = VendRunner(port, lambda status, detail: reports.append((status, detail)), ready_timeout=2)
    return sim, port, runner, reports


def job(mode="REMOTE", selection=7, amount="92.00", timeout=3.0):
    return VendJob(id="vj-test", selection_no=selection, amount=Decimal(amount), mode=mode, timeout_sec=timeout, payment_id="1A2B3C4D")


def test_parse_line_and_amounts():
    assert parse_line("c,STATUS,VEND,1.00,2").args == ("VEND", "1.00", "2")
    assert parse_line("c,STATUS,ENABLED").status == "ENABLED"
    assert parse_line("c,VEND,SUCCESS").kind == "VEND"
    assert parse_line('c,ERR,"VEND 3"').args == ("VEND 3",)
    assert parse_line("c,ERR,-3").args == ("-3",)
    assert parse_line("x,00,0014320154,11,0003000000").kind == "SNIFF"
    assert parse_line("") is None
    assert format_amount(92) == "92.00"
    assert format_amount("1.005") == "1.01"
    assert parse_item("65535") is None
    assert parse_item("7") == 7


def test_detection_from_sniffer_setup():
    detection = Detection()
    apply_sniff(detection, ("00", "0014320154", "11", "0003000000"))
    apply_sniff(detection, ("00", "0014323438", "17", "0400000060"))
    assert detection.feature_level == 3
    assert detection.remote_option is True
    level1 = Detection()
    apply_sniff(level1, ("00", "1", "11", "0001000000"))
    assert level1.feature_level == 1


@pytest.mark.parametrize("mode", ["REMOTE", "CREDIT"])
def test_detect_mode_with_simulator(mode):
    sim = SimulatedQibixx(mode=mode)
    port = QibixxPort(sim)
    try:
        result = detect_mode(port, timeout=3, settle=0.2)
    finally:
        port.close()
    assert result.mode == mode
    assert sim.commands[:3] == ["X,1", "C,0", "C,1"]
    assert sim.commands[-1] == "X,0"


def test_remote_success_sends_exact_selection():
    sim, port, runner, reports = make()
    try:
        result = runner.run(job())
    finally:
        port.close()
    assert result.status == "SUCCESS"
    assert "C,SEL,92.00,1A2B3C4D,7" in sim.commands
    assert "C,VEND,92.00" in sim.commands
    assert [status for status, _ in reports] == ["SENT", "DISPENSING"]


@pytest.mark.parametrize("outcome,detail", [("fail", "veremedi"), ("cancel", "iptal"), ("reset", "sıfırladı"), ("deny", "reddetti")])
def test_remote_machine_failures(outcome, detail):
    sim, port, runner, _ = make(outcomes=[outcome])
    try:
        result = runner.run(job())
    finally:
        port.close()
    assert result.status == "FAILED"
    assert detail in result.detail


def test_remote_item_mismatch_is_denied():
    sim, port, runner, _ = make(outcomes=["wrong_item"], wrong_selection=12)
    try:
        result = runner.run(job())
    finally:
        port.close()
    assert result.status == "FAILED"
    assert "C,VEND,-1" in sim.commands
    assert not any(cmd.startswith("C,VEND,9") for cmd in sim.commands)


def test_remote_price_above_paid_is_denied():
    sim, port, runner, _ = make(prices={7: "120.00"})
    try:
        result = runner.run(job())
    finally:
        port.close()
    assert result.status == "FAILED"
    assert "C,VEND,-1" in sim.commands


def test_remote_silent_machine_times_out():
    sim, port, runner, _ = make(outcomes=["silent"])
    try:
        result = runner.run(job(timeout=0.6))
    finally:
        port.close()
    assert result.status == "FAILED"
    assert "süre doldu" in result.detail


def test_remote_on_credit_only_vmc_fails_without_charge():
    sim, port, runner, _ = make(mode="CREDIT")
    try:
        result = runner.run(job())
    finally:
        port.close()
    assert result.status == "FAILED"
    assert not any(cmd.startswith("C,VEND") for cmd in sim.commands)


def test_credit_success_on_right_button():
    sim, port, runner, reports = make(mode="CREDIT", customer_selection=7)
    try:
        result = runner.run(job(mode="CREDIT"))
    finally:
        port.close()
    assert result.status == "SUCCESS"
    assert "C,START,92.00" in sim.commands
    assert "C,VEND,92.00" in sim.commands
    assert reports[0][0] == "WAITING_SELECTION"


def test_credit_wrong_button_is_denied_then_right_button_succeeds():
    sim, port, runner, reports = make(mode="CREDIT", customer_selection=7, outcomes=["wrong_button"], wrong_selection=3)
    try:
        result = runner.run(job(mode="CREDIT"))
    finally:
        port.close()
    assert result.status == "SUCCESS"
    vends = [cmd for cmd in sim.commands if cmd.startswith("C,VEND")]
    assert vends == ["C,VEND,-1", "C,VEND,92.00"]
    wrong = [detail for status, detail in reports if status == "WAITING_SELECTION" and isinstance(detail, dict)]
    assert wrong and wrong[0]["pressedSelectionNo"] == 3
    assert "Yanlış ürün" in wrong[0]["message"]


def test_credit_price_mismatch_is_denied():
    sim, port, runner, _ = make(mode="CREDIT", customer_selection=7, prices={7: "80.00"})
    try:
        result = runner.run(job(mode="CREDIT", timeout=0.8))
    finally:
        port.close()
    assert result.status == "FAILED"
    assert "C,VEND,-1" in sim.commands
    assert sim.commands[-1] == "C,STOP"


def test_credit_no_press_stops_session():
    sim, port, runner, _ = make(mode="CREDIT", customer_selection=7, outcomes=["no_press"])
    try:
        result = runner.run(job(mode="CREDIT", timeout=0.6))
    finally:
        port.close()
    assert result.status == "FAILED"
    assert sim.commands[-1] == "C,STOP"
    assert "seçmedi" in result.detail
