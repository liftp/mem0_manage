# mem0 Agent 全局记忆系统 — 项目 / 部署 / 配置使用手册

> 适用范围：本仓库（基于 [mem0](https://github.com/mem0ai/mem0) 官方自托管 server 二次开发）的本地 / 内网部署、配置与接入。
> 成文时间：2026-10-17。已对照 `main.py` / `mcp_server.py` / `identity.py` / `agent_router.py` / `docker-compose*.yml` / `.env` 等真实代码核对。

---

## 1. 项目概述

### 1.1 它是什么

一个**项目级多用户长期记忆系统**，让多个 AI 编码助手（harness）通过统一接口**共享同一份记忆**，按「项目 / 用户 / harness」三级命名空间隔离，团队共享区需显式授权。

- 底座：mem0 官方 `Memory` 引擎（记忆抽取 + 向量检索）。
- 增强层（`server/extra/`）：身份绑定、三层 scope 隔离、ACL 授权、审计日志。
- 接入层：官方 REST（`/memories` `/search`）+ 增强 REST（`/mem0-agent/*`）+ 可选的 MCP 转发层（给只支持 MCP 的 harness 用，例如 WorkBuddy）。

### 1.2 核心价值

| 能力 | 说明 | 是否依赖外部 key |
|---|---|---|
| 文本 → 向量（embedding） | 默认本地 ONNX（fastembed），离线、零 key | **否**（默认） |
| 记忆抽取 / 去重 / 合并 | LLM 把对话提炼成结构化长期记忆（`infer=true`） | 是（LLM key） |
| 检索 | 向量相似度检索，跨可见 scope 合并去重 | 否 |
| 多 harness 共享 | 同一用户不同助手可读写同一份记忆 | 否 |
| 团队共享 / 授权 | `sharing_acl` 显式授权 | 否 |

### 1.3 技术栈

- 后端：FastAPI（mem0 官方 server，打了两处补丁）
- 存储：PostgreSQL + pgvector（向量表）+ 第二库 `mem0_app`（业务 / 鉴权 / 审计）
- 本地嵌入：`fastembed` + `onnxruntime`，模型权重随镜像固化（`server/models/`）
- 容器：Docker（WSL2 内 Docker-in-WSL），镜像走 `docker.1ms.run` 镜像
- 接入网关：可选 MCP 转发层（`fastmcp` + `httpx`）

---

## 2. 系统架构

### 2.1 架构图

```
                 ┌────────────────────────────────────────────┐
                 │            harness clients                 │
                 │ workbuddy / qoder / trae / zcode / codex / │
                 │ claude-code / deepseek-coder / dashboard   │
                 └───────────┬────────────────────────────────┘
                             │ REST (X-API-Key) 或 MCP (Bearer)
                             ▼
              127.0.0.1:8000 / 8001  (WSL2 自动转发 Windows localhost)
                             │
        ┌────────────────────┴─────────────────────┐
        ▼                                          ▼
  mem0-server (FastAPI)                    mem0-mcp (fastmcp, 可选)
  + 增强层 server/extra/                   转发 /mcp -> /mem0-agent/*
        │
        ├──▶ Postgres + pgvector  (向量记忆 memories 表)
        ├──▶ mem0_app 库          (users/api_keys/settings + harness_keys/
        │                          sharing_acl/audit_log)
        └──▶ 外部 LLM/Embedder API（仅切到 openai/gemini 或 infer=true 时出网）
```

### 2.2 端口

| 服务 | 容器内 | 宿主机(WSL) | Windows |
|---|---|---|---|
| mem0-server | 8000 | 8000 | `127.0.0.1:8000`（WSL2 自动转发，无需 netsh） |
| mem0-mcp | 8001 | 8001 | `127.0.0.1:8001` |
| postgres | 5432 | 5432 | —（一般不对外） |

### 2.3 双认证体系

| 层级 | 凭证 | 用途 |
|---|---|---|
| 官方层 | JWT（`/auth/register|login`）或 `X-API-Key` 官方 api-key | Dashboard、人工操作、`/configure` |
| 增强层 | `X-API-Key: m0hk_xxx` | harness 程序化接入，查 `harness_keys` 表（bcrypt 校验） |

harness key 形如 `m0hk_<base64url 32B>`，由 `bootstrap.sh` 为每个 harness 生成一个，写入部署目录的 `harness-keys.txt`，明文**只此一次**。

身份覆盖请求头（一个 key 服务多用户 / 多项目时）：
- `X-Mem0-User`：覆盖默认 user
- `X-Mem0-Project`：覆盖默认 project

### 2.4 三层 scope 共享模型

mem0 原生用 `(user_id, agent_id, run_id)` 定位记忆。本系统在上面封装出 **scope**：

| Scope 形态 | 编码为 | 语义 | 默认可见 |
|---|---|---|---|
| 个人跨 harness | `proj_{p}:u_{user}` | 同一用户的所有 harness 共享 | 本人全部 harness |
| 私有单 harness | `proj_{p}:u_{user}:a_{harness}` | 仅该用户该 harness | 仅该 harness |
| 团队共享 | `proj_{p}:team` | 项目级知识库 | 需 `sharing_acl` 授权 |

> **写入默认落到个人跨 harness 区**，所以 workbuddy 写的记忆 trae 也能搜到；私有区严格隔离（越权 403）。

### 2.5 数据流

写入：
```
POST /mem0-agent/memories {messages, scope?, agent_id?, metadata?, infer}
  1. X-API-Key -> Identity {harness, user, project}
  2. scope 缺省 = proj_{project}:u_{user}（个人跨 harness 区）
  3. check_write_allowed(scope)  -> 403 越权
  4. mem0.add(user_id, agent_id, metadata{scope, harness}, infer)
  5. 写 audit_log
```
检索：
```
POST /mem0-agent/search {query, scopes?, top_k, threshold}
  1. X-API-Key -> Identity
  2. scopes 缺省 = visible_scopes = 个人私有 + 个人共享 + ACL 授权 scope
  3. check_read_allowed -> 403 显式越权
  4. 逐 scope 查询 -> 按 memory id 合并取最高分 -> 按 score 降序返回 top_k
```

---

## 3. 部署

### 3.1 环境要求

- **WSL2 + Docker-in-WSL**（默认发行版，root 可用 `wsl -u root`）。
- 网络：本机出网需走 **WSL-NAT 代理** `http://172.20.240.1:17890`（构建期 pip 用；运行时除 LLM 出网外基本不用）。
- 镜像：`docker.1ms.run` 镜像 `docker.io`（直连不可达）。
- pip 镜像：`mirrors.aliyun.com`（tuna 在本环境 403）。

### 3.2 目录结构（部署树 `/usr/local/soft/mem0`）

| 路径 | 说明 |
|---|---|
| `server/` | mem0 官方 server 源码（两处补丁）+ 增强层 `server/extra/` |
| `server/models/` | 预置 fastembed ONNX 模型（随源码分发，离线可用，约 110MB） |
| `server/Dockerfile` | mem0-server 镜像（固化模型、清空构建代理） |
| `Dockerfile.mcp` | MCP 转发层镜像 |
| `docker-compose.yml` | postgres(pgvector) + mem0-server |
| `docker-compose.mcp.yml` | 叠加 mem0-mcp 服务（overlay） |
| `docker-compose.dashboard.yml` | 可选官方 Dashboard |
| `.env` / `.env.example` | 全部可配置项 |
| `scripts/bootstrap.sh` | 一键部署（建表→admin→7 个 harness key→冒烟） |
| `scripts/sync_to_wsl.sh` | 工作区 → WSL 部署目录同步（排除 .env/keys） |
| `scripts/download_model.sh` | 下载新 fastembed 模型到 `server/models/` |
| `scripts/reset_memories_table.sh` | 换 embedder 维度后重建向量表 |
| `harness/*.rest.md` / `codex.config.toml` | 各 harness 接入说明 |
| `docs/ARCHITECTURE.md` | 架构文档 |
| `harness-keys.txt` | 部署生成的各 harness key（仅本机，已在 .gitignore） |

> ⚠️ 双副本：`C:/pro/mem0_manage/mem0`（Windows 可编辑）↔ `/usr/local/soft/mem0`（WSL 实际运行）。改代码后需 `scripts/sync_to_wsl.sh` 或手工 `docker compose cp` 同步。

### 3.3 一键部署（核心）

```bash
# 1. 把源码同步到 WSL 部署目录
./scripts/sync_to_wsl.sh          # 在 WSL 内执行，源=工作区，目的=/usr/local/soft/mem

# 2. 进部署目录执行 bootstrap
cd /usr/local/soft/mem0
./scripts/bootstrap.sh
```

`bootstrap.sh` 流程：
1. 检查 `.env`（没有则从 `.env.example` 复制）—— **这一步需要 `.env` 里 `POSTGRES_PASSWORD` 必填**。
2. 生成 `JWT_SECRET` 并回填 `.env`。
3. `docker compose up -d --build`（postgres + mem0-server）。
4. 等 API 就绪 → 注册 admin（`ADMIN_EMAIL` / `ADMIN_PASSWORD`）。
5. 为 7 个 harness 创建 key，写入 `harness-keys.txt`。
6. 跑冒烟测试（`scripts/smoke_test.py`：隔离 / 共享 / 授权 / 越权）。

### 3.4 起 MCP 转发层（WorkBuddy 用）

MCP 层是独立服务，需要**叠加**第二个 compose 文件：

```bash
cd /usr/local/soft/mem0
docker compose -f docker-compose.yml -f docker-compose.mcp.yml up -d
```

该服务：
- 容器名 `mem0-agent-memory-mcp`，端口 `8001`。
- 暴露 `http://127.0.0.1:8001/mcp`（Streamable HTTP）。
- 把入站 `Authorization: Bearer <key>` 转成下游 `X-API-Key`（与 WorkBuddy `mem0-global` 配置对接）。
- `restart: unless-stopped`，并**清空构建期代理**（避免内网请求被 502）。

### 3.5 验证部署

```bash
# 容器状态
docker compose -f docker-compose.yml -f docker-compose.mcp.yml ps

# 健康检查
curl http://127.0.0.1:8000/auth/setup-status
curl http://127.0.0.1:8001/                      # MCP 层健康检查

# 完整冒烟（Windows 侧用真实 key）
python3 scripts/verify_from_windows.sh
```

---

## 4. 配置

### 4.1 `.env` 全量说明

完整字段见 `.env.example`。关键项：

**LLM（记忆抽取，仅 `infer=true` 需要 key）**
| 变量 | 默认值 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `deepseek` | `deepseek` / `openai` / `anthropic` / `gemini` |
| `DEEPSEEK_API_KEY` | 空 | 填后启用记忆抽取 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | 空 / `gpt-4o-mini` | 切 openai 时填 |
| `OPENAI_BASE_URL_OVERRIDE` | 空 | 阿里云 DashScope / SiliconFlow / vLLM 等 OpenAI 兼容网关 |

**Embedder（向量化，决定是否需要 key）**
| 变量 | 默认值 | 说明 |
|---|---|---|
| `EMBEDDER_PROVIDER` | `fastembed` | `fastembed`（本地 ONNX，零 key）/ `openai` / `gemini` |
| `EMBEDDER_MODEL` | `snowflake/snowflake-arctic-embed-xs` | 本地模型 id（目录名 `/`→`-`） |
| `EMBEDDING_DIMS` | `384` | 必须与模型一致（OpenAI 为 1536），建表时固化 |
| `OPENAI_EMBEDDER_MODEL` / `OPENAI_EMBEDDER_BASE_URL` | 空 | 切 openai embedder 时填 |

**Postgres**
| 变量 | 默认值 |
|---|---|
| `POSTGRES_HOST/PORT/DB/USER` | `postgres` / `5432` / `postgres` / `postgres` |
| `POSTGRES_PASSWORD` | （**必填**，缺失 compose 直接报错） |
| `POSTGRES_COLLECTION_NAME` | `memories` |
| `APP_DB_NAME` | `mem0_app` |

**mem0 server**
| 变量 | 默认值 | 说明 |
|---|---|---|
| `MEM0_PORT` | `8000` | |
| `ADMIN_EMAIL` / `ADMIN_PASSWORD` | `admin@mem0.dev` / `change-me-admin` | **必须用公网风格域名**（pydantic `EmailStr` 拒绝 `.local`） |
| `JWT_SECRET` | 空（bootstrap 自动生成） | |
| `AUTH_DISABLED` | `false` | 本地开发可设 `true` |
| `MEM0_TELEMETRY` | `false` | |
| `DEFAULT_PROJECT` | `default` | |

**模型下载源**
| 变量 | 默认值 |
|---|---|
| `HF_ENDPOINT` | `https://hf-mirror.com` |

### 4.2 LLM 运行时切换（无需重建镜像）

改 `.env` 的 `LLM_PROVIDER` + 对应 key，`docker compose restart mem0` 即可。DeepSeek 走 OpenAI 兼容端点，因此 `BUNDLED_LLM_PROVIDERS=(openai,anthropic,gemini)` 校验天然通过。

### 4.3 Embedder 切换：本地 ONNX vs API

这是本项目最容易踩坑的地方，单独说明：

- **默认 `EMBEDDER_PROVIDER=fastembed`**：用 `server/models/` 里固化好的 ONNX 权重 + `onnxruntime` 本地推理，**零 key、零网络**。写入和检索都用它。
- **切换到 `openai` / `gemini`**：必须配对应 embedding key，否则 embedding 直接失败（**不会自动退回 ONNX**）。同时 `EMBEDDING_DIMS` 需改成 1536（OpenAI）并重建向量表。

**ONNX 离线加载机制**（`server/main.py:73-91`）：
- 镜像构建时 `COPY server/models/ /app/models/`。
- 启动时若检测到 `/app/models/<org>-<name>` 目录，则 monkey-patch `fastembed.TextEmbedding.__init__`，注入 `specific_model_path`，**短路掉所有下载逻辑**。
- `HF_HUB_OFFLINE=1` + `TRANSFORMERS_OFFLINE=1` 在 Dockerfile 已固化，确保 fastembed 绝不访问 HF Hub。

> 换模型：`python3 scripts/download_model.sh <org/model>`，目录名按 `/`→`-` 约定，再改 `EMBEDDER_MODEL`。

### 4.4 更换 embedder 维度 → 必须重建向量表

pgvector 表的向量宽度在建表时固化（`embedding_model_dims`），维度变化会导致每次 insert 失败。步骤：

```bash
./scripts/reset_memories_table.sh     # DROP 旧表
docker compose restart mem0            # 必须重启！pgvector 适配器有 _collection_ensured 实例缓存，只 DROP 不重启不会重建
```

### 4.5 🔴 关键坑位：`config_overrides`（曾导致 502 "Connection error."）

**症状**：服务能起、`list_scopes` 正常，但只要 `add` 就 502 `memory add failed: Connection error.`，耗时 ~16s（网络重试）。

**根因**：`server/main.py` 的 `initialize_state()` 启动时会合并 `mem0_app.settings` 表里 `key='config_overrides'` 的一行 JSON。如果曾通过 Dashboard 的 `/configure` 把 embedder 强切成 `openai`（且配了假 key），这行会被持久化，覆盖默认 fastembed，导致 mem0 实际走 `mem0/embeddings/openai.py` 去连 OpenAI API 而失败。`main.py` 日志里打印的 "Embedder provider=fastembed" 只是**默认**配置，被 override 盖掉，极具误导性。

> Traceback 才是真相：`.../mem0/embeddings/openai.py ... openai.APIConnectionError: Connection error.`

**修复**：

```sql
DELETE FROM settings WHERE key='config_overrides';
```
然后 `docker compose restart mem0` 恢复默认（离线 fastembed）。

**结论**：保持「不写 override」即为离线可用状态。如确需用 Dashboard `/configure` 改配置，务必确认 embedder 仍是 fastembed 且不要填假 key。

### 4.6 代理相关注意事项

- 构建期：compose 把宿主机 `HTTP_PROXY`/`HTTPS_PROXY` 作为 build-arg 传入 `server/Dockerfile`，pip 才能出网装包；Dockerfile 末尾**已清空**这些变量，不污染运行时。
- 运行时 mem0 容器：`HTTP_PROXY=http://172.20.240.1:17890` 仅用于 LLM 出网；`NO_PROXY` 已包含 `postgres,mem0,mem0-mcp,localhost,127.0.0.1`。
- MCP 容器：overlay 里**显式清空**代理 + `httpx.Client(trust_env=False)`，否则内网 `http://mem0:8000` 会被 502。
- 在 **WSL 宿主**侧用 Python 打 mem0 的 REST 时，记得 `HTTP_PROXY= HTTPS_PROXY=` 清空，否则 urllib 走代理访问 `127.0.0.1` 会失败。

---

## 5. 使用

### 5.1 增强层 REST API（`/mem0-agent/*`，需 `X-API-Key`）

取 key：`harness-keys.txt` 里 `workbuddy=` 后面那段。服务地址 `http://127.0.0.1:8000`。

**写入记忆**
```bash
curl -X POST http://127.0.0.1:8000/mem0-agent/memories \
  -H "X-API-Key: <WORKBUDDY_KEY>" -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"项目使用 uv 管理依赖"}],"infer":false}'
```
- `infer: true`（默认）：调 LLM 抽取结构化记忆，**需 LLM key**。
- `infer: false`：**原样直写**，不依赖 LLM，离线可用 ✅（推荐先用这个跑通）。
- `scope` 省略 → 个人跨 harness 区（其他 harness 可见）；指定 `"scope":"proj_default:u_default:a_workbuddy"` → 仅 workbuddy 私有。

**检索记忆**
```bash
curl -X POST http://127.0.0.1:8000/mem0-agent/search \
  -H "X-API-Key: <WORKBUDDY_KEY>" -H "Content-Type: application/json" \
  -d '{"query":"依赖管理方式","top_k":5,"threshold":0.0}'
```
默认检索所有可见 scope 并合并去重；`"scopes":["proj_default:u_default"]` 可限定。

**查看可见 scope**
```bash
curl http://127.0.0.1:8000/mem0-agent/scopes -H "X-API-Key: <WORKBUDDY_KEY>"
```

**团队共享授权**
```bash
curl -X POST http://127.0.0.1:8000/mem0-agent/sharing/grant \
  -H "X-API-Key: <WORKBUDDY_KEY>" -H "Content-Type: application/json" \
  -d '{"scope":"proj_default:team","grantee_type":"user","grantee_id":"bob","permission":"write"}'
```

**官方接口**（`/memories` `/search` `/configure` `/auth/*` `/api-keys`）保持原样，JWT + X-API-Key 双认证。

### 5.2 WorkBuddy MCP 集成

MCP 转发层把上面的 REST 暴露为 MCP 工具。在 `~/.workbuddy/mcp.json` 配置：

```json
{
  "mcpServers": {
    "mem0-global": {
      "url": "http://127.0.0.1:8001/mcp",
      "headers": { "Authorization": "Bearer <WORKBUDDY_KEY_FROM_harness-keys.txt>" }
    }
  }
}
```

配置后 WorkBuddy 会显示 `mem0-global` 连接，提供 4 个工具：

| 工具 | 签名 | 说明 |
|---|---|---|
| `add_memory` | `(content, infer=True, scope="")` | 写记忆。`infer=false` 直写（离线）；`scope` 留空=个人共享区 |
| `search_memory` | `(query, top_k=5, threshold=0.0, scopes="")` | 跨可见 scope 检索；`scopes` 逗号分隔可限定 |
| `list_scopes` | `()` | 列出当前身份可见 scope |
| `grant_sharing` | `(scope, grantee_type, grantee_id, permission="write")` | 授权 |

**实测可用**（2026-10-17）：`list_scopes` → `add_memory(infer=false)` → `search_memory` 全链路通过，写入 0.46s、检索 score 0.945。

**关于「每次对话自动写入」**：MCP 只暴露工具，由模型自主决定是否调用，**不会自动捕获对话**。想每轮落盘，最实用做法是给 agent 加一条**常驻指令**：

> 每次对话结束前，把本次关键结论 / 用户偏好 / 重要决策用 `mem0-global` 的 `add_memory` 写入共享记忆：`add_memory(content=..., infer=false)`（离线存原文）；要自动抽取结构化事实用 `infer=true`（需 LLM key）。scope 留空即可。

（WorkBuddy 自动化只支持定时 / 单次触发，没有「每轮对话」事件；hook 文档未提供可用的对话级事件，故不推荐。）

### 5.3 其他 harness 接入

`harness/` 目录下有各 harness 的接入说明（`workbuddy.rest.md` / `qoder.rest.md` / `trae.rest.md` / `zcode.rest.md` / `codex.rest.md` / `claude-code.rest.md` / `deepseek-coder.rest.md`）。统一做法：把对应 key 填进各 harness 的 MCP / REST 配置。

---

## 6. 运维与排障

### 6.1 常用运维命令

```bash
cd /usr/local/soft/mem0
docker compose -f docker-compose.yml -f docker-compose.mcp.yml ps        # 状态
docker compose -f docker-compose.yml -f docker-compose.mcp.yml logs -f mem0   # 看 mem0 日志
docker compose -f docker-compose.yml -f docker-compose.mcp.yml restart mem0  # 重启
docker compose down                      # 停止
docker compose down -v                   # 停止并清空数据（危险）
```

### 6.2 故障排查表

| 现象 | 可能原因 | 处理 |
|---|---|---|
| `docker compose up` 卡住 / 构建失败 | pip 出网被代理挡；没传 build-arg 代理 | 从已 export 代理的 shell 跑 build；确认 `docker.1ms.run` 可达 |
| `add` 502 `Connection error.`（~16s） | `config_overrides` 把 embedder 强切成 openai 且 key 假 | `DELETE FROM settings WHERE key='config_overrides';` 再 restart |
| MCP 层所有请求 502 | MCP 容器走构建期代理访问 `mem0` 主机名失败 | overlay 已清空代理 + `trust_env=False`；确认叠加 mcp overlay 启动 |
| 换了 embedder 维度后 insert 失败 | pgvector 表宽度固化 | `reset_memories_table.sh` + 重启 mem0 |
| 改了代码没生效 | WSL 副本未同步 | `sync_to_wsl.sh` 或 `docker compose cp`；改 Dockerfile 需 `--build` |
| `add` 返回 401 `Invalid harness API key` | `harness-keys.txt` 与 DB 不一致（DB 被重置） | 重新 `create_harness_key('workbuddy')` 并刷新两端 key |
| 记忆「没写入」 | MCP 连接上了但 agent 没主动调 `add_memory` | MCP 不会自动写；加常驻指令或外部脚本 |
| 检索不到刚写的 | search 用了错误的 `filters` / scope 越权 | 用 `/mem0-agent/scopes` 确认可见 scope；`infer=false` 直写也能检索 |

### 6.3 双数据库说明

Postgres 内两个 database：
- `postgres`（默认库）：向量集合 `memories`（pgvector，惰性建表，首次 add 才创建）。
- `mem0_app`：官方业务表（alembic 管理）+ 增强表 `harness_keys` / `sharing_acl` / `audit_log`（`create_extra_tables()` 管理）。

`init-db.sh` 在卷首次初始化时创建 `mem0_app` 库。

---

## 7. 安全

- 官方 JWT + X-API-Key 认证；每个 harness 独立 key（bcrypt 哈希存 `harness_keys`）。
- 增强层强制 scope 校验，越权访问返回 403。
- 所有写操作审计落 `mem0_app.audit_log`。
- `harness-keys.txt` 仅存本机，已在 `.gitignore` 排除 —— **不要提交到仓库**。
- admin 邮箱必须用公网风格域名（`admin@mem0.dev`），否则 `EmailStr` 校验失败。

---

## 8. 已知限制 / 后续项

- `audit_log` 无自动清理（量大了加 cron prune）。
- harness key 明文不回查（bcrypt 校验），丢失需新建。
- 未做多 project 强隔离（project 由 header 覆盖，信任 harness）。
- 未启用 graph store（`enable_graph=false`，后续可开 neo4j）。
- 官方 Dashboard（`docker-compose.dashboard.yml`）可选，但注意它的 `/configure` 可能写入 `config_overrides`（见 4.5），需谨慎。
- 部署树根目录的 `probe_tmp.py` 是调试残留，可删除。
