# mem0 Agent 全局记忆系统

基于 [mem0](https://github.com/mem0ai/mem0) 官方自托管 server 构建的**项目级多用户知识管理系统**。
多个 coding harness（workbuddy / qoder / trae / zcode / codex / claude code / deepseek coder）
通过统一 REST API 共享记忆，按 **项目 / 用户 / agent** 三级命名空间隔离，团队共享区显式授权。

## 架构

```
harness clients (MCP / REST, http://127.0.0.1:8000)
        |
        v
  mem0-server (FastAPI)  <-- server/ 派生自 mem0 官方 main 分支
        |                   + 增强层 server/extra/: identity/scope/ACL/audit
        v
  Postgres + pgvector    <-- postgres 库: memories 向量表
                              mem0_app 库: 官方业务表 + harness_keys/sharing_acl/audit_log
```

## 快速开始

部署环境：WSL Ubuntu（Docker）。把本目录同步到 WSL 后执行：

```bash
cp .env.example .env        # 填 DEEPSEEK_API_KEY（LLM 抽取用，可后补）、改密码
./scripts/bootstrap.sh      # 一键: 起容器 -> 建表 -> 建 admin + 7 个 harness keys -> 冒烟测试
```

日常同步代码到 WSL 部署目录：

```bash
./scripts/sync_to_wsl.sh    # rsync 工作区 -> /usr/local/soft/mem（排除 .env / keys / 缓存）
```

## 目录

| 路径 | 说明 |
|---|---|
| `server/` | mem0 官方 server 源码（同步自 main 分支，打了两处补丁）+ 增强层 `server/extra/` |
| `server/models/` | 预置的 fastembed ONNX 模型（随源码分发，离线可用，约 110MB） |
| `docker-compose.yml` | postgres(pgvector) + mem0-server 编排 |
| `.env.example` | 全部可配置项（LLM / Embedder 运行时切换） |
| `scripts/bootstrap.sh` | 一键部署 |
| `scripts/sync_to_wsl.sh` | 工作区 → WSL 部署目录同步 |
| `scripts/smoke_test.py` | 端到端验证（隔离 / 共享 / 授权 / 越权） |
| `scripts/run_smoke.sh` | 用已生成的 harness-keys.txt 跑冒烟测试 |
| `scripts/check_api.sh` | API 与增强层路由健康检查 |
| `scripts/verify_deploy.sh` | 最终验收：干净重启 → 等 API → 冒烟测试 → 状态汇总 |
| `scripts/verify_from_windows.sh` | 从 Windows 侧用真实 harness key 打通读写检索 |
| `scripts/download_model.sh` | 下载新的 fastembed 模型到 `server/models/` |
| `scripts/reset_memories_table.sh` | 改了 embedding 维度后重建向量表 |
| `harness/` | 各 harness 的 REST 接入说明（7 个 harness） |
| `docs/ARCHITECTURE.md` | 架构与接入文档 |

## 记忆共享模型

增强层把 mem0 的 `(user_id, agent_id)` 封装成 **scope** 字符串：

| scope | 含义 | 可见性 |
|---|---|---|
| `proj_{P}:u_{user}` | 个人跨 harness 区 | 该用户的所有 harness 可读写（**默认写入目标**） |
| `proj_{P}:u_{user}:a_{harness}` | harness 私有区 | 仅该用户该 harness |
| `proj_{P}:team` | 团队共享区 | 需在 `sharing_acl` 显式授权后读写 |

写入默认落在个人跨 harness 区，因此 workbuddy 写的记忆 trae 能搜到；
私有区则严格隔离（越权返回 403）。授权通过 `POST /mem0-agent/sharing/grant` 管理。

## API

增强层挂在 `/mem0-agent` 前缀（需 `X-API-Key`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/mem0-agent/memories` | 写记忆，`scope` 可省略（默认个人区），`infer=false` 为直写 |
| POST | `/mem0-agent/search` | 跨可见 scope 检索，按分数合并去重 |
| GET | `/mem0-agent/scopes` | 列出当前身份可见的 scope |
| POST | `/mem0-agent/sharing/grant` | 授予 user / harness / project 某 scope 的 read/write |
| POST | `/mem0-agent/sharing/revoke` | 撤销授权 |
| GET | `/mem0-agent/sharing` | 查看本项目下的授权记录 |

官方接口（`/memories` `/search` `/configure` `/auth/*` `/api-keys` `/entities`）保持原样，
JWT + X-API-Key 双认证不变。

## 接入 harness

每个 harness 在 bootstrap 时生成独立 key，写入部署目录的 `harness-keys.txt`：

```
workbuddy=m0hk_xxxx
qoder=m0hk_xxxx
trae=m0hk_xxxx
...
```

把对应 key 填进 `harness/<name>.mcp.json` 即可。WSL2 默认把 Windows 的
`localhost:8000` 转发进 WSL，因此 Windows 侧 harness 直接用
`http://127.0.0.1:8000` 无需额外端口转发。

## LLM / Embedder

| 用途 | 默认 | 切换方式 |
|---|---|---|
| 记忆抽取 LLM | `deepseek`（OpenAI 兼容端点） | `.env` 设 `LLM_PROVIDER=openai\|anthropic\|gemini` + 对应 key |
| 向量化 Embedder | `fastembed` 本地 ONNX（`snowflake-arctic-embed-xs`，384 维） | `.env` 设 `EMBEDDER_PROVIDER=openai\|gemini` + key，同时把 `EMBEDDING_DIMS` 改成 1536 |

未填 LLM key 时服务仍可启动，`infer=false` 的直写可用；填 key 后自动启用记忆抽取。

> 换 embedder 模型或改维度后，需 `scripts/reset_memories_table.sh` 重建向量表。

## 已知坑位（实现说明）

- **fastembed 默认模型的 xet 死路**：`BAAI/bge-small-en-v1.5` 被 fastembed 映射到
  `Qdrant/bge-small-en-v1.5-onnx-Q`，该仓库的 ONNX 权重只通过 xet CAS 分发，
  而 hf-mirror 的 xet 端点返回 401。因此改用 `snowflake-arctic-embed-xs`
  （普通 HTTP/LFS 可达），模型文件由 `scripts/download_model.sh` 预下载并
  vendor 进 `server/models/`，`server/main.py` 通过 fastembed 的
  `specific_model_path` 参数直接加载，运行时零网络依赖。
- **mem0 不透传 cache_dir**：`FastEmbedEmbedding` 只调
  `TextEmbedding(model_name=...)`，模型会落到 `/tmp` 且每次重启重下。
  `server/main.py` 在 import 阶段 monkey-patch 了 `TextEmbedding.__init__`。
- **两个数据库**：向量表在 `POSTGRES_DB`（默认 `postgres`），业务表在
  `APP_DB_NAME`（默认 `mem0_app`），由 `server/init-db.sh` 建库。
- **`_collection_ensured` 实例缓存**：pgvector 适配器会缓存"表已建"状态，
  手动 DROP 表后必须重启容器才会重建。
- **admin 邮箱**：pydantic `EmailStr` 拒绝 `.local` / `.localhost` 等保留域名，
  用 `admin@mem0.dev`。

## 安全

- server 自带 JWT + X-API-Key 认证；每个 harness 一个独立 key（bcrypt 哈希存 `harness_keys`）
- 增强层强制 scope 校验：越权访问返回 403
- 所有写操作审计日志落 `mem0_app.audit_log`
- `harness-keys.txt` 仅存本机，已在 `.gitignore` 中排除
