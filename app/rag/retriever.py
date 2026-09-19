"""制度检索器(PRD §9.3)。RAG_MODE 控制后端:

- mock   (默认):规则匹配基线,零依赖,保持协作者原有行为;
- local  :本地制度切片关键词检索,零外部服务,用于生产化演示前的真实检索基线;
- milvus :向量混合检索(需已运行 build_index.py 建立索引;编码依赖见 requirements-rag.txt)。

任务⑥增强:制度扩充(多费用类型)、表格行切片、元数据过滤(expense_type/department)。
任何后端失败都降级回 mock(PRD §23.5「检索失败降级」),输出契约 PolicyEvidence 不变。
"""
from __future__ import annotations

import os
from pathlib import Path

from app.config import load_environment
from app.models.audit import PolicyEvidence
from .chunker import chunk_policy_documents

load_environment()

POLICY_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"

RAG_MODE = os.environ.get("RAG_MODE", "mock").strip().lower()

_FIRST_TIER = {"北京", "上海", "广州", "深圳"}


def _load_all_policy() -> list[PolicyEvidence]:
    """加载全部制度(含扩充的 expense_policy.json),映射为 PolicyEvidence。

    通过 chunker 拿到已按行切片、带元数据的 chunk,供 mock 侧过滤。
    契约 PolicyEvidence 不变(仍为 chunk_id/section/content/score)。
    """
    chunks = chunk_policy_documents(POLICY_DIR)
    return [PolicyEvidence(chunk_id=c.chunk_id, section=c.section_path,
                           content=c.content, score=0.0) for c in chunks]


def _resolve_expense_type(files=None) -> str:
    """从上传文件名推断费用类型(任务⑥:元数据过滤的 expense_type)。

    invoice/payment/approval 关键字 -> TRAVEL(默认业务是差旅报销);
    医药/药房/pharmacy -> MEDICAL;采购/办公/office -> PURCHASE。
    """
    names = " ".join(f[0].lower() if isinstance(f, tuple) and f else str(f or "") for f in (files or []))
    if any(k in names for k in ("医药", "药", "pharm", "medical", "处方")):
        return "MEDICAL"
    if any(k in names for k in ("采购", "办公", "office", "purchase", "设备")):
        return "PURCHASE"
    return "TRAVEL"


def retrieve_policy(city: str | None, query: str | None = None,
                    expense_type: str | None = None, department: str | None = None,
                    files=None) -> list[PolicyEvidence]:
    """按城市(或自由文本 query)检索制度,支持元数据过滤(任务⑥)。

    - expense_type: 费用类型(TRAVEL/EXPENSE/MEDICAL/PURCHASE);
    - department : 适用部门(如 研发部);
    - files      : 上传文件名列表,用于自动推断 expense_type(未显式指定时)。

    未指定费用类型时默认 TRAVEL(当前唯一接入的审核场景是差旅报销),
    以保证差旅 Recall@1 契约不被扩充制度干扰。返回契约 PolicyEvidence 不变。
    """
    if files and not expense_type:
        expense_type = _resolve_expense_type(files)
    if not expense_type:
        expense_type = "TRAVEL"

    if RAG_MODE == "local":
        try:
            return _retrieve_local(query or _query_from_city(city))
        except Exception as exc:  # noqa: BLE001 — 检索失败必须降级不可中断主流程
            print(f"[rag] local 检索失败,降级 mock: {exc!r}")
            return _retrieve_mock(city, expense_type, department)
    if RAG_MODE == "milvus":
        try:
            return _retrieve_milvus(query or city or "", expense_type, department)
        except Exception as exc:  # noqa: BLE001 — 检索失败必须降级不可中断主流程
            print(f"[rag] milvus 检索失败,降级 mock: {exc!r}")
            return _retrieve_mock(city, expense_type, department)
    return _retrieve_mock(city, expense_type, department)


def _retrieve_local(query: str) -> list[PolicyEvidence]:
    from .local_store import retrieve_local

    return retrieve_local(query, limit=2)


def _query_from_city(city: str | None) -> str:
    if not city:
        return ""
    return f"{city} 住宿标准 差旅 报销 酒店"


def _retrieve_mock(city: str | None, expense_type: str | None = None,
                   department: str | None = None) -> list[PolicyEvidence]:
    """规则匹配基线(任务⑥):城市匹配 + 费用类型过滤。

    兼容协作者基线:仅按城市查询(expense_type=TRAVEL/None)时,保持原 travel 语义
    (一线命中 A、其他命中 B),不让扩充制度改变默认结果;
    显式传其他费用类型时,全量加载并按费用类型过滤(医药/采购制度命中)。
    """
    if expense_type and expense_type != "TRAVEL":
        return _retrieve_mock_by_expense(city, expense_type)

    # 原协作者逻辑(仅 travel 制度按城市匹配)
    city = city.removesuffix("市") if city else None
    if not city:
        return []
    matches = []
    for item in _load_all_policy():
        if city in item.content and item.chunk_id.startswith("TRAVEL"):
            matches.append(item.model_copy(update={"score": 1.0}))
        elif ("其他城市" in item.content
              and city not in _FIRST_TIER and item.chunk_id.startswith("TRAVEL")):
            matches.append(item.model_copy(update={"score": 0.8}))
    return matches[:2]


def _retrieve_mock_by_expense(city: str | None, expense_type: str) -> list[PolicyEvidence]:
    """按费用类型过滤检索(任务⑥:元数据过滤的 mock 侧近似)。

    遍历全量制度(含扩充),按 content 关键词近似匹配费用类型;
    若同时给了 city,优先返回同时含城市的条款。
    """
    matches = []
    for item in _load_all_policy():
        if not _content_matches_expense(item.content, expense_type):
            continue
        if city:
            c = city.removesuffix("市") if city else ""
            if c and c in item.content:
                matches.append(item.model_copy(update={"score": 1.0}))
            elif "其他城市" in item.content and c not in _FIRST_TIER:
                matches.append(item.model_copy(update={"score": 0.8}))
        else:
            matches.append(item.model_copy(update={"score": 0.8}))
    return matches[:2]


def _content_matches_expense(content: str, expense_type: str) -> bool:
    """按条文内容关键词近似判断费用类型(供 mock 过滤)。"""
    if expense_type == "MEDICAL":
        return any(k in content for k in ("医药", "处方", "住院", "医保", "药"))
    if expense_type == "PURCHASE":
        return any(k in content for k in ("采购", "办公", "设备", "询价"))
    return True


def _retrieve_milvus(query: str, expense_type: str | None = None,
                     department: str | None = None) -> list[PolicyEvidence]:
    """Milvus 混合检索:编码查询 → 稠密+稀疏 → RRF 融合(任务⑥:元数据过滤)。"""
    from FlagEmbedding import BGEM3FlagModel
    from .vector_store import VectorStore

    model_path = os.environ.get(
        "BGE_MODEL_DIR",
        r"<bge-m3 目录>",
    )
    model = BGEM3FlagModel(model_name_or_path=model_path, use_fp16=False)
    out = model.encode([query], return_dense=True, return_sparse=True)
    dense = out["dense_vecs"][0].tolist()
    sparse = dict(out["lexical_weights"][0])

    # 元数据过滤表达式(milvus filter 语法)
    clauses = []
    if expense_type and expense_type != "TRAVEL":
        clauses.append(f'expense_type == "{expense_type}"')
    if department and department != "ALL":
        clauses.append(f'department == "{department}"')
    filter_expr = " && ".join(clauses) if clauses else ""

    store = VectorStore()
    return store.hybrid_search(dense_vec=dense, sparse_vec=sparse, limit=2, filters=filter_expr)
