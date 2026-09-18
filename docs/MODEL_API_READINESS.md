# 真实模型 API 接入前 Checklist

当前项目默认 `MODEL_PROVIDER=mock`，本地测试和演示不需要购买 API。只有当你要验证真实 LLM 字段抽取效果时，才需要购买或申请一个 OpenAI-compatible API Key。

## 什么时候可以买 API

可以买最小额度 API 的条件：

1. `python -m unittest discover -s tests -v` 通过。
2. `python -m app.evaluation.runner` 通过。
3. `python -m app.services.model_smoke` 在 mock 模式下能正常输出 `SKIPPED`。
4. 你准备开始比较“规则基线 vs 真实 LLM 补字段”的效果。

不建议现在买大套餐。首次只需要按量计费或最低额度，用来跑少量评测样例。

## 推荐购买类型

优先选择 OpenAI-compatible 协议的服务，因为当前 `ModelGateway` 使用 Chat Completions 兼容接口：

```env
MODEL_PROVIDER=openai-compatible
MODEL_BASE_URL=https://api.example.com/v1
MODEL_API_KEY=你的key
MODEL_NAME=你的模型名
```

常见选择包括 DeepSeek、通义千问兼容接口、OpenAI-compatible 网关等。先选便宜、稳定、支持 JSON 输出的模型即可，不需要一开始买最贵模型。

## 接入步骤

1. 复制环境变量模板：

   ```powershell
   copy .env.example .env
   ```

2. 在 `.env` 里填写真实配置。不要提交 `.env`。

3. 项目会自动读取仓库根目录下的 `.env`。如果你不想写 `.env`，也可以在当前终端临时设置环境变量。PowerShell 示例：

   ```powershell
   $env:MODEL_PROVIDER="openai-compatible"
   $env:MODEL_BASE_URL="https://api.example.com/v1"
   $env:MODEL_API_KEY="你的key"
   $env:MODEL_NAME="你的模型名"
   ```

4. 先跑连通性检查：

   ```powershell
   .\.venv\Scripts\python.exe -m app.services.model_smoke
   ```

5. 再跑 LLM 单测和评测：

   ```powershell
   .\.venv\Scripts\python.exe -m unittest tests.test_llm_field_extractor tests.test_model_smoke -v
   .\.venv\Scripts\python.exe -m app.evaluation.runner --format markdown --output reports/eval-real-model.md
   ```

6. 用 `docs/REAL_MODEL_EVAL_TEMPLATE.md` 记录本次模型、配置、评测指标和失败用例。

## 判断是否稳定

真实模型初次接入后，不要只看“能不能返回”。重点看：

- 是否严格返回 JSON；
- 字段是否有原文 `source_text`；
- 金额和日期是否被标准化；
- 是否出现低置信字段被拒收；
- 评测集准确率是否低于 mock 基线；
- Agent trace 中 `llm_extraction` 是否频繁 `fallback_used=True`。

如果评测下降，先不要改业务规则，优先排查模型配置、Prompt、输出格式和字段证据。
