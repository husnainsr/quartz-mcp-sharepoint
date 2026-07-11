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
const logBody  = document.getElementById("log-body");
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

function renderLogRows(l) {
  const ok = l.status === "ok";
  const { body, sources } = splitSources(l.response);

  const tr = document.createElement("tr");
  tr.className = "log-tr";
  tr.innerHTML = `
    <td class="cell-when">${fmtWhen(l.created_at)}</td>
    <td><span class="tag ${ok ? "tag--active" : "tag--revoked"}">${escapeHtml(l.status)}</span></td>
    <td class="cell-dur">${fmtDuration(l.duration_ms)}</td>
    <td class="cell-clip cell-q" title="${escapeHtml(l.query)}">${escapeHtml(l.query)}</td>
    <td class="cell-clip cell-a">${escapeHtml(body)}</td>`;

  const sourcesRow = sources.length
    ? `<div class="log-row">
         <span class="log-key">Sources</span>
         <div class="log-sources">${sources.map((s) => `<span class="log-src">${escapeHtml(s)}</span>`).join("")}</div>
       </div>`
    : "";

  const detail = document.createElement("tr");
  detail.className = "log-detail";
  detail.hidden = true;
  detail.innerHTML = `
    <td colspan="5">
      <div class="log-row">
        <span class="log-key">Query</span>
        <div class="log-query">${escapeHtml(l.query)}</div>
      </div>
      <div class="log-row">
        <span class="log-key">Answer</span>
        <div class="log-response">${mdLite(body)}</div>
      </div>
      ${sourcesRow}
    </td>`;

  tr.addEventListener("click", () => {
    detail.hidden = !detail.hidden;
    tr.classList.toggle("is-open", !detail.hidden);
  });
  return [tr, detail];
}

async function loadLogs(reset) {
  if (reset) { offset = 0; logBody.innerHTML = ""; }
  const { logs, total: t } = await api.logs(PAGE, offset);
  total = t;
  if (!logs.length && offset === 0) {
    logBody.innerHTML = `
      <tr class="log-empty"><td colspan="5">
        <div class="empty">
          <div class="prism" aria-hidden="true"></div>
          <div>No queries logged yet. They'll appear here as clients use the search tool.</div>
        </div>
      </td></tr>`;
  } else {
    for (const l of logs) {
      const [tr, detail] = renderLogRows(l);
      logBody.appendChild(tr);
      logBody.appendChild(detail);
    }
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
