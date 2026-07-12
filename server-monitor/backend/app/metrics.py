import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import docker
import psutil

SYSFS_PATH = Path(os.getenv("SYSFS_PATH", "/sys"))
PLEX_CONTAINER_NAMES = {"plex", "plexmediaserver"}
PLEX_SERVICE_NAME = "plexmediaserver"

_plex_cpu_cache: dict[str, tuple[int, int]] = {}


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


def _is_plex_container(name: str) -> bool:
    return name.lower().lstrip("/") in PLEX_CONTAINER_NAMES


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


def _docker_cpu_percent(container_id: str, stats: dict) -> float:
    cpu_stats = stats.get("cpu_stats", {})
    precpu_stats = stats.get("precpu_stats", {})
    cpu_usage = cpu_stats.get("cpu_usage", {})
    precpu_usage = precpu_stats.get("cpu_usage", {})

    cpu_delta = cpu_usage.get("total_usage", 0) - precpu_usage.get("total_usage", 0)
    system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)

    if system_delta <= 0 or cpu_delta <= 0:
        cached = _plex_cpu_cache.get(container_id)
        if cached:
            prev_cpu, prev_system = cached
            cpu_delta = cpu_usage.get("total_usage", 0) - prev_cpu
            system_delta = cpu_stats.get("system_cpu_usage", 0) - prev_system

    _plex_cpu_cache[container_id] = (
        cpu_usage.get("total_usage", 0),
        cpu_stats.get("system_cpu_usage", 0),
    )

    if system_delta <= 0 or cpu_delta <= 0:
        return 0.0

    cpu_count = cpu_stats.get("online_cpus") or len(cpu_usage.get("percpu_usage", [])) or 1
    return round((cpu_delta / system_delta) * cpu_count * 100.0, 1)


def _get_plex_from_docker() -> dict | None:
    try:
        client = docker.from_env()
        client.ping()
    except Exception:
        return None

    for container in client.containers.list(all=True):
        if not _is_plex_container(container.name):
            continue

        state = container.attrs.get("State", {})
        status = container.status
        started_at = _parse_iso_timestamp(state.get("StartedAt", ""))
        uptime_seconds = int(time.time() - started_at) if started_at and status == "running" else 0

        resources = {"cpu_percent": 0.0, "memory_bytes": 0, "memory_limit": 0}
        if status == "running":
            try:
                stats = container.stats(stream=False)
                mem_stats = stats.get("memory_stats", {})
                resources = {
                    "cpu_percent": _docker_cpu_percent(container.id, stats),
                    "memory_bytes": mem_stats.get("usage", 0) or 0,
                    "memory_limit": mem_stats.get("limit", 0) or 0,
                }
            except Exception:
                pass

        return {
            "found": True,
            "source": "docker",
            "name": container.name,
            "status": status,
            "uptime": {
                "seconds": uptime_seconds,
                "formatted": _format_uptime(uptime_seconds) if uptime_seconds else "—",
            },
            "resources": resources,
            "image": (
                container.image.tags[0]
                if container.image.tags
                else container.image.short_id
            ),
            "container_id": container.short_id,
        }

    return None


def _parse_systemctl_show(output: str) -> dict:
    props: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    return props


def _get_plex_from_systemd() -> dict | None:
    if not shutil.which("systemctl"):
        return None

    try:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                PLEX_SERVICE_NAME,
                "--property=ActiveState,SubState,MainPID,ActiveEnterTimestamp,MemoryCurrent",
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

    if active_state in ("inactive", "failed", "not-found") and props.get("MainPID", "0") == "0":
        status_result = subprocess.run(
            ["systemctl", "is-active", PLEX_SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if status_result.returncode != 0 and active_state == "not-found":
            return None

    main_pid = int(props.get("MainPID", "0") or "0")
    started_at = _parse_iso_timestamp(props.get("ActiveEnterTimestamp", ""))
    is_running = active_state == "active" and sub_state == "running"
    uptime_seconds = int(time.time() - started_at) if started_at and is_running else 0

    resources = {"cpu_percent": 0.0, "memory_bytes": 0, "memory_limit": 0}
    memory_current = props.get("MemoryCurrent", "[not set]")
    if memory_current.isdigit():
        resources["memory_bytes"] = int(memory_current)

    if is_running and main_pid > 0:
        try:
            proc = psutil.Process(main_pid)
            resources["cpu_percent"] = round(proc.cpu_percent(interval=0.1), 1)
            if not resources["memory_bytes"]:
                resources["memory_bytes"] = proc.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    status = "running" if is_running else active_state

    return {
        "found": True,
        "source": "systemd",
        "name": PLEX_SERVICE_NAME,
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
        "resources": resources,
    }


def get_plex_status() -> dict:
    docker_info = _get_plex_from_docker()
    if docker_info:
        return docker_info

    systemd_info = _get_plex_from_systemd()
    if systemd_info:
        return systemd_info

    return {
        "found": False,
        "source": None,
        "name": None,
        "status": "not_found",
        "uptime": {"seconds": 0, "formatted": "—"},
        "resources": {"cpu_percent": 0.0, "memory_bytes": 0, "memory_limit": 0},
    }


def collect_all() -> dict:
    return {
        "cpu": get_cpu(),
        "memory": get_memory(),
        "disk": get_disk(),
        "uptime": get_uptime(),
        "temperature": get_temperature(),
        "docker": get_docker_containers(),
        "plex": get_plex_status(),
        "hostname": os.uname().nodename,
        "timestamp": time.time(),
    }
