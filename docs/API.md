# DocAudit API 说明

## 1. 目标

本 API 是 Agent 与系统工程方向的第三阶段交付，用于把现有审核工作流暴露为可联调的 HTTP 接口。

当前版本使用内存任务存储，适合本地开发、接口联调和演示，不适合作为生产持久化方案。后续可以将 `app/api/store.py` 替换为 SQLite、PostgreSQL 或 LangGraph Checkpoint。

## 2. 启动方式

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --reload --port 8102
```

打开接口文档：

```text
http://127.0.0.1:8102/docs
```

协作者 的建议开发端口是 `8102`。公共联调时可以改为 `8000`。

## 3. 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |
| `POST` | `/api/v1/audit-tasks` | 创建审核任务 |
| `POST` | `/api/v1/audit-tasks/{task_id}/documents` | 上传单份材料 |
| `POST` | `/api/v1/audit-tasks/{task_id}/run` | 运行审核 |
| `GET` | `/api/v1/audit-tasks/{task_id}` | 获取任务概要 |
| `GET` | `/api/v1/audit-tasks/{task_id}/fields` | 获取结构化字段 |
| `GET` | `/api/v1/audit-tasks/{task_id}/risks` | 获取风险列表 |
| `GET` | `/api/v1/audit-tasks/{task_id}/trace` | 获取执行轨迹 |

## 4. 状态流转

```text
CREATED -> READY -> COMPLETED
                  -> FAILED
```

状态含义：

| 状态 | 含义 |
| --- | --- |
| `CREATED` | 任务已创建，但尚未上传材料 |
| `READY` | 至少已上传一份材料，可以启动审核 |
| `COMPLETED` | 审核完成，可以查询字段、风险和轨迹 |
| `FAILED` | 审核运行失败，任务概要中会返回 `error` |

## 5. 本地测试

运行完整测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

只运行 API 测试：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_api_audit_task -v
```

API 测试会覆盖：

- 创建审核任务。
- 上传 `data/demo/normal` 三份样例并得到 `PASS`。
- 上传 `data/demo/over_limit` 三份样例并得到 `REVIEW_REQUIRED`。
- 查询字段、风险和执行轨迹。
- 空任务运行被拒绝。
- 不存在的任务返回 `404`。

## 6. 当前限制

- 任务存储在内存中，进程重启后任务丢失。
- 尚未实现用户、角色和权限控制。
- 尚未实现人工修正字段和人工复核决策接口。
- 尚未实现报告导出。
- 尚未实现数据库查重、任务恢复和幂等请求 ID。

这些限制是有意保留的 MVP 边界，避免在 Agent 主线尚未稳定时过早引入数据库和权限复杂度。

## 7. 后续建议

优先级建议：

1. 增加人工复核接口：`POST /api/v1/review-items/{id}/decision`。
2. 增加字段修正接口：`PATCH /api/v1/fields/{id}`。
3. 将内存任务存储替换为 SQLite 或 PostgreSQL。
4. 将 `trace` 拆成结构化节点轨迹与工具调用轨迹。
5. 增加报告导出接口：`POST /api/v1/audit-tasks/{id}/report`。
