# DocAudit Agent 演示指南

这份指南用于演示、演示或对外说明讲解。当前版本已升级为审核工作台形态，默认不调用真实 API，但任务、材料、审核结果和审计事件会持久化到本地 SQLite。

## 1. 启动方式

```powershell
.\.venv\Scripts\python.exe -m streamlit run streamlit_app.py
```

如果首次运行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## 2. 推荐演示流程

1. 打开页面后先看左侧“系统运行状态”：
   - 模型模式：默认 `Mock / 离线安全模式`
   - OCR 模式：默认 `mock`
   - RAG 模式：默认 `mock`
   - 任务存储：默认 `sqlite`

   任务列表以**报销单名称**展示（按“日期区间 + 人物 + 事件”自动生成，如
   `2026.8.1-8.2 张三 住宿报销`），技术编号在最后一列，点选行即可打开详情。

2. 在左侧“快速演示”选择样例。

3. 点击“创建样例任务”，页面会进入真实任务流：创建任务、写入材料、进入待审核状态。

4. 在“审核详情”点击“运行 / 重新运行审核”。

> 提示（FR-204 重复发票查重）：同一张演示发票第二次提交时会命中查重并产生
> `DUPLICATE_INVOICE`（HIGH）风险，这是预期行为。若演示时希望只看到材料本身的
> 结论，先清空登记表：`rm -f data/runtime/invoice_registry.sqlite3`
> （或换用不同发票号码的样例）。评测命令已自动隔离该登记表，不受影响。

5. 按顺序讲解：
   - 顶部任务总数、待审核、需复核、已通过指标；
   - 任务中心列表；
   - 材料清单；
   - 审核结论指标卡；
   - 风险看板；
   - 字段证据和人工字段修正；
   - 检查项状态；
   - 制度依据；
   - 人工复核工作台；
   - 事件时间线；
   - JSON 审核包下载。

6. 重点强调：现在页面不是直接调用 `run_audit` 的单次脚本，而是走任务中心和 SQLite 持久化，服务重启后历史任务仍可读取。

## 3. 五组内置样例

| 样例 | 预期状态 | 预期风险 | 讲解重点 |
| --- | --- | --- | --- |
| 正常报销：三类材料齐全 | PASS | 无 | 字段、制度、检查项均通过 |
| 住宿超标 + 主体不一致 | REVIEW_REQUIRED | `HOTEL_LIMIT`、`APPLICANT_MATCH` | 多风险卡片和制度引用 |
| 缺少付款凭证 | REVIEW_REQUIRED | `REQUIRED_DOCUMENTS`、`AMOUNT_MATCH` | 缺材料导致无法完成金额核对 |
| 主体不一致但金额正常 | REVIEW_REQUIRED | `APPLICANT_MATCH` | 金额合规但主体不一致 |
| 发票日期超出行程 | REVIEW_REQUIRED | `DATE_RANGE` | 日期规则可解释 |

## 4. 讲解要点

可以这样说明系统边界：

- “当前版本先保证财务审核主流程可解释、可测试、可回归。”
- “LLM 只负责补字段，不直接决定审核结论。”
- “审核结论由确定性工具输出，降低幻觉风险。”
- “真实模型接入前已有 JSON Schema、原文证据校验、低置信拒收和 fallback。”
- “默认 mock 模式不需要 API Key，适合本地演示和团队协作。”
- “任务、上传材料、审核结果、字段修正、复核决策和事件日志已经持久化，页面更接近真实审核后台。”

## 5. 真实 API 演示

如果已经购买 API：

1. 复制 `.env.example` 为 `.env`。
2. 填写 `MODEL_PROVIDER`、`MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME`。
3. 运行：

   ```powershell
   .\.venv\Scripts\python.exe -m app.services.model_smoke
   .\.venv\Scripts\python.exe -m app.evaluation.runner --format markdown --output reports/eval-real-model.md
   ```

4. 用 `docs/REAL_MODEL_EVAL_TEMPLATE.md` 记录结果。

## 6. 不要夸大的能力

当前不能宣称已经实现：

- 生产级 OCR；
- 生产级向量知识库；
- 多用户权限；
- 完整 LangGraph 编排；
- 企业级财务规则全覆盖。

可以宣称已经实现：

- 可运行审核主流程；
- 可解释风险输出；
- 人工修正闭环接口；
- 本地 SQLite 任务持久化；
- 任务中心、复核工作台和审计事件时间线；
- 模型 API 接入护栏；
- 回归评测基线；
- 稳定演示工作台。
