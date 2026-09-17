# Agent 工作流开发说明

## 1. 开发目标

本次提交是 Agent 方向的第一个工程化提交，目标不是立即接入大模型，而是先把审核流程变成可观测、可测试、可继续迁移到 LangGraph 的工作流骨架。

PRD 对协作者 的主要要求包括：

- 维护 `AuditState`、Tool Schema、API、执行轨迹、部署与回归报告。
- 使用固定工作流作为主流程，在局部不确定场景中逐步增强 Planner 或 ReAct 能力。
- 保留节点、工具调用、状态、耗时、失败原因，支持后续回归测试和演示。
- 对重复调用、预算耗尽或无进展场景设置终止条件，避免 Agent 无限循环。

因此，第一阶段先补齐 Agent 的状态和轨迹能力，再逐步接入 LangGraph、模型路由、人工复核和评测报告。

## 2. 当前工作流

当前审核流程仍然是稳定的固定顺序：

```text
parse_documents -> extract -> retrieve -> check -> report
```

每个节点负责一类确定工作：

| 节点 | 职责 | 当前实现 |
| --- | --- | --- |
| `parse_documents` | 解析上传文件 | 调用 `app.parsers.loader.parse_document` |
| `extract` | 抽取统一字段 | 调用 `app.extraction.field_extractor.extract_fields` |
| `retrieve` | 检索制度证据 | 调用 `app.rag.retriever.retrieve_policy` |
| `check` | 执行业务工具 | 调用材料、金额、日期、住宿上限检查 |
| `report` | 生成结构化报告 | 返回 `AuditReport` |

固定流程的好处是稳定、可复现，适合作为财务审核类 Agent 的主干。LangGraph 后续可以承载状态迁移和条件边，但不应一开始就把所有业务判断交给自由 ReAct 循环。

## 3. 新增状态结构

文件：`app/models/state.py`

新增结构包括：

| 结构 | 用途 |
| --- | --- |
| `AgentTraceEvent` | 记录节点名称、状态、说明和耗时 |
| `ToolCallRecord` | 记录工具名称、执行状态、摘要和证据引用 |
| `HumanReviewItem` | 记录需要人工复核的原因、严重级别和证据 |
| `AgentControl` | 记录最大步数、当前步数、重试次数和终止原因 |
| `AuditState` | 汇总任务 ID、当前节点、文件、字段、证据、工具调用、人工复核项和报告 |

`AuditReport.trace` 仍保留为字符串列表，兼容 Streamlit 页面和已有测试。结构化轨迹先保存在运行态 `AuditState` 中，后续可以扩展到数据库或前端调试面板。

## 4. 轨迹与工具调用

每个节点执行时都会产生两类信息：

1. 节点轨迹

   示例：

   ```text
   parse_documents: 解析3份材料 (12.34ms)
   extract: 抽取8个字段 (1.23ms)
   retrieve: 检索到1条制度 (0.42ms)
   check: 执行4项确定性检查 (2.10ms)
   ```

2. 工具调用记录

   当前 `retrieve` 与 `check` 节点会记录工具调用：

   - `retrieve_policy`
   - `required_documents`
   - `amount_match`
   - `date_range`
   - `hotel_limit`

报告 trace 末尾会追加运行摘要：

```text
task_id=audit-xxxxxxxxxxxx
steps=5/10
tool_calls=5
human_review_items=0
```

这些字段后续可以用于：

- UI 展示 Agent 执行轨迹。
- 统计平均步数、工具成功率、人工介入率。
- 生成回归测试报告。
- 对外说明时解释每个结论从哪里来。

## 5. 终止条件

`run_audit(files, max_steps=10)` 新增 `max_steps` 参数。

每进入一个节点前，都会调用 `require_next_step`：

- 如果已经有 `terminated_reason`，立即停止。
- 如果 `step_count >= max_steps`，抛出 `RuntimeError`。
- 否则 `step_count + 1`，继续执行。

这不是完整的防循环系统，但已经为 PRD 中的“重复调用、预算、无进展终止”提供了第一层机制。后续可以继续扩展：

- 重复工具调用检测。
- 每个工具单独超时与重试预算。
- 无新增字段、无新增证据、无风险变化时停止。
- 模型 Token 或费用预算停止。

## 6. 人工复核策略

当前规则是：任何 `CheckResult.passed is not True` 的检查项都会产生：

- 一个 `RiskItem`
- 一个 `HumanReviewItem`

也就是说：

- `passed=True`：通过，不进入人工复核。
- `passed=False`：确定性风险，进入人工复核。
- `passed=None`：缺字段、缺制度或无法判断，进入人工复核。

这样符合财务审核场景的保守原则：没有证据时不判定合规，低置信度或缺依据时交给人工确认。

## 7. 本地运行

推荐在仓库根目录使用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

如果已经由 Codex 配好环境，可直接运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

当前应通过全部测试。

## 8. 测试覆盖

新增测试文件：`tests/test_agent_workflow.py`

覆盖内容：

| 测试 | 验证点 |
| --- | --- |
| `test_report_trace_contains_agent_runtime_summary` | 报告 trace 包含 task_id、步数、工具调用数和节点轨迹 |
| `test_review_case_counts_human_review_items` | 风险案例会产生人工复核项统计 |
| `test_max_steps_stops_before_unbounded_execution` | `max_steps` 能阻止无界执行 |

已有测试继续覆盖：

- 正常报销样例通过。
- 住宿超标样例生成风险和制度引用。
- 金额使用 Decimal 比较。
- 非法日期进入复核。
- 缺少付款材料进入复核。
- 图片和扫描 PDF 会进入 Mock OCR 路由。

## 9. Tool Schema 与注册表

第二阶段新增 `app/tools/schema.py` 和 `app/tools/registry.py`，将业务工具从直接函数调用升级为统一注册表执行。

当前注册工具：

| 工具 | 类别 | 职责 |
| --- | --- | --- |
| `required_documents` | `document` | 检查发票、付款凭证、审批单是否齐全 |
| `amount_match` | `amount` | 检查发票金额和付款金额是否一致 |
| `date_range` | `date` | 检查发票日期是否位于出差日期范围内 |
| `hotel_limit` | `policy` | 基于制度证据检查住宿金额是否超标 |

每个工具包含：

- `ToolSpec`：工具名、描述、类别、输入字段、幂等性和超时配置。
- `RegisteredTool`：工具元数据和实际 handler。
- `ToolExecution`：工具规范、输入摘要和结构化结果。

`check` 节点现在通过 `get_tool(name).execute(**kwargs)` 执行工具，不再直接调用底层函数。这样后续接入 LangGraph、Planner 或评测系统时，可以统一回答这些问题：

- Agent 调用了哪个工具？
- 工具输入是什么？
- 工具输出是通过、失败还是无法判断？
- 该工具是否幂等，能不能自动重试？
- 哪些证据参与了本次判断？

新增测试文件：`tests/test_tool_registry.py`

覆盖内容：

| 测试 | 验证点 |
| --- | --- |
| `test_registry_contains_core_audit_tools` | 四个核心审核工具已注册且默认幂等 |
| `test_amount_tool_executes_through_registry` | 金额工具可以通过注册表执行并保留输入摘要 |
| `test_hotel_limit_tool_keeps_policy_evidence_summary` | 制度工具可以处理证据列表并保留证据引用 |
| `test_unknown_tool_is_rejected` | 未注册工具会被拒绝 |

## 10. 后续开发路线

建议后续按以下顺序推进：

1. 将 `AuditState` 从 `TypedDict` 进一步收敛为 Pydantic 输入/输出模型。
2. 给 Tool 增加超时、错误捕获和重试预算。
3. 用 LangGraph 替换当前手写 for-loop，但复用现有节点函数。
4. 增加条件边：缺字段、低置信度、无制度依据时进入人工复核节点。
5. 增加 trace 导出能力，输出 JSON 回归报告。
6. 将 Streamlit 页面中的执行轨迹从字符串升级成表格视图。

## 11. 分支与提交建议

推荐分支：

```text
feature/agent-workflow
feature/agent-tool-schema
```

推荐提交信息：

```text
feat(agent): add workflow state tracing and guardrails
feat(agent): add tool schema registry
```

合并策略：

- 功能分支先通过本地测试。
- 推送到 Gitee 后发起合并到 `develop`。
- `develop` 集成测试稳定后，再合并到 `main` 作为演示稳定版。
