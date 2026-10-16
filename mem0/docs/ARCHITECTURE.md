# 架构文档

## 1. 总体架构

```
                    ┌──────────────────────────────────────────┐
                    │            harness clients               │
                    │  workbuddy / qoder / trae / zcode /      │
                    │  codex / claude-code / deepseek-coder    │
                    └───────┬──────────────────────────────────┘
                            │ REST (X-API-Key)
                            ▼
              127.0.0.1:8000 (Windows → WSL2 localhost 转发)
                    │
                    ▼
              WSL docker: mem0-server (FastAPI)
                    │
        ┌───────────┴────────────┐
        ▼                        ▼
  Postgres + pgvector      (可选) 外部 LLM/Embedder API
  - 向量记忆 (payload JSONB)   DeepSeek / OpenAI
  - 元数据/授权/审计
```

> mem0 server 只提供 REST 接口，没有 MCP 端点。各 harness 通过
> `harness/*.rest.md` 中的 curl / HTTP 协议接入；若某 harness 只支持 MCP，
> 需自建薄转发层。

### 端口

| 服务 | 容器内 | 宿主机(WSL) | Windows |
|---|---|---|---|
| mem0-server | 8000 | 8000 | 127.0.0.1:8000（WSL2 自动转发，无需 netsh） |
| postgres | 5432 | 5432 | - |

## 2. 三层记忆共享模型

mem0 原生用 `user_id` / `agent_id` / `run_id` 三元组定位记忆。本系统在其上定义了
**scope（命名空间）** 概念，编码为 `user_id`（+ 可选 `agent_id`）：

| Scope 形态 | user_id | agent_id | 语义 | 默认可见 |
|---|---|---|---|---|
| 个人跨 harness | `proj_{p}:u_{user}` | - | 同一用户所有 harness 共享 | 本人全部 harness |
| 私有单 harness | `proj_{p}:u_{user}` | `a_{harness}` | 仅该 harness | 仅该 harness |
| 团队共享 | `proj_{p}:team` | - | 项目级团队知识库 | ACL 授权成员 |

### 写入路径

```
POST /mem0-agent/memories {messages, scope?, agent_id?, metadata?, infer}
  1. X-API-Key -> Identity {harness, user, project}
  2. scope 缺省 = scope_user(project, user)   (个人跨 harness)
  3. check_write_allowed(scope)               -> 403 if 越权
  4. mem0.add(user_id, agent_id, metadata{scope, harness})
  5. audit_log
```

### 检索路径

```
POST /mem0-agent/search {query, scopes?, top_k, threshold}
  1. X-API-Key -> Identity
  2. scopes 缺省 = visible_scopes(identity)
     = 个人私有 + 个人共享 + ACL 授权的 scope
  3. check_read_allowed(scopes)  -> 403 if 显式越权
  4. 逐 scope 查询, 按 memory id 合并, 取最高分
  5. 按 score 降序返回 top_k
```

## 3. 认证体系

两层并存：

1. **官方层**：`/auth/register` `/auth/login` JWT + `/api-keys` 管理（dashboard 用）
2. **增强层**：`X-API-Key` -> `harness_keys` 表（harness 程序化接入）

harness key 形如 `m0hk_<base64url 32B>`，bcrypt 哈希存储，部署时由 bootstrap 为
每个 harness 创建一个，label = `harness:<name>`，原始值只显示一次（`harness-keys.txt`）。

身份覆盖：请求头 `X-Mem0-User` / `X-Mem0-Project`（用于一个 key 服务多用户或切项目）。

## 4. 数据库表

Postgres 内两个 database：

- `postgres`（默认库）：mem0 向量集合 `memories`（pgvector）
- `mem0_app`：官方表（users / api_keys / request_logs / settings / refresh_token_jtis，alembic 管理）
  + 增强表（本系统 `CREATE TABLE IF NOT EXISTS` 管理）：

| 表 | 用途 |
|---|---|
| `harness_keys` | harness key 哈希 + 绑定身份 |
| `sharing_acl` | scope 授权（grantee_type: user/harness/project） |
| `audit_log` | 写操作审计 |

## 5. LLM / Embedder 运行时切换

`server/main.py` 从环境变量构建 mem0 config，无需改镜像：

| 变量 | 取值 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `deepseek` \| `openai` \| `anthropic` \| `gemini` | 记忆抽取 LLM |
| `EMBEDDER_PROVIDER` | `fastembed` \| `openai` \| `gemini` | 向量化 |

- DeepSeek 通过 `openai` provider + `openai_base_url=https://api.deepseek.com` 表达
  （OpenAI 兼容端点），因此 `BUNDLED_*_PROVIDERS` 校验不受影响
- `EMBEDDER_PROVIDER=fastembed`（默认）用本地 ONNX 模型，模型文件 vendor 在
  `server/models/`，`server/main.py` 借 fastembed 的 `specific_model_path` 参数
  直接加载，构建与运行均零网络依赖
- Embedder 维度通过 `EMBEDDING_DIMS` 配置（必须与所选模型一致，pgvector 建表时固化；
  默认 fastembed 模型为 384 维，OpenAI `text-embedding-3-small` 为 1536 维）
- dashboard 的 `/configure` 可运行时覆盖并持久化到 `settings` 表

**注意**：更换 embedder 模型（维度变化）需要删掉 `memories` 表后重启容器
（`scripts/reset_memories_table.sh`）。pgvector 适配器有 `_collection_ensured`
实例级缓存，只 DROP 表不重启不会重建。

## 6. 部署

```bash
# 1. 同步本目录到 WSL
./scripts/sync_to_wsl.sh

# 2. WSL 内执行
cd /usr/local/soft/mem && ./scripts/bootstrap.sh
```

bootstrap 流程：`.env` 检查 -> `JWT_SECRET` 生成 -> `docker compose up -d --build`
-> 注册 admin -> 创建 7 个 harness keys -> 冒烟测试。

## 7. 运维

```bash
docker compose logs -f mem0     # 看日志
docker compose restart mem0     # 重启
docker compose down             # 停止
docker compose down -v          # 停止并清空数据
```

## 8. 已知限制 / 后续项

- `audit_log` 无自动清理（量大了加 cron prune）
- harness key 明文不回查（bcrypt 校验），丢失需重新创建
- 未做多 project 强隔离（project 由 header 覆盖，信任 harness）
- 未启用 graph store（`enable_graph=false`，后续可开 neo4j）
