"""制度检索器(PRD §9.3)。RAG_MODE 控制后端:

- mock   (默认):规则匹配基线,零依赖,保持接口原有行为;
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
from .chunker import PolicyChunk, chunk_policy_documents

load_environment()

POLICY_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"

RAG_MODE = os.environ.get("RAG_MODE", "mock").strip().lower()

#: 最近一次检索的**说明**(用了哪个引擎、是否降级、命中几条)。
#: 审核流程(``app.agent.nodes.retrieve``)会把它写进 trace —— 因为降级路径原来是静默的:
#: milvus/local 检索一失败就退到 mock,只 print 一行,结论看起来却一样。
#: 对"制度依据必须可追溯"的审核系统来说,这种静默降级是隐患(也让集成测试产生假信心)。
RETRIEVAL_NOTE = ""

#: 最近一次 milvus 检索用的重排层(实体 / 实体+bge-reranker-v2-m3 …),写进 trace 便于追溯
LAST_RERANK_LABEL = ""

#: 送进重排的候选条款数(重排要有腾挪空间:只取 2 条则排序问题无从修正)
_CANDIDATES = 6


def set_retrieval_note(text: str) -> None:
    global RETRIEVAL_NOTE
    RETRIEVAL_NOTE = text


def _degrade_reason(exc: BaseException) -> str:
    """把底层原因也带上。

    milvus-lite 是**文件级单进程独占锁**:工作台与 API 两个进程同时跑时,后启动的那个拿不到锁,
    pymilvus 只会抛一个没有信息的 ``ConnectionConfigException()``,真正的原因(DataDirLockedError)
    在 ``__cause__`` 里。不带出来时,运维只看到"检索失败"却不知道是锁冲突。
    """
    cause = exc.__cause__
    text = f"{exc!r}"
    if cause is not None and repr(cause) != repr(exc):
        text += f"(根因: {cause!r})"
    return text


def _fusion_label() -> str:
    """当前融合方式的可读标签(与 vector_store.SPARSE_WEIGHT_ENV 对应)。"""
    from .vector_store import SPARSE_WEIGHT_ENV

    weight = os.environ.get(SPARSE_WEIGHT_ENV, "0") or "0"
    try:
        return "稠密" if float(weight) <= 0 else f"稠密+稀疏(权重{weight})"
    except ValueError:
        return "稠密"

_FIRST_TIER = {"北京", "上海", "广州", "深圳"}


def _load_all_policy() -> list[PolicyEvidence]:
    """加载全部制度(含扩充的 expense_policy.json),映射为 PolicyEvidence。

    通过 chunker 拿到已按行切片、带元数据的 chunk,供 mock 侧过滤。
    契约 PolicyEvidence 不变(仍为 chunk_id/section/content/score)。
    """
    chunks = chunk_policy_documents(POLICY_DIR)
    return [PolicyEvidence(chunk_id=c.chunk_id, section=c.section_path,
                           content=c.content, score=0.0) for c in chunks]


def published_chunks(department: str | None = None, expense_type: str | None = None,
                     as_of: str | None = None) -> list[PolicyChunk]:
    """已发布制度的切片(FR-302/FR-304);制度存储不可用时返回空列表,不影响主流程。"""
    try:
        from app.services.policy_store import load_published_chunks

        return load_published_chunks(department=department, expense_type=expense_type, as_of=as_of)
    except Exception as exc:  # noqa: BLE001 — 制度库异常不得中断审核
        print(f"[rag] 读取已发布制度失败:{exc!r}")
        return []


def _published_evidence(department: str | None = None, expense_type: str | None = None,
                        as_of: str | None = None) -> list[PolicyEvidence]:
    return [
        PolicyEvidence(chunk_id=c.chunk_id, section=c.section_path, content=c.content, score=0.0)
        for c in published_chunks(department=department, expense_type=expense_type, as_of=as_of)
    ]


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


def _local_evidence(query: str, city: str | None, expense_type: str | None,
                    department: str | None, as_of: str | None) -> list[PolicyEvidence]:
    """走一次本地关键词检索(含已发布制度 + 城市语义重排)。"""
    extra = published_chunks(department=department, expense_type=expense_type, as_of=as_of)
    evidence = _retrieve_local(query or _query_from_city(city), extra_chunks=extra)
    return _rerank_by_city(evidence, city)


def retrieve_policy(city: str | None, query: str | None = None,
                    expense_type: str | None = None, department: str | None = None,
                    files=None, as_of: str | None = None) -> list[PolicyEvidence]:
    """按城市(或自由文本 query)检索制度,支持元数据过滤(任务⑥/FR-304)。

    - expense_type: 费用类型(TRAVEL/EXPENSE/MEDICAL/PURCHASE);
    - department : 适用部门(如 研发部);
    - files      : 上传文件名列表,用于自动推断 expense_type(未显式指定时);
    - as_of      : 任务日期,用于制度版本生效区间过滤(FR-303/304)。

    降级链:**milvus → local → mock**。向量检索失败时先退到本地关键词检索(仍是真检索、
    结果可引用),只有 local 也失败才退到规则匹配基线。原来的降级链是 milvus → mock,
    而 mock 只按城市字符串匹配、不看查询内容(问"发票代码怎么填"也会返回住宿条款),
    用它兜底等于把"依据不可用"伪装成"有依据"。

    未指定费用类型时默认 TRAVEL(当前唯一接入的审核场景是差旅报销),
    以保证差旅 Recall@1 契约不被扩充制度干扰。返回契约 PolicyEvidence 不变。
    """
    if files and not expense_type:
        expense_type = _resolve_expense_type(files)
    if not expense_type:
        expense_type = "TRAVEL"

    if RAG_MODE == "local":
        try:
            evidence = _local_evidence(query, city, expense_type, department, as_of)
            set_retrieval_note(f"local(本地切片检索,命中{len(evidence)}条)")
            return evidence
        except Exception as exc:  # noqa: BLE001 — 检索失败必须降级不可中断主流程
            set_retrieval_note(f"local 检索失败，已降级 mock:{_degrade_reason(exc)}")
            print(f"[rag] local 检索失败,降级 mock: {exc!r}")
            return _retrieve_mock(city, expense_type, department, as_of)
    if RAG_MODE == "milvus":
        try:
            global LAST_RERANK_LABEL
            LAST_RERANK_LABEL = ""  # 防止上一次调用的标签残留到这次 trace
            evidence = _retrieve_milvus(query or city or "", expense_type, department, city)
            note = f"milvus(融合={_fusion_label()}"
            if LAST_RERANK_LABEL:
                note += f",重排={LAST_RERANK_LABEL}"
            set_retrieval_note(note + f",命中{len(evidence)}条)")
            return evidence
        except Exception as exc:  # noqa: BLE001
            reason = _degrade_reason(exc)
            try:
                evidence = _local_evidence(query, city, expense_type, department, as_of)
                set_retrieval_note(f"milvus 检索失败，已降级 local(关键词检索):{reason}")
                print(f"[rag] milvus 检索失败,降级 local: {exc!r}")
                return evidence
            except Exception as exc2:  # noqa: BLE001 — 最后一层兜底
                set_retrieval_note(
                    f"milvus 检索失败，已降级 mock:{reason};local 亦失败:{_degrade_reason(exc2)}")
                print(f"[rag] milvus/local 均失败,降级 mock: {exc!r} / {exc2!r}")
                return _retrieve_mock(city, expense_type, department, as_of)
    set_retrieval_note("mock(规则匹配，未使用向量检索)")
    return _retrieve_mock(city, expense_type, department, as_of)


def _retrieve_local(query: str, extra_chunks: list[PolicyChunk] | None = None) -> list[PolicyEvidence]:
    from .local_store import retrieve_local

    return retrieve_local(query, limit=2, extra_chunks=extra_chunks)


def _query_from_city(city: str | None) -> str:
    if not city:
        return ""
    return f"{city} 住宿标准 差旅 报销 酒店"


def _rerank_by_city(evidence: list[PolicyEvidence], city: str | None) -> list[PolicyEvidence]:
    """local 检索结果按城市语义重排(任务⑥,与 mock 规则一致)。

    关键词检索无法区分"北京、上海…的住宿标准"与"其他城市的住宿标准",
    会导致非一线城市误命中一线条款。按下列优先级稳定重排:
    直接含查询城市 > 其他城市兜底(查询城市非一线) > 其余。
    """
    if not city:
        return evidence
    c = city.removesuffix("市")

    def rank(item: PolicyEvidence) -> int:
        if c and c in item.content:
            return 0
        if "其他城市" in item.content and c not in _FIRST_TIER:
            return 1
        return 2

    return sorted(evidence, key=rank)


def _retrieve_mock(city: str | None, expense_type: str | None = None,
                   department: str | None = None, as_of: str | None = None) -> list[PolicyEvidence]:
    """规则匹配基线(任务⑥):城市匹配 + 费用类型过滤 + 已发布制度参与。

    兼容既有行为:仅按城市查询(expense_type=TRAVEL/None)时,保持原 travel 语义
    (一线命中 A、其他命中 B);已发布制度仅在条款显式包含该城市时以更高权重参与
    (score 1.05),不覆盖基线的"其他城市"兜底条款,避免改变既有评测结论。
    """
    if expense_type and expense_type != "TRAVEL":
        return _retrieve_mock_by_expense(city, expense_type, department, as_of)

    city = city.removesuffix("市") if city else None
    if not city:
        return []
    matches: list[tuple[float, int, PolicyEvidence]] = []
    for order, item in enumerate(_load_all_policy()):
        if city in item.content and item.chunk_id.startswith("TRAVEL"):
            matches.append((1.0, order, item))
        elif ("其他城市" in item.content
              and city not in _FIRST_TIER and item.chunk_id.startswith("TRAVEL")):
            matches.append((0.8, order, item))
    base_count = len(matches)
    for order, item in enumerate(_published_evidence(department=department, expense_type="TRAVEL", as_of=as_of)):
        if city in item.content:
            matches.append((1.05, base_count + order, item))
    matches.sort(key=lambda entry: (-entry[0], entry[1]))
    return [item.model_copy(update={"score": score}) for score, _order, item in matches[:2]]


def _retrieve_mock_by_expense(city: str | None, expense_type: str,
                              department: str | None = None,
                              as_of: str | None = None) -> list[PolicyEvidence]:
    """按费用类型过滤检索(任务⑥:元数据过滤的 mock 侧近似)。"""
    matches: list[tuple[float, int, PolicyEvidence]] = []
    for order, item in enumerate(_load_all_policy()):
        if not _content_matches_expense(item.content, expense_type):
            continue
        if city:
            c = city.removesuffix("市") if city else ""
            if c and c in item.content:
                matches.append((1.0, order, item))
            elif "其他城市" in item.content and c not in _FIRST_TIER:
                matches.append((0.8, order, item))
        else:
            matches.append((0.8, order, item))
    base_count = len(matches)
    # 已发布制度已按 expense_type/department/生效区间过滤,直接参与排序
    for order, item in enumerate(_published_evidence(department=department, expense_type=expense_type, as_of=as_of)):
        score = 1.05 if (city and city.removesuffix("市") in item.content) else 0.9
        matches.append((score, base_count + order, item))
    matches.sort(key=lambda entry: (-entry[0], entry[1]))
    return [item.model_copy(update={"score": score}) for score, _order, item in matches[:2]]


def _content_matches_expense(content: str, expense_type: str) -> bool:
    """按条文内容关键词近似判断费用类型(供 mock 过滤)。"""
    if expense_type == "MEDICAL":
        return any(k in content for k in ("医药", "处方", "住院", "医保", "药"))
    if expense_type == "PURCHASE":
        return any(k in content for k in ("采购", "办公", "设备", "询价"))
    return True


def _retrieve_milvus(query: str, expense_type: str | None = None,
                     department: str | None = None, city: str | None = None) -> list[PolicyEvidence]:
    """Milvus 检索:编码查询 → 稠密(可选+稀疏加权) → 元数据过滤 → 重排(任务⑥)。

    编码器与连接都走进程内单例(``embedding.get_model`` / ``vector_store.get_store``):
    原来每次检索都重新加载一次 bge-m3 权重(实测 3.1s/次),纯属浪费。

    候选池取 ``_CANDIDATES`` 条后交给 ``rerank`` 重排再截断:只取 2 条的话重排没有腾挪空间,
    "上海住宿标准"这类实体张冠李戴的排序问题就修不掉。
    """
    global LAST_RERANK_LABEL

    from .embedding import encode_query, get_model
    from .rerank import rerank
    from .vector_store import get_store

    dense, sparse = encode_query(get_model(), query)

    # 元数据过滤表达式(milvus filter 语法)
    clauses = []
    if expense_type and expense_type != "TRAVEL":
        clauses.append(f'expense_type == "{expense_type}"')
    if department and department != "ALL":
        clauses.append(f'department == "{department}"')
    filter_expr = " && ".join(clauses) if clauses else ""

    candidates = get_store().hybrid_search(dense_vec=dense, sparse_vec=sparse,
                                           limit=_CANDIDATES, filters=filter_expr)
    evidence, label = rerank(query, candidates, city=city, top_k=2)
    LAST_RERANK_LABEL = label
    return evidence
