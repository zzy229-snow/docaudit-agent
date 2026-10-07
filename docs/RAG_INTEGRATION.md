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

### 重排（rerank）：修掉「实体张冠李戴」的排序

双编码器把 query 和 doc **分别**编码，分不清条款里到底有没有提到用户问的那个具体实体。实测：

```
查询"上海住宿标准"
  TRAVEL-V1-4.2-B  其他城市 450 元/晚        distance 0.5849   ← 排序在前（错）
  TRAVEL-V1-4.2-A  北京/上海/广州/深圳 600   distance 0.6034   ← 才是答案
```

只差 0.019，**靠调融合权重救不回来**（稀疏权重 0/0.3/0.5 三档排序不变）。所以检索后加一层重排
（`app/rag/rerank.py`），候选池从 2 条放大到 6 条再重排截断：

| 层 | 依赖 | 作用 |
| --- | --- | --- |
| **实体重排**（始终开启） | 无 | 条款提到查询/任务城市 > 「其他城市」兜底（查询城市不在条例枚举里时）> 其余；同档内保持原顺序 |
| **交叉编码器**（可选） | bge-reranker 权重 | query+doc 一起过模型打分，应对措辞差异/同义改写等更泛化的排序问题 |

启用交叉编码器：

```bash
BGE_RERANKER_DIR=D:\models\bge-reranker-v2-m3     # 或把权重放到 data/rag/bge-reranker-v2-m3
# RAG_RERANK=0 可显式关闭这一层
```

未配置权重时自动只用实体重排，**不报错**；加载/打分失败也退回实体重排结果（排序问题不该升级成
检索失败）。用了哪一层会写进 trace：`rag: milvus(融合=稠密,重排=实体,命中2条)`。

实测（同一套 5 条查询，走生产入口 `retrieve_policy`）：

| | 首名命中率 |
| --- | --- |
| 只靠向量 | 3/5（"上海"和"住宿费能报多少钱"给的是「其他城市」条款） |
| 加实体重排 | **5/5** |

回归测试：`tests/test_rag_rerank.py`（15 例，含"实体重排不得在查询无实体时改动顺序"这类可复现性约束）。

#### 前提：查询文本自己要有信息量

交叉编码器是**对着查询文本**打分的，所以查询文本必须先站得住。实测（2026-10-07）：

| 查询文本 | 向量 top1 | 交叉编码器 top1 | 最终 | 上海的住宿上限 |
| --- | --- | --- | --- | --- |
| `上海`（旧行为：milvus 分支传的是 `query or city`） | A | B（0.0081 vs 0.0076，噪声级） | ❌ B 在前 | **450 元（错）** |
| `上海 住宿标准 差旅 报销 酒店`（现在的做法） | B | A（0.514 vs 0.205） | ✅ A 在前 | 600 元（对） |

- 检索文本统一由 `_query_from_city()` 展开 —— local 分支一直是这么做的，milvus 分支漏了，
  于是"只给城市"的调用（审核流程就是这么调的）把光秃秃的城市名送进了向量库；
- 查询信息量不足（有效字符 < 4）时**跳过交叉编码器**（`rerank.query_is_informative`），
  只用实体重排：噪声级打分不该决定排序。
- 回归测试：`tests/test_rag_query_construction.py`（4 例）。

这个坑是加了交叉编码器之后才暴露出来的：旧代码只看向量距离，错序时"看起来只是不甚精确"；
换成打得准的交叉编码器后错误被放大成"上海按 450 元算超标"，反而更容易被发现。

### 降级链：milvus → local → mock

检索失败（未建索引、模型缺失、Milvus 异常）时**不会中断审核**，按下面的顺序退：

1. **milvus 失败 → local**（本地关键词检索，仍是真检索、结果可引用）；
2. **local 也失败 → mock**（规则匹配基线，只按城市名字符串匹配）；
3. 两层失败原因都写进 `RETRIEVAL_NOTE`，出现在审核 trace 的 `rag:` 行里。

为什么不让 milvus 直接退到 mock：mock **不看查询内容** —— 实测问"发票代码和发票号码怎么填"，
它照样返回住宿标准条款。把这种结果当"制度依据"写进报告，等于把"依据不可用"伪装成"有依据"。

```
rag: milvus(融合=稠密,重排=实体,命中2条)
rag: milvus 检索失败，已降级 local(关键词检索):ConnectionConfigException()(根因: DataDirLockedError(...))
rag: milvus 检索失败，已降级 mock:...;local 亦失败:...
```

回归测试：`tests/test_rag_degrade_chain.py`（5 例，含"不得在没降级时出现降级字样"的误报约束）。

### 性能：编码器与连接进程内复用

bge-m3 权重约 2GB，**加载一次约 13 秒**（冷盘）。所以 `app/rag/embedding.py` 用进程内单例持有模型、
`vector_store.get_store()` 复用同一个 Milvus 连接——检索时只做「编码 + 近邻搜索」。

实测（同一进程连续 5 次查询，16 chunk 语料）：

| | 首次查询 | 之后每次 |
| --- | --- | --- |
| 复用前（每次 new 模型） | 16.2s | 3.1s |
| 复用后 | 13.0s | **0.15s** |

内存代价：模型常驻约 2GB。机器吃紧时设 `RAG_BGE_FP16=1` 减半（CPU 上可能更慢，需实测取舍）；
不想要常驻就用 `RAG_MODE=local`（关键词检索，零权重依赖，毫秒级）。

`tests/test_rag_embedding_cache.py` 用假模型把「同一路径只加载一次」钉成回归测试，
防止以后有人把 `BGEM3FlagModel(...)` 挪回函数体内导致性能静默退化。

### 单进程独占：milvus-lite 的锁限制（多进程部署必读）

**Milvus Lite 是文件级数据库，同一时刻只允许一个进程打开 `data/rag/policy.db`。** 本项目会起两个进程
（API `8000` + 工作台 `8500`），它们都可能在审核时检索制度，于是**后拿到锁的那个会失败并降级到 mock**，
表现就是 trace 里出现上面第二条 `rag:` 行（根因写着 `DataDirLockedError`）。

应对方式：

| 场景 | 做法 |
| --- | --- |
| 演示/单进程使用 | 只让一个进程承担检索：要么只用工作台，要么只用 API；另一个进程的审核会降级到 mock（trace 可见，不会给出错误结论） |
| 多进程/生产 | 换成 **Milvus 服务端**（独立进程，客户端可并发），把 `MILVUS_URI` 指向它即可；或用 `RAG_MODE=local`（纯文件切片检索，无锁问题） |

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
- 制度冲突检测；
- 记录每次审核的检索 query、top-k 和命中分数；
- 把 `section_path`（含条款号）一起编码进索引 —— 现在只编码 `content`，所以"按条款号问"
  （如"4.2 条怎么规定的"）在向量侧命中不了，只有关键词侧（local）能匹配条款号。
- （已完成）rerank：实体重排 + 可选 bge-reranker 交叉编码器，见 §2「重排（rerank）」。
