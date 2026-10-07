"""检索结果重排(rerank)。

为什么需要它:纯向量(双编码器)把 query 和 doc **分别**编码,只能比"语义整体像不像",分不清
条款里到底有没有提到用户问的那个具体实体。实测(16 chunk 语料):

    查询"上海住宿标准"    →  distance
      TRAVEL-V1-4.2-B  其他城市 450 元/晚     0.5849   ← 排序在前(错)
      TRAVEL-V1-4.2-A  北京/上海/广州/深圳 600 0.6034   ← 才是答案
    两者只差 0.019,靠调融合权重救不回来(实测稀疏权重 0/0.3/0.5 三档结果不变)。

两层重排:

1. **实体重排**(零依赖,始终开启):制度条款出现的**城市名**与查询/任务城市的比对。城市是差旅制度
   的天然分桶键(A=一线、B=其他城市兜底),这一层专治上面这类"实体张冠李戴"。
2. **bge-reranker 交叉编码器**(可选,需权重):query 与 doc 一起过模型打分,应对措辞差异、
   同义改写等更泛化的排序问题。配置方式见 ``get_reranker``;未配置时自动只用第 1 层,不报错。

设计约束:重排**只调顺序,不增删条款之外的语义**;任何一层失败都退回上一层的结果(排序问题
不该升级成检索失败),并在 ``RETRIEVAL_NOTE`` 里写清用了哪一层 —— 审核报告要能追溯依据。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from app.models.audit import PolicyEvidence

_LOCK = threading.Lock()
_RERANKER = None
_RERANKER_PATH: str | None = None
_RERANKER_ERROR = ""

#: 环境变量:指向 bge-reranker 权重目录(优先);未设时尝试仓库内 data/rag/bge-reranker*
RERANKER_DIR_ENV = "BGE_RERANKER_DIR"
#: 环境变量:设为 0/false 可显式关闭交叉编码器重排(只用实体重排)
RERANK_ENV = "RAG_RERANK"
FP16_ENV = "RAG_BGE_FP16"

#: 制度条款里**枚举**的城市(A 条款写的就是"北京、上海、广州、深圳 600 元/晚"的这几个)。
#: 只有查询命中的城市**不在这里面**时,"其他城市"兜底条款才应优先 —— 否则"杭州住宿标准"
#: 会被误判成"命中枚举城市",把本该兜底的条款压下去(实测踩过)。
_ENUMERATED_CITIES = ("北京", "上海", "广州", "深圳")

#: 用于从查询里**识别实体**的城市表(比枚举表宽:杭州/成都等虽不在 A 条款里,
#: 但它们出现在查询中同样说明用户在问"某个具体城市",兜底条款应优先)。
_KNOWN_CITIES = _ENUMERATED_CITIES + (
    "杭州", "南京", "苏州", "成都", "武汉", "西安", "重庆", "天津", "长沙", "郑州", "青岛", "厦门",
)


def _repo_reranker_dir() -> Path | None:
    """在 data/rag 下找一个 bge-reranker* 目录(约定式发现,免得每台机器都配环境变量)。"""
    base = Path(__file__).resolve().parents[2] / "data" / "rag"
    if not base.exists():
        return None
    for child in sorted(base.glob("bge-reranker*")):
        if child.is_dir():
            return child
    return None


def rerank_enabled() -> bool:
    return os.environ.get(RERANK_ENV, "1").strip().lower() not in {"0", "false", "no", "off"}


def get_reranker():
    """返回交叉编码器(进程内单例),没配权重时返回 ``None``。"""
    global _RERANKER, _RERANKER_PATH, _RERANKER_ERROR
    if not rerank_enabled():
        _RERANKER_ERROR = f"{RERANK_ENV}=0 已显式关闭交叉编码器重排"
        return None

    configured = os.environ.get(RERANKER_DIR_ENV, "").strip()
    path = configured or None
    if not path:
        found = _repo_reranker_dir()
        path = str(found) if found else None
    if not path:
        _RERANKER_ERROR = (
            f"未配置交叉编码器重排(设 {RERANKER_DIR_ENV} 或把权重放到 data/rag/bge-reranker-v2-m3)"
        )
        return None
    if not Path(path).exists():
        _RERANKER_ERROR = f"{RERANKER_DIR_ENV} 指向的目录不存在:{path}"
        return None

    with _LOCK:
        if _RERANKER is not None and _RERANKER_PATH == str(path):
            return _RERANKER
        try:
            from FlagEmbedding import FlagReranker

            _RERANKER = FlagReranker(str(path), use_fp16=os.environ.get(FP16_ENV, "").strip().lower()
                                     in {"1", "true", "yes", "on"})
            _RERANKER_PATH = str(path)
            _RERANKER_ERROR = ""
        except Exception as exc:  # noqa: BLE001 — 重排是增强项,缺了就退回上一层
            _RERANKER = None
            _RERANKER_PATH = None
            _RERANKER_ERROR = f"加载交叉编码器失败:{exc!r}"
        return _RERANKER


def reranker_status() -> str:
    """当前重排状态的可读说明(写进 trace 用)。"""
    return _RERANKER_ERROR


def reset_reranker() -> None:
    """丢弃缓存的交叉编码器(测试或切换权重目录后使用)。"""
    global _RERANKER, _RERANKER_PATH, _RERANKER_ERROR
    with _LOCK:
        _RERANKER = None
        _RERANKER_PATH = None
        _RERANKER_ERROR = ""


def _mentioned_entities(query: str, city: str | None) -> tuple[str, ...]:
    """查询/任务里出现的城市(城市是差旅制度的天然分桶键)。"""
    text = f"{city or ''} {query or ''}"
    found = [c for c in _KNOWN_CITIES if c in text]
    if city:
        cleaned = city.removesuffix("市")
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return tuple(found)


def entity_rank(evidence: list[PolicyEvidence], query: str, city: str | None) -> list[PolicyEvidence]:
    """实体重排:条款提到用户问的城市 > "其他城市"兜底(查询城市不在条例枚举里) > 其余。

    同一档内保持原顺序(stable),不引入新的不确定性 —— 排序必须可复现,否则同一次审核
    两次运行可能给出不同依据。
    """
    mentioned = _mentioned_entities(query, city)
    if not mentioned:
        return list(evidence)

    def rank(item: PolicyEvidence) -> int:
        if any(entity in item.content for entity in mentioned):
            return 0
        if "其他城市" in item.content:
            # 只在查询没有命中 A 条款枚举城市时,"其他城市"兜底才优先(如"杭州")
            return 1 if not any(e in _ENUMERATED_CITIES for e in mentioned) else 2
        return 2

    return sorted(evidence, key=rank)


def cross_encoder_scores(query: str, evidence: list[PolicyEvidence],
                         reranker=None) -> list[float] | None:
    """用交叉编码器给候选打分;不可用或失败时返回 ``None``。"""
    if reranker is None:
        reranker = get_reranker()
    if reranker is None or not evidence:
        return None
    try:
        pairs = [[query, item.content] for item in evidence]
        scores = reranker.compute_score(pairs, normalize=True)
        if isinstance(scores, (int, float)):
            scores = [float(scores)]
        return [float(s) for s in scores]
    except Exception:  # noqa: BLE001 — 打分失败就用上一层顺序
        return None


def rerank(query: str, evidence: list[PolicyEvidence], city: str | None = None,
           top_k: int | None = None) -> tuple[list[PolicyEvidence], str]:
    """重排候选条款,返回 ``(结果, 用了哪一层)``。

    顺序:实体重排 → 交叉编码器(如果可用) → 截断到 ``top_k``。
    交叉编码器加载/打分失败时保留实体重排结果 —— 排序问题不该升级成检索失败 ——
    但把失败原因写进第二返回值(trace 里可见)。
    """
    if not evidence:
        return [], "无候选"

    result = entity_rank(evidence, query, city)
    label = "实体"
    reranker = get_reranker()

    if reranker is not None:
        scores = cross_encoder_scores(query, result, reranker=reranker)
        if scores is not None:
            order = sorted(range(len(result)), key=lambda i: (-scores[i], i))
            result = [result[i] for i in order]
            name = Path(_RERANKER_PATH).name if _RERANKER_PATH else "交叉编码器"
            label = f"实体+{name}"
        else:
            label = "实体(交叉编码器打分失败)"
    elif _RERANKER_ERROR:
        # 标签要短(trace 一行),详细原因放 reranker_status() / 文档
        label = "实体(交叉编码器不可用)"

    if top_k is not None:
        result = result[:top_k]
    return result, label
