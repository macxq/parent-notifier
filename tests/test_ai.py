import os
import unittest
from unittest.mock import patch

from vulcan_messages import analyze_importance_with_ai

class TestAIAnalysis(unittest.TestCase):
    def test_missing_api_key_returns_default(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=True):
            res = analyze_importance_with_ai("Temat", "Cześć rodzice")
            self.assertFalse(res["is_important"])
            self.assertEqual(res["important_note"], "")
            self.assertEqual(res["highlighted_body"], "Cześć rodzice")
            self.assertEqual(res["calendar_events"], [])

if __name__ == "__main__":
    unittest.main()
