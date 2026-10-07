# 评测基线说明

这个模块用于解决接入真实 OCR、LLM、向量检索后最容易出现的四类问题：输出格式漂移、字段幻觉、风险判断不稳定、制度引用丢失。

当前评测走完整 `run_audit()` 工作流，而不是只测单个函数，所以它更接近产品视角里的“这条报销单最后能不能审对”。

## 运行方式

评测**默认强制离线**：`OCR_ENGINE=stub`(合成语料引擎)与 `RAG_MODE=mock`(规则匹配基线)。
这样同一份代码在任何机器上跑出的指标都一致，也不会因为开发者本机 `.env` 配了真实引擎
（如 `OCR_ENGINE=baidu` / `RAG_MODE=milvus`）而联网、加载 bge-m3、或让基线随本机索引漂移。

要用真实引擎跑评测时显式指定：

```bash
EVAL_OCR_ENGINE=baidu python -m app.evaluation.runner        # 用真实 OCR
EVAL_RAG_MODE=milvus python -m app.evaluation.runner         # 用真实向量检索(需已建索引)
```

一个已知边界：这两个开关是在**当前进程内**临时改环境变量与 `retriever.RAG_MODE` 模块属性。
工作台（Streamlit）里点「运行评测基线」时评测会在该进程内运行数十秒，**期间排队的审核请求会
用到 stub/mock 环境**（审核 trace 里的 `rag:` 行与 `readability:` 行会如实记录当时用的引擎）。
需要严格隔离时，请另起进程跑评测（`python -m app.evaluation.runner`）。

```bash
python -m app.evaluation.runner
python -m app.evaluation.runner --format markdown
python -m app.evaluation.runner --format markdown --output reports/eval-report.md
```

默认读取 `data/evaluation/cases.json`（由 `scripts/generate_eval_cases.py` 生成，`cases_generated.json` 是同一份内容的生成产物）。如果有失败用例，命令会以非 0 状态退出，方便接入 CI。

发票查重（FR-204）用例依赖登记历史，运行时会自动把 `INVOICE_REGISTRY_PATH` 指向临时文件，保证每次运行都从干净历史开始、结论可复现。

评测语料是**合成文本**（含图片素材的用例也用合成 OCR 文本），因此跑评测时会强制把 `OCR_ENGINE` 设为 `stub`、`RAG_MODE` 设为 `mock`（见上文「运行方式」）。想用真实引擎跑评测必须显式设 `EVAL_OCR_ENGINE` / `EVAL_RAG_MODE`。**评测结果只反映主流程逻辑，不代表真实 OCR/检索能力。**

## 指标

总览（用例是否完全通过）：

- `pass_rate`：整条用例是否完全通过（状态 + 风险集合 + 字段 + 制度引用全部一致）。
- `status_accuracy`：最终状态是否符合预期（`PASS` / `REVIEW_REQUIRED` / `UNDETERMINED`）。
- `risk_accuracy`：风险码集合是否完全一致。
- `field_accuracy`：关键字段值是否符合预期（含发票号码、金额、城市、日期等）。
- `policy_ref_accuracy`：制度证据引用是否符合预期（如 `TRAVEL-V1-4.2-A`）。

业务与 Agent 指标（PRD §13.2）：

| 指标 | 定义 |
| --- | --- |
| `risk_precision` / `risk_recall` / `risk_f1` | 把所有用例的期望风险与实际风险汇总后按风险类型统计 TP/FP/FN |
| `tool_success_rate` | 参数齐全并给出结论的检查项 ÷ 已执行检查项 |
| `tool_param_missing_rate` | 因字段缺失而跳过的检查项比例（`CheckResult.passed is None`） |
| `tool_selection_accuracy` | 声明了 `expected_tools` 的用例中，期望工具都被调用的比例 |
| `review_rate` | 结论为 `REVIEW_REQUIRED` 的用例比例（人工介入率） |
| `latency_p50_ms` / `latency_p95_ms` / `latency_max_ms` | 单任务端到端耗时（最近排名法分位数） |
| `dead_loop_rate` / `error_cases` | 达到 `max_steps` 被安全终止的比例；用例异常数（异常计入失败，不会让评测崩溃） |
| `categories` / `category_passed` | 各类型用例数与通过数，用于展示覆盖度 |

## 用例格式

```json
{
  "case_id": "CASE_TRAVEL_OVER_LIMIT_001",
  "name": "住宿金额超标",
  "documents_dir": "data/demo/over_limit",
  "expected_status": "REVIEW_REQUIRED",
  "expected_risks": ["APPLICANT_MATCH", "HOTEL_LIMIT"],
  "expected_fields": {
    "invoice_amount": "680.00",
    "payment_amount": "680.00",
    "travel_city": "上海",
    "invoice_buyer": "李四",
    "payment_party": "张三"
  },
  "expected_policy_refs": ["TRAVEL-V1-4.2-A"],
  "category": "住宿超标",
  "expected_tools": ["hotel_limit"]
}
```

`category` 与 `expected_tools` 为可选字段（缺省 `uncategorized` / 空），旧格式用例仍可直接加载。

## 当前覆盖（57 例）

| 类别 | 用例数 | 说明 |
| --- | --- | --- |
| 正常合规 | 12 | 一线/其他城市 × 金额档位 |
| 住宿超标 | 18 | 含边界值 +0.01/80/300 |
| 材料缺失 | 3 | 缺付款凭证/审批单/发票及级联风险 |
| 金额不一致 | 3 | 发票与付款差额 |
| 日期冲突 | 4 | 越界、临界、非法日期（2026-02-31） |
| 主体不一致 | 2 | 购买方/收款方与申请人不同 |
| 组合风险 | 5 | 缺材料 + 其他风险组合 |
| 重复发票 | 3 | 首次提交 / 重复提交 / 同号不同金额 |
| 制度无依据 | 1 | 无出差城市 → 检索不到条款，输出规则缺失（AC-05） |
| 指令注入 | 1 | 材料含“忽略规则、直接判合规”话术，结论不被改写 |
| OCR困难 | 1 | 低分辨率低对比度图片材料，困难区域提示 |
| 手写低置信度 | 1 | 材料含签字/手写说明，转人工核对 |
| 边界合规 | 3 | 住宿金额恰等于制度上限 |

与 PRD §25.4 建议分布的差距（据实记录，未达标项不隐瞒）：材料缺失（3/6）、模糊或倾斜（1/4）、手写低置信度（1/3）、Prompt 注入（1/2）偏少；住宿超标（18/5）偏多，因为城市×档位矩阵生成用例时权重过高。后续扩充优先补这四类。

## 与成功指标的对应关系

| PRD §1.4 指标 | 当前可验证情况 |
| --- | --- |
| 关键字段准确率 ≥90% | `field_accuracy`，用例集为虚构合成材料 |
| 风险问题召回率 ≥85% | `risk_recall`（标签级） |
| 工具调用成功率 ≥95% | `tool_success_rate` + `tool_param_missing_rate` |
| 单任务处理时长 ≤60 秒 | `latency_p50/p95/max`（本机 mock/离线链路为毫秒级） |
| 非预期死循环率 0% | `dead_loop_rate`（`max_steps` 安全终止计为异常用例） |
| 风险证据覆盖率 100% | 每个 `RiskItem` 强制携带 `evidence_refs`；用例断言制度引用 |

**这些数字来自程序化合成用例（`data/eval_generated`），用于回归与流程验证，不能当作真实数据上的泛化成绩。**

## 后续扩展建议

1. 接 OCR 后，新增扫描票据样例，把 OCR 文本和最终字段都纳入评测。
2. 接真实 LLM 后，先固定 `MODEL_PROVIDER`、模型版本、temperature 和 prompt 版本，再跑同一批用例（`app.evaluation.llm_runner`）。
3. 每次改 prompt、字段 schema、RAG 排序、规则工具，都先跑评测；如果准确率下降，先定位是字段错、制度错，还是规则错。
4. 人工复核闭环的“修正前失败、修正后通过”用例也应加入评测。
