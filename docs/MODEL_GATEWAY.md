# ModelGateway 与 LLM 字段抽取

## 1. 目标

本模块用于把真实 LLM 接入限制在可控边界内。默认模式是 `mock`，不需要 API Key，也不会影响当前规则版审核闭环。

真实模型只作为字段抽取的补充能力：

- 规则抽取先运行。
- LLM 只补充规则没有抽到的字段。
- LLM 输出必须通过 Pydantic 校验。
- LLM 字段必须带可在原文中找到的 `source_text`。
- 校验失败或 API 异常时直接降级为空结果，不阻断主流程。

## 2. 环境变量

默认配置：

```env
MODEL_PROVIDER=mock
```

项目启动时会自动读取仓库根目录下的 `.env`；真实密钥应只放在本地 `.env`，不要提交仓库。

OpenAI-compatible API 配置：

```env
MODEL_PROVIDER=openai-compatible
MODEL_BASE_URL=https://api.example.com/v1
MODEL_API_KEY=你的key
MODEL_NAME=你的模型名
MODEL_TIMEOUT_SECONDS=30
MODEL_EXTRACTION_MAX_ATTEMPTS=2
MODEL_EXTRACTION_MIN_CONFIDENCE=0.7
```

可以接入 DeepSeek、通义千问兼容接口、OpenAI-compatible 网关等，前提是它们兼容 OpenAI Chat Completions 协议。

购买或填写真实 API 前，先阅读 `docs/MODEL_API_READINESS.md`。配置后可运行：

```powershell
.\.venv\Scripts\python.exe -m app.services.model_smoke
```

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
- 字段名白名单校验，Schema 之外的字段会被拒收。
- `source_text` 必须能在原文中找到，降低幻觉字段进入主流程的概率。
- 金额、日期、城市、姓名、发票号会做基础格式校验和标准化。
- 默认最多重试 `MODEL_EXTRACTION_MAX_ATTEMPTS=2` 次，第二次使用更强约束 Prompt。
- 低于 `MODEL_EXTRACTION_MIN_CONFIDENCE=0.7` 的字段会被拒收。
- 失败时降级为空 LLM 抽取结果，保留规则抽取结果。
- 规则字段优先，LLM 不覆盖已有确定性字段。
- Agent trace 会记录 LLM 尝试次数、接受字段数、拒收字段数和是否兜底。

后续建议：

1. 记录模型调用耗时、模型名、Prompt 版本和错误类型。
2. 将 LLM 字段候选低置信度结果进入人工复核。
3. 增加固定评测集，对比 mock、规则、真实模型三种模式。
4. 将风险摘要、Planner 等更高风险能力延后接入。

更完整的稳定性说明见 `docs/LLM_ROBUSTNESS.md`。

## 5. 界面"填空"接入（上线交付用）

客户上线时不该拿到我们的内置 key，所以工作台侧栏提供 **模型 API 设置（填空）**：
选供应商（DeepSeek 官方 / OpenAI 兼容）→ 接口地址与模型名自动带出 → 只填客户的 API Key
→「测试连接」当场验证（失败会把模型返回的原因原样显示）→「保存到本次会话」。

实现要点：

| 关注点 | 做法 | 为什么 |
| --- | --- | --- |
| 配置怎么进审核链路 | `run_audit(..., llm_settings={...})` → `state["llm_settings"]` → extract 节点 `ModelGateway(**settings)` | 走**参数**而不是写进程环境变量；环境变量在多用户并发下会互相覆盖 |
| 优先级 | 页面填写 > 环境变量(`.env`) | 不填就完全保持原有行为（向后兼容） |
| 存哪里 | 只存浏览器会话的 `st.session_state` | 不落盘、不进日志、不改 `.env` |
| 作用范围 | 只影响工作台（Streamlit）进程 | 接口 `/api/v1` 仍读服务端 `.env`，由运维配置 |
| 回显 | 只显示 key 位数（如"key 23 位，不回显"） | 避免截图/投屏泄露 |
| 切换供应商 | 显式重置输入框（Streamlit 的 widget state 会压住 `value=`） | 否则改了供应商但地址还停在上一个 |

已知边界（后续要做完整版时）：配置是**会话级**的，刷新页面/换浏览器要重填；若要多人共用一套，
需要服务端加密存储（DPAPI 或对称密钥）+ RBAC + 掩码回显 + 审计事件 + `/api/v1/settings/*`。

## 6. 测试

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_model_gateway tests.test_llm_field_extractor -v
```

完整测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
