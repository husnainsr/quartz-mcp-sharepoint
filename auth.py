from __future__ import annotations

from urllib.parse import parse_qs

from starlette.responses import JSONResponse

import db
from logx import log


class BearerTokenMiddleware:
    def __init__(self, app, tokens: dict[str, str] | None = None):
        self.app = app
        self.tokens = tokens or {}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        if not db.is_mcp_enabled():
            response = JSONResponse(
                {"error": "MCP server is currently disabled by the administrator"},
                status_code=503,
            )
            return await response(scope, receive, send)

        headers = dict(scope["headers"])
        auth_header = headers.get(b"authorization", b"").decode()
        token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""

        if not token:
            query = parse_qs(scope.get("query_string", b"").decode())
            token = query.get("token", [""])[0]

        label = self.tokens.get(token)
        if label is None and db.is_valid_token(token):
            label = "api-key"

        if label is None:
            response = JSONResponse(
                {"error": "Unauthorized — missing or invalid Bearer token"},
                status_code=401,
            )
            return await response(scope, receive, send)

        log(f"[quartz-v2] Authenticated request from '{label}'")
        return await self.app(scope, receive, send)
