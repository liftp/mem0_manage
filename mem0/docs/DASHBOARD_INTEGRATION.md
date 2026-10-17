# 接入官方 Dashboard（图形管理后台）— 方案 A

本项目（`mem0ai 2.2.1` 的补丁版，含 `/mem0-agent` 增强层）默认只有 REST API，没有前端。
官方 mem0 的 dashboard 与 server 同仓库（`mem0ai/mem0` 的 `server/dashboard`，Next.js），
需要**单独拉取源码 + 构建镜像**后叠加到现有 compose 中。

## 一、架构与数据流

```
浏览器(Windows) ──http://localhost:3000──> WSL 转发 ──> dashboard 容器(:3000)
   │                                                  │  NEXT_PUBLIC_API_URL=http://localhost:8000
   │                                                  │  API_INTERNAL_URL=http://mem0:8000
   │                                                  ▼
   └──http://localhost:8000──> WSL 转发 ──> mem0 容器(:8000) ──> Postgres+pgvector
```

- `NEXT_PUBLIC_API_URL`：浏览器侧访问 API 的地址（WSL2 自动把 Windows 的 localhost 转发进 WSL）。
- `API_INTERNAL_URL`：dashboard 容器内侧访问 mem0 服务（走 compose 网络，用服务名 `mem0:8000`）。
- mem0 的 `DASHBOARD_URL`（主 compose 已设 `http://localhost:3000`）必须 == 浏览器访问 dashboard 的 origin，否则 CORS 拦截。

## 二、步骤

### 1. 拉取匹配版本的 dashboard 源码
```bash
cd /usr/local/soft/mem0
bash scripts/fetch_dashboard.sh          # 默认拉 v2.2.1；覆盖版本: MEM0_TAG=vX.Y.Z bash scripts/fetch_dashboard.sh
```
脚本会 sparse-clone `mem0ai/mem0` 的 `server/dashboard` 到 `./dashboard`（含官方 `Dockerfile`）。
若 clone 被代理拦，确认 `git config --global http.proxy` 指向你的 WSL-NAT-PROXY。

> 若仓库里 dashboard 还引用了官方 `server/` 下的共享模块导致构建报错，按需再 sparse-checkout 对应目录。

### 2.（可选兜底）若官方 Dockerfile 因离线/代理导致 npm install 失败
```bash
cp dashboard/Dockerfile.proxy dashboard/Dockerfile
```
该 Dockerfile 仅在 build 阶段把代理透传给 npm，并把 `NEXT_PUBLIC_API_URL` 固化进构建产物。

### 3. 启动 dashboard 服务（叠加到主 compose）
```bash
cd /usr/local/soft/mem0
docker compose -f docker-compose.yml -f docker-compose.dashboard.yml up -d --build dashboard
```
首次会构建 Next.js 镜像（npm install 经代理，可能较慢）。构建完自动起容器并映射 `3000:3000`。

### 4. 创建管理员账号（官方 JWT 体系，与 m0hk_ 无关）
打开 `http://localhost:3000`：
- 首次无管理员 → 自动跳转到 `/setup` 向导，填 名称/邮箱/密码 即创建首个 admin；
- 之后生成首个 API key（`m0sk_...`，官方 `api_keys` 表，与增强层 `m0hk_` harness key 是两套体系）。

或用命令行（`server/Makefile` 自带）：
```bash
make bootstrap            # 起全部并自动 seed 管理员
# 或仅 seed: make seed EMAIL=admin@example.com PASSWORD='xxx'
```

### 5. 验证
```bash
curl -s -o /dev/null -w "dashboard=%{http_code}\n" http://localhost:3000/api/health
curl -s -o /dev/null -w "api=%{http_code}\n"        http://localhost:8000/docs
docker compose ps        # mem0 与 dashboard 均为 Up
```

## 三、能力与边界（务必看清）

| 能做什么 | 说明 |
|---|---|
| 浏览 / 搜索 / 删除记忆 | dashboard 直接读 **同一个 pgvector `memories` 集合**。通过 `/mem0-agent/memories` 写入的记忆（带 `scope`/`harness` 元数据）**也能在 dashboard 看到**，只是没有 scope 分组 UI |
| 官方 API key 管理 | `/auth/*` + `/api-keys`，生成 `m0sk_...`，与 `m0hk_` harness key 独立 |
| 配置查看 | 只读展示当前 LLM/Embedder 配置 |

| 看不到 / 做不到 | 原因 |
|---|---|
| **增强层 scope / ACL 管理** | `proj_xxx:team`、私有单 harness、团队共享授权是本项目自研层，官方 UI 不认识 `scope` 概念 → 这部分**仍只能走 REST**（`/mem0-agent/sharing/*`） |
| 多项目强隔离视图 | 项目内 project 由 header 覆盖，官方 UI 无对应语义 |

## 四、风险与注意

1. **版本耦合（最高优先级）**：dashboard 必须匹配 `mem0ai 2.2.1`。用错版本 → API schema 漂移 → 白屏。升级 mem0 时同步升级 dashboard 源码到对应 tag。
2. **两套鉴权并存**：dashboard 用官方 JWT（`/auth/login` + `m0sk_`）；harness 程序化接入用 `X-API-Key: m0hk_`。不要混用，也不要指望在 dashboard 里管理 harness key。
3. **构建需出网 + 代理**：npm install 走 `HTTP_PROXY`（已在 compose build.args 透传）。若代理对 `registry.npmjs.org` 也像拦截 `tuna` 那样拦截，构建会失败——此时需把 npm 源/代理加白，或改用官方 Dockerfile 的运行时改写机制。
4. **端口占用**：dashboard 用 `3000`，mem0 用 `8000`。确保 Windows/WSL 上这两个端口未被占用。
5. **未在此环境实测构建**：本环境无宿主 node、且 npm 构建需联网验证，上述文件为"可直接执行"的接入片段，首次 `up --build` 由你在 WSL 终端跑以确认能通过代理编译。

## 五、相关文件
- `scripts/fetch_dashboard.sh` — 拉取匹配版本 dashboard 源码
- `docker-compose.dashboard.yml` — 叠加的 dashboard 服务定义
- `dashboard/Dockerfile.proxy` — 代理兜底 Dockerfile
- 主 `docker-compose.yml` — 已含 `mem0` 服务与 `mem0_network`，`DASHBOARD_URL=http://localhost:3000`
