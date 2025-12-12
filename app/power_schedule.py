from __future__ import annotations
import os
from pathlib import Path
import re
import yaml
import logging

DEFAULT_SCHEDULE = {
    "on_time": "07:30",
    "off_time": "19:00",
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "enabled": False,
}

VALID_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

POWER_SCHEDULE_PATH = Path(
    os.environ.get("ROOMCTL_POWER_SCHEDULE", "/opt/roomctl/config/power_schedule.yaml")
)

log = logging.getLogger(__name__)


def _normalize_time(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Orario non valido")
    value = value.strip()
    if not re.match(r"^\d{2}:\d{2}$", value):
        raise ValueError("Formato orario non valido (hh:mm)")
    hh, mm = value.split(":", 1)
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("Orario fuori range (00:00-23:59)")
    return f"{h:02d}:{m:02d}"


def _normalize_days(days: list[str] | tuple[str, ...] | None) -> list[str]:
    out: list[str] = []
    for d in list(days or []):
        d_norm = str(d).strip().lower()
        if d_norm in VALID_DAYS and d_norm not in out:
            out.append(d_norm)
    if not out:
        out = list(DEFAULT_SCHEDULE["days"])
    return out


def _normalize_schedule(data: dict | None) -> dict:
    if not isinstance(data, dict):
        data = {}
    on_time = _normalize_time(data.get("on_time", DEFAULT_SCHEDULE["on_time"]))
    off_time = _normalize_time(data.get("off_time", DEFAULT_SCHEDULE["off_time"]))
    enabled = bool(data.get("enabled", DEFAULT_SCHEDULE["enabled"]))
    days = _normalize_days(data.get("days"))
    return {
        "on_time": on_time,
        "off_time": off_time,
        "days": days,
        "enabled": enabled,
    }


def load_power_schedule() -> dict:
    path = POWER_SCHEDULE_PATH
    if not path.is_file():
        return dict(DEFAULT_SCHEDULE)
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.error("Impossibile leggere la pianificazione %s: %s", path, exc)
        return dict(DEFAULT_SCHEDULE)
    return _normalize_schedule(data)


def save_power_schedule(schedule: dict) -> dict:
    normalized = _normalize_schedule(schedule)
    path = POWER_SCHEDULE_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(normalized, f, allow_unicode=True)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"Impossibile salvare la pianificazione: {exc}") from exc
    return normalized
