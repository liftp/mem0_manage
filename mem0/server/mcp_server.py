"""
mem0 MCP thin-forwarding layer (standalone service).

Exposes the mem0 agent-memory operations as MCP tools over Streamable HTTP, so a
harness that only speaks MCP (e.g. WorkBuddy via mcp-remote) can read/write the
shared memory. Each tool forwards to the existing /mem0-agent/* REST routes of the
mem0 server, carrying the caller's harness key.

Auth model:
  The inbound `Authorization: Bearer <key>` (sent by mcp-remote from the
  `mem0-global` config) is captured and forwarded as the `X-API-Key` that the REST
  layer expects. Falls back to MEM0_HARNESS_KEY env if no bearer is present.
"""
from __future__ import annotations

import contextvars
import os
from typing import Any

import httpx
from fastmcp import FastMCP
from starlette.middleware import Middleware

_bearer_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mcp_bearer_token", default=None
)


class BearerCaptureMiddleware:
    """Capture `Authorization: Bearer <key>` so tools can forward it as X-API-Key."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            for k, v in scope.get("headers") or []:
                if k.lower() == b"authorization":
                    val = v.decode("latin-1", "ignore")
                    if val.lower().startswith("bearer "):
                        _bearer_token.set(val[7:].strip())
                    break
        await self.app(scope, receive, send)


def _key() -> str:
    tok = _bearer_token.get()
    if tok:
        return tok
    if os.environ.get("MEM0_HARNESS_KEY"):
        return os.environ["MEM0_HARNESS_KEY"]
    # fallback: read workbuddy key from a local harness-keys.txt
    for cand in (
        "/usr/local/soft/mem0/harness-keys.txt",
        os.path.expanduser("~/harness-keys.txt"),
    ):
        try:
            with open(cand) as f:
                for line in f:
                    if line.startswith("workbuddy="):
                        return line.strip().split("=", 1)[1].strip()
        except OSError:
            pass
    raise RuntimeError("No harness key available for MCP forwarding")


def _base() -> str:
    return os.environ.get("MEM0_BASE_URL", "http://mem0:8000").rstrip("/")


# Internal-only traffic: the MCP container lives on the same Docker network as mem0 and
# never needs external egress (the Dockerfile bakes HTTP_PROXY only for build-time pip).
# We therefore disable environment proxy discovery so calls to http://mem0:8000 are NOT
# routed through the WSL-NAT proxy, which cannot resolve the in-cluster hostname and would
# otherwise return 502 for every request (the exact failure seen earlier).
_client = httpx.Client(trust_env=False, timeout=60)


def _post(path: str, payload: dict) -> dict:
    resp = _client.post(
        f"{_base()}{path}",
        json=payload,
        headers={"X-API-Key": _key(), "Content-Type": "application/json"},
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"mem0 upstream {path} -> {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


def _get(path: str) -> dict:
    resp = _client.get(f"{_base()}{path}", headers={"X-API-Key": _key()})
    if resp.status_code >= 400:
        raise RuntimeError(f"mem0 upstream {path} -> {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}


mcp = FastMCP("mem0-agent-memory")


@mcp.tool()
def add_memory(content: str, infer: bool = True, scope: str = "") -> dict:
    """Write a memory. `content` is the raw statement to remember.
    `infer=True` lets the LLM extract structured facts (needs LLM key); False stores verbatim.
    `scope` optionally pins a namespace (e.g. 'proj_default:u_default:a_workbuddy');
    omit to use the personal cross-harness scope (visible to other harnesses)."""
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": content}],
        "infer": infer,
    }
    if scope:
        payload["scope"] = scope
    return _post("/mem0-agent/memories", payload)


@mcp.tool()
def search_memory(query: str, top_k: int = 5, threshold: float = 0.0, scopes: str = "") -> dict:
    """Search memories across visible scopes. `query` is the natural-language question.
    `scopes` is an optional comma-separated list to restrict; omit to search everything visible."""
    payload: dict[str, Any] = {"query": query, "top_k": top_k, "threshold": threshold}
    if scopes:
        payload["scopes"] = [s.strip() for s in scopes.split(",") if s.strip()]
    return _post("/mem0-agent/search", payload)


@mcp.tool()
def list_scopes() -> dict:
    """List the memory scopes this harness/key can see."""
    return _get("/mem0-agent/scopes")


@mcp.tool()
def grant_sharing(scope: str, grantee_type: str, grantee_id: str, permission: str = "write") -> dict:
    """Grant another user/harness/project read or write access to a scope.
    grantee_type: 'user' | 'harness' | 'project'. permission: 'read' | 'write'."""
    payload = {
        "scope": scope,
        "grantee_type": grantee_type,
        "grantee_id": grantee_id,
        "permission": permission,
    }
    return _post("/mem0-agent/sharing/grant", payload)


def main() -> None:
    import uvicorn

    app = mcp.http_app(
        path="/mcp",
        transport="streamable-http",
        middleware=[Middleware(BearerCaptureMiddleware)],
        host_origin_protection=None,
    )
    port = int(os.environ.get("MCP_PORT", "8001"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
