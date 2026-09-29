"""D7 UI smoke tests with Streamlit's simulated app runner."""

from __future__ import annotations

from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest


APP_FILE = Path(__file__).resolve().parents[1] / "streamlit_app.py"


class StreamlitAppTests(unittest.TestCase):
    def test_default_question_shows_card_sources_and_debug(self) -> None:
        app = AppTest.from_file(str(APP_FILE)).run()
        self.assertFalse(app.exception)
        self.assertFalse(app.warning)
        self.assertEqual(app.checkbox[0].label, "用 DeepSeek 为文档证据生成短解释")
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.tabs), 3)
        self.assertEqual(app.metric[0].value, "3.7832 m")
        self.assertFalse(app.error)

    def test_mae_and_unsupported_question_states(self) -> None:
        app = AppTest.from_file(str(APP_FILE)).run()
        app.text_input[0].set_value("本批次 MAE 是多少")
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.metric[0].value, "0.212531 m")
        app.text_input[0].set_value("明天适合去野餐吗")
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)


if __name__ == "__main__":
    unittest.main()
