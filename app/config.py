"""Environment loading helpers.

The project keeps real credentials out of git. Developers can copy
``.env.example`` to ``.env`` locally, and runtime entry points call
``load_environment`` before reading environment variables.

测试期间**不读 .env**(见 ``_under_test_runner``):``.env`` 可能配了真实 OCR/模型
(如 ``OCR_ENGINE=baidu``),让单测联网、花额度、结论不可复现。需要 .env 值时可显式
``load_environment(force=True)``。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _under_test_runner() -> bool:
    """是否运行在单元测试之下(用于跳过 .env,保证离线确定性)。"""
    if os.environ.get("AUDIT_LOAD_DOTENV") == "1":
        return False           # 显式要求读 .env
    if os.environ.get("AUDIT_LOAD_DOTENV") == "0":
        return True
    return "unittest" in sys.modules


def under_test_runner() -> bool:
    """当前进程是否为测试运行器。

    与 :func:`load_environment` 同一口径。除 ``.env`` 外,任务库等"生产数据落点"
    也用它做隔离判断 —— 单测不该往生产 ``data/runtime`` 里写演示报销单
    (2026-10-01 实测:一轮 335 例会写进 28 条任务)。
    """
    return _under_test_runner()


def load_environment(dotenv_path: str | Path | None = None, force: bool = False) -> bool:
    path = Path(dotenv_path) if dotenv_path is not None else repo_root() / ".env"
    if not path.exists():
        return False
    if _under_test_runner() and not force:
        return False
    return load_dotenv(dotenv_path=path, override=False)
