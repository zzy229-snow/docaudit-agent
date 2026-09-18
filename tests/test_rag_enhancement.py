"""任务⑥ RAG 增强测试:表格切片 + 费用类型/部门元数据过滤 + 制度扩充。

不依赖 Milvus/bge-m3,纯逻辑验证(mock 路由)。Milvus 端需真实索引,留集成标记。
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from app.rag import retriever, chunker
from app.rag.retriever import retrieve_policy
from app.rag.chunker import _split_table, chunk_policy_documents

POLICY_DIR = Path(__file__).resolve().parents[1] / "data" / "policies"


class TableChunkingTests(unittest.TestCase):
    def test_table_splits_into_rows_keeping_header(self):
        """表格内容按行拆开,每行保留表头信息。"""
        table = (
            "表4-1 差旅费用标准表(元):\n"
            "| 费用类型 | 一线城市 | 二线城市 | 其他城市 | 适用部门 |\n"
            "| 住宿费/晚 | 600 | 500 | 450 | 市场部、销售部 |\n"
            "| 餐补/天 | 150 | 120 | 100 | ALL |"
        )
        parts = _split_table(table)
        self.assertEqual(len(parts), 2)
        r1_kid, r1_text = parts[0]
        self.assertEqual(r1_kid, "-R1")
        self.assertIn("住宿费/晚", r1_text)
        self.assertIn("600", r1_text)
        self.assertIn("市场部", r1_text)
        self.assertIn("一线城市", r1_text)  # 表头保留

    def test_non_table_content_unchanged(self):
        text = "出差费发票抬头必须为申请人本人姓名。"
        parts = _split_table(text)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0][0], "")
        self.assertEqual(parts[0][1], text)

    def test_policy_dir_contains_expansion(self):
        """制度扩充:新增 expense_policy.json。"""
        files = {f.name for f in POLICY_DIR.glob("*.json")}
        self.assertTrue({"travel_policy.json", "expense_policy.json"} <= files)


class ExpenseFilterTests(unittest.TestCase):
    """mock 路由下费用类型过滤 + 制度扩充命中。"""

    def setUp(self):
        self._mode = retriever.RAG_MODE
        retriever.RAG_MODE = "mock"

    def tearDown(self):
        retriever.RAG_MODE = self._mode

    def test_medical_expense_hits_medical_policy(self):
        """费用类型 MEDICAL:命中医药制度(不含城市信息)。"""
        result = retrieve_policy(None, expense_type="MEDICAL")
        self.assertTrue(result)
        self.assertIn("医药", result[0].content)

    def test_purchase_expense_hits_purchase_policy(self):
        """费用类型 PURCHASE:命中采购制度。"""
        result = retrieve_policy(None, expense_type="PURCHASE")
        self.assertTrue(result)
        self.assertIn("采购", result[0].content)

    def test_travel_expense_prefers_travel_policy(self):
        """TRAVEL + 城市:仍命中差旅条款。"""
        result = retrieve_policy("上海", expense_type="TRAVEL")
        self.assertEqual(result[0].chunk_id, "TRAVEL-V1-4.2-A")

    def test_files_infer_expense_type(self):
        """从文件名自动推断费用类型(医药材料)。"""
        files = [("医药发票.jpg", b"\x00"), ("处方扫描.pdf", b"\x00")]
        result = retrieve_policy(None, files=files)
        self.assertTrue(result)
        self.assertIn("医药", result[0].content)

    def test_files_infer_purchase_from_office(self):
        """办公采购文件名 → PURCHASE。"""
        files = [("办公采购单.txt", b"\x00")]
        result = retrieve_policy(None, files=files)
        self.assertTrue(any("采购" in r.content for r in result))


class ChunkerMetaTests(unittest.TestCase):
    def test_chunker_infers_expense_type_metadata(self):
        """chunk 携带 expense_type(供 Milvus 元数据过滤)。"""
        chunks = chunk_policy_documents(POLICY_DIR)
        by_kid = {c.chunk_id: c for c in chunks}
        # 医药条款 -> MEDICAL
        self.assertEqual(by_kid["PHARM-V1-3.2"].expense_type, "MEDICAL")
        self.assertEqual(by_kid["PHARM-V1-3.2"].policy_id, "MEDICAL")
        # 采购条款 -> PURCHASE
        self.assertEqual(by_kid["OFFICE-V2-2.1"].expense_type, "PURCHASE")
        self.assertEqual(by_kid["OFFICE-V2-2.1"].version, "V2")
        # 差旅表格行 -> TRAVEL
        self.assertEqual(by_kid["EXP-V1-4.3-TABLE-R1"].expense_type, "EXPENSE")

    def test_table_row_chunk_has_department(self):
        """表格行拆出的 chunk 带部门标记。"""
        chunks = chunk_policy_documents(POLICY_DIR)
        by_kid = {c.chunk_id: c for c in chunks}
        # 市场部/销售部行
        self.assertIn(by_kid["EXP-V1-4.3-TABLE-R1"].department, ("市场部", "销售部"))
        # ALL 行 -> ALL
        self.assertEqual(by_kid["EXP-V1-4.3-TABLE-R5"].department, "ALL")


if __name__ == "__main__":
    unittest.main()
