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
from app.rag.vector_store import VectorStore

POLICY_DIR = Path(__file__).resolve().parents[2] / "data" / "policies"
MODEL_DIR = Path(__file__).resolve().parents[2] / "data" / "rag" / "bge-m3"
#: bge-m3 权重目录:优先 BGE_MODEL_DIR,其次仓库内 data/rag/bge-m3(未下载时给出可操作提示)
DEFAULT_MODEL_DIR = Path(os.environ["BGE_MODEL_DIR"]) if os.environ.get("BGE_MODEL_DIR") else MODEL_DIR


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

    from FlagEmbedding import BGEM3FlagModel

    model_path = DEFAULT_MODEL_DIR if DEFAULT_MODEL_DIR.exists() else MODEL_DIR
    if not model_path.exists():
        raise SystemExit(
            f"找不到 bge-m3 权重目录:{model_path}\n"
            "请用 BGE_MODEL_DIR 指向你本机的 bge-m3 目录,或把权重下载到 "
            f"{MODEL_DIR}(见 docs/RAG_INTEGRATION.md)"
        )
    print(f"[1/3] 加载 bge-m3: {model_path}")
    model = BGEM3FlagModel(model_name_or_path=str(model_path), use_fp16=False)

    print("[2/3] 切片制度...")
    chunks = chunk_policy_documents(POLICY_DIR)
    print(f"      共 {len(chunks)} 个 chunk")
    if not chunks:
        print("      无制度内容,退出")
        return

    print("[3/3] 编码 + 写入 Milvus Lite...")
    dense, sparse = encode(model, chunks)
    store = VectorStore()
    if not args.append:
        store.clear()
    rows = [c.to_milvus_row(d, s) for c, d, s in zip(chunks, dense, sparse)]
    total = store.insert(rows)
    print(f"      索引完成,collection={store.collection},row_count={total}")


if __name__ == "__main__":
    main()
