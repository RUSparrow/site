import os
import time
from pathlib import Path

import docker
import psutil

SYSFS_PATH = Path(os.getenv("SYSFS_PATH", "/sys"))


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
        "load_avg": [round(x, 2) for x in os.getloadavg()],
    }


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


def collect_all() -> dict:
    return {
        "cpu": get_cpu(),
        "memory": get_memory(),
        "disk": get_disk(),
        "uptime": get_uptime(),
        "temperature": get_temperature(),
        "docker": get_docker_containers(),
        "hostname": os.uname().nodename,
        "timestamp": time.time(),
    }
