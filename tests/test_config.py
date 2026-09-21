import os
import tempfile
import unittest
from pathlib import Path

from app.config import load_environment, repo_root


class ConfigTests(unittest.TestCase):
    def test_repo_root_points_to_project_root(self):
        self.assertTrue((repo_root() / "README.md").exists())

    def test_load_environment_reads_dotenv_without_overriding_existing_values(self):
        old_value = os.environ.get("DOC_AUDIT_TEST_ENV")
        old_existing = os.environ.get("DOC_AUDIT_EXISTING_ENV")
        try:
            os.environ.pop("DOC_AUDIT_TEST_ENV", None)
            os.environ["DOC_AUDIT_EXISTING_ENV"] = "keep-me"
            with tempfile.TemporaryDirectory() as tmp:
                dotenv = Path(tmp) / ".env"
                dotenv.write_text(
                    "DOC_AUDIT_TEST_ENV=loaded\nDOC_AUDIT_EXISTING_ENV=from-file\n",
                    encoding="utf-8",
                )

                loaded = load_environment(dotenv, force=True)

            self.assertTrue(loaded)
            self.assertEqual(os.environ["DOC_AUDIT_TEST_ENV"], "loaded")
            self.assertEqual(os.environ["DOC_AUDIT_EXISTING_ENV"], "keep-me")
        finally:
            if old_value is None:
                os.environ.pop("DOC_AUDIT_TEST_ENV", None)
            else:
                os.environ["DOC_AUDIT_TEST_ENV"] = old_value
            if old_existing is None:
                os.environ.pop("DOC_AUDIT_EXISTING_ENV", None)
            else:
                os.environ["DOC_AUDIT_EXISTING_ENV"] = old_existing

    def test_dotenv_is_skipped_under_test_runner(self):
        """测试期间不读 .env:否则本机配了真实 OCR/模型会让单测联网、结论不可复现。"""
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("DOC_AUDIT_TEST_ENV=should-not-load\n", encoding="utf-8")
            old = os.environ.pop("DOC_AUDIT_TEST_ENV", None)
            try:
                self.assertFalse(load_environment(dotenv))
                self.assertNotIn("DOC_AUDIT_TEST_ENV", os.environ)
            finally:
                if old is not None:
                    os.environ["DOC_AUDIT_TEST_ENV"] = old

    def test_audit_load_dotenv_env_var_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("DOC_AUDIT_FORCED_ENV=loaded\n", encoding="utf-8")
            old_forced = os.environ.get("DOC_AUDIT_FORCED_ENV")
            old_flag = os.environ.get("AUDIT_LOAD_DOTENV")
            try:
                os.environ["AUDIT_LOAD_DOTENV"] = "1"
                self.assertTrue(load_environment(dotenv))
                self.assertEqual(os.environ["DOC_AUDIT_FORCED_ENV"], "loaded")
            finally:
                for key, previous in (("DOC_AUDIT_FORCED_ENV", old_forced), ("AUDIT_LOAD_DOTENV", old_flag)):
                    if previous is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = previous


if __name__ == "__main__":
    unittest.main()
