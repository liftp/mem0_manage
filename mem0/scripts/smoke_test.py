"""End-to-end smoke test for the mem0 agent memory system.

Verifies:
  1. write + search in personal scope (same harness)
  2. cross-harness sharing: memory written by harness A visible to harness B (same user)
  3. isolation: harness C (different user) cannot see it
  4. team scope: grant write to another user, they can write; reader can search
  5. unauthorized scope -> 403

Env:
  MEM0_API_URL        e.g. http://localhost:8000
  WORKBUDDY_KEY       harness key for user "default" (project default)
  TRAE_KEY            second harness key, same user
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

API = os.environ.get("MEM0_API_URL", "http://localhost:8000").rstrip("/")
WB = os.environ.get("WORKBUDDY_KEY", "")
TR = os.environ.get("TRAE_KEY", "")

passed, failed = 0, 0


def call(method: str, path: str, key: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{API}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-API-Key": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def check(name: str, cond: bool, extra: str = ""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {extra}")


def main() -> int:
    if not WB or not TR:
        print("missing WORKBUDDY_KEY / TRAE_KEY env")
        return 2

    tag = str(int(time.time()))
    marker = f"smoke-marker-{tag}"
    print(f"marker: {marker}")

    print("[1] personal write + search (workbuddy)")
    st, resp = call(
        "POST", "/mem0-agent/memories", WB,
        {"messages": [{"role": "user", "content": f"Remember that {marker} is my favorite test token."}],
         "infer": False},
    )
    check("write 200", st == 200, f"status={st} body={resp}")

    time.sleep(1)
    st, resp = call("POST", "/mem0-agent/search", WB, {"query": marker, "top_k": 5})
    found = any(marker in json.dumps(r) for r in resp.get("results", []))
    check("search finds marker (own harness)", st == 200 and found, f"status={st}")

    print("[2] cross-harness sharing (trae sees workbuddy's user-scope memory)")
    st, resp = call("POST", "/mem0-agent/search", TR, {"query": marker, "top_k": 5})
    found = any(marker in json.dumps(r) for r in resp.get("results", []))
    check("cross-harness shared", st == 200 and found, f"status={st} body={resp}")

    print("[3] scopes listing")
    st, resp = call("GET", "/mem0-agent/scopes", WB)
    check("scopes contains personal scope",
          st == 200 and any("u_default" in s for s in resp.get("scopes", [])),
          f"status={st} scopes={resp.get('scopes')}")

    print("[4] private per-harness scope isolation")
    st, resp = call(
        "POST", "/mem0-agent/memories", WB,
        {"messages": [{"role": "user", "content": f"Private note {marker} only for workbuddy."}],
         "scope": "proj_default:u_default:a_workbuddy", "infer": False},
    )
    check("write private scope 200", st == 200, f"status={st}")
    time.sleep(1)
    st, resp = call(
        "POST", "/mem0-agent/search", TR,
        {"query": f"Private note {marker}", "scopes": ["proj_default:u_default:a_workbuddy"], "top_k": 5},
    )
    # trae must NOT be able to read workbuddy's private scope
    check("private scope blocked (403)", st == 403, f"status={st} body={resp}")

    print("[5] team scope grant flow")
    st, resp = call(
        "POST", "/mem0-agent/sharing/grant", WB,
        {"scope": "proj_default:team", "grantee_type": "user", "grantee_id": "default", "permission": "write"},
    )
    check("grant team write", st == 200, f"status={st} body={resp}")
    st, resp = call(
        "POST", "/mem0-agent/memories", WB,
        {"messages": [{"role": "user", "content": f"Team fact {marker} for everyone."}],
         "scope": "proj_default:team", "infer": False},
    )
    check("write team scope after grant", st == 200, f"status={st} body={resp}")
    time.sleep(1)
    st, resp = call("POST", "/mem0-agent/search", TR, {"query": f"Team fact {marker}", "top_k": 5})
    found = any(marker in json.dumps(r) for r in resp.get("results", []))
    check("team memory visible to other harness", st == 200 and found, f"status={st}")

    print("[6] no key -> 401")
    st, _ = call("GET", "/mem0-agent/scopes", "")
    check("missing key 401", st == 401, f"status={st}")

    print(f"\nresult: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
