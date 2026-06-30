# quartz-sharepoint-mcp

An MCP server that mirrors a SharePoint document library to disk and lets AI assistants search it via the [Model Context Protocol](https://modelcontextprotocol.io).

## How it works

1. On startup, all files under your configured SharePoint path are downloaded locally.
2. A background thread polls Microsoft Graph for changes every `POLL_INTERVAL` seconds, keeping the mirror in sync.
3. A single MCP tool — `search_sharepoint(query)` — runs `opencode` against the local files and returns the result.

```
SharePoint (Graph API) → local mirror → opencode → MCP client
```

## Prerequisites

- Python 3.11+
- [`opencode`](https://opencode.ai) in your `PATH`
- An Azure AD **App Registration** with `Sites.Read.All` (or equivalent) granted as an application permission

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Fill in your Azure and SharePoint credentials

# 3. Run
python server.py
```

The server starts on `http://0.0.0.0:8001` by default.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `AZURE_TENANT_ID` | ✓ | — | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | ✓ | — | App registration client ID |
| `AZURE_CLIENT_SECRET` | ✓ | — | App registration client secret |
| `SHAREPOINT_HOSTNAME` | ✓ | — | e.g. `yourorg.sharepoint.com` |
| `SHAREPOINT_SITE_PATH` | ✓ | — | e.g. `/sites/YourSite` |
| `SHAREPOINT_ROOT_PATH` | ✓ | — | Folder path within the site |
| `AUTH_TOKENS` | ✓ | — | Bootstrap tokens: `label:token,...` |
| `ADMIN_USERNAME` | ✓ | — | Admin panel username |
| `ADMIN_PASSWORD` | ✓ | — | Admin panel password |
| `ADMIN_JWT_SECRET` | ✓ | — | Secret for admin JWT signing |

## Endpoints

| Path | Description |
|---|---|
| `GET /` | Admin UI (token management) |
| `POST /mcp` | MCP streamable-HTTP endpoint (bearer auth required) |
| `/admin/api/*` | Admin REST API |

## MCP tool

**`search_sharepoint`** — accepts a natural-language query, runs it against the local mirror via `opencode`, and returns the answer as plain text.

To connect from an MCP client:

```json
{
  "mcpServers": {
    "sharepoint": {
      "url": "http://localhost:8001/mcp",
      "headers": { "Authorization": "Bearer <your-token>" }
    }
  }
}
```

## Admin panel

Visit `http://localhost:8001` to manage bearer tokens — create, enable/disable, or revoke access without restarting the server.
