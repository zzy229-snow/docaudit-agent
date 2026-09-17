# ModelGateway 与 LLM 字段抽取

## 1. 目标

本模块用于把真实 LLM 接入限制在可控边界内。默认模式是 `mock`，不需要 API Key，也不会影响当前规则版审核闭环。

真实模型只作为字段抽取的补充能力：

- 规则抽取先运行。
- LLM 只补充规则没有抽到的字段。
- LLM 输出必须通过 Pydantic 校验。
- 校验失败或 API 异常时直接降级为空结果，不阻断主流程。

## 2. 环境变量

默认配置：

```env
MODEL_PROVIDER=mock
```

OpenAI-compatible API 配置：

```env
MODEL_PROVIDER=openai-compatible
MODEL_BASE_URL=https://api.example.com/v1
MODEL_API_KEY=你的key
MODEL_NAME=你的模型名
MODEL_TIMEOUT_SECONDS=30
```

可以接入 DeepSeek、通义千问兼容接口、OpenAI-compatible 网关等，前提是它们兼容 OpenAI Chat Completions 协议。

## 3. Prompt 与输出格式

字段抽取 Prompt 位于：

```text
app/prompts/field_extraction_v1.txt
```

模型只能返回 JSON 对象：

```json
{
  "fields": {
    "invoice_amount": {
      "value": "580.00",
      "confidence": 0.95,
      "source_text": "发票金额：580.00",
      "page_no": 1
    }
  }
}
```

不允许 Markdown、解释性文本或无证据猜测。

## 4. 稳定性策略

当前已实现：

- `ModelGateway` 统一模型入口。
- 默认 `mock`，不依赖真实 API。
- 真实 API 调用超时由 `MODEL_TIMEOUT_SECONDS` 控制。
- JSON 响应经过解析与 Pydantic 校验。
- 失败时降级为空 LLM 抽取结果，保留规则抽取结果。
- 规则字段优先，LLM 不覆盖已有确定性字段。

后续建议：

1. 增加一次“格式修复”重试。
2. 记录模型调用耗时、模型名、Prompt 版本和错误类型。
3. 将 LLM 字段候选低置信度结果进入人工复核。
4. 增加固定评测集，对比 mock、规则、真实模型三种模式。
5. 将风险摘要、Planner 等更高风险能力延后接入。

## 5. 测试

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_model_gateway tests.test_llm_field_extractor -v
```

完整测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
