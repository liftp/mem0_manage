# DeepSeek Coder REST 接入 (不支持 MCP 时的替代方案)

harness 通过统一 REST API 读写记忆。所有请求需带 `X-API-Key: <DEEPSEEK_CODER_KEY>`
（值来自部署时生成的 `harness-keys.txt`）。

## 写入记忆

```bash
curl -X POST http://127.0.0.1:8000/mem0-agent/memories \
  -H "X-API-Key: <DEEPSEEK_CODER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{"role": "user", "content": "项目使用 uv 管理依赖"}],
    "infer": true
  }'
```

scope 省略时默认写入个人跨 harness 共享区 `proj_default:u_default`。
指定私有 scope：`"scope": "proj_default:u_default:a_deepseek-coder"`。

## 检索记忆

```bash
curl -X POST http://127.0.0.1:8000/mem0-agent/search \
  -H "X-API-Key: <DEEPSEEK_CODER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"query": "依赖管理方式", "top_k": 5}'
```

## 查看可见 scope

```bash
curl http://127.0.0.1:8000/mem0-agent/scopes \
  -H "X-API-Key: <DEEPSEEK_CODER_KEY>"
```

## 团队共享授权

```bash
# 授权用户 bob 读写团队区
curl -X POST http://127.0.0.1:8000/mem0-agent/sharing/grant \
  -H "X-API-Key: <KEY>" -H "Content-Type: application/json" \
  -d '{"scope":"proj_default:team","grantee_type":"user","grantee_id":"bob","permission":"write"}'
```

## 身份覆盖

| Header | 说明 |
|---|---|
| `X-Mem0-User` | 覆盖默认用户（多用户共享同一 key 时） |
| `X-Mem0-Project` | 覆盖默认项目 |
