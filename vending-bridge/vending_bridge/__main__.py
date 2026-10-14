"""Komut satiri: `python -m vending_bridge [--config /etc/vending-bridge/config.toml] [--simulator]`."""
from __future__ import annotations

import argparse
import logging
import signal
import sys

from . import __version__
from .agent import Agent, ApiClient
from .config import load_config
from .qibixx import QibixxPort, open_serial
from .simulator import SimulatedQibixx


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vending-bridge", description="MagicPay Kiosk kahve otomati MDB koprusu")
    parser.add_argument("--config", help="config.toml yolu (varsayilan /etc/vending-bridge/config.toml)")
    parser.add_argument("--simulator", action="store_true", help="Gercek seri port yerine simulator kullan")
    parser.add_argument("--detect-only", action="store_true", help="Yalnizca VMC modunu tespit edip yazdir")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    config = load_config(args.config)
    logging.basicConfig(level=getattr(logging, config.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("vending_bridge")

    if args.simulator or config.simulator:
        transport = SimulatedQibixx(mode=config.simulator_mode)
        log.warning("SIMULATOR modu: gercek makineye baglanilmiyor.")
    else:
        transport = open_serial(config.serial_port, config.baudrate)
    port = QibixxPort(transport)

    agent_ = Agent(ApiClient(config.api_url, config.machine_token), port, config)
    if args.detect_only:
        detection = agent_.detect()
        print(f"mod={detection.mode} ozellik_seviyesi={detection.feature_level} opsiyonlar={detection.option_bits}")
        port.close()
        return 0
    if not config.machine_token:
        log.error("MACHINE_TOKEN tanimli degil; panelden 'Token olustur' ile alinan anahtari girin.")
        return 2

    def shutdown(*_):
        agent_.stop()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        agent_.detect()
        agent_.run_forever()
    finally:
        port.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
