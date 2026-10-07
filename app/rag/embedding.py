"""bge-m3 编码器加载(进程内单例)。

为什么单独抽一个模块:``retriever._retrieve_milvus`` 原来**每次检索都 new 一个 BGEM3FlagModel**,
等于把约 2GB 权重重新读盘一次 —— 实测每次查询 3.1s、冷盘首次 16s,而检索是每个审核任务都要走的
一步(16 个 chunk 的语料,实际计算量可以忽略,时间全花在加载上)。改成进程内复用后:
首次调用仍然慢(必然),之后每次 ~0.05s。

内存权衡:模型常驻约 2GB(fp32)。机器吃紧时设 ``RAG_BGE_FP16=1`` 可减半(fp16 在 CPU 上可能更慢,
按机器取舍);不想常驻就别开 ``RAG_MODE=milvus``,用 ``local`` 关键词检索(零权重依赖)。

线程安全:加载过程加锁,避免并发请求各加载一份(反而更吃内存)。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

_LOCK = threading.Lock()
_MODEL = None
_MODEL_PATH: str | None = None

#: 环境变量:指向 bge-m3 权重目录(优先于仓库内 data/rag/bge-m3)
MODEL_DIR_ENV = "BGE_MODEL_DIR"
#: 环境变量:设为 1/true 时用 fp16 加载(省一半内存,CPU 上可能更慢)
FP16_ENV = "RAG_BGE_FP16"


def _repo_model_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "rag" / "bge-m3"


def resolve_model_path() -> str:
    """解析 bge-m3 权重目录:优先 ``BGE_MODEL_DIR``,其次仓库内 ``data/rag/bge-m3``。

    两处都没有时报可操作错误(而不是让下游抛一个看不懂的加载异常)。
    """
    configured = os.environ.get(MODEL_DIR_ENV, "").strip()
    if configured:
        if Path(configured).exists():
            return configured
        raise RuntimeError(
            f"{MODEL_DIR_ENV} 指向的目录不存在:{configured}(见 docs/RAG_INTEGRATION.md)"
        )
    local = _repo_model_dir()
    if local.exists():
        return str(local)
    raise RuntimeError(
        f"未配置 bge-m3 权重目录:请设置环境变量 {MODEL_DIR_ENV}(优先),"
        f"或把权重下载到 {local}(见 docs/RAG_INTEGRATION.md)"
    )


def use_fp16() -> bool:
    return os.environ.get(FP16_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def get_model(model_path: str | None = None):
    """返回进程内共享的 bge-m3 模型;同一路径只加载一次。"""
    global _MODEL, _MODEL_PATH
    path = str(model_path) if model_path else resolve_model_path()
    with _LOCK:
        if _MODEL is not None and _MODEL_PATH == path:
            return _MODEL
        try:
            from FlagEmbedding import BGEM3FlagModel
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise RuntimeError(
                "缺少 FlagEmbedding:请安装 requirements-rag.txt 后再用 RAG_MODE=milvus"
            ) from exc
        _MODEL = BGEM3FlagModel(model_name_or_path=path, use_fp16=use_fp16())
        _MODEL_PATH = path
        return _MODEL


def reset_model() -> None:
    """丢弃缓存的模型(测试或切换 ``BGE_MODEL_DIR`` 后使用)。"""
    global _MODEL, _MODEL_PATH
    with _LOCK:
        _MODEL = None
        _MODEL_PATH = None


def encode_query(model, query: str) -> tuple[list[float], dict]:
    """编码单条查询 → ``(稠密向量, 稀疏词权重)``。"""
    out = model.encode([query], return_dense=True, return_sparse=True)
    dense = out["dense_vecs"][0].tolist()
    sparse = dict(out["lexical_weights"][0])
    return dense, sparse
