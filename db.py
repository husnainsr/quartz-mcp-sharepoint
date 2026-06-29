from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("ADMIN_DB_PATH", Path(__file__).parent / "admin.db"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                token      TEXT    NOT NULL UNIQUE,
                label      TEXT    NOT NULL DEFAULT '',
                created_at TEXT    NOT NULL,
                revoked    INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('mcp_enabled', '1')"
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_keys() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, token, label, created_at, revoked FROM api_keys ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def create_key(label: str = "") -> dict:
    token = "qz_" + secrets.token_urlsafe(32)
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO api_keys (token, label, created_at) VALUES (?, ?, ?)",
            (token, label.strip(), _now()),
        )
        key_id = cur.lastrowid
    return {"id": key_id, "token": token, "label": label.strip(), "revoked": 0}


def revoke_key(key_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("UPDATE api_keys SET revoked = 1 WHERE id = ?", (key_id,))
    return cur.rowcount > 0


def delete_key(key_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))
    return cur.rowcount > 0


def is_valid_token(token: str) -> bool:
    if not token:
        return False
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM api_keys WHERE token = ? AND revoked = 0", (token,)
        ).fetchone()
    return row is not None


def get_setting(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def is_mcp_enabled() -> bool:
    return get_setting("mcp_enabled", "1") == "1"


def set_mcp_enabled(enabled: bool) -> None:
    set_setting("mcp_enabled", "1" if enabled else "0")
