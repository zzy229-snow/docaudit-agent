"""运行环境隔离的回归测试。

背景(2026-10-01 实测):单测会通过 API/存储用例建报销单。任务库路径没隔离时,
**每跑一轮测试就往生产 ``data/runtime/audit_tasks.sqlite3`` 写 28 条演示任务**,
工作台任务列表里凭空多出"张三/alice 住宿报销"这类演示数据。

为什么闸门装在 ``app/api/store.py`` 而不是只放 ``tests/__init__.py``:
``unittest discover -s tests`` 把测试模块当**顶层模块**导入(``test_xxx`` 而不是
``tests.test_xxx``),``tests/__init__.py`` 不会先执行,存储单例早就指向生产库了。
所以真正的闸门在应用侧 —— 与 ``app/config.py`` 的"单测不读 .env"同一口径。

这几条测试锁住该规则:谁顺手拆了闸门,这里立刻红。
"""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from app.api.store import _default_db_path
from app.config import repo_root, under_test_runner


class RuntimeIsolationTest(unittest.TestCase):
    def test_under_test_runner_detected(self):
        self.assertTrue(under_test_runner(), "单测进程必须被识别出来,否则闸门不生效")

    def test_task_store_not_in_production_runtime(self):
        db_path = _default_db_path()
        production_dir = repo_root() / "data" / "runtime"
        self.assertFalse(production_dir in db_path.parents,
                         f"单测的任务库落到了生产目录:{db_path}")
        self.assertTrue(db_path.parent.name.startswith(("docaudit_unittest_", "docaudit_tests_")))

    def test_explicit_env_still_wins(self):
        """显式指定 AUDIT_TASK_DB 时按指定的走(闸门只兜默认值)。"""
        from unittest import mock

        target = repo_root() / "data" / "runtime" / "explicit.sqlite3"
        with mock.patch.dict(os.environ, {"AUDIT_TASK_DB": str(target)}, clear=False):
            self.assertEqual(_default_db_path(), target)


if __name__ == "__main__":
    unittest.main()
