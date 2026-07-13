import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import docker
import psutil

SYSFS_PATH = Path(os.getenv("SYSFS_PATH", "/sys"))
PLEX_SERVICE_NAME = "plexmediaserver"
PLEX_WEB_HOST = os.getenv("PLEX_WEB_HOST", "127.0.0.1")
PLEX_WEB_PORT = int(os.getenv("PLEX_WEB_PORT", "32400"))
MEDIA_MOUNT = Path(os.getenv("MEDIA_MOUNT", "/media"))


def _format_uptime(seconds: float) -> str:
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}д")
    if hours:
        parts.append(f"{hours}ч")
    if minutes:
        parts.append(f"{minutes}м")
    parts.append(f"{secs}с")
    return " ".join(parts)


def get_cpu() -> dict:
    return {
        "percent": round(psutil.cpu_percent(interval=0.1), 1),
        "cores": psutil.cpu_count(logical=True) or 0,
        "model": _get_cpu_model(),
    }


def _get_cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.startswith(("model name", "Hardware", "Processor")) and ":" in line:
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "CPU"


def get_memory() -> dict:
    mem = psutil.virtual_memory()
    return {
        "total": mem.total,
        "used": mem.used,
        "available": mem.available,
        "percent": round(mem.percent, 1),
    }


def get_disk() -> dict:
    usage = psutil.disk_usage("/")
    return {
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "percent": round(usage.percent, 1),
        "mount": "/",
    }


def get_media_disk() -> dict | None:
    if not MEDIA_MOUNT.is_dir():
        return None

    try:
        usage = psutil.disk_usage(MEDIA_MOUNT)
        root_usage = psutil.disk_usage("/")
    except OSError:
        return None

    if usage.total == root_usage.total and usage.used == root_usage.used:
        return None

    return {
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "percent": round(usage.percent, 1),
    }


def get_uptime() -> dict:
    seconds = time.time() - psutil.boot_time()
    return {
        "seconds": int(seconds),
        "formatted": _format_uptime(seconds),
    }


def get_temperature() -> dict | None:
    thermal_dir = SYSFS_PATH / "class" / "thermal"
    if not thermal_dir.exists():
        return None

    readings = []
    for zone in sorted(thermal_dir.glob("thermal_zone*")):
        temp_file = zone / "temp"
        if not temp_file.exists():
            continue
        try:
            celsius = int(temp_file.read_text().strip()) / 1000.0
        except (OSError, ValueError):
            continue

        type_file = zone / "type"
        label = type_file.read_text().strip() if type_file.exists() else zone.name
        readings.append({"label": label, "celsius": round(celsius, 1)})

    if not readings:
        return None

    hottest = max(readings, key=lambda r: r["celsius"])
    return {
        "celsius": hottest["celsius"],
        "label": hottest["label"],
        "zones": readings,
    }


def get_docker_containers() -> dict:
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        return {"available": False, "error": str(exc), "containers": []}

    containers = []
    for container in client.containers.list(all=True):
        containers.append({
            "id": container.short_id,
            "name": container.name,
            "status": container.status,
            "image": (
                container.image.tags[0]
                if container.image.tags
                else container.image.short_id
            ),
            "created": container.attrs.get("Created", ""),
        })

    containers.sort(key=lambda c: c["name"])
    return {"available": True, "containers": containers, "total": len(containers)}


def _parse_iso_timestamp(value: str) -> float | None:
    if not value or value in ("n/a", "0"):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _parse_systemctl_show(output: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    return props


def _map_service_status(active_state: str, sub_state: str) -> str:
    if active_state == "active" and sub_state == "running":
        return "running"
    return "stopped"


def _check_plex_web() -> dict:
    host = PLEX_WEB_HOST
    port = PLEX_WEB_PORT
    url = f"http://{host}:{port}/identity"

    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=3) as response:
            return {
                "available": True,
                "host": host,
                "port": port,
                "url": url,
                "status_code": response.status,
            }
    except urllib.error.HTTPError as exc:
        return {
            "available": exc.code < 500,
            "host": host,
            "port": port,
            "url": url,
            "status_code": exc.code,
            "error": str(exc),
        }
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        # Fallback: TCP connect if HTTP fails (e.g. redirect/SSL quirks)
        try:
            with socket.create_connection((host, port), timeout=3):
                return {
                    "available": True,
                    "host": host,
                    "port": port,
                    "url": url,
                    "status_code": None,
                    "note": "tcp_ok",
                }
        except OSError:
            pass

        return {
            "available": False,
            "host": host,
            "port": port,
            "url": url,
            "error": str(exc),
        }


def _get_plex_from_systemd() -> dict | None:
    if not shutil.which("systemctl"):
        return None

    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                PLEX_SERVICE_NAME,
                "--property=ActiveState,SubState,MainPID,ActiveEnterTimestamp,UnitFileState",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    props = _parse_systemctl_show(result.stdout)
    active_state = props.get("ActiveState", "unknown")
    sub_state = props.get("SubState", "")
    unit_state = props.get("UnitFileState", "")

    if active_state == "not-found" and unit_state in ("", "disabled", "masked"):
        return None

    main_pid = int(props.get("MainPID", "0") or "0")
    started_at = _parse_iso_timestamp(props.get("ActiveEnterTimestamp", ""))
    status = _map_service_status(active_state, sub_state)
    is_running = status == "running"
    uptime_seconds = int(time.time() - started_at) if started_at and is_running else 0

    return {
        "found": True,
        "service": f"{PLEX_SERVICE_NAME}.service",
        "status": status,
        "systemd": {
            "active_state": active_state,
            "sub_state": sub_state,
            "main_pid": main_pid,
        },
        "uptime": {
            "seconds": uptime_seconds,
            "formatted": _format_uptime(uptime_seconds) if uptime_seconds else "—",
        },
    }


def get_plex_status() -> dict:
    service_info = _get_plex_from_systemd()
    web_info = _check_plex_web()

    if not service_info:
        return {
            "found": False,
            "service": f"{PLEX_SERVICE_NAME}.service",
            "status": "not_found",
            "uptime": {"seconds": 0, "formatted": "—"},
            "web": web_info,
        }

    return {
        **service_info,
        "web": web_info,
    }


def collect_all() -> dict:
    return {
        "cpu": get_cpu(),
        "memory": get_memory(),
        "disk": get_disk(),
        "media_disk": get_media_disk(),
        "uptime": get_uptime(),
        "temperature": get_temperature(),
        "docker": get_docker_containers(),
        "plex": get_plex_status(),
        "hostname": os.uname().nodename,
        "timestamp": time.time(),
    }
