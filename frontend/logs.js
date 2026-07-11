/* Quartz SharePoint v2 — query logs page */

const api = {
  async req(method, path, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (res.status === 401) {
      window.location.href = "/login.html";
      throw new Error("unauthorized");
    }
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `Request failed (${res.status})`);
    }
    return res.status === 204 ? null : res.json();
  },
  me:        () => api.req("GET", "/admin/api/me"),
  logs:      (limit, offset) => api.req("GET", `/admin/api/logs?limit=${limit}&offset=${offset}`),
  clearLogs: () => api.req("DELETE", "/admin/api/logs"),
  logout:    () => api.req("POST", "/admin/api/logout"),
};

let toastTimer;
function toast(msg) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function fmtWhen(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}

function fmtDuration(ms) {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

const PAGE = 25;
const logList  = document.getElementById("log-list");
const logCount = document.getElementById("log-count");
const loadMore = document.getElementById("load-more");
let offset = 0;
let total = 0;

function renderLog(l) {
  const card = document.createElement("div");
  card.className = "log-card";
  const ok = l.status === "ok";
  card.innerHTML = `
    <div class="log-head">
      <span class="tag ${ok ? "tag--active" : "tag--revoked"}">${escapeHtml(l.status)}</span>
      <span class="log-when">${fmtWhen(l.created_at)}</span>
      <span class="log-duration">${fmtDuration(l.duration_ms)}</span>
    </div>
    <div class="log-query">${escapeHtml(l.query)}</div>
    <div class="log-response is-collapsed">${escapeHtml(l.response)}</div>
    <button class="log-toggle" type="button">Show full response</button>`;

  const resp = card.querySelector(".log-response");
  const toggle = card.querySelector(".log-toggle");
  toggle.addEventListener("click", () => {
    const collapsed = resp.classList.toggle("is-collapsed");
    toggle.textContent = collapsed ? "Show full response" : "Collapse";
  });
  return card;
}

async function loadLogs(reset) {
  if (reset) { offset = 0; logList.innerHTML = ""; }
  const { logs, total: t } = await api.logs(PAGE, offset);
  total = t;
  if (!logs.length && offset === 0) {
    logList.innerHTML = `
      <div class="empty">
        <div class="prism" aria-hidden="true"></div>
        <div>No queries logged yet. They'll appear here as clients use the search tool.</div>
      </div>`;
  } else {
    for (const l of logs) logList.appendChild(renderLog(l));
  }
  offset += logs.length;
  logCount.textContent = total ? `${total} total` : "";
  loadMore.hidden = offset >= total;
}

document.getElementById("refresh-logs").addEventListener("click", () => {
  loadLogs(true).catch((e) => toast(e.message));
});

loadMore.addEventListener("click", () => {
  loadLogs(false).catch((e) => toast(e.message));
});

document.getElementById("clear-logs").addEventListener("click", async () => {
  if (!confirm("Delete all query logs permanently?")) return;
  try { await api.clearLogs(); toast("Logs cleared"); loadLogs(true); }
  catch (e) { toast(e.message); }
});

document.getElementById("logout").addEventListener("click", async () => {
  try { await api.logout(); } catch {}
  window.location.href = "/login.html";
});

(async function init() {
  try {
    const { username } = await api.me();
    document.getElementById("who").textContent = username;
    await loadLogs(true);
  } catch (e) {
    if (e.message !== "unauthorized") toast(e.message);
  }
})();
