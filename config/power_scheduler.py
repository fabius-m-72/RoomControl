#!/usr/bin/env python3
"""
Applica la pianificazione di accensione/spegnimento del Raspberry Pi 5 usando l'RTC.

- Legge il file power_schedule.yaml generato dall'interfaccia operatore.
- Calcola il prossimo evento valido per accensione/spegnimento nei giorni selezionati.
- Programma i comandi tramite systemd-run (se disponibile):
  * Accensione: imposta SUBITO l'allarme RTC con rtcwake in modalità "no".
  * Spegnimento: pianifica /sbin/shutdown -h now con un timer systemd.

Suggerimento: installa prima lo script in /opt con i permessi di esecuzione, poi
abilita la pianificazione (cron o systemd). Esempio systemd completo:
  sudo install -m 755 config/power_scheduler.py /opt/roomctl/config/power_scheduler.py
  sudo install -m 644 config/roomctl-power-scheduler.* /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now roomctl-power-scheduler.timer

Se stai aggiornando i file unit già installati, copia nuovamente i file e poi
esegui:
  sudo systemctl daemon-reload
  sudo systemctl restart roomctl-power-scheduler.timer

Se preferisci cron, esegui lo script all'avvio e ogni notte per aggiornare
l'allarme del giorno successivo, ad esempio:
  @reboot /opt/roomctl/config/power_scheduler.py
  03  *  *  *  * /opt/roomctl/config/power_scheduler.py

Per verificare la pianificazione attuale (file YAML + timer systemd):
  sudo /usr/bin/python3 -u /opt/roomctl/config/power_scheduler.py --status
"""
from __future__ import annotations
import argparse
import datetime as dt
import os
from pathlib import Path
import shutil
import subprocess
import sys
import yaml

SCHEDULE_PATH = Path(os.environ.get("ROOMCTL_POWER_SCHEDULE", "/opt/roomctl/config/power_schedule.yaml"))

DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

dbg=bool(os.environ.get("ROOMCTL_POWER_DEBUG"))

def _load_schedule() -> dict:
    if not SCHEDULE_PATH.is_file():
        return {}
    with SCHEDULE_PATH.open("r", encoding="utf-8") as f:
        payload = yaml.safe_load(f) or {}
        if dbg:
            print("schedule: ", payload)
        return payload


def _normalize_time(value, default: str) -> str:
    """Normalizza valori orari accettando stringhe, numeri o dt.time."""

    if isinstance(value, dt.time):
        return f"{value.hour:02d}:{value.minute:02d}"

    if value is None:
        return default

    s = str(value).strip()
    if not s:
        return default

    h: int | None = None
    m: int | None = None

    if s.isdigit():
        # Permette formati "730" o "0730" -> 07:30
        if len(s) in (3, 4):
            h, m = int(s[:-2]), int(s[-2:])
    elif ":" in s:
        try:
            hh, mm = s.split(":", 1)
            h, m = int(hh), int(mm)
        except ValueError:
            h = m = None

    if h is None or m is None or not (0 <= h <= 23 and 0 <= m <= 59):
        return default

    return f"{h:02d}:{m:02d}"


def _normalize_schedule(rdata: dict | None) -> dict:
    data = rdata or {}
    on_time = _normalize_time(data.get("on_time"), "07:30")
    off_time = _normalize_time(data.get("off_time"), "19:00")
    days = [str(d).strip().lower() for d in (data.get("days") or []) if str(d).strip()]
    if not days:
        days = list(DAY_ORDER[:5])
    enabled = bool(data.get("enabled", False))
    if dbg:
        print(
            "schedule_normalized: ",
            {"on_time": on_time, "off_time": off_time, "days": days, "enabled": enabled},
        )
    return {
        "on_time": on_time,
        "off_time": off_time,
        "days": days,
        "enabled": enabled,
    }


def _parse_time(value: str) -> dt.time:
    hh, mm = value.split(":", 1)
    return dt.time(hour=int(hh), minute=int(mm))


def _next_occurrence(time_str: str, days: list[str]) -> dt.datetime | None:
    now = dt.datetime.now()
    tgt_time = _parse_time(time_str)
    wanted = [d.lower() for d in days]
    for delta in range(8):
        candidate_day = now + dt.timedelta(days=delta)
        day_code = DAY_ORDER[candidate_day.weekday()]
        if day_code not in wanted:
            continue
        candidate_dt = candidate_day.replace(
            hour=tgt_time.hour, minute=tgt_time.minute, second=0, microsecond=0
        )
        if candidate_dt <= now:
            continue
        return candidate_dt
    return None


def _detect_rtc_device() -> tuple[Path | None, Path | None]:
    base = Path("/sys/class/rtc")
    if base.is_dir():
        for entry in sorted(base.iterdir()):
            if not entry.is_dir():
                continue
            dev = Path("/dev") / entry.name
            wakealarm = entry / "wakealarm"
            if dev.exists():
                if dbg:
                    print(
                        "dev_rtc_detected: ",
                        dev,
                        wakealarm if wakealarm.is_file() else None,
                    )
                return dev, wakealarm if wakealarm.is_file() else None

    for candidate in (Path("/dev/rtc"), Path("/dev/rtc0")):
        if candidate.exists():
            wakealarm = Path("/sys/class/rtc/rtc0/wakealarm")
            if dbg:
                print(
                    "rtc_detected: ",
                    candidate,
                    wakealarm if wakealarm.is_file() else None,
                )
            return candidate, wakealarm if wakealarm.is_file() else None

    return None, None


def _program_rtc_wake(when: dt.datetime) -> bool:
    rtc_device, _ = _detect_rtc_device()
    if not rtc_device:
        print(
            "Nessun dispositivo RTC trovato (attesi /dev/rtc*): impossibile programmare l'accensione.",
            file=sys.stderr,
        )
        return False

    rtcwake = shutil.which("rtcwake") or "/usr/sbin/rtcwake"
    epoch = int(when.timestamp())
    cmd = [rtcwake, "-m", "no", "-t", str(epoch), "-d", str(rtc_device)]
    print("Programmo RTC wake immediatamente:", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stdout.strip() or "", file=sys.stderr)
        print(result.stderr.strip() or "", file=sys.stderr)
        return False
    return True


def _schedule_systemd(unit: str, when: dt.datetime, command: list[str]) -> bool:
    runner = shutil.which("systemd-run")
    if not runner:
        print(
            "systemd-run non trovato: impossibile pianificare automaticamente",
            file=sys.stderr,
        )
        return False

    ctl = shutil.which("systemctl")
    if ctl:
        for suffix in (".timer", ".service"):
            name = f"{unit}{suffix}"
            state = subprocess.run(
                [ctl, "show", "--value", "-p", "LoadState", name],
                capture_output=True,
                text=True,
                check=False,
            )
            if state.stdout.strip() != "not-found":
                subprocess.run([ctl, "stop", name], check=False)
                subprocess.run([ctl, "reset-failed", name], check=False)


    def _supports_replace() -> bool:
        try:
            result = subprocess.run(
                [runner, "--help"], capture_output=True, text=True, check=False, timeout=5
            )
        except Exception:
            return False
        help_text = (result.stdout or "") + (result.stderr or "")
        return "--replace" in help_text

    calendar = when.strftime("%Y-%m-%d %H:%M:%S")
    cmd = [runner, "--unit", unit]
    if _supports_replace():
        cmd.append("--replace")
    cmd.extend(
        [
            "--timer-property",
            "Persistent=true",
            "--on-calendar",
            calendar,
            *command,
        ]
    )
    print("Eseguo:", " ".join(cmd))
    subprocess.run(cmd, check=False)
    return True


def _print_status(cfg: dict) -> None:
    normalized = _normalize_schedule(cfg)
    days = ", ".join(normalized["days"]) if normalized["days"] else "(nessuno)"
    print("Stato pianificazione power:")
    print(f"  Abilitata: {'sì' if normalized['enabled'] else 'no'}")
    print(f"  Accensione (on_time): {normalized['on_time']}")
    print(f"  Spegnimento (off_time): {normalized['off_time']}")
    print(f"  Giorni attivi: {days}")

    next_on = _next_occurrence(normalized["on_time"], normalized["days"])
    next_off = _next_occurrence(normalized["off_time"], normalized["days"])
    if next_on:
        print(f"  Prossima accensione programmabile: {next_on}")
    else:
        print("  Nessuna prossima accensione trovata (entro 7 giorni)")
    if next_off:
        print(f"  Prossimo spegnimento programmabile: {next_off}")
    else:
        print("  Nessuno spegnimento trovato (entro 7 giorni)")

    rtc_device, wakealarm = _detect_rtc_device()
    if rtc_device:
        print(f"  Dispositivo RTC rilevato: {rtc_device}")
    else:
        print("  Dispositivo RTC rilevato: (nessuno)")

    if wakealarm and wakealarm.is_file():
        try:
            value = wakealarm.read_text(encoding="utf-8").strip()
            if value:
                try:
                    epoch = int(value)
                    ts = dt.datetime.fromtimestamp(epoch)
                    print(f"  wakealarm RTC attuale: {value} ({ts})")
                except ValueError:
                    print(f"  wakealarm RTC attuale: {value}")
            else:
                print("  wakealarm RTC attuale: non impostato")
        except OSError as exc:
            print(f"  impossibile leggere wakealarm: {exc}")
    else:
        print("  wakealarm RTC attuale: file non trovato")

    ctl = shutil.which("systemctl")
    if ctl:
        print("\nStato unità systemd (se presenti):")
        for unit in ("roomctl-rtcwake", "roomctl-poweroff"):
            for suffix in (".timer", ".service"):
                name = f"{unit}{suffix}"
                result = subprocess.run(
                    [ctl, "status", name, "--no-pager"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                status_line = result.stdout.splitlines()[0] if result.stdout else "(nessun output)"
                print(f"  {name}: {status_line}")
    else:
        print("\n(systemctl non disponibile; impossibile leggere lo stato dei timer)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--status",
        action="store_true",
        help="Mostra la pianificazione salvata e lo stato dei timer roomctl-*",
    )
    args = parser.parse_args(argv)

    cfg = _normalize_schedule(_load_schedule())

    if args.status:
        _print_status(cfg)
        return 0

    if not cfg or not cfg.get("enabled"):
        print("Pianificazione disabilitata o mancante; nessuna azione.")
        return 0

    days = cfg.get("days") or DAY_ORDER[:5]
    on_time = cfg.get("on_time", "07:30")
    off_time = cfg.get("off_time", "19:00")

    next_on = _next_occurrence(on_time, days)
    next_off = _next_occurrence(off_time, days)

    if next_on:
        _program_rtc_wake(next_on)
    else:
        print("Nessuna occorrenza di accensione trovata nei prossimi 7 giorni", file=sys.stderr)

    if next_off:
        _schedule_systemd(
            "roomctl-poweroff",
            next_off,
            ["/sbin/shutdown", "-h", "now"],
        )
    else:
        print("Nessuna occorrenza di spegnimento trovata nei prossimi 7 giorni", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
