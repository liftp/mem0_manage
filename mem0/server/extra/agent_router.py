"""Extra API router for the mem0 agent memory system.

Mounted at /mem0-agent by the patched main.py. Provides:
- POST /mem0-agent/memories      write memory to a scope
- POST /mem0-agent/search        search across visible scopes (merged)
- GET  /mem0-agent/scopes        list visible scopes
- POST /mem0-agent/sharing/grant grant ACL
- POST /mem0-agent/sharing/revoke
- GET  /mem0-agent/sharing       list grants for caller
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from extra.identity import (
    Identity,
    MemorySearchRequest,
    MemoryWriteRequest,
    GrantRequest,
    audit,
    check_read_allowed,
    check_write_allowed,
    get_identity,
    scope_agent,
    scope_user,
    visible_scopes,
)

router = APIRouter(prefix="/mem0-agent", tags=["agent-memory"])


def _get_memory():
    from server_state import get_memory_instance

    return get_memory_instance()


@router.post("/memories")
def write_memory(req: MemoryWriteRequest, ident: Identity = Depends(get_identity)):
    """Write a memory into a namespace scope.

    Default scope: personal cross-harness scope (shared by all harnesses of this user).
    scope = 'proj_{p}:u_{user}:a_{harness}' -> private to one harness.
    scope = 'proj_{p}:team' -> team shared (requires write ACL).
    """
    from db import SessionLocal

    scope = req.scope or scope_user(ident.project, ident.user)
    with SessionLocal() as db:
        check_write_allowed(scope, ident, db)

    user_id, agent_id = _split(scope)
    metadata = dict(req.metadata or {})
    metadata["scope"] = scope
    metadata["harness"] = ident.harness

    try:
        result = _get_memory().add(
            messages=req.messages,
            user_id=user_id,
            agent_id=agent_id,
            metadata=metadata,
            infer=req.infer,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"memory add failed: {e}")

    audit(ident, "add", scope, detail=str(len(req.messages)))
    return {"scope": scope, "result": result}


@router.post("/search")
def search_memory(req: MemorySearchRequest, ident: Identity = Depends(get_identity)):
    """Search memories across visible scopes, merged and de-duplicated."""
    from db import SessionLocal

    with SessionLocal() as db:
        allowed = visible_scopes(ident, db)
    scopes = req.scopes or allowed
    check_read_allowed(scopes, ident, db)

    # One query per scope, merged by (id) with best score kept.
    merged: dict[str, dict] = {}
    for scope in scopes:
        user_id, agent_id = _split(scope)
        filters = {"user_id": user_id}
        if agent_id:
            filters["agent_id"] = agent_id
        try:
            res = _get_memory().search(
                query=req.query,
                filters=filters,
                top_k=req.top_k,
                threshold=req.threshold,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"search failed: {e}")
        for item in res.get("results", []):
            mid = item.get("id")
            score = item.get("score") or 0.0
            if mid not in merged or score > (merged[mid].get("score") or 0.0):
                item = dict(item)
                item["scope"] = scope
                merged[mid] = item

    results = sorted(merged.values(), key=lambda x: x.get("score") or 0.0, reverse=True)[: req.top_k]
    audit(ident, "search", ",".join(scopes))
    return {"results": results, "scopes": scopes}


@router.get("/scopes")
def list_scopes(ident: Identity = Depends(get_identity)):
    from db import SessionLocal

    with SessionLocal() as db:
        return {"scopes": visible_scopes(ident, db)}


@router.post("/sharing/grant")
def grant(req: GrantRequest, ident: Identity = Depends(get_identity)):
    """Grant read/write on a scope to a user/harness/project."""
    from db import SessionLocal
    import uuid

    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO sharing_acl (id, scope, grantee_type, grantee_id, permission, created_by) "
                "VALUES (:i, :s, :gt, :gi, :perm, :cb)"
            ),
            {
                "i": str(uuid.uuid4()),
                "s": req.scope,
                "gt": req.grantee_type,
                "gi": req.grantee_id,
                "perm": req.permission,
                "cb": f"{ident.harness}:{ident.user}",
            },
        )
        db.commit()
    audit(ident, "grant", req.scope, detail=f"{req.grantee_type}:{req.grantee_id}={req.permission}")
    return {"granted": req.model_dump()}


@router.post("/sharing/revoke")
def revoke(req: GrantRequest, ident: Identity = Depends(get_identity)):
    from db import SessionLocal

    with SessionLocal() as db:
        db.execute(
            text(
                "DELETE FROM sharing_acl WHERE scope = :s AND grantee_type = :gt AND grantee_id = :gi"
            ),
            {"s": req.scope, "gt": req.grantee_type, "gi": req.grantee_id},
        )
        db.commit()
    audit(ident, "revoke", req.scope, detail=f"{req.grantee_type}:{req.grantee_id}")
    return {"revoked": req.model_dump()}


@router.get("/sharing")
def list_grants(ident: Identity = Depends(get_identity)):
    from db import SessionLocal

    with SessionLocal() as db:
        rows = (
            db.execute(
                text(
                    "SELECT scope, grantee_type, grantee_id, permission, created_by, created_at "
                    "FROM sharing_acl "
                    "WHERE scope LIKE :pat OR created_by = :cb "
                    "ORDER BY created_at DESC LIMIT 200"
                ),
                {"pat": f"proj_{ident.project}%", "cb": f"{ident.harness}:{ident.user}"},
            )
            .mappings()
            .all()
        )
    return {"grants": [dict(r) for r in rows]}


def _split(scope: str) -> tuple[str, str | None]:
    """Split a scope into (user_id, agent_id) for the mem0 core API."""
    from extra.identity import split_scope

    return split_scope(scope)
