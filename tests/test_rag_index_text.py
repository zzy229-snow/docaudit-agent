"""索引文本构造测试(app/rag/build_index.embedding_text)。

背景:索引原来只编码 ``content``,而条款号在 ``section_path`` 里 —— 于是"按条款号问"
(如"4.2 条怎么规定的")在向量侧完全命中不了,只有 local(关键词)分支能命中(它检索时拼了
section_path)。本用例把"编码文本必须包含条款路径"钉住:否则以后有人把它改回 ``c.content``,
这个能力会静默消失(而检索仍能返回结果,不容易发现)。
"""
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.rag.build_index import encode, embedding_text  # noqa: E402
from app.rag.chunker import PolicyChunk  # noqa: E402


def chunk(section: str, content: str, cid: str = "TRAVEL-V1-4.2-A") -> PolicyChunk:
    return PolicyChunk(chunk_id=cid, section_path=section, content=content,
                       policy_id="TRAVEL", version="V1", department="ALL",
                       expense_type="TRAVEL", effective_from="2026-01-01", effective_to="")


class _RecordingModel:
    def __init__(self):
        self.texts = None

    def encode(self, texts, return_dense=True, return_sparse=True):
        self.texts = list(texts)
        return {"dense_vecs": [_FakeVec([0.1, 0.2]) for _ in texts],
                "lexical_weights": [{1: 1.0} for _ in texts]}


class _FakeVec(list):
    def tolist(self):
        return list(self)


class EmbeddingTextTests(unittest.TestCase):
    def test_chunk_id_and_section_path_are_included(self):
        """制度编号 + 条款路径都必须进编码文本(编号类查询的唯一入口)。"""
        text = embedding_text(chunk("差旅制度 > 4.2 住宿标准", "北京、上海 600 元/晚"))
        self.assertIn("TRAVEL-V1-4.2-A", text)
        self.assertIn("4.2 住宿标准", text)
        self.assertIn("600 元/晚", text)

    def test_order_is_id_then_path_then_content(self):
        """编号 → 路径 → 正文:入口词在前,不易被长正文淹没。"""
        text = embedding_text(chunk("4.2 住宿", "正文内容"))
        self.assertEqual(["TRAVEL-V1-4.2-A", "4.2 住宿", "正文内容"], text.split("\n"))

    def test_missing_fields_are_skipped(self):
        self.assertEqual("TRAVEL-V1-4.2-A\n纯正文", embedding_text(chunk("", "纯正文")))
        self.assertEqual("", embedding_text(chunk("", "", cid="")))

    def test_encode_uses_embedding_text(self):
        model = _RecordingModel()
        chunks = [chunk("4.2 住宿标准", "正文A"), chunk("5.1 其他", "正文B")]
        encode(model, chunks)
        self.assertEqual([embedding_text(c) for c in chunks], model.texts)

    def test_encode_returns_dense_and_sparse(self):
        dense, sparse = encode(_RecordingModel(), [chunk("4.2", "正文")])
        self.assertEqual([[0.1, 0.2]], dense)
        self.assertEqual([{1: 1.0}], sparse)


if __name__ == "__main__":
    unittest.main()
