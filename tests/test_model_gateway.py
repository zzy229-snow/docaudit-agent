import unittest

from app.services.model_gateway import ModelGatewayError, parse_json_object


class ModelGatewayTests(unittest.TestCase):
    def test_parse_json_object_accepts_markdown_fence(self):
        value = parse_json_object('```json\n{"ok": true}\n```')

        self.assertEqual(value, {"ok": True})

    def test_parse_json_object_rejects_missing_json(self):
        with self.assertRaises(ModelGatewayError):
            parse_json_object("not json")


if __name__ == "__main__":
    unittest.main()
