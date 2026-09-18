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

                loaded = load_environment(dotenv)

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


if __name__ == "__main__":
    unittest.main()
