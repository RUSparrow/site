const REFRESH_INTERVAL = 5000;
const API_URL = "/api/metrics";

const $ = (id) => document.getElementById(id);

function formatBytes(bytes) {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  return `${(bytes / 1024 ** i).toFixed(1)} ${units[i]}`;
}

function setProgressBar(barEl, percent) {
  barEl.style.width = `${percent}%`;
  barEl.classList.remove("progress__bar--warn", "progress__bar--danger");
  if (percent >= 90) {
    barEl.classList.add("progress__bar--danger");
  } else if (percent >= 70) {
    barEl.classList.add("progress__bar--warn");
  }
}

function statusBadgeClass(status) {
  const map = {
    running: "status-badge--running",
    exited: "status-badge--exited",
    stopped: "status-badge--stopped",
    paused: "status-badge--paused",
    restarting: "status-badge--restarting",
  };
  return map[status] || "";
}

function updateConnection(ok) {
  const dot = $("status-dot");
  const label = $("connection-status");
  dot.classList.toggle("status-dot--ok", ok);
  dot.classList.toggle("status-dot--error", !ok);
  label.textContent = ok ? "Онлайн" : "Ошибка подключения";
}

function renderPlex(plex) {
  const section = $("plex-section");
  const grid = $("plex-grid");
  const empty = $("plex-empty");
  const badge = $("plex-status-badge");

  if (!plex.found) {
    grid.hidden = true;
    empty.hidden = false;
    badge.textContent = "не найден";
    badge.className = "status-badge status-badge--exited";
    return;
  }

  grid.hidden = false;
  empty.hidden = true;

  const isRunning = plex.status === "running" || plex.status === "active";
  badge.textContent = plex.status;
  badge.className = `status-badge ${statusBadgeClass(isRunning ? "running" : plex.status)}`;

  $("plex-source").textContent = plex.source === "docker" ? "Docker" : "systemd";
  $("plex-name").textContent = plex.name || "—";
  $("plex-uptime").textContent = plex.uptime.formatted;

  const res = plex.resources || {};
  $("plex-cpu").textContent = `${res.cpu_percent ?? 0}%`;
  $("plex-ram").textContent = formatBytes(res.memory_bytes || 0);

  if (plex.source === "docker") {
    $("plex-extra").textContent = plex.image || plex.container_id || "—";
  } else if (plex.systemd) {
    $("plex-extra").textContent = `PID ${plex.systemd.main_pid || "—"}`;
  } else {
    $("plex-extra").textContent = "—";
  }
}

function renderMetrics(data) {
  $("hostname").textContent = data.hostname || "—";

  const cpu = data.cpu;
  $("cpu-value").textContent = `${cpu.percent}%`;
  setProgressBar($("cpu-bar"), cpu.percent);
  $("cpu-meta").textContent = `${cpu.cores} ядер · load ${cpu.load_avg.join(" / ")}`;

  const mem = data.memory;
  $("ram-value").textContent = `${mem.percent}%`;
  setProgressBar($("ram-bar"), mem.percent);
  $("ram-meta").textContent = `${formatBytes(mem.used)} / ${formatBytes(mem.total)}`;

  const disk = data.disk;
  $("disk-value").textContent = `${disk.percent}%`;
  setProgressBar($("disk-bar"), disk.percent);
  $("disk-meta").textContent = `${formatBytes(disk.used)} / ${formatBytes(disk.total)} (${disk.mount})`;

  $("uptime-value").textContent = data.uptime.formatted;

  const temp = data.temperature;
  if (temp) {
    $("temp-value").textContent = `${temp.celsius}°C`;
    $("temp-meta").textContent = temp.label;
  } else {
    $("temp-value").textContent = "N/A";
    $("temp-meta").textContent = "Датчик недоступен";
  }

  renderPlex(data.plex || { found: false });

  const docker = data.docker;
  const tbody = $("docker-tbody");
  const errorEl = $("docker-error");

  $("docker-count").textContent = docker.available ? docker.total : "—";

  if (!docker.available) {
    tbody.innerHTML = `<tr><td colspan="4" class="docker-table__empty">Docker недоступен</td></tr>`;
    errorEl.hidden = false;
    errorEl.textContent = docker.error || "Не удалось подключиться к Docker";
    return;
  }

  errorEl.hidden = true;

  if (!docker.containers.length) {
    tbody.innerHTML = `<tr><td colspan="4" class="docker-table__empty">Контейнеры не найдены</td></tr>`;
    return;
  }

  tbody.innerHTML = docker.containers
    .map(
      (c) => `
      <tr>
        <td>${c.name}</td>
        <td><span class="status-badge ${statusBadgeClass(c.status)}">${c.status}</span></td>
        <td>${c.image}</td>
        <td><code>${c.id}</code></td>
      </tr>`
    )
    .join("");
}

function updateTimestamp() {
  const now = new Date();
  $("last-updated").textContent = `Обновлено: ${now.toLocaleTimeString("ru-RU")}`;
}

async function fetchMetrics() {
  try {
    const res = await fetch(API_URL);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderMetrics(data);
    updateConnection(true);
    updateTimestamp();
  } catch (err) {
    updateConnection(false);
    console.error("Metrics fetch error:", err);
  }
}

fetchMetrics();
setInterval(fetchMetrics, REFRESH_INTERVAL);
