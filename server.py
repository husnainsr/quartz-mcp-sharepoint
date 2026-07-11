"""
Quartz SharePoint v2 — single-process FastAPI app.

  /mcp        — MCP streamable-http endpoint (bearer-auth'd)
  /admin/api  — admin REST API (key management + on/off toggle)
  /           — static admin UI

One tool exposed via MCP:
  search_sharepoint(query) — runs opencode CLI against the local mirror
                             and returns its answer verbatim.

On startup a background thread runs the SharePoint mirror loop:
  - first run: downloads all files under SHAREPOINT_ROOT_PATH to local_files/
  - subsequent: polls Graph delta every POLL_INTERVAL seconds for changes
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
# Fall back to parent .env (when running from v2/ without its own .env)
if not os.environ.get("AZURE_TENANT_ID"):
    load_dotenv(Path(__file__).parent.parent / ".env")

import db
from admin.routes import router as admin_router
from auth import BearerTokenMiddleware
from logx import configure, log

configure()

from anyio import to_thread
from fastapi import FastAPI
from fastapi.responses import FileResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

FRONTEND_DIR = Path(__file__).parent / "frontend"

# ── Config ──────────────────────────────────────────────────────────────────────

MIRROR_DIR    = Path(os.environ.get("MIRROR_DIR", Path(__file__).parent / "local_files"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
OPENCODE_PATH = os.environ.get("OPENCODE_PATH", "opencode")
OPENCODE_MODEL = os.environ.get("OPENCODE_MODEL", "opencode/deepseek-v4-flash-free")
OPENCODE_TIMEOUT = int(os.environ.get("OPENCODE_TIMEOUT", "300"))

_AUTH_TOKENS_RAW = os.environ.get("AUTH_TOKENS", "")
_ALLOWED_HOSTS_RAW = os.environ.get("ALLOWED_HOSTS", "localhost:8001,localhost")
PORT = int(os.environ.get("PORT", "8001"))
HOST = os.environ.get("HOST", "0.0.0.0")


def _parse_tokens(raw: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if ":" in part:
            label, token = part.split(":", 1)
            tokens[token.strip()] = label.strip()
    return tokens


def _parse_hosts(raw: str) -> list[str]:
    return [h.strip() for h in raw.split(",") if h.strip()]


# ── MCP tool ────────────────────────────────────────────────────────────────────

_SEARCH_PROMPT = """\
<role>
You are a document research assistant for Quartz Project Services, a professional services firm. \
You have access to their entire SharePoint document library, which has been mirrored locally into \
the current working directory. Your job is to find and read the right files and return a clear, \
accurate answer to the user's query.
</role>

<constraints>
## Absolute Rules — Never Violate These

- **Read-only mode:** You are running in STRICT READ-ONLY plan mode. You have ZERO write \
permissions anywhere on the filesystem. Do NOT copy, move, extract, or create files anywhere — \
including /tmp, /var, or any subdirectory.
- **No disk-writing tools:** Do NOT use pandoc, unpack scripts, or any tool that writes to disk. \
Read files directly from their original path in the current directory using the appropriate skill.
- **No delegation:** Do NOT spawn sub-agents, delegate tasks, or run arbitrary code.
- **No exposure:** Never reveal or mention the local mirror directory path to the user.
</constraints>

<instructions>

## Step 1 — Extract Keywords

Before touching any file, decompose the query into 3–6 keywords and their realistic variants:

- **Partial stems** — e.g. "manage" → `manag*` covers manager, managing, management
- **Proper nouns** — names, cities, project names: try both exact spelling and likely misspellings
- **Document-type hints** — words like team, register, schedule, minutes, report, directory, contact

**Example**
Query: `"find about Steve Dougthy in managing team in London"`
Keywords: `Steve`, `Dougth*`, `manag*`, `team*`, `london*`, `director*`, `contact*`

---

## Step 2 — Search Files with Grep

Use `grep` with your keywords to find candidate files quickly. Always use `-i` (case-insensitive) \
and `-l` (filenames only — do not dump file contents). Run multiple greps in parallel, one per \
keyword or keyword group.

**Do NOT glob the entire directory tree and read every file. Grep first, read second.**

**Example greps for the query above:**
```
grep -ri "steve" . --include="*.docx" --include="*.xlsx" --include="*.pdf" -l
grep -ri "dougth" . -l
grep -ri "london" . --include="*.docx" --include="*.xlsx" -l
grep -ri "manag" . --include="*.docx" -l
```

After running your greps, pick the **1–3 most relevant files** from the combined results.

---

## Step 3 — Read Immediately, Answer Fast

> **Critical rule:** As soon as you identify even one promising file — read it immediately. \
Do NOT queue up additional searches before reading. Speed is essential.

- Read the first relevant file right away.
- If it answers the query → **stop searching and respond**.
- If it does not contain the answer → continue to the next candidate.

**How to read non-plain-text files — use the correct skill, no exceptions:**

| Extension | Skill to use |
|-----------|--------------|
| `.docx`   | `docx` skill |
| `.xlsx`   | `xlsx` skill |
| `.pdf`    | `pdf` skill  |
| `.pptx`   | `pptx` skill |

If a file cannot be read for any reason (DRM, permission error, corrupt file), \
**skip it immediately** and move to the next candidate. Do not attempt any workaround.

---

## Step 4 — Respond Based on Intent

Determine what the user is asking and respond accordingly:

- **Find / Locate** — Return the file path(s) with a one-sentence description of each.
- **Read / Summarise / Q&A** — Provide a thorough, well-structured answer drawn from the file contents.

**Path formatting rules:**
- Strip any leading `local_files` segment from every path you return.
- Example: `local_files/Training/file.docx` → `Training/file.docx`
- Paths must always start from the SharePoint folder name, never from `local_files`.

Always end your response with a source line listing every file you read:
`Source: <filename(s)>`

</instructions>

<output_format>
When you are ready to deliver your final answer, output exactly this marker on its own line, \
followed immediately by your answer — nothing else after it:

FINAL_ANSWER:
<your answer here>
</output_format>

<query>
{query}
</query>
"""

mcp = FastMCP(
    "quartz-sharepoint",
    instructions=(
        "This server gives you access to the Quartz Project Services SharePoint library. "
        "Use the search_sharepoint tool for ANY query about SharePoint files — whether you "
        "need to find a file, read its contents, summarise it, or answer questions about it. "
        "Never tell the user you cannot read a file format; call the tool with the query and "
        "it will handle reading .pdf, .docx, .xlsx, and .pptx files automatically. "
        "Think of this tool as delegating to another agent — pass the user's query exactly as "
        "they said it, in full natural language, so the agent understands the full intent. "
        "NEVER simplify the query into keywords or fragments. "
        "Example: if the user asks 'who is the managing director in the London office?', "
        "pass exactly that — NOT 'managing director London' or 'london director name'."
    ),
)


@mcp.tool()
async def search_sharepoint(query: str) -> str:
    """
    Query the SharePoint document library. Handles both finding files and reading their contents.

    Treat this as delegating to another agent — pass the user's query exactly as they said it,
    as a full natural language sentence. NEVER reduce it to keywords or fragments.
    Good:  query="who is the managing director in the London office?"
    Bad:   query="managing director London"

    Use this tool for ANY of the following:
    - Locating a file:   query="find the JCT D&B notice pack template"
    - Reading a file:    query="what's in the risk register pdf?"
    - Summarising:       query="summarise the latest meeting minutes"
    - Q&A over content:  query="what are the action items from the Canmoor minutes?"

    The tool can read .pdf, .docx, .xlsx, and .pptx files — never assume you cannot
    access a file's contents. Always call this tool and let it handle the file.
    """
    return await to_thread.run_sync(
        lambda: _run_search(query), abandon_on_cancel=True
    )


_LOG_DIR = Path(__file__).parent / "logs"


def _write_opencode_log(query: str, stdout: str, stderr: str) -> None:
    try:
        _LOG_DIR.mkdir(exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        slug = "".join(c if c.isalnum() else "_" for c in query[:50]).strip("_")
        log_file = _LOG_DIR / f"{ts}_{slug}.txt"
        with log_file.open("w", encoding="utf-8") as f:
            f.write(f"Query: {query}\n")
            f.write(f"Time:  {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("\n── STDOUT ──────────────────────────────────────────────────────────────\n")
            f.write(stdout or "(empty)")
            f.write("\n\n── STDERR ──────────────────────────────────────────────────────────────\n")
            f.write(stderr or "(empty)")
        log(f"[opencode] log saved → logs/{log_file.name}")
    except Exception as e:
        log(f"[opencode] failed to write log: {e}")


def _run_search(query: str) -> str:
    started = time.monotonic()
    text, status = _do_search(query)
    duration_ms = int((time.monotonic() - started) * 1000)
    try:
        db.log_query(query, text, status, duration_ms)
    except Exception as e:
        log(f"[opencode] failed to log query to db: {e}")
    return text


def _do_search(query: str) -> tuple[str, str]:
    if not MIRROR_DIR.exists() or not any(MIRROR_DIR.iterdir()):
        return (
            "The local SharePoint mirror is empty or not yet downloaded. "
            "Wait for the initial sync to complete and try again."
        ), "no-mirror"

    prompt = _SEARCH_PROMPT.format(query=query)

    try:
        result = subprocess.run(
            [OPENCODE_PATH, "--agent", "plan", "--model", OPENCODE_MODEL, "run", prompt],
            cwd=str(MIRROR_DIR),
            capture_output=True,
            text=True,
            timeout=OPENCODE_TIMEOUT,
        )
        output = result.stdout.strip()
        stderr = result.stderr.strip()
        _write_opencode_log(query, output, stderr)
        if not output and stderr:
            output = stderr
        marker = "FINAL_ANSWER:"
        if marker in output:
            output = output.split(marker, 1)[1].strip()
        if output:
            return output, "ok"
        return "opencode returned no output.", "empty"
    except FileNotFoundError:
        return (
            f"opencode not found at '{OPENCODE_PATH}'. "
            "Install it or set OPENCODE_PATH in your .env."
        ), "error"
    except subprocess.TimeoutExpired:
        return f"Search timed out after {OPENCODE_TIMEOUT}s. Try a more specific query.", "timeout"
    except Exception as e:
        return f"Search error: {e}", "error"


# ── Mirror background thread ─────────────────────────────────────────────────────

_MIRROR_STARTED = False


def start_mirror() -> None:
    global _MIRROR_STARTED
    if _MIRROR_STARTED:
        return
    _MIRROR_STARTED = True

    def _loop() -> None:
        sys.path.insert(0, str(Path(__file__).parent))
        import mirror as _mirror
        # Override mirror's paths with values from our env (critical in Docker
        # where MIRROR_DIR=/data/local_files differs from mirror.py's default).
        _mirror.MIRROR_DIR = MIRROR_DIR
        _mirror.STATE_FILE = MIRROR_DIR.parent / ".mirror_state.json"

        from mirror import (
            _apply_delta, _delta, _full_download,
            _list_folder_recursive, _load_state, _save_state,
        )

        MIRROR_DIR.mkdir(parents=True, exist_ok=True)
        state = _load_state()
        delta_link: str | None = state.get("delta_link")
        id_map: dict = dict(state.get("id_map", {}))

        if delta_link is None:
            log("[mirror] Starting full download ...")
            try:
                items = _list_folder_recursive()
                _full_download(items, id_map)
                log("[mirror] Full download complete — establishing delta baseline ...")
                _, delta_link = _delta(None)
                _save_state(delta_link, id_map)
                log(f"[mirror] Baseline established. Polling every {POLL_INTERVAL}s")
            except Exception as e:
                log(f"[mirror] Initial download failed: {e}")
                return
        else:
            log(f"[mirror] Resuming from saved state. Polling every {POLL_INTERVAL}s")

        while True:
            time.sleep(POLL_INTERVAL)
            try:
                items, new_link = _delta(delta_link)
                if items:
                    _apply_delta(items, id_map)
                else:
                    log(f"[mirror] No changes ({time.strftime('%H:%M:%S')})")
                delta_link = new_link
                _save_state(delta_link, id_map)
            except Exception as e:
                log(f"[mirror] Poll error: {e} — retrying next cycle")

    threading.Thread(target=_loop, daemon=True, name="mirror").start()
    log("[mirror] Background sync thread started")


# ── FastAPI app ──────────────────────────────────────────────────────────────────

class _NormalizeMcpPath:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path") == "/mcp":
            scope = dict(scope)
            scope["path"] = "/mcp/"
            scope["raw_path"] = b"/mcp/"
        return await self.app(scope, receive, send)


_STATIC = {
    "/": "index.html",
    "/index.html": "index.html",
    "/login.html": "login.html",
    "/logs.html": "logs.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
    "/logs.js": "logs.js",
}


def build_app() -> _NormalizeMcpPath:
    db.init_db()
    start_mirror()

    hosts = _parse_hosts(_ALLOWED_HOSTS_RAW)
    # Always include localhost variants so local dev works without env config
    for _h in ["localhost", "localhost:8001", "127.0.0.1", "127.0.0.1:8001"]:
        if _h not in hosts:
            hosts.append(_h)
    origins = [f"https://{h}" for h in hosts] + [f"http://{h}" for h in hosts]
    mcp.settings.transport_security = TransportSecuritySettings(
        allowed_hosts=hosts, allowed_origins=origins
    )
    mcp.settings.streamable_http_path = "/"
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # noqa: ARG001
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = FastAPI(title="Quartz SharePoint v2", lifespan=lifespan)

    def _static(filename: str):
        async def handler():
            return FileResponse(FRONTEND_DIR / filename)
        return handler

    for url, filename in _STATIC.items():
        app.add_api_route(url, _static(filename), methods=["GET"], include_in_schema=False)

    app.include_router(admin_router)

    tokens = _parse_tokens(_AUTH_TOKENS_RAW)
    app.mount("/mcp", BearerTokenMiddleware(mcp_app, tokens))

    log(
        f"[quartz-v2] App ready — /mcp (auth), /admin/api, static UI "
        f"({len(tokens)} bootstrap token(s))"
    )
    return _NormalizeMcpPath(app)


if __name__ == "__main__":
    import uvicorn
    app = build_app()
    log(f"[quartz-v2] HTTP on {HOST}:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
