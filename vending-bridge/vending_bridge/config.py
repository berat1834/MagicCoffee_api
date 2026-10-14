"""Yapilandirma: `config.toml` (varsayilan /etc/vending-bridge/config.toml) + ortam degiskenleri (oncelikli)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = "/etc/vending-bridge/config.toml"


@dataclass
class BridgeConfig:
    api_url: str = "https://magickiosk-api.magicpay.ai"
    machine_token: str = ""
    serial_port: str = "/dev/ttyAMA0"
    baudrate: int = 115200
    # AUTO: panel/tespit belirler; REMOTE veya CREDIT: koprude zorla.
    mode: str = "AUTO"
    simulator: bool = False
    simulator_mode: str = "REMOTE"
    poll_wait: int = 25
    heartbeat_interval: float = 30.0
    detect_timeout: float = 20.0
    ready_timeout: float = 10.0
    log_level: str = "INFO"

    @property
    def forced_mode(self) -> str | None:
        mode = (self.mode or "AUTO").upper()
        return mode if mode in ("REMOTE", "CREDIT") else None


ENV_NAMES = {
    "api_url": "API_URL",
    "machine_token": "MACHINE_TOKEN",
    "serial_port": "SERIAL_PORT",
    "baudrate": "BAUDRATE",
    "mode": "MODE",
    "simulator": "SIMULATOR",
    "simulator_mode": "SIMULATOR_MODE",
    "poll_wait": "POLL_WAIT",
    "heartbeat_interval": "HEARTBEAT_INTERVAL",
    "detect_timeout": "DETECT_TIMEOUT",
    "ready_timeout": "READY_TIMEOUT",
    "log_level": "LOG_LEVEL",
}


def _coerce(kind: Any, value: Any) -> Any:
    if kind in (bool, "bool"):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "evet")
    if kind in (int, "int"):
        return int(value)
    if kind in (float, "float"):
        return float(value)
    return str(value)


def load_config(path: str | None = None, env: dict[str, str] | None = None) -> BridgeConfig:
    env = dict(os.environ if env is None else env)
    config = BridgeConfig()
    types = {item.name: item.type for item in fields(BridgeConfig)}
    config_path = Path(path or env.get("VENDING_BRIDGE_CONFIG") or DEFAULT_CONFIG_PATH)
    if config_path.is_file():
        with config_path.open("rb") as handle:
            data = tomllib.load(handle)
        section = data.get("bridge", data)
        for key, value in section.items():
            if key in types:
                setattr(config, key, _coerce(types[key], value))
    for key, name in ENV_NAMES.items():
        if env.get(name, "").strip():
            setattr(config, key, _coerce(types[key], env[name].strip()))
    config.api_url = config.api_url.rstrip("/")
    config.mode = (config.mode or "AUTO").upper()
    return config
