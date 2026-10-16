# Codex 接入 mem0 全局记忆

所有请求走统一 REST API，需带 `X-API-Key: <CODEX_KEY>`
（值来自部署时生成的 `harness-keys.txt` 中 `Codex=` 后面那段）。

服务地址：`http://127.0.0.1:8000`（WSL2 默认把 Windows 的 localhost 转发进 WSL）。

## 写入记忆

```bash
curl -X POST http://127.0.0.1:8000/mem0-agent/memories \
  -H "X-API-Key: <CODEX_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "项目使用 uv 管理依赖"}],
    "infer": true
  }'
```

- `infer: true`（默认）— 调 LLM 抽取记忆，需在 `.env` 配好 `DEEPSEEK_API_KEY`
- `infer: false` — 原样直写，不依赖 LLM
- `scope` 省略时默认写入个人跨 harness 共享区 `proj_default:u_default`（其他 harness 可见）
- 指定私有 scope：`"scope": "proj_default:u_default:a_codex"`

## 检索记忆

```bash
curl -X POST http://127.0.0.1:8000/mem0-agent/search \
  -H "X-API-Key: <CODEX_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"query": "依赖管理方式", "top_k": 5}'
```

默认检索所有可见 scope 并合并去重；也可用 `"scopes": ["proj_default:u_default"]` 指定。

## 查看可见 scope

```bash
curl http://127.0.0.1:8000/mem0-agent/scopes \
  -H "X-API-Key: <CODEX_KEY>"
```

## 团队共享授权

```bash
# 授权用户 bob 读写团队区
curl -X POST http://127.0.0.1:8000/mem0-agent/sharing/grant \
  -H "X-API-Key: <CODEX_KEY>" -H "Content-Type: application/json" \
  -d '{"scope":"proj_default:team","grantee_type":"user","grantee_id":"bob","permission":"write"}'
```

## 身份覆盖

| Header | 说明 |
|---|---|
| `X-Mem0-User` | 覆盖默认用户（多用户共享同一 key 时） |
| `X-Mem0-Project` | 覆盖默认项目 |
