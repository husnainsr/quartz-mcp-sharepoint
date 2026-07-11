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

/* Minimal markdown: bold, inline code, line breaks. Input is escaped first. */
function mdLite(s) {
  let h = escapeHtml(s);
  h = h.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  h = h.replace(/`([^`]+)`/g, "<code>$1</code>");
  return h.replace(/\n/g, "<br>");
}

/* Pull a trailing "Source: a, b, c" line out of the response body. */
function splitSources(text) {
  const m = text.match(/(?:^|\n)\s*Source:\s*([^\n]+)\s*$/i);
  if (!m) return { body: text.trim(), sources: [] };
  return {
    body: text.slice(0, m.index).trim(),
    sources: m[1].split(/,\s*/).map((s) => s.trim()).filter(Boolean),
  };
}

function renderLog(l) {
  const card = document.createElement("div");
  card.className = "log-card";
  const ok = l.status === "ok";
  const { body, sources } = splitSources(l.response);

  const sourcesRow = sources.length
    ? `<div class="log-row">
         <span class="log-key">Sources</span>
         <div class="log-sources">${sources.map((s) => `<span class="log-src">${escapeHtml(s)}</span>`).join("")}</div>
       </div>`
    : "";

  card.innerHTML = `
    <div class="log-head">
      <span class="tag ${ok ? "tag--active" : "tag--revoked"}">${escapeHtml(l.status)}</span>
      <span class="log-when">${fmtWhen(l.created_at)}</span>
      <span class="log-duration">${fmtDuration(l.duration_ms)}</span>
    </div>
    <div class="log-row">
      <span class="log-key">Query</span>
      <div class="log-query">${escapeHtml(l.query)}</div>
    </div>
    <div class="log-row">
      <span class="log-key">Answer</span>
      <div>
        <div class="log-response is-collapsed">${mdLite(body)}</div>
        <button class="log-toggle" type="button" hidden>Show more</button>
      </div>
    </div>
    ${sourcesRow}`;

  const resp = card.querySelector(".log-response");
  const toggle = card.querySelector(".log-toggle");
  toggle.addEventListener("click", () => {
    const collapsed = resp.classList.toggle("is-collapsed");
    toggle.textContent = collapsed ? "Show more" : "Show less";
  });
  // Only offer expand when the answer is actually clamped.
  requestAnimationFrame(() => {
    if (resp.scrollHeight > resp.clientHeight + 2) toggle.hidden = false;
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
