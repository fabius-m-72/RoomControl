from __future__ import annotations
import os
from pathlib import Path
import yaml
import logging

# Percorso della YAML (puoi cambiarlo con env ROOMCTL_CONFIG)
CONFIG_PATH = Path(os.environ.get("ROOMCTL_CONFIG", "/opt/roomctl/config/devices.yaml"))

# Default ragionevoli: adatta gli IP ai tuoi
DEFAULT_DEVICES: dict = {
    "projector": {
        "host": "192.168.1.220",
        "port": 4352,
        "password": "1234",             # se PJLINK 1 serve la password
        "nic_warmup_s": 12,         # attesa dopo mains ON prima di PJLink
        "pjlink_timeout_s": 8,
        "pjlink_retries": 4,
        "post_power_on_delay_s": 1.5,
    },
    "shelly1": {
        "base": "http://192.168.1.10",  # mains: ch1 proiettore, ch2 DSP
        "ch1": 0,
        "ch2": 1,
    },
    "shelly2": {
        "base": "http://192.168.1.11",  # telo: ch1 giù, ch2 su (pulse)
        "ch1": 0,
        "ch2": 1,
        "inverti_corsa": False,
    },
}

log = logging.getLogger(__name__)

def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.error("Impossibile leggere config YAML %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        log.error("Config YAML non valida: %s", path)
        return {}
    return data

# Carica YAML se esiste e fai il merge con i default
_yaml = _load_yaml(CONFIG_PATH)
devices: dict = _deep_merge(DEFAULT_DEVICES, _yaml)

