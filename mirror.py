"""
SharePoint → local mirror.

First run: downloads every file under SHAREPOINT_ROOT_PATH into ./local_files/
            in parallel (default 16 workers) with a live progress bar.
Subsequent runs (every POLL_INTERVAL seconds): uses Graph delta to detect
additions, modifications, and deletions — applies them to the local copy.

Usage:
    python v2/mirror.py

Reads the same .env as the rest of the project. Requires:
    AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET
    SHAREPOINT_HOSTNAME, SHAREPOINT_SITE_PATH, SHAREPOINT_ROOT_PATH

Optional:
    MIRROR_DIR          — where to save files (default: v2/local_files)
    POLL_INTERVAL       — seconds between delta polls (default: 60)
    DOWNLOAD_WORKERS    — parallel download threads for initial sync (default: 16)
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TENANT_ID      = os.environ["AZURE_TENANT_ID"]
CLIENT_ID      = os.environ["AZURE_CLIENT_ID"]
CLIENT_SECRET  = os.environ["AZURE_CLIENT_SECRET"]
HOSTNAME       = os.environ["SHAREPOINT_HOSTNAME"]
SITE_PATH      = os.environ["SHAREPOINT_SITE_PATH"]
ROOT_PATH      = os.environ["SHAREPOINT_ROOT_PATH"].strip("/")

MIRROR_DIR     = Path(os.environ.get("MIRROR_DIR", Path(__file__).parent / "local_files"))
POLL_INTERVAL  = int(os.environ.get("POLL_INTERVAL", "60"))
DL_WORKERS     = int(os.environ.get("DOWNLOAD_WORKERS", "16"))
STATE_FILE     = Path(__file__).parent / ".mirror_state.json"
GRAPH_BASE     = "https://graph.microsoft.com/v1.0"
HTTP_TIMEOUT   = (10, 120)

# ---------------------------------------------------------------------------
# Auth  (thread-safe: lock protects token refresh)
# ---------------------------------------------------------------------------

_token: str | None = None
_token_expiry: float = 0
_token_lock = threading.Lock()


def _get_token() -> str:
    global _token, _token_expiry
    with _token_lock:
        if _token and time.time() < _token_expiry - 60:
            return _token  # type: ignore[return-value]
        resp = requests.post(
            f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        _token = data["access_token"]
        _token_expiry = time.time() + data.get("expires_in", 3600)
        return _token  # type: ignore[return-value]


def _headers() -> dict:
    return {"Authorization": f"Bearer {_get_token()}"}


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

_site_id: str | None = None


def _get_site_id() -> str:
    global _site_id
    if _site_id is None:
        resp = requests.get(
            f"{GRAPH_BASE}/sites/{HOSTNAME}:{SITE_PATH}",
            headers=_headers(), timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        _site_id = resp.json()["id"]
    return _site_id  # type: ignore[return-value]


def _delta(link: str | None = None) -> tuple[list[dict], str]:
    """Fetch delta feed. Returns (items, delta_link)."""
    site_id = _get_site_id()
    url: str | None = link or f"{GRAPH_BASE}/sites/{site_id}/drive/root/delta"
    items: list[dict] = []
    delta_link: str = link or ""
    while url:
        resp = requests.get(url, headers=_headers(), timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        if "@odata.deltaLink" in data:
            delta_link = data["@odata.deltaLink"]
    return items, delta_link


def _list_folder_recursive(rel_path: str = "") -> list[dict]:
    """List all files under rel_path (scoped to our root — fast, no full-drive scan)."""
    from urllib.parse import quote
    site_id = _get_site_id()
    full = f"{ROOT_PATH}/{rel_path}".strip("/") if rel_path else ROOT_PATH
    encoded = quote(full)
    url: str | None = f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{encoded}:/children"
    all_items: list[dict] = []
    while url:
        resp = requests.get(url, headers=_headers(), timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        all_items.extend(data.get("value", []))
        url = data.get("@odata.nextLink")

    result: list[dict] = []
    subdirs: list[str] = []
    for item in all_items:
        if "folder" in item:
            child_rel = f"{rel_path}/{item['name']}".lstrip("/") if rel_path else item["name"]
            subdirs.append(child_rel)
            result.append(item)
        else:
            result.append(item)

    # recurse into subdirectories in parallel
    if subdirs:
        with ThreadPoolExecutor(max_workers=min(len(subdirs), 8)) as pool:
            futures = {pool.submit(_list_folder_recursive, d): d for d in subdirs}
            for f in as_completed(futures):
                try:
                    result.extend(f.result())
                except Exception as e:
                    print(f"  ✗ list error in '{futures[f]}': {e}", file=sys.stderr)

    return result


def _download_url(item: dict) -> str | None:
    return item.get("@microsoft.graph.downloadUrl") or item.get("downloadUrl")


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _item_rel_path(item: dict) -> str | None:
    """Return path relative to ROOT_PATH, or None if outside it."""
    name = item.get("name", "")
    parent_ref = item.get("parentReference", {}).get("path", "")
    marker = "root:"
    if marker not in parent_ref:
        return None
    after_root = parent_ref.split(marker, 1)[1].lstrip("/")
    if after_root != ROOT_PATH and not after_root.startswith(ROOT_PATH + "/"):
        return None
    rel_parent = after_root[len(ROOT_PATH):].lstrip("/")
    return f"{rel_parent}/{name}".lstrip("/") if rel_parent else name


def _local_path(rel: str) -> Path:
    return MIRROR_DIR / rel


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"delta_link": None, "id_map": {}}


def _save_state(delta_link: str, id_map: dict) -> None:
    STATE_FILE.write_text(json.dumps({"delta_link": delta_link, "id_map": id_map}, indent=2))


# ---------------------------------------------------------------------------
# Progress bar (no external deps)
# ---------------------------------------------------------------------------

_COL = 110  # max display width per line


def _fmt_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024**2:.1f} MB"
    return f"{size_bytes / 1024**3:.2f} GB"


def _trunc(s: str, width: int) -> str:
    return s if len(s) <= width else s[:width - 3] + "..."


_MAX_VISIBLE_SLOTS = 8


class _Progress:
    """
    Live multi-worker display — 8 visible worker rows + 1 bar row.

    Cursor invariant: after start() and after every _redraw(), the cursor sits
    on the line immediately below the panel. move-up distance is always
    (_panel_lines + 1) to account for the extra newline print() appends.
    """

    def __init__(self, total: int, n_workers: int) -> None:
        self.total = total
        self.done = 0
        self.errors = 0
        self.skipped = 0
        self._lock = threading.Lock()
        self._start = time.time()
        self._bar_width = 40
        self._visible = min(n_workers, _MAX_VISIBLE_SLOTS)
        self._slots: list[tuple[str, int] | None] = [None] * self._visible
        self._thread_slot: dict[int, int] = {}
        self._free_slots: list[int] = list(range(self._visible))
        # panel_lines = visible worker rows + 1 bar row
        self._panel_lines = self._visible + 1
        self._last_redraw: float = 0.0
        self._redraw_interval = 0.08

    # -- slot management -----------------------------------------------------

    def claim_slot(self, name: str, size: int) -> None:
        with self._lock:
            idx = self._free_slots.pop(0) if self._free_slots else 0
            slot_idx = idx % self._visible
            self._thread_slot[threading.get_ident()] = slot_idx
            self._slots[slot_idx] = (name, size)
            self._redraw()

    def release_slot(self, error: bool = False, skipped: bool = False) -> None:
        with self._lock:
            self.done += 1
            if error:
                self.errors += 1
            if skipped:
                self.skipped += 1
            idx = self._thread_slot.pop(threading.get_ident(), None)
            if idx is not None:
                self._slots[idx] = None
                if idx not in self._free_slots:
                    self._free_slots.append(idx)
                    self._free_slots.sort()
            self._redraw(force=self.done == self.total)

    # -- rendering -----------------------------------------------------------

    def _bar_line(self) -> str:
        pct = self.done / self.total if self.total else 1.0
        filled = int(self._bar_width * pct)
        bar = "█" * filled + "░" * (self._bar_width - filled)
        elapsed = time.time() - self._start
        speed = self.done / elapsed if elapsed > 0 else 0
        eta = (self.total - self.done) / speed if speed > 0 else 0
        err_str = f"  ✗ {self.errors}" if self.errors else ""
        return (
            f"  [{bar}] {self.done}/{self.total}"
            f"  {pct*100:.1f}%"
            f"  {speed:.1f} files/s"
            f"  ETA {eta:.0f}s{err_str}"
        )

    def _redraw(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_redraw < self._redraw_interval:
            return
        self._last_redraw = now

        lines: list[str] = []
        for i, slot in enumerate(self._slots):
            if slot:
                name, size = slot
                label = f"  [{i+1:>2}] {name}"
                size_str = f"({_fmt_size(size)})"
                gap = max(_COL - len(label) - len(size_str) - 1, 1)
                row = f"{label}{' ' * gap}{size_str}"
            else:
                row = f"  [{i+1:>2}] —"
            lines.append(_trunc(row, _COL).ljust(_COL))
        lines.append(self._bar_line().ljust(_COL))

        # Cursor is on the line below the panel.
        # Move up (panel_lines + 1): +1 because print() will add a trailing \n
        # that lands us back one line below the panel for the next redraw.
        move_up = f"\x1b[{self._panel_lines + 1}A\r"
        print(move_up + "\n".join(lines), flush=True)

    def finish(self) -> None:
        elapsed = time.time() - self._start
        downloaded = self.done - self.skipped - self.errors
        blank = " " * _COL
        move_up = f"\x1b[{self._panel_lines + 1}A\r"
        clear = ("\n" + blank + "\r") * self._panel_lines
        summary = (
            f"  Done — {downloaded} downloaded  {self.skipped} skipped"
            f"  in {elapsed:.1f}s  ({self.done / max(elapsed, 0.001):.1f} files/s)"
            + (f"  ✗ {self.errors} errors" if self.errors else "")
        )
        print(f"{move_up}{blank}{clear}{summary}", flush=True)

    def start(self) -> None:
        """Print blank panel + one trailing newline — establishes cursor invariant."""
        blank = " " * _COL
        # _panel_lines blank rows; print() adds the trailing \n = cursor one below panel
        print("\n".join([blank] * self._panel_lines), flush=True)


# ---------------------------------------------------------------------------
# Download / delete
# ---------------------------------------------------------------------------

def _download_item(item: dict, rel: str) -> None:
    url = _download_url(item)
    if not url:
        site_id = _get_site_id()
        item_id = item["id"]
        url = f"{GRAPH_BASE}/sites/{site_id}/drive/items/{item_id}/content"

    local = _local_path(rel)
    local.parent.mkdir(parents=True, exist_ok=True)

    resp = requests.get(url, headers=_headers(), timeout=HTTP_TIMEOUT, stream=True)
    resp.raise_for_status()
    with local.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 17):  # 128 KB
            f.write(chunk)


def _delete_local(rel: str) -> None:
    import shutil
    local = _local_path(rel)
    if not local.exists():
        return
    if local.is_dir():
        shutil.rmtree(local)
    else:
        local.unlink()
        try:
            local.parent.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Freshness check
# ---------------------------------------------------------------------------

def _is_current(item: dict, rel: str) -> bool:
    """True if the local file exists and is at least as new as SharePoint's copy."""
    local = _local_path(rel)
    if not local.exists():
        return False
    sp_modified = item.get("lastModifiedDateTime")
    if not sp_modified:
        return False
    import datetime
    sp_ts = datetime.datetime.fromisoformat(sp_modified.replace("Z", "+00:00")).timestamp()
    local_ts = local.stat().st_mtime
    return local_ts >= sp_ts


# ---------------------------------------------------------------------------
# Initial full download  (parallel)
# ---------------------------------------------------------------------------

def _full_download(items: list[dict], id_map: dict) -> None:
    files = [i for i in items if "folder" not in i and "deleted" not in i]

    # Build id_map for folders first so future delta events can resolve paths
    for item in items:
        if "folder" in item:
            rel = _item_rel_path(item)
            iid = item.get("id")
            if rel and iid:
                id_map[iid] = rel

    total = len(files)
    print(f"\n  Fetched file list — {total} files to download ({DL_WORKERS} parallel workers)")

    progress = _Progress(total, n_workers=DL_WORKERS)
    progress.start()
    errors: list[str] = []

    def _worker(item: dict) -> None:
        rel = _item_rel_path(item)
        iid = item.get("id")
        size = item.get("size", 0)
        name = rel or item.get("name", "?")
        if iid and rel:
            id_map[iid] = rel
        progress.claim_slot(name=name, size=size)
        if rel is None:
            progress.release_slot()
            return
        if _is_current(item, rel):
            progress.release_slot(skipped=True)
            return
        try:
            _download_item(item, rel)
            progress.release_slot()
        except Exception as e:
            errors.append(f"{rel}: {e}")
            progress.release_slot(error=True)

    with ThreadPoolExecutor(max_workers=DL_WORKERS) as pool:
        futures = [pool.submit(_worker, item) for item in files]
        for _ in as_completed(futures):
            pass

    progress.finish()

    if errors:
        print(f"\n  Errors ({len(errors)}):", file=sys.stderr)
        for err in errors:
            print(f"    ✗ {err}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Delta apply
# ---------------------------------------------------------------------------

def _apply_delta(items: list[dict], id_map: dict) -> None:
    added = modified = deleted = 0
    for item in items:
        iid = item.get("id")

        if "deleted" in item:
            former = id_map.pop(iid, None) if iid else None
            if former:
                _delete_local(former)
                deleted += 1
                print(f"  ✗ DELETED   {former}")
            continue

        if "folder" in item:
            rel = _item_rel_path(item)
            if rel and iid:
                id_map[iid] = rel
            continue

        rel = _item_rel_path(item)
        if rel is None:
            continue
        if iid:
            is_new = iid not in id_map
            id_map[iid] = rel
        else:
            is_new = not _local_path(rel).exists()

        try:
            _download_item(item, rel)
            if is_new:
                added += 1
                print(f"  + ADDED     {rel}")
            else:
                modified += 1
                print(f"  ~ MODIFIED  {rel}")
        except Exception as e:
            print(f"  ✗ ERROR     {rel}: {e}", file=sys.stderr)

    if added + modified + deleted:
        print(f"\n  Cycle done — +{added} added  ~{modified} modified  -{deleted} deleted\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    delta_link: str | None = state.get("delta_link")
    id_map: dict = dict(state.get("id_map", {}))

    if delta_link is None:
        print("\n  No state found — listing files under your root folder ...")
        items = _list_folder_recursive()
        _full_download(items, id_map)
        # Establish delta baseline AFTER download (fast: just saves the token, no re-scan of content)
        print("\n  Establishing delta baseline for polling ...")
        _, delta_link = _delta(None)
        _save_state(delta_link, id_map)
        print(f"\n  Polling for changes every {POLL_INTERVAL}s  (Ctrl+C to stop)\n")
    else:
        print(f"\n  Resuming from saved state. Polling every {POLL_INTERVAL}s  (Ctrl+C to stop)\n")

    while True:
        time.sleep(POLL_INTERVAL)
        try:
            items, new_link = _delta(delta_link)
            if items:
                _apply_delta(items, id_map)
            else:
                print(f"  ✓ No changes  ({time.strftime('%H:%M:%S')})")
            delta_link = new_link
            _save_state(delta_link, id_map)
        except KeyboardInterrupt:
            print("\n  Stopped.")
            sys.exit(0)
        except Exception as e:
            print(f"  ✗ Poll error: {e} — retrying next cycle", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Stopped.")
        sys.exit(0)
