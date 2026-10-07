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

```powershell
# 1) 装依赖(注意 milvus-lite:只装 pymilvus 会在建索引时报
#    "milvus-lite is required for local database connections")
.\\.venv\\Scripts\\python.exe -m pip install -r requirements-rag.txt

# 2) 建索引(bge-m3 权重需自行下载,~2GB;或用 BGE_MODEL_DIR 指向已有权重)
$env:BGE_MODEL_DIR = "D:\\models\\bge-m3"
.\\.venv\\Scripts\\python.exe -m app.rag.build_index
```

启用：

```env
RAG_MODE=milvus
BGE_MODEL_DIR=D:\models\bge-m3
```

### 融合策略：默认只用稠密通道

`RAG_SPARSE_WEIGHT` 默认 `0`（只用稠密向量）。**为什么**（2026-10-07 实测，三条黄金查询）：

- 稀疏通道在本项目语料（16 chunk）上的 top1 常与语义无关 —— 问"杭州出差酒店费用上限"返回
  `PHARM-V1-3.3`（药品制度）；
- 等权 RRF 会把两路秩次相加，让"两路都排中等"的无关 chunk 压过"稠密通道第 1 名"的正确条款
  （实测把 `TRAVEL-V1-4.2-B` 挤出第 1 名）；候选数越小、平票越多，同一查询两次运行还会翻转；
- 稠密通道三条黄金查询全中。

需要精确词命中（编号/金额类查询）时，设 `RAG_SPARSE_WEIGHT=0.3` 打开稠密+稀疏加权融合。
实现与完整依据见 `app/rag/vector_store.py` 的 `SPARSE_WEIGHT_ENV`。

### 降级是可见的

检索失败（未建索引、模型缺失、Milvus 异常）时会降级到规则匹配，**不会中断审核**；降级原因会由
`app/rag/retriever.py` 记入 `RETRIEVAL_NOTE`，并出现在审核 trace 的 `rag:` 行里，例如：

```
rag: milvus(融合=稠密,命中2条)
rag: milvus 检索失败，已降级 mock:RuntimeError('未配置 bge-m3 权重目录...')
```

集成测试 `tests/test_rag_milvus.py` 会断言"确实走了 milvus 且未降级"，并在缺少权重目录时整组跳过 ——
避免出现"环境没配好、测试却全绿"的假信心（这是实际踩过的坑）。

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
