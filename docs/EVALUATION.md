# 评测基线说明

这个模块用于解决接入真实 OCR、LLM、向量检索后最容易出现的四类问题：输出格式漂移、字段幻觉、风险判断不稳定、制度引用丢失。

当前评测走完整 `run_audit()` 工作流，而不是只测单个函数，所以它更接近产品视角里的“这条报销单最后能不能审对”。

## 运行方式

```bash
python -m app.evaluation.runner
python -m app.evaluation.runner --format markdown
```

默认读取 `data/evaluation/cases.json`。如果有失败用例，命令会以非 0 状态退出，方便后续接入 CI。

## 当前指标

- `pass_rate`：整条用例是否完全通过。
- `status_accuracy`：最终状态是否符合预期，例如 `PASS` 或 `REVIEW_REQUIRED`。
- `risk_accuracy`：风险码集合是否符合预期，例如 `HOTEL_LIMIT`。
- `field_accuracy`：关键字段值是否符合预期，例如发票金额、付款金额、城市、日期。
- `policy_ref_accuracy`：制度证据引用是否符合预期，例如 `TRAVEL-V1-4.2-A`。

## 用例格式

```json
{
  "case_id": "CASE_TRAVEL_OVER_LIMIT_001",
  "name": "住宿金额超标",
  "documents_dir": "data/demo/over_limit",
  "expected_status": "REVIEW_REQUIRED",
  "expected_risks": ["HOTEL_LIMIT"],
  "expected_fields": {
    "invoice_amount": "680.00",
    "payment_amount": "680.00",
    "travel_city": "上海"
  },
  "expected_policy_refs": ["TRAVEL-V1-4.2-A"]
}
```

## 后续扩展建议

1. 接 OCR 后，新增扫描票据样例，把 OCR 文本和最终字段都纳入评测。
2. 接真实 LLM 后，先固定 `MODEL_PROVIDER`、模型版本、temperature 和 prompt 版本，再跑同一批用例。
3. 每次改 prompt、字段 schema、RAG 排序、规则工具，都先跑评测；如果准确率下降，先定位是字段错、制度错，还是规则错。
4. 新增人工复核闭环后，把“人工修正前失败、修正后通过”的用例也加入评测。
