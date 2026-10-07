"""编码器/连接复用测试(app/rag/embedding.py、vector_store.get_store)。

起因:``retriever._retrieve_milvus`` 原来每次检索都 ``BGEM3FlagModel(...)``,实测每次 3.1s、
冷盘 16s(16 个 chunk 的语料,时间全花在加载 ~2GB 权重上)。这里把"只加载一次"钉成回归测试 ——
否则以后有人顺手把它挪回函数体内,性能会静默退回去。

不加载真实权重:注入一个假的 ``FlagEmbedding`` 模块统计实例化次数。
"""
import os
import sys
import types
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.rag import embedding  # noqa: E402


class _FakeVec(list):
    """模拟 numpy 数组:embedding.encode_query 会调 .tolist()。"""

    def tolist(self):
        return list(self)


class _FakeModel:
    instances = 0
    last_kwargs: dict = {}

    def __init__(self, model_name_or_path=None, use_fp16=False):
        _FakeModel.instances += 1
        _FakeModel.last_kwargs = {"path": model_name_or_path, "use_fp16": use_fp16}

    def encode(self, texts, return_dense=True, return_sparse=True):
        return {"dense_vecs": [_FakeVec([0.1, 0.2, 0.3]) for _ in texts],
                "lexical_weights": [{11: 0.5, 22: 0.25} for _ in texts]}


class EmbeddingCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_module = sys.modules.get("FlagEmbedding")
        fake = types.ModuleType("FlagEmbedding")
        fake.BGEM3FlagModel = _FakeModel
        sys.modules["FlagEmbedding"] = fake

    @classmethod
    def tearDownClass(cls):
        if cls._saved_module is None:
            sys.modules.pop("FlagEmbedding", None)
        else:
            sys.modules["FlagEmbedding"] = cls._saved_module

    def setUp(self):
        embedding.reset_model()
        _FakeModel.instances = 0
        # 记下原值:本用例会改这两个环境变量,不能污染其他用例(踩过:清理 BGE_MODEL_DIR 后
        # 按字母序排在其后的 test_rag_milvus 直接降级到 mock 而失败)
        self._saved_env = {k: os.environ.get(k) for k in (embedding.MODEL_DIR_ENV, embedding.FP16_ENV)}

    def tearDown(self):
        embedding.reset_model()
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_model_is_loaded_once_for_same_path(self):
        """同一路径重复取模型 = 同一个实例、且只加载一次(核心回归断言)。"""
        first = embedding.get_model("/tmp/some-bge-m3")
        second = embedding.get_model("/tmp/some-bge-m3")
        self.assertIs(first, second)
        self.assertEqual(1, _FakeModel.instances)

    def test_model_reloads_when_path_changes(self):
        embedding.get_model("/tmp/a")
        embedding.get_model("/tmp/b")
        self.assertEqual(2, _FakeModel.instances)

    def test_reset_model_forces_reload(self):
        embedding.get_model("/tmp/a")
        embedding.reset_model()
        embedding.get_model("/tmp/a")
        self.assertEqual(2, _FakeModel.instances)

    def test_fp16_switch_is_passed_through(self):
        os.environ[embedding.FP16_ENV] = "1"
        embedding.get_model("/tmp/a")
        self.assertTrue(_FakeModel.last_kwargs["use_fp16"])
        embedding.reset_model()
        os.environ[embedding.FP16_ENV] = "0"
        embedding.get_model("/tmp/a")
        self.assertFalse(_FakeModel.last_kwargs["use_fp16"])

    def test_resolve_model_path_prefers_env_and_reports_missing_dir(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            os.environ[embedding.MODEL_DIR_ENV] = tmp
            self.assertEqual(tmp, embedding.resolve_model_path())

        os.environ[embedding.MODEL_DIR_ENV] = str(BASE / "no-such-dir")
        with self.assertRaises(RuntimeError) as ctx:
            embedding.resolve_model_path()
        # 报错要可操作:说清是哪个环境变量、指向哪、去哪看文档
        self.assertIn(embedding.MODEL_DIR_ENV, str(ctx.exception))
        self.assertIn("docs/RAG_INTEGRATION.md", str(ctx.exception))

    def test_encode_query_shape(self):
        dense, sparse = embedding.encode_query(_FakeModel(), "北京住宿标准")
        self.assertEqual([0.1, 0.2, 0.3], dense)
        self.assertEqual({11: 0.5, 22: 0.25}, sparse)

    def test_missing_flagembedding_gives_actionable_error(self):
        sys.modules["FlagEmbedding"] = None  # 模拟未安装
        try:
            with self.assertRaises(RuntimeError) as ctx:
                embedding.get_model("/tmp/a")
            self.assertIn("requirements-rag.txt", str(ctx.exception))
        finally:
            fake = types.ModuleType("FlagEmbedding")
            fake.BGEM3FlagModel = _FakeModel
            sys.modules["FlagEmbedding"] = fake


class StoreCacheTests(unittest.TestCase):
    def test_get_store_reuses_connection_per_uri(self):
        """同 uri 只建一次连接(milvus-lite 打开文件库有成本,且锁语义要清楚)。"""
        from app.rag import vector_store

        calls = []

        class _FakeStore:
            def __init__(self, uri=None, collection=None):
                calls.append((uri, collection))

        saved = vector_store.VectorStore
        vector_store.VectorStore = _FakeStore
        try:
            vector_store.reset_stores()
            a = vector_store.get_store(uri="/tmp/x.db", collection="c1")
            b = vector_store.get_store(uri="/tmp/x.db", collection="c1")
            c = vector_store.get_store(uri="/tmp/y.db", collection="c1")
            self.assertIs(a, b)
            self.assertIsNot(a, c)
            self.assertEqual(2, len(calls))
        finally:
            vector_store.VectorStore = saved
            vector_store.reset_stores()


if __name__ == "__main__":
    unittest.main()
