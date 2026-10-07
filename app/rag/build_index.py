"""建立制度向量索引(PRD §9.1 索引流程)。

运行环境:需要 bge-m3 编码能力(FlagEmbedding+torch,见 requirements-rag.txt)。
建议装在独立虚拟环境/conda 环境里(不要混进主环境),并设置 BGE_MODEL_DIR 指向 bge-m3 权重:
    BGE_MODEL_DIR=<你的 bge-m3 目录> python -m app.rag.build_index

用法:
    python -m app.rag.build_index            # 全量重建(清空后插入)
    python -m app.rag.build_index --append   # 不清空,增量插入
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.rag.chunker import chunk_policy_documents

POLICY_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"
MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "rag" / "bge-m3"


def encode(model, chunks):
    """bge-m3 稠密+稀疏编码。"""
    out = model.encode([c.content for c in chunks], return_dense=True, return_sparse=True)
    dense = [v.tolist() for v in out["dense_vecs"]]
    sparse = [dict(w) for w in out["lexical_weights"]]
    return dense, sparse


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--append", action="store_true", help="增量插入,不清空现有索引")
    args = ap.parse_args()
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    from app.rag.embedding import get_model, resolve_model_path
    from app.rag.vector_store import get_store

    try:
        model_path = resolve_model_path()
    except RuntimeError as exc:
        raise SystemExit(f"{exc}\n请用 BGE_MODEL_DIR 指向你本机的 bge-m3 目录,或把权重下载到 {MODEL_DIR}")
    print(f"[1/3] 加载 bge-m3: {model_path}")
    model = get_model(model_path)

    print("[2/3] 切片制度...")
    chunks = chunk_policy_documents(POLICY_DIR)
    print(f"      共 {len(chunks)} 个 chunk")
    if not chunks:
        print("      无制度内容,退出")
        return

    print("[3/3] 编码 + 写入 Milvus Lite...")
    dense, sparse = encode(model, chunks)
    store = get_store()
    if not args.append:
        store.clear()
    rows = [c.to_milvus_row(d, s) for c, d, s in zip(chunks, dense, sparse)]
    total = store.insert(rows)
    print(f"      索引完成,collection={store.collection},row_count={total}")


if __name__ == "__main__":
    main()
