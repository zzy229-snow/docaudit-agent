# 部署与启动说明（PRD §3 MVP / §17.2 交付物 / §22.3 端口约定）

项目支持三种启动方式：Docker Compose（推荐用于演示与联调）、一键脚本（本机开发）、手动命令。

## 1. Docker Compose（单机部署）

```bash
docker compose up -d --build          # API:8000,审核工作台:8500
docker compose ps
docker compose logs -f api
```

| 服务 | 端口（默认） | 说明 |
| --- | --- | --- |
| `api` | 8000（公共联调，PRD §22.3） | FastAPI：`/docs` 为 OpenAPI 文档 |
| `web` | 8500（公共联调） | Streamlit 审核工作台 |

环境变量覆盖方式：在仓库根目录的 `.env` 中写 `KEY=VALUE`，compose 会自动读取用于插值
（`.env` 已在 `.gitignore` 中，不要提交密钥）。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `AGENT_ENGINE` | `langgraph` | `langgraph` / `sequential` |
| `AUDIT_TASK_STORE` | `sqlite` | `sqlite` / `memory` |
| `OCR_ENGINE` | `mock` | `mock` / `tesseract` / `http` / `mineru` / `auto` |
| `RAG_MODE` | `local` | `mock` / `local` / `milvus` |
| `MODEL_PROVIDER` | `mock` | 真实模型见 `docs/MODEL_GATEWAY.md` |
| `AUTH_MODE` | `off` | 设为 `enforce` 开启 RBAC（§2 角色 / §15 访问控制） |
| `API_PORT` / `WEB_PORT` | `8000` / `8500` | 宿主机端口（个人调试可用 8101/8501、8102/8502） |

持久化：`./data/runtime` 挂载到容器内 `/app/data/runtime`，保存任务库
`audit_tasks.sqlite3`、发票查重登记表 `invoice_registry.sqlite3` 与制度库 `policies.sqlite3`，
容器重建不丢数据。镜像不包含 `data/runtime`、`data/rag` 与 `.env`（见 `.dockerignore`）。

容器内验证：

```bash
docker compose exec api python -m unittest discover -s tests
docker compose exec api python -m app.evaluation.runner --format markdown
docker compose exec api python -c "from app.agent.graph import resolve_engine; print(resolve_engine())"
```

## 2. 一键脚本（本机开发）

```bash
bash scripts/start.sh                     # 创建 .venv → 安装依赖 → 启动 API(8000)+工作台(8500)
API_PORT=8101 WEB_PORT=8501 bash scripts/start.sh   # 自定义端口
bash scripts/stop.sh                      # 按 logs/*.pid 停止
```

Windows 使用 `scripts\start.bat`（可用 `set API_PORT=8102 && set WEB_PORT=8502` 覆盖端口）。
日志写入 `logs/api.log`、`logs/web.log`（`logs/` 不进入版本库）。

## 3. 手动命令

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                              # 默认 mock 模式,无需密钥
uvicorn app.api.main:app --reload --port 8000      # API 与文档 http://127.0.0.1:8000/docs
streamlit run streamlit_app.py                     # 审核工作台
```

## 4. 权限模式（AUTH_MODE=enforce）

| 头 | 说明 |
| --- | --- |
| `X-User-Id` | 用户标识（与任务 `applicant` 对应） |
| `X-Role` | `APPLICANT` / `REVIEWER` / `POLICY_ADMIN` / `ADMIN` / `DEVELOPER` |
| `X-Department` | 可选，部门（用于制度部门过滤） |

```bash
curl -H "X-User-Id: alice" -H "X-Role: APPLICANT" \
     -H "Content-Type: application/json" \
     -d '{"applicant":"alice","department":"研发部","expense_type":"TRAVEL"}' \
     http://127.0.0.1:8000/api/v1/audit-tasks
```

- 申请人只能访问本人任务；越权访问返回 404（不泄露任务是否存在，AC-09）并写入
  `ACCESS_DENIED` 审计事件；
- 制度管理接口需要 `POLICY_ADMIN`/`ADMIN`；评测接口需要 `DEVELOPER`/`ADMIN`；
  复核决策需要 `REVIEWER`/`ADMIN`；
- 该实现是**最小可用 RBAC**：身份来自请求头，真实部署应替换为 SSO/JWT 校验
  （接口形状不变），见 `docs/API.md` §9 当前限制。

## 5. 未纳入镜像的重依赖

`requirements-rag.txt`（bge-m3 / FlagEmbedding / pymilvus）与 OCR 引擎（Tesseract、MinerU）
不进入默认镜像：它们在 `RAG_MODE=milvus` 或 `OCR_ENGINE=tesseract|mineru|auto` 时才需要，
按 PRD §23.5 由云端/独立服务承载。默认镜像使用 `RAG_MODE=local` + `OCR_ENGINE=mock`，
零外部依赖即可完整跑通链路与评测。
