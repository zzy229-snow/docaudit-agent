"""测试包:为整个测试套件钉住离线确定性组件。

正式防线在 ``app.config.load_environment``(测试运行时不读 ``.env``);这里再加一层
兜底,让 ``.env`` 里配的真实 OCR/模型(例如 ``OCR_ENGINE=baidu``)无论以何种方式被
加载都不会影响测试。需要真组件的用例请用 ``patch.dict(os.environ, ...)`` 显式指定。
"""
import os

#: 单测默认走的离线组件(评测另有 stub 语料引擎,见 app/evaluation/runner.py)
os.environ.setdefault("OCR_ENGINE", "mock")
os.environ.setdefault("MODEL_PROVIDER", "mock")
os.environ.setdefault("RAG_MODE", "mock")
