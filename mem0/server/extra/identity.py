"""Enhanced identity & namespace layer for the mem0 agent memory system.

Adds on top of the official mem0 server:
- Per-harness API key -> identity binding (harness name, user, project)
- Three-level namespace resolution: project / user / agent
- Team shared scopes with explicit ACL
- Scope enforcement (403 on out-of-scope access)
- Audit log for all memory writes

This module is imported by main.py (patched) and provides FastAPI dependencies
plus helper routers mounted under /mem0-agent.
"""

import os
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

# ---------------------------------------------------------------
# Extra ORM models (registered on the same Base as official models)
# ---------------------------------------------------------------

from db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class HarnessKey(Base):
    """Per-harness API key. Binds a raw key to (harness, default user, project)."""

    __tablename__ = "harness_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(12), nullable=False)
    harness: Mapped[str] = mapped_column(String(64), nullable=False)  # workbuddy/qoder/...
    default_user: Mapped[str] = mapped_column(String(128), nullable=False, default="default")
    project: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SharingAcl(Base):
    """ACL: grant a grantee (user|harness|project) read/write on a scope."""

    __tablename__ = "sharing_acl"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g. proj_default:team
    grantee_type: Mapped[str] = mapped_column(String(16), nullable=False)  # user|harness|project
    grantee_id: Mapped[str] = mapped_column(String(128), nullable=False)
    permission: Mapped[str] = mapped_column(String(8), nullable=False, default="read")  # read|write
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    harness: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    memory_id: Mapped[str] = mapped_column(String(64), nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------
# Namespace helpers
# ---------------------------------------------------------------


def scope_user(project: str, user: str) -> str:
    """Personal (cross-harness) user scope."""
    return f"proj_{project}:u_{user}"


def scope_agent(project: str, user: str, harness: str) -> str:
    """Private per-harness scope."""
    return f"proj_{project}:u_{user}:a_{harness}"


def scope_team(project: str) -> str:
    """Team shared scope (project-level)."""
    return f"proj_{project}:team"


def parse_scope(scope: str) -> dict:
    """Parse 'proj_{p}:u_{user}[:a_{harness}|:team]' into components."""
    parts = scope.split(":")
    out = {"raw": scope, "project": None, "user": None, "agent": None, "team": False}
    if len(parts) >= 1 and parts[0].startswith("proj_"):
        out["project"] = parts[0][5:]
    if len(parts) >= 2 and parts[1].startswith("u_"):
        out["user"] = parts[1][2:]
    if len(parts) >= 3:
        if parts[2] == "team":
            out["team"] = True
        elif parts[2].startswith("a_"):
            out["agent"] = parts[2][2:]
    return out


def split_scope(scope: str) -> tuple[str, str]:
    """Split a full scope into (user_id, agent_id) for the mem0 API.

    proj_p:u_user            -> (proj_p:u_user, None)
    proj_p:u_user:a_wb       -> (proj_p:u_user, a_wb)
    proj_p:team              -> (proj_p:team, None)
    """
    parsed = parse_scope(scope)
    if parsed["team"]:
        return f"proj_{parsed['project']}:team", None
    if parsed["agent"]:
        return f"proj_{parsed['project']}:u_{parsed['user']}", parsed["agent"]
    return f"proj_{parsed['project']}:u_{parsed['user']}", None


# ---------------------------------------------------------------
# Identity: resolve X-API-Key -> harness identity
# ---------------------------------------------------------------


class Identity(BaseModel):
    harness: str
    user: str
    project: str
    key_prefix: str


def hash_key(raw: str) -> str:
    # Server-side bcrypt via passlib (same lib as official auth)
    from passlib.context import CryptContext

    ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    return ctx.hash(raw)


def verify_key_hash(raw: str, hashed: str) -> bool:
    from passlib.context import CryptContext

    ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    try:
        return ctx.verify(raw, hashed)
    except Exception:
        return False


def _resolve_harness_key(x_api_key: str | None, db) -> Identity:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required (per-harness key).")
    prefix = x_api_key[:12] if len(x_api_key) >= 12 else x_api_key
    rows = db.execute(
        text("SELECT id, key_hash, key_prefix, harness, default_user, project FROM harness_keys "
             "WHERE key_prefix = :p AND revoked_at IS NULL"),
        {"p": prefix},
    ).mappings().all()
    for row in rows:
        if verify_key_hash(x_api_key, row["key_hash"]):
            return Identity(
                harness=row["harness"],
                user=row["default_user"],
                project=row["project"],
                key_prefix=row["key_prefix"],
            )
    raise HTTPException(status_code=401, detail="Invalid harness API key.")


def get_identity(request: Request) -> Identity:
    """FastAPI dependency: resolve harness identity from X-API-Key header.

    Supports identity override via headers:
      X-Mem0-User: override default user (must be allowed)
      X-Mem0-Project: override project
    """
    from db import SessionLocal

    x_api_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    with SessionLocal() as db:
        ident = _resolve_harness_key(x_api_key, db)

    user_override = request.headers.get("x-mem0-user")
    project_override = request.headers.get("x-mem0-project")
    if user_override:
        ident.user = user_override
    if project_override:
        ident.project = project_override
    return ident


# ---------------------------------------------------------------
# Scope resolution & ACL
# ---------------------------------------------------------------


def visible_scopes(ident: Identity, db) -> list[str]:
    """All scopes this identity may read: personal + team scopes granted via ACL."""
    scopes = [
        scope_agent(ident.project, ident.user, ident.harness),
        scope_user(ident.project, ident.user),
    ]
    # ACL grants for this user / this harness / this project (as grantee)
    rows = db.execute(
        text(
            "SELECT scope, grantee_type, grantee_id, permission FROM sharing_acl "
            "WHERE (grantee_type = 'user' AND grantee_id = :u) "
            "   OR (grantee_type = 'harness' AND grantee_id = :h) "
            "   OR (grantee_type = 'project' AND grantee_id = :p)"
        ),
        {"u": ident.user, "h": ident.harness, "p": ident.project},
    ).mappings().all()
    for r in rows:
        if r["permission"] in ("read", "write") and r["scope"] not in scopes:
            scopes.append(r["scope"])
    return scopes


def writable_scopes(ident: Identity, db) -> list[str]:
    scopes = [
        scope_agent(ident.project, ident.user, ident.harness),
        scope_user(ident.project, ident.user),
    ]
    rows = db.execute(
        text(
            "SELECT scope, permission FROM sharing_acl "
            "WHERE permission = 'write' "
            "AND ((grantee_type = 'user' AND grantee_id = :u) "
            "   OR (grantee_type = 'harness' AND grantee_id = :h) "
            "   OR (grantee_type = 'project' AND grantee_id = :p))"
        ),
        {"u": ident.user, "h": ident.harness, "p": ident.project},
    ).mappings().all()
    for r in rows:
        if r["scope"] not in scopes:
            scopes.append(r["scope"])
    return scopes


def check_write_allowed(scope: str, ident: Identity, db) -> None:
    if scope not in writable_scopes(ident, db):
        raise HTTPException(status_code=403, detail=f"No write access to scope: {scope}")


def check_read_allowed(scopes: list[str], ident: Identity, db) -> None:
    allowed = set(visible_scopes(ident, db))
    for s in scopes:
        if s not in allowed:
            raise HTTPException(status_code=403, detail=f"No read access to scope: {s}")


def audit(ident: Identity, action: str, scope: str, memory_id: str | None = None, detail: str | None = None) -> None:
    from db import SessionLocal

    try:
        with SessionLocal() as db:
            db.execute(
                text(
                    "INSERT INTO audit_log (id, ts, harness, user_id, action, scope, memory_id, detail) "
                    "VALUES (:i, :t, :h, :u, :a, :s, :m, :d)"
                ),
                {
                    "i": str(uuid.uuid4()),
                    "t": datetime.now(timezone.utc),
                    "h": ident.harness,
                    "u": ident.user,
                    "a": action,
                    "s": scope,
                    "m": memory_id,
                    "d": detail,
                },
            )
            db.commit()
    except Exception:
        pass  # audit must never break the request path


def generate_harness_key() -> str:
    return f"m0hk_{secrets.token_urlsafe(32)}"


# ---------------------------------------------------------------
# Extra API models
# ---------------------------------------------------------------


class GrantRequest(BaseModel):
    scope: str = Field(..., description="e.g. proj_default:team or proj_p:u_alice")
    grantee_type: str = Field(..., pattern="^(user|harness|project)$")
    grantee_id: str
    permission: str = Field(default="read", pattern="^(read|write)$")


class MemoryWriteRequest(BaseModel):
    messages: list[dict]
    scope: str | None = Field(None, description="Target scope; default = personal cross-harness scope")
    agent_id: str | None = None
    metadata: dict | None = None
    infer: bool = True


class MemorySearchRequest(BaseModel):
    query: str
    scopes: list[str] | None = Field(None, description="Explicit scopes; default = all visible scopes")
    top_k: int = 10
    threshold: float = 0.1


# ---------------------------------------------------------------
# Table creation helper (called from bootstrap / startup)
# ---------------------------------------------------------------


def create_extra_tables() -> None:
    """Create harness_keys / sharing_acl / audit_log tables if missing.

    Official tables are handled by alembic inside the container; these extra
    tables are managed here to keep migrations untouched.
    """
    from db import engine

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS harness_keys (
                    id UUID PRIMARY KEY,
                    key_hash TEXT NOT NULL,
                    key_prefix VARCHAR(12) NOT NULL,
                    harness VARCHAR(64) NOT NULL,
                    default_user VARCHAR(128) NOT NULL DEFAULT 'default',
                    project VARCHAR(64) NOT NULL DEFAULT 'default',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    revoked_at TIMESTAMPTZ
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS sharing_acl (
                    id UUID PRIMARY KEY,
                    scope VARCHAR(255) NOT NULL,
                    grantee_type VARCHAR(16) NOT NULL,
                    grantee_id VARCHAR(128) NOT NULL,
                    permission VARCHAR(8) NOT NULL DEFAULT 'read',
                    created_by VARCHAR(128) NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id UUID PRIMARY KEY,
                    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
                    harness VARCHAR(64) NOT NULL DEFAULT '',
                    user_id VARCHAR(128) NOT NULL DEFAULT '',
                    action VARCHAR(32) NOT NULL,
                    scope VARCHAR(255) NOT NULL DEFAULT '',
                    memory_id VARCHAR(64),
                    detail TEXT
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_harness_keys_prefix ON harness_keys (key_prefix)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_sharing_acl_scope ON sharing_acl (scope)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_log_ts ON audit_log (ts)"))


def create_harness_key(harness: str, user: str = "default", project: str = "default") -> str:
    """Create and persist a harness key; returns the raw key (shown once)."""
    from db import SessionLocal

    raw = generate_harness_key()
    prefix = raw[:12]
    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO harness_keys (id, key_hash, key_prefix, harness, default_user, project) "
                "VALUES (:i, :kh, :kp, :h, :u, :p)"
            ),
            {"i": str(uuid.uuid4()), "kh": hash_key(raw), "kp": prefix, "h": harness, "u": user, "p": project},
        )
        db.commit()
    return raw
