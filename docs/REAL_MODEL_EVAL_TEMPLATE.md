# 真实模型试运行评测记录模板

> 用途：每次接入或切换真实 LLM 后，记录配置、评测结果和问题。建议每次只改一个变量，例如模型名、Prompt、阈值或评测集。

## 1. 基本信息

| 项目 | 填写 |
| --- | --- |
| 日期 |  |
| 操作人 |  |
| Git commit |  |
| MODEL_PROVIDER | openai-compatible |
| MODEL_BASE_URL |  |
| MODEL_NAME |  |
| MODEL_TIMEOUT_SECONDS | 30 |
| MODEL_EXTRACTION_MAX_ATTEMPTS | 2 |
| MODEL_EXTRACTION_MIN_CONFIDENCE | 0.7 |
| 评测命令 | `python -m app.evaluation.runner --format markdown --output reports/eval-real-model.md` |

## 2. 连通性检查

```powershell
python -m app.services.model_smoke
```

结果：

```text
粘贴输出
```

结论：

- [ ] API Key 可用
- [ ] Base URL 可用
- [ ] 模型名可用
- [ ] JSON 解析正常

## 3. 评测结果

```powershell
python -m app.evaluation.runner --format markdown --output reports/eval-real-model.md
```

| 指标 | 结果 |
| --- | --- |
| total_cases |  |
| passed_cases |  |
| pass_rate |  |
| status_accuracy |  |
| risk_accuracy |  |
| field_accuracy |  |
| policy_ref_accuracy |  |

## 4. 失败用例记录

| Case | 失败类型 | 现象 | 初步原因 | 处理建议 |
| --- | --- | --- | --- | --- |
|  | 字段 / 风险 / 制度引用 / API异常 |  |  |  |

## 5. Agent trace 观察

重点看 `llm_extraction`：

```text
llm_extraction: attempts=?, accepted=?, rejected=?, fallback_used=?
```

观察：

- [ ] 是否频繁重试
- [ ] 是否频繁 fallback
- [ ] 是否有大量字段因 source_text 不存在被拒收
- [ ] 是否有低置信字段被拒收

## 6. 决策

- [ ] 保持当前模型配置
- [ ] 调整 Prompt
- [ ] 调整置信度阈值
- [ ] 更换模型
- [ ] 扩充评测集
- [ ] 暂不使用真实模型，继续走规则基线

结论：

```text
填写本次是否推荐继续使用该模型，以及原因。
```
