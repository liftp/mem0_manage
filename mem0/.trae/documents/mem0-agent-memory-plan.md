# mem0 Agent 全局记忆系统 — 实施计划

## Summary

在 WSL Ubuntu-22.04 (`/usr/local/soft/mem`) 构建基于 mem0 官方架构的 agent 全局记忆系统：
mem0 server + Postgres/pgvector + Docker Compose，支持多 harness（workbuddy / qoder / trae / zcode / codex / claude code / deepseek coder）通过统一记忆服务 API 共享记忆，按「项目 / 用户 / agent」三级命名空间隔离，显式授权的团队共享区，LLM 与 Embedding 运行时双配置切换（OpenAI / DeepSeek）。

## Current State Analysis

### 环境事实（已验证）

| 项 | 状态 |
|---|---|
| 工作区 `c:\install\wb_workspace\mem0` | 空目录，全新项目 |
| WSL Ubuntu-22.04 | Running，WSL2 |
| Docker（WSL 内） | 28.2.2 + Compose v2.36.2，可正常拉取运行（hello-world 验证通过） |
| Docker socket 权限 | han 用户**不在** docker 组，需 `sudo`（密码 12345）或把 han 加入 docker 组 |
| `/usr/local/soft/mem` | 已存在（root 所有，空目录，218G 可用空间） |
| 端口占用 | 5432 / 6333 / 8000 / 3000 均空闲 |
| 已有容器 | minio / chat-redis / rmqdashboard 运行中（同 bridge 网络，无端口冲突） |
| 已有镜像 | 无 postgres / qdrant / mem0 相关镜像，需拉取 |
| Windows 侧 | Python 3.10.4、Node 20.9.0、pypi 可达；`api.openai.com` **不可达** |
| 已装 pip 包 | 仅 uvicorn 0.23.2，无 fastapi / mem0 / qdrant 客户端 |

### 架构决策（已与用户确认）

1. **LLM/Embedding 双配置，运行时切换**：环境变量统一配置 `base_url / api_key / model`，默认 DeepSeek（当前网络可达），保留 OpenAI 兼容切换开关。
2. **mem0 官方 server + Postgres/pgvector + Docker Compose**，构建于 WSL `/usr/local/soft/mem`。
3. **统一记忆服务 API + 每 harness 独立接入配置**：各 harness 作为 client 接入，记忆按 项目 / 用户 / agent 三级命名空间隔离，共享区显式授权。

### 关键设计：harness 记忆共享模型

```
┌─────────────────────────────────────────────────────────────┐
│                    mem0-server (FastAPI)                     │
│  POST /memories  (add/search/get/update/delete/history)      │
│  + 自定义层: user_id 强制注入 / 共享区授权 / 多 agent 可见性    │
├─────────────────────────────────────────────────────────────┤
│  llm: DeepSeek(base_url 可切换)  embedder: OpenAI/BGE(可切换) │
├──────────────────────┬──────────────────────────────────────┤
│  Postgres + pgvector │  Qdrant (向量存储, 可选)               │
│  (主存储/元数据/授权)  │                                       │
└──────────────────────┴──────────────────────────────────────┘
         ▲                    ▲                    ▲
    workbuddy/qoder/     trae/zcode/        codex/claude code/
    deepseek coder       (各自 MCP 或 REST     deepseek coder
     (各自 MCP 或 REST     client 配置)         (REST client)
      client 配置)
```

**命名空间规则**（`user_id` / `agent_id` / `run_id` 映射）：
- 个人私有记忆：`user_id = u_{user}`, `agent_id = a_{harness}`
- 跨 harness 共享（同一用户）：`user_id = u_{user}`, `agent_id = "shared"` 或 metadata `scope=user`
- 团队/项目共享：`user_id = "proj_{project}"`, `agent_id = "team"`，配 ACL 表授权
- 项目级隔离：`user_id` 前缀带 project 维度，即 `user_id = "{project}:{user}"`

**检索策略**：search 时按 caller 身份拼接可见 scope 列表（个人 + 显式授权的团队/项目 scope），多次 query 合并去重，按 `score + recency` 排序返回。

### 为什么用 Postgres/pgvector 而非纯 Qdrant

mem0 官方 server 的 graph store 与 metadata 均原生支持 Postgres；pgvector 可同时承担向量与关系存储，单容器即可满足，减少 Docker 依赖面。Qdrant 作为可选高性能向量后端保留在 compose 中（默认不启用，配置开关切换）。

## Proposed Changes

### 目录布局（WSL: `/usr/local/soft/mem`）

```
/usr/local/soft/mem/
├── docker-compose.yml            # postgres(pgvector) + qdrant + mem0-server
├── .env                          # LLM/Embedding/DB/端口配置(双 provider 切换)
├── .env.example
├── config/
│   ├── mem0.config.json          # mem0 主配置(llm/embedder/vector_store/graph_store)
│   └── history_migrate.sql        # mem0 表结构初始化(pgvector)
├── app/                          # 自定义增强层(挂载进 mem0-server 容器)
│   ├── Dockerfile                # 基于 mem0 server 官方镜像叠加增强层
│   ├── requirements.txt
│   ├── main.py                   # FastAPI 入口, 包装官方 server + 增强路由
│   ├── auth.py                   # API key 认证(每 harness 一个 key, 绑定身份)
│   ├── namespace.py              # 三级命名空间解析 + 可见 scope 计算
│   ├── sharing.py                # 团队共享区 ACL(授权/撤销/查询)
│   ├── routers/
│   │   ├── memories.py           # 代理/增强官方 memory 端点(注入身份校验)
│   │   ├── sharing.py            # 共享区管理端点
│   │   └── health.py             # 健康检查/配置查看(脱敏)
│   └── clients/
│       └── mem0_client.py        # 复用 mem0 SDK 的 Memory 封装
├── harness/                      # 各 harness 接入配置模板
│   ├── workbuddy.mcp.json
│   ├── qoder.mcp.json
│   ├── trae.mcp.json
│   ├── zcode.mcp.json
│   ├── codex.mcp.json
│   ├── claude-code.mcp.json
│   └── deepseek-coder.mcp.json
├── scripts/
│   ├── bootstrap.sh              # 一键: 建目录→起容器→初始化表→健康检查
│   ├── init-db.sql               # 自定义表: harness_keys / sharing_acl / audit_log
│   ├── smoke-test.py             # 端到端验证(写/查/共享/隔离)
│   └── windows-proxy.ps1         # Windows 侧本地端口转发(127.0.0.1:8000 → WSL)
└── docs/
    └── ARCHITECTURE.md            # 架构/接入文档(仅按需)
```

### 具体变更与实现要点

#### 1. Docker Compose（`/usr/local/soft/mem/docker-compose.yml`）

- `postgres`: `pgvector/pgvector:pg16`，端口 `5432:5432`，卷 `pgdata`，环境来自 `.env`
- `qdrant`: `qdrant/qdrant:latest`，端口 `6333:6333`，卷 `qdrant_data`（可选后端，默认配置指向 pgvector）
- `mem0-server`: 基于 `mem0ai/mem0-server:latest`（官方镜像）的派生镜像，挂载 `app/`，端口 `8000:8000`，`depends_on` postgres + qdrant，healthcheck
- 网络：`mem_net`（bridge）

#### 2. mem0 配置（`config/mem0.config.json`）

- `llm.provider: openai`（兼容模式），`config.base_url` 指向 `.env` 的 `LLM_BASE_URL`（默认 DeepSeek `https://api.deepseek.com`，可切 OpenAI），`api_key` / `model`（默认 `deepseek-chat`）来自 `.env`
- `embedder.provider: openai`，`base_url` 指向 `EMBED_BASE_URL`（默认 `https://api.openai.com/v1`，若 OpenAI 不可达可切 `https://api.deepseek.com` + `deepseek-embedding`，或本地 BGE `http://host.docker.internal:11434` Ollama 方案），dimensions 默认 1536
- `vector_store.provider: pgvector`，`dbname/user/password/host/port` 来自 `.env`
- `graph_store.enabled: false`（首期关闭，降低依赖）
- `version: v1.1`

#### 3. 增强层（`app/`，挂载进 server 容器）

- **auth.py**：`Authorization: Bearer mk_<harness>_<random>`，启动时从 Postgres `harness_keys` 表加载（key → `harness_name, default_user, allowed_scopes`），未注册 key 返回 401
- **namespace.py**：解析 `user_id` / `agent_id` 为规范 scope 串（`{project}:{user}:{agent}`），提供 `resolve_visible_scopes(caller)`：个人 scope + 共享 scope（来自 ACL）
- **sharing.py + routers/sharing.py**：
  - `POST /sharing/grant` — 授权某 user/harness 访问某 scope（`scope, grantee_type, grantee_id, permission=read|write`）
  - `POST /sharing/revoke`、`GET /sharing/list?scope=`
  - 表 `sharing_acl(id, scope, grantee_type[user|harness|project], grantee_id, permission, created_at, created_by)`
- **routers/memories.py**：包装官方端点，请求前：
  1. 校验 API key → caller 身份
  2. 校验目标 scope 是否在 `resolve_visible_scopes(caller)` 内（越权 403）
  3. search 时展开为多 scope 查询合并结果
- **audit_log**：所有写操作记录 `(ts, key_id, action, scope, memory_id)`，便于审计

#### 4. harness 接入（`harness/*.mcp.json`）

统一格式（MCP stdio 模式，客户端各自改路径即可）：

```json
{
  "mcpServers": {
    "mem0-global": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://127.0.0.1:8000/mcp",
               "--header", "Authorization: Bearer ${MEM0_API_KEY}"]
    }
  }
}
```

- 每个 harness 用自己的 key（`MEM0_API_KEY` 环境变量注入，例如 workbuddy 用 `mk_workbuddy_xxx`）
- 官方 mem0 server 已内置 MCP 端点（`/mcp`），增强层保持其路径不变
- 对不支持 MCP 的 harness（codex / deepseek coder）提供 REST 说明：`POST http://127.0.0.1:8000/memories` + `search`

#### 5. 脚本

- `scripts/bootstrap.sh`：`sudo docker compose up -d` → 等待 postgres healthy → 执行 `init-db.sql` + `history_migrate.sql` → 写入默认 harness keys → curl 健康检查
- `scripts/smoke-test.py`：用两个不同 harness key 验证：写入隔离、跨 harness 同 user 共享、团队 scope 授权后可见、未授权 403
- `scripts/windows-proxy.ps1`：Windows 侧 `netsh interface portproxy` 把 `127.0.0.1:8000` 转发到 WSL IP，方便 Windows 上的 harness 直接访问

#### 6. 工作区（`c:\install\wb_workspace\mem0`）

- `README.md`：项目说明 + 快速开始（指向 WSL 路径与 bootstrap 命令）
- `harness/` 配置模板副本（Windows 侧方便各 harness 读取）
- `.gitignore`

## Assumptions & Decisions

1. WSL 中 han 无 docker 权限 → bootstrap 内统一 `sudo`（密码通过 `sudo -S` 或临时把 han 加入 docker 组，推荐后者：`sudo usermod -aG docker han`，需重登 WSL 生效）
2. LLM 默认 DeepSeek（网络可达），OpenAI 为可切换项；Embedding 默认随 LLM 同 provider，若 DeepSeek embedding 不可用则回落 OpenAI/BGE 本地，`.env` 切换
3. mem0 官方镜像 `mem0ai/mem0-server` 需能拉取（hub.docker.com，已确认 docker 可正常拉取）
4. 首期关闭 graph store，降低依赖；schema 为单 project 多 user 模型（`user_id = "{project}:{user}"`），project 由 API key 绑定 + 请求参数双重约束
5. API key 明文存 Postgres（内网自用，不上公网）；如需更高安全可换 hash，作为后续项
6. 端口：server 8000 / postgres 5432 / qdrant 6333，均空闲

## Verification

1. `bootstrap.sh` 跑通：3 容器 healthy，`curl 127.0.0.1:8000/health` 200
2. `smoke-test.py` 全绿：
   - harness A 写入 → harness B（同 user，shared scope）能检索到
   - harness C（不同 user）检索不到（隔离验证）
   - 团队 scope 授权后 C 可见，撤销后不可见
   - 无 key / 错 key → 401，越权 scope → 403
3. 实际 harness 接入冒烟：任选一个 harness（如 workbuddy）按模板配置 MCP，对话中写入并检索一条记忆
4. Windows 侧 `portproxy` 配置后，Windows 进程可访问 `127.0.0.1:8000`

## 实施顺序

1. 环境准备：han 加入 docker 组 / 建目录结构
2. docker-compose + .env + mem0.config.json
3. app 增强层（auth → namespace → memories 代理 → sharing）
4. init-db.sql + bootstrap.sh，起服务
5. harness 接入模板 + smoke-test
6. Windows portproxy + README
7. 端到端验证
