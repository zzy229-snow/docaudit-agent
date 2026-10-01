"""运行时模型配置(界面"填空")的测试。

上线交付时不能给客户内置我们的 key,所以支持把 provider/base_url/model/api_key
从界面传进来 —— 走**参数**,不改进程环境变量(多用户并发下会互相覆盖)。
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from app.agent.graph import run_audit
from app.services.model_gateway import ModelGateway
from app.services.model_smoke import run_smoke_check

DEMO_TEXT = (
    "申请人：张三\n出差城市：郑州\n出差开始日期：2026-08-01\n出差结束日期：2026-08-02\n"
    "发票号码：12345678\n发票代码：110000000000\n开票日期：2026-08-01\n价税合计：500.00\n"
    "住宿费金额：500.00\n住宿天数：1\n购买方：示例科技有限公司\n"
).encode("utf-8")


class GatewayParamsTest(unittest.TestCase):
    """显式参数优先于环境变量。"""

    def test_explicit_params_override_env(self):
        with mock.patch.dict(os.environ, {
            "MODEL_PROVIDER": "mock", "MODEL_NAME": "env-model",
            "MODEL_BASE_URL": "http://env/v1", "MODEL_API_KEY": "env-key",
        }, clear=False):
            gateway = ModelGateway(
                provider="openai-compatible",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
                api_key="page-key",
            )

        self.assertEqual(gateway.provider, "openai-compatible")
        self.assertEqual(gateway.model, "deepseek-chat")
        self.assertEqual(gateway.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(gateway.api_key, "page-key")     # 没被环境变量里的 env-key 覆盖
        self.assertTrue(gateway.available())

    def test_env_used_when_no_params(self):
        with mock.patch.dict(os.environ, {"MODEL_PROVIDER": "mock"}, clear=False):
            gateway = ModelGateway()

        self.assertEqual(gateway.provider, "mock")
        self.assertTrue(gateway.available())

    def test_incomplete_page_config_is_unavailable(self):
        """页面只填了一半(缺 key)时不能当成可用,否则会在审核中途报错。"""
        gateway = ModelGateway(provider="openai-compatible", base_url="http://x/v1",
                               model="m", api_key="")
        self.assertFalse(gateway.available())

    def test_smoke_check_offline_skips(self):
        result = run_smoke_check(ModelGateway(provider="mock"))
        self.assertEqual(result["status"], "SKIPPED")


class RunAuditSettingsTest(unittest.TestCase):
    """run_audit 要把界面配置一路传到抽取节点。"""

    def _recorder(self, recorded: dict):
        class Recorder:
            def __init__(self, **kwargs):
                recorded.update(kwargs)
                self.provider = kwargs.get("provider", "mock")

            def available(self):
                return False

            def complete_json(self, instruction="", content="", **kwargs):
                return {}

        return Recorder

    def test_settings_reach_extraction(self):
        recorded: dict = {}
        with mock.patch("app.agent.nodes.ModelGateway", self._recorder(recorded)):
            run_audit(
                [("invoice.txt", DEMO_TEXT)],
                llm_settings={
                    "provider": "openai-compatible",
                    "base_url": "https://api.deepseek.com/v1",
                    "model": "deepseek-chat",
                    "api_key": "page-key",
                },
            )

        self.assertEqual(recorded.get("api_key"), "page-key")
        self.assertEqual(recorded.get("model"), "deepseek-chat")

    def test_no_settings_keeps_env_behaviour(self):
        """不传配置时仍然按环境变量走(向后兼容,API 侧不受影响)。"""
        recorded: dict = {}
        with mock.patch("app.agent.nodes.ModelGateway", self._recorder(recorded)):
            report = run_audit([("invoice.txt", DEMO_TEXT)])

        self.assertEqual(recorded, {})          # 未传任何显式参数
        self.assertIsNotNone(report.status)

    def test_settings_do_not_leak_into_process_env(self):
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": "env-key"}, clear=False):
            with mock.patch("app.agent.nodes.ModelGateway", self._recorder({})):
                run_audit([("invoice.txt", DEMO_TEXT)],
                          llm_settings={"provider": "openai-compatible",
                                        "base_url": "http://x/v1", "model": "m",
                                        "api_key": "page-key"})

            self.assertEqual(os.environ["MODEL_API_KEY"], "env-key")


if __name__ == "__main__":
    unittest.main()
