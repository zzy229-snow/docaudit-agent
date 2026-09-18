import unittest

from app.services.model_gateway import ModelGatewayError
from app.services.model_smoke import run_smoke_check


class MockGateway:
    provider = "mock"
    model = ""
    base_url = ""

    def available(self):
        return True


class MissingConfigGateway:
    provider = "openai-compatible"
    model = ""
    base_url = ""

    def available(self):
        return False


class GoodGateway:
    provider = "openai-compatible"
    model = "demo-model"
    base_url = "https://example.com/v1"

    def available(self):
        return True

    def complete_json(self, instruction: str, content: str):
        return {"ok": True, "purpose": "model_smoke"}


class BadGateway(GoodGateway):
    def complete_json(self, instruction: str, content: str):
        return {"ok": False}


class ModelSmokeTests(unittest.TestCase):
    def test_mock_mode_skips_real_api_call(self):
        result = run_smoke_check(MockGateway())

        self.assertEqual(result["status"], "SKIPPED")
        self.assertTrue(result["available"])

    def test_missing_real_config_fails_without_calling_api(self):
        result = run_smoke_check(MissingConfigGateway())

        self.assertEqual(result["status"], "FAILED")
        self.assertIn("配置不完整", result["message"])

    def test_real_gateway_success(self):
        result = run_smoke_check(GoodGateway())

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["response"], {"ok": True, "purpose": "model_smoke"})

    def test_real_gateway_bad_response_raises(self):
        with self.assertRaises(ModelGatewayError):
            run_smoke_check(BadGateway())


if __name__ == "__main__":
    unittest.main()
