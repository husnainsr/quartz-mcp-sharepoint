/* Quartz SharePoint v2 admin dashboard */

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
  keys:      () => api.req("GET", "/admin/api/keys"),
  createKey: (label) => api.req("POST", "/admin/api/keys", { label }),
  revokeKey: (id) => api.req("POST", `/admin/api/keys/${id}/revoke`),
  deleteKey: (id) => api.req("DELETE", `/admin/api/keys/${id}`),
  server:    () => api.req("GET", "/admin/api/server"),
  setServer: (enabled) => api.req("POST", "/admin/api/server", { enabled }),
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

function fmtDate(iso) {
  try {
    return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  } catch { return iso; }
}

function maskToken(token) {
  if (!token) return "";
  return token.length > 16 ? `${token.slice(0, 8)}…${token.slice(-4)}` : token;
}

// ── Server toggle ─────────────────────────────────────────────────────────────
const statusCard  = document.getElementById("status-card");
const statusState = document.getElementById("status-state");
const statusSub   = document.getElementById("status-sub");
const serverSwitch = document.getElementById("server-switch");

function renderServer(enabled) {
  serverSwitch.setAttribute("aria-checked", String(enabled));
  statusCard.classList.toggle("is-online", enabled);
  statusState.textContent = enabled ? "Server online" : "Server stopped";
  statusSub.textContent = enabled
    ? "Clients can connect and call tools."
    : "All MCP requests are refused until you start it.";
}

async function loadServer() {
  const { enabled } = await api.server();
  renderServer(enabled);
}

serverSwitch.addEventListener("click", async () => {
  const next = serverSwitch.getAttribute("aria-checked") !== "true";
  renderServer(next);
  try {
    const { enabled } = await api.setServer(next);
    renderServer(enabled);
    toast(enabled ? "Server started" : "Server stopped");
  } catch (e) {
    renderServer(!next);
    toast(e.message);
  }
});

// ── Keys ──────────────────────────────────────────────────────────────────────
const keyList  = document.getElementById("key-list");
const keyCount = document.getElementById("key-count");

function renderKeys(keys) {
  keyCount.textContent = keys.length
    ? `${keys.filter((k) => !k.revoked).length} active · ${keys.length} total`
    : "";

  if (!keys.length) {
    keyList.innerHTML = `
      <div class="empty">
        <div class="prism" aria-hidden="true"></div>
        <div>No keys yet. Generate one to let a client connect.</div>
      </div>`;
    return;
  }

  keyList.innerHTML = "";
  for (const k of keys) {
    const card = document.createElement("div");
    card.className = "key-card" + (k.revoked ? " is-revoked" : "");
    card.innerHTML = `
      <span class="key-facet" aria-hidden="true"></span>
      <div class="key-main">
        <div class="key-label">
          ${escapeHtml(k.label || "Untitled key")}
          <span class="tag ${k.revoked ? "tag--revoked" : "tag--active"}">
            ${k.revoked ? "Revoked" : "Active"}
          </span>
        </div>
        <div class="key-token">${escapeHtml(maskToken(k.token))}</div>
        <div class="key-meta">Created ${fmtDate(k.created_at)}</div>
      </div>
      <div class="key-actions"></div>`;

    const actions = card.querySelector(".key-actions");
    if (!k.revoked) {
      const revoke = document.createElement("button");
      revoke.className = "btn btn--ghost";
      revoke.textContent = "Revoke";
      revoke.addEventListener("click", () => onRevoke(k));
      actions.appendChild(revoke);
    }
    const del = document.createElement("button");
    del.className = "btn btn--danger";
    del.textContent = "Delete";
    del.addEventListener("click", () => onDelete(k));
    actions.appendChild(del);

    keyList.appendChild(card);
  }
}

async function loadKeys() {
  const { keys } = await api.keys();
  renderKeys(keys);
}

async function onRevoke(k) {
  if (!confirm(`Revoke "${k.label || "this key"}"? Clients using it will stop working.`)) return;
  try { await api.revokeKey(k.id); toast("Key revoked"); loadKeys(); }
  catch (e) { toast(e.message); }
}

async function onDelete(k) {
  if (!confirm(`Delete "${k.label || "this key"}" permanently?`)) return;
  try { await api.deleteKey(k.id); toast("Key deleted"); loadKeys(); }
  catch (e) { toast(e.message); }
}

// ── Create key + reveal modal ─────────────────────────────────────────────────
const reveal      = document.getElementById("reveal");
const revealToken = document.getElementById("reveal-token");
const connectToken = document.getElementById("connect-token");

document.getElementById("create-key").addEventListener("click", async () => {
  const labelInput = document.getElementById("new-label");
  try {
    const key = await api.createKey(labelInput.value.trim());
    labelInput.value = "";
    revealToken.textContent = key.token;
    reveal.classList.add("show");
    connectToken.value = key.token;
    renderConfig();
    loadKeys();
  } catch (e) { toast(e.message); }
});

document.getElementById("copy-token").addEventListener("click", async () => {
  try { await navigator.clipboard.writeText(revealToken.textContent); toast("Copied to clipboard"); }
  catch { toast("Copy failed — select the text manually"); }
});

document.getElementById("reveal-done").addEventListener("click", () => reveal.classList.remove("show"));
reveal.addEventListener("click", (e) => { if (e.target === reveal) reveal.classList.remove("show"); });

// ── Connect a client ──────────────────────────────────────────────────────────
const MCP_URL = `${window.location.origin}/mcp`;
const TOKEN_PLACEHOLDER = "<YOUR_API_KEY>";

const CONFIGS = {
  claude: {
    path: ".mcp.json (project) or ~/.claude.json (global)",
    hint: 'Or run: claude mcp add --transport http quartz-sharepoint <url> --header "Authorization: Bearer <key>"',
    build: (url, token) => JSON.stringify({
      mcpServers: { "quartz-sharepoint": { type: "http", url, headers: { Authorization: `Bearer ${token}` } } },
    }, null, 2),
  },
  opencode: {
    path: "opencode.json",
    hint: "Place in your project root or ~/.config/opencode/opencode.json.",
    build: (url, token) => JSON.stringify({
      $schema: "https://opencode.ai/config.json",
      mcp: { "quartz-sharepoint": { type: "remote", url, enabled: true, headers: { Authorization: `Bearer ${token}` } } },
    }, null, 2),
  },
  cursor: {
    path: "~/.cursor/mcp.json (global) or .cursor/mcp.json (project)",
    hint: "Restart Cursor or hit refresh in Settings → MCP after saving.",
    build: (url, token) => JSON.stringify({
      mcpServers: { "quartz-sharepoint": { url, headers: { Authorization: `Bearer ${token}` } } },
    }, null, 2),
  },
  codex: {
    path: "~/.codex/config.toml",
    hint: "export QUARTZ_SP_TOKEN=<key> first, then reference it below.",
    build: (url, token) => [
      "[mcp_servers.quartz-sharepoint]",
      `url = "${url}"`,
      `bearer_token_env_var = "QUARTZ_SP_TOKEN"`,
      token === TOKEN_PLACEHOLDER ? "# export QUARTZ_SP_TOKEN=<YOUR_API_KEY>" : `# export QUARTZ_SP_TOKEN=${token}`,
    ].join("\n"),
  },
  antigravity: {
    path: "~/.gemini/config/mcp_config.json",
    hint: 'Antigravity uses "serverUrl" (not "url"). Hit refresh in Installed MCP Servers after saving.',
    build: (url, token) => JSON.stringify({
      mcpServers: { "quartz-sharepoint": { serverUrl: url, headers: { Authorization: `Bearer ${token}` } } },
    }, null, 2),
  },
};

const toolSelect  = document.getElementById("tool-select");
const configCode  = document.getElementById("config-code");
const configPath  = document.getElementById("config-path");
const configHint  = document.getElementById("config-hint");

function renderConfig() {
  const cfg = CONFIGS[toolSelect.value];
  const token = connectToken.value.trim() || TOKEN_PLACEHOLDER;
  configCode.textContent = cfg.build(MCP_URL, token);
  configPath.textContent = cfg.path;
  configHint.textContent = cfg.hint;
}

document.getElementById("mcp-url").textContent = MCP_URL;
toolSelect.addEventListener("change", renderConfig);
connectToken.addEventListener("input", renderConfig);
renderConfig();

async function copyText(text, okMsg) {
  try { await navigator.clipboard.writeText(text); toast(okMsg); }
  catch { toast("Copy failed — select the text manually"); }
}

document.getElementById("copy-url").addEventListener("click", () => copyText(MCP_URL, "URL copied"));
document.getElementById("copy-config").addEventListener("click", () => copyText(configCode.textContent, "Config copied"));

document.getElementById("logout").addEventListener("click", async () => {
  try { await api.logout(); } catch {}
  window.location.href = "/login.html";
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

(async function init() {
  try {
    const { username } = await api.me();
    document.getElementById("who").textContent = username;
    await Promise.all([loadServer(), loadKeys()]);
  } catch (e) {
    if (e.message !== "unauthorized") toast(e.message);
  }
})();
