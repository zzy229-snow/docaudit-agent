# 真实 RAG 接入说明

系统现在支持三种制度检索模式：

| 模式 | 配置 | 说明 |
| --- | --- | --- |
| Mock | `RAG_MODE=mock` | 规则匹配演示基线，适合单测和离线演示 |
| Local | `RAG_MODE=local` | 基于制度切片的本地关键词检索，不依赖外部服务 |
| Milvus | `RAG_MODE=milvus` | bge-m3 + Milvus Lite 混合向量检索 |

生产化演示建议至少使用 `local`，这样制度依据来自真实政策切片检索，而不是 mock 兜底逻辑。

## 1. 本地真实检索

`.env`：

```env
RAG_MODE=local
```

制度文件位于：

```text
data/policies/travel_policy.json
```

运行评测：

```powershell
.\.venv\Scripts\python.exe -m app.evaluation.rag_runner --format markdown
```

这会检查评测用例中预期的制度条款是否能被检索命中。

## 2. Milvus 向量检索

Milvus 路径适合更接近生产的语义检索，但需要额外安装 `requirements-rag.txt` 中的依赖，并准备 bge-m3 模型。

构建索引：

```powershell
.\.venv\Scripts\python.exe -m app.rag.build_index
```

启用：

```env
RAG_MODE=milvus
BGE_MODEL_DIR=D:\path\to\bge-m3
```

## 3. RAG 评测指标

`app.evaluation.rag_runner` 输出：

- 总用例数；
- 需要制度检索评测的用例数；
- 制度条款命中数；
- 命中率；
- 每个 case 的 query、预期制度条款、实际命中条款、缺失条款。

如果 `local` 命中但 `milvus` 不命中，说明是向量索引、embedding 或召回参数问题；如果两者都不命中，通常说明制度切片或 query 构造不合理。

## 4. 后续生产化方向

- 从 JSON 制度扩展到 Word/PDF 制度文档解析；
- 保存 chunk 的制度版本、生效日期、部门、费用类型；
- 增加 rerank；
- 增加制度冲突检测；
- 增加按日期和部门过滤；
- 记录每次审核的检索 query、top-k 和命中分数。
