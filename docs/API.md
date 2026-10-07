# DocAudit API 说明

## 1. 目标

本 API 用于把审核 Agent 工作流暴露为可联调、可恢复、可追踪的 HTTP 服务。

当前版本默认使用本地 SQLite 存储任务、上传材料、审核报告、字段修正、人工复核项和审计事件。它仍不是完整生产系统，但已经避免了“服务重启后任务丢失”的玩具化问题。后续可以把 `app/api/store.py` 的实现替换为 PostgreSQL 或 LangGraph Checkpoint。

## 2. 启动方式

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --reload --port 8000
```

打开接口文档：

```text
http://127.0.0.1:8000/docs
```

`127.0.0.1` 指**运行服务的那台机器**（服务没启动则打不开这个页面）；要让别的机器访问，启动时加 `--host 0.0.0.0`，并把地址换成本机 IP 或域名，详见 README「FastAPI接口」一节。

端口可自定义（`--port` 或环境变量 `API_PORT`，例如 `8102`），文档地址随之变化。

## 3. 接口清单

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |
| `POST` | `/api/v1/audit-tasks` | 创建审核任务 |
| `GET` | `/api/v1/audit-tasks` | 查询审核任务列表 |
| `POST` | `/api/v1/audit-tasks/{task_id}/documents` | 上传单份材料 |
| `POST` | `/api/v1/audit-tasks/{task_id}/run` | 运行审核 |
| `GET` | `/api/v1/audit-tasks/{task_id}` | 获取任务概要 |
| `GET` | `/api/v1/audit-tasks/{task_id}/fields` | 获取结构化字段 |
| `GET` | `/api/v1/audit-tasks/{task_id}/risks` | 获取风险列表 |
| `GET` | `/api/v1/audit-tasks/{task_id}/trace` | 获取执行轨迹 |
| `GET` | `/api/v1/audit-tasks/{task_id}/summary` | 获取面向页面展示的可解释摘要 |
| `GET` | `/api/v1/audit-tasks/{task_id}/report.html` | 下载 HTML 正式审核报告 |
| `GET` | `/api/v1/audit-tasks/{task_id}/review-items` | 获取人工复核项 |
| `GET` | `/api/v1/audit-tasks/{task_id}/events` | 获取任务生命周期事件 |
| `PATCH` | `/api/v1/audit-tasks/{task_id}/fields/{field_name}` | 提交字段人工修正 |
| `POST` | `/api/v1/review-items/{review_item_id}/decision` | 提交人工复核决策 |

## 4. 任务存储

默认配置：

```text
AUDIT_TASK_STORE=sqlite
AUDIT_TASK_DB=data/runtime/audit_tasks.sqlite3
```

如果没有配置 `AUDIT_TASK_DB`，系统会自动使用仓库根目录下的 `data/runtime/audit_tasks.sqlite3`。本地调试时如果希望回到无状态模式，可以设置：

```text
AUDIT_TASK_STORE=memory
```

SQLite 当前持久化内容：

| 数据 | 说明 |
| --- | --- |
| 任务概要 | `task_id`、状态、创建时间、更新时间、错误信息 |
| 上传材料 | 文件名和文件二进制内容 |
| 审核报告 | 字段、风险、制度证据、检查结果、执行轨迹 |
| 字段修正 | 原值、新值、修正原因、时间 |
| 复核项 | 风险类型、处理状态、处理人 |
| 审计事件 | 任务创建、材料上传、审核完成、字段修正、复核决策等 |

## 5. 状态流转

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

### 5.1 审核结论（`result_status`）

审核完成后的结论有四种，前两种是"对材料给出的判断"，后两种是"系统明确说明自己给不出判断"：

| 结论 | 含义 | 前端展示 |
| --- | --- | --- |
| `PASS` | 未发现 HIGH/MEDIUM 风险 | 通过 |
| `REVIEW_REQUIRED` | 存在 HIGH/MEDIUM 风险，需人工复核 | 需复核 |
| `UNDETERMINED` | **材料未被真正识别**（演示引擎文本 / 解析为空 / 抽不到任何字段），系统不给结论；只输出一条 `MATERIAL_UNREADABLE`（HIGH）风险说明原因与改法 | 无法判定（材料未识别） |
| `FAILED` | 流程本身失败（步数/预算/无进展），`failure_reason` 给出原因 | 失败 |

`UNDETERMINED` 的典型触发条件：

```text
OCR_ENGINE=mock（演示模式）  +  上传图片/扫描件  -> 解析出的是内置演示文本
OCR 只吐出乱码或空白（有效文本不足 12 字）
材料解析成功但一个字段都没抽到（无证据即无结论）
```

此时报告里的 `fields` 为空（人工填写/修正过的字段保留），`failure_reason` 说明怎么改：

```json
{
  "status": "UNDETERMINED",
  "failure_reason": "invoice.png 的内容并未被真正识别：当前 OCR 引擎是演示模式（OCR_ENGINE=mock），系统解析出的是内置演示文本，与上传材料无关。……请在 .env 配置真实 OCR 引擎（OCR_ENGINE=tesseract|http|mineru）后重新提交。"
}
```

## 6. 本地测试

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
- 查询字段、风险、执行轨迹和可解释摘要。
- 查询任务列表和任务生命周期事件。
- 对字段提交人工修正，重新运行审核并确认风险变化。
- 对复核项提交决策并保留处理人。
- SQLite 存储跨实例重建后仍能读取任务、文件、报告和事件。
- 空任务运行被拒绝。
- 不存在的任务返回 `404`。

## 7. 可解释摘要

`GET /api/v1/audit-tasks/{task_id}/summary` 返回面向前端展示的结构化摘要，适合直接渲染审核结果页。

摘要包含：

| 字段 | 含义 |
| --- | --- |
| `status` | 审核状态 |
| `conclusion` | 面向用户的结论说明 |
| `next_action` | 建议下一步动作 |
| `key_fields` | 关键字段、置信度和原文来源 |
| `risks` | 风险类型、级别、原因、证据和处理建议 |
| `checks` | 各业务工具的检查状态与说明 |
| `policy_evidence` | 制度条款引用 |
| `trace` | Agent 执行轨迹 |

## 8. 任务命名（新增）

任务有两个标识：技术编号 `task-xxxxxxxxxxxx`（接口/审计用）与**报销单名称**（人看的主标识）。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| PATCH | `/api/v1/audit-tasks/{task_id}` | 改报销单名称（改名后锁定，不再被审核结果覆盖） |

命名规则：**日期区间 + 人物 + 事件**，例如 `2026.9.18-9.19 张三 住宿报销`。

- 建单时按 `applicant` + `expense_type` + `note`(备注/事由，作为“事件”) 生成，例如 `张三 住宿报销`；
- 运行审核后按抽取字段补全日期区间（出差开始/结束，缺失时回退发票日期）；
- `title` 可显式传入，传了就锁定；未锁定的名称会在每次审核后被自动补全；
- 名称会做清洗（去控制字符、折叠空白、限长 80 字符）并用于 HTML 报告的标题与下载文件名。

```bash
curl -X POST http://127.0.0.1:8000/api/v1/audit-tasks \
  -H "Content-Type: application/json" \
  -d '{"applicant":"张三","department":"市场部","expense_type":"HOTEL","note":"住宿报销"}'
# -> {"task_id":"task-...","title":"张三 住宿报销", ...}   运行审核后 title -> "2026.8.1-8.2 张三 住宿报销"

curl -X PATCH http://127.0.0.1:8000/api/v1/audit-tasks/task-xxxxxxxxxxxx \
  -H "Content-Type: application/json" -d '{"title":"2026.9.18-9.19 张三 住宿报销"}'
```

## 9. 制度管理与评测接口（新增）

### 9.1 制度管理（PRD §5.4 FR-301~FR-304）

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/policies` | 上传制度 TXT/DOCX/PDF，录入名称、版本、生效时间、部门、费用类型；立即切片入库，状态 `draft` |
| GET | `/api/v1/policies` | 制度列表（可按 `status_filter` 过滤） |
| GET | `/api/v1/policies/{policy_id}` | 制度详情（含切片数） |
| POST | `/api/v1/policies/{policy_id}/publish` | 发布索引；`switch_version=true` 时同时停用同制度名旧版本 |
| POST | `/api/v1/policies/{policy_id}/disable` | 停用制度，历史版本保留可追溯 |

- 元数据不完整或无切片时拒绝发布（FR-301/302）；
- 停用/切换版本只改状态，不物理删除（FR-303）；
- 检索侧按部门、费用类型与生效区间过滤（FR-304）：`retrieve_policy(city, department=..., as_of=...)`，
  审核流程可通过 `run_audit(..., department=..., as_of=...)` 传入任务上下文；
- 切片策略与基线制度一致：按章/条/款切分、保留父标题路径 `section_path`、表格按行切分并保留表头；
- 制度库位置由 `POLICY_STORE_PATH` 控制（默认 `data/runtime/policies.sqlite3`）。

示例：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/policies \
  -F "file=@travel_policy.txt" -F "name=差旅住宿制度" -F "version=V1" \
  -F "effective_from=2026-01-01" -F "department=ALL" -F "expense_type=TRAVEL"
curl -X POST "http://127.0.0.1:8000/api/v1/policies/UP-XXXXXXXX/publish?switch_version=true"
```

### 9.2 离线评测（PRD §12.2）

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/api/v1/evaluations/run` | 运行回归评测，返回指标（可含逐例结果或 Markdown） |

请求体（可省略）：`{"cases_path": "data/evaluation/cases.json", "format": "json"}`。
`cases_path` 必须位于仓库目录内，否则返回 400（防路径穿越）；文件不存在返回 404。

## 10. 当前限制

- 当前 SQLite 是本地单机持久化，不适合作为多实例部署的共享数据库。
- 权限为最小可用 RBAC（`AUTH_MODE=enforce` + 请求头身份 + 角色白名单 + 任务归属），尚未接入 SSO/JWT 与组织范围授权。
- 字段修正后的第一版实现采用全量重跑，尚未做依赖图局部重跑。
- 当前报告导出为 HTML，尚未提供 PDF 原生生成。
- 尚未实现幂等请求 ID、文件去重和任务恢复锁。
- RAG_MODE=milvus 时，新发布制度的向量索引重建仍需要单独运行构建脚本（local 模式即时生效）。
- **已知边界：默认配置（`OCR_ENGINE=mock`）不识别图片/扫描件，这类材料会得到"无法判定"而不是结论**；识别能力取决于配置的引擎（Tesseract/HTTP/MinerU），引擎本身的准确率不在本项目的实现范围内。
- 材料可读性判定按"演示文本标记 / 有效文本长度 / 是否抽到字段"三条确定性规则判断，不做图像质量评分；极端情况下（清晰图片但正文不含任何可抽字段）会判为无法判定，需要人工补充字段后重跑。

这些限制是当前生产化改造的下一批边界，不应在对外说明中夸大。

## 11. 后续建议

优先级建议：

1. 将字段修正后的全量重跑升级为按依赖节点局部重跑。
2. 将 SQLite 存储替换为 PostgreSQL，并增加 Alembic 迁移。
3. 将 `trace` 拆成结构化节点轨迹与工具调用轨迹。
4. 将 HTML 报告进一步扩展为 PDF 报告、电子签章和归档流水号。
