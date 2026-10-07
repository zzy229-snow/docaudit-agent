# 演示制度

本项目附带虚构演示制度,用于 RAG 检索与审核规则演示。**全部为虚构数据,不得用于真实报销。**

## 文件结构

| 文件 | 前辍 | 费用类型(expense_type) | 说明 |
|------|------|------------------------|------|
| `travel_policy.json` | `TRAVEL-` | TRAVEL | 差旅住宿标准(一线/其他城市) |
| `expense_policy.json` | `EXP-`/`PHARM-`/`OFFICE-` | EXPENSE/MEDICAL/PURCHASE | 综合费用表、医药、办公采购 |

## 切片与检索(任务⑥ RAG 增强)

- **表格切片**:`expense_policy.json` 中的"表4-1 差旅费用标准表"按**数据行**切分为独立 chunk(表头保留在前缀),检索命中具体行而非整表。行 chunk_id 形如 `EXP-V1-4.3-TABLE-R1`。
- **元数据过滤**:每个 chunk 携带 `expense_type`/`department`,Milvus 检索支持按费用类型与部门过滤。审核时按上传文件名自动推断费用类型(医药→MEDICAL、采购→PURCHASE,默认差旅→TRAVEL)。
- **版本**:chunk_id 前缀如 `OFFICE-V2` 表示制度版本 V2;切片器据此写入 `version` 元数据。

## 使用

```bash
# 建立/重建 Milvus 索引(bge-m3 编码,需 独立的 RAG 环境 环境)
BGE_MODEL_DIR=<bge-m3 目录> python -m app.rag.build_index

# 运行检索(默认 mock;设 RAG_MODE=milvus 使用向量检索)
```
