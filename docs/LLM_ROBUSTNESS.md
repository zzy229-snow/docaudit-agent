# LLM 字段抽取稳定性护栏

真实模型接入后，最大风险不是“能不能调用 API”，而是模型输出是否长期稳定、可验证、可回退。本模块把 LLM 放在规则抽取之后，只用于补充规则没有抽到的字段，并通过校验层拦截不稳定输出。

## 当前策略

1. **固定字段白名单**

   只接受 `invoice_amount`、`payment_amount`、`invoice_date`、`travel_start_date`、`travel_end_date`、`travel_city`、`invoice_number`、`applicant_name`。模型输出其他字段会被拒收。

2. **严格 JSON Schema**

   模型输出必须是：

   ```json
   {
     "fields": {
       "invoice_amount": {
         "value": "580.00",
         "confidence": 0.9,
         "source_text": "发票金额：580.00",
         "page_no": 1
       }
     }
   }
   ```

3. **原文证据校验**

   每个字段都必须带 `source_text`，且该片段必须能在上传文档原文中找到。找不到原文证据的字段会被视为幻觉并拒收。

4. **置信度阈值**

   默认只接受 `confidence >= 0.7` 的字段。可以通过环境变量调整：

   ```bash
   MODEL_EXTRACTION_MIN_CONFIDENCE=0.8
   ```

5. **字段格式标准化**

   - 金额统一为两位小数字符串，例如 `¥580` → `580.00`。
   - 日期统一为 `YYYY-MM-DD`，例如 `2026年08月01日` → `2026-08-01`。
   - 城市、姓名、发票号会做基础格式校验。

6. **失败重试与兜底**

   默认最多尝试 2 次。第一次 JSON 崩坏或 Schema 不合格时，会用更强约束 prompt 再试一次。仍失败则返回空字段，让主流程继续使用规则抽取结果。

   ```bash
   MODEL_EXTRACTION_MAX_ATTEMPTS=2
   ```

## 为什么不让 LLM 直接决定审核结果

审核结果必须由可解释的确定性工具输出，例如金额一致性、日期范围、住宿标准等。LLM 只负责“读材料、补字段”，不能直接决定 `PASS` 或 `REVIEW_REQUIRED`。这样可以降低幻觉影响，也方便讲解时解释系统边界。

## 接入真实 API 后的验证流程

1. 配置真实模型：

   ```bash
   MODEL_PROVIDER=openai-compatible
   MODEL_BASE_URL=https://example.com/v1
   MODEL_NAME=your-model-name
   MODEL_API_KEY=your-api-key
   ```

2. 先跑单测：

   ```bash
   python -m unittest tests.test_llm_field_extractor -v
   ```

3. 再跑端到端评测：

   ```bash
   python -m app.evaluation.runner
   ```

4. 如果评测下降，优先定位：

   - 字段值是否抽错；
   - `source_text` 是否不在原文中；
   - 金额/日期格式是否被拒收；
   - 制度检索是否命中错误；
   - 规则工具是否按预期生成风险。

## 后续可以继续增强

- 把 `LlmExtractionDiagnostics` 写入 agent trace，让前端显示“模型字段被拒收的原因”。
- 对真实模型跑多轮稳定性评测，统计同一用例重复运行的方差。
- 增加 prompt 版本号，将评测报告与 prompt 版本绑定。
- 对低置信字段自动生成“人工复核项”，进入人工修正闭环。
