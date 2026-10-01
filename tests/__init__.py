"""测试包:为整个测试套件钉住离线、隔离的运行环境。

三层防线:

1. ``app.config.load_environment``(正式防线):测试运行时不读 ``.env``;
2. 这里钉离线组件(OCR/MODEL/RAG),让 ``.env`` 里配的真实引擎(如 ``OCR_ENGINE=baidu``)
   无论以何种方式被加载都不影响测试;
3. 这里把**任务库 / 发票登记表 / 已发布制度库**切到临时目录 —— 单测会走 API 建任务,
   不钉的话每跑一次测试就在生产 ``data/runtime`` 里留下演示报销单
   (2026-10-01 实测:一轮 335 例往生产库写了 28 条任务/25 份材料)。

需要真组件或真路径的用例,请用 ``patch.dict(os.environ, ...)`` 显式指定。
"""
import os
import tempfile
from pathlib import Path

#: 单测默认走的离线组件(评测另有 stub 语料引擎,见 app/evaluation/runner.py)
os.environ.setdefault("OCR_ENGINE", "mock")
os.environ.setdefault("MODEL_PROVIDER", "mock")
os.environ.setdefault("RAG_MODE", "mock")

#: 单测专用临时运行时目录。必须在任何 ``app.*`` 导入之前赋值:
#: ``app/api/store.py`` 在导入时就用 ``AUDIT_TASK_DB`` 建了单例。
TEST_RUNTIME_DIR = Path(tempfile.mkdtemp(prefix="docaudit_tests_"))
os.environ["AUDIT_TASK_DB"] = str(TEST_RUNTIME_DIR / "audit_tasks.sqlite3")
os.environ["INVOICE_REGISTRY_PATH"] = str(TEST_RUNTIME_DIR / "invoice_registry.sqlite3")
os.environ["POLICY_STORE_PATH"] = str(TEST_RUNTIME_DIR / "policies.sqlite3")
