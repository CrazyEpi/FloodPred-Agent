"""D7 UI smoke tests with Streamlit's simulated app runner."""

from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from app.router import QueryResult, RoutePlan


APP_FILE = Path(__file__).resolve().parents[1] / "streamlit_app.py"


class StreamlitAppTests(unittest.TestCase):
    def test_default_question_shows_card_sources_and_debug(self) -> None:
        app = AppTest.from_file(str(APP_FILE)).run()
        self.assertFalse(app.exception)
        self.assertFalse(app.warning)
        self.assertEqual(app.checkbox[0].label, "用 DeepSeek 理解和回答")
        self.assertEqual(app.checkbox[1].label, "开启 Thinking（显示思维链）")
        app.checkbox[0].set_value(False)
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual([tab.label for tab in app.tabs], ["回答", "证据", "运行细节"])
        self.assertEqual(app.metric[0].value, "3.7832 m")
        self.assertFalse(app.error)

    def test_mae_and_unsupported_question_states(self) -> None:
        app = AppTest.from_file(str(APP_FILE)).run()
        app.checkbox[0].set_value(False)
        app.text_input[0].set_value("本批次 MAE 是多少")
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.metric[0].value, "0.212531 m")
        app.text_input[0].set_value("明天适合去野餐吗")
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)

    def test_reasoning_content_is_only_in_debug_expander(self) -> None:
        result = QueryResult(
            request_id="test", question="你好", as_of_utc=None,
            plan=RoutePlan((), (), None), response_mode="greeting", answer_text="你好！",
            reasoning_traces=[{"stage": "问题规划", "content": "这只是模型的中间文字"}],
        )
        with patch("app.router.run_query", return_value=result):
            app = AppTest.from_file(str(APP_FILE)).run()
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any("思维链 · 问题规划" in entry.label for entry in app.expander))
        self.assertTrue(any("不是项目事实" in entry.value for entry in app.warning))

    def test_thinking_checkbox_reaches_router(self) -> None:
        result = QueryResult(
            request_id="test", question="你好", as_of_utc=None,
            plan=RoutePlan((), (), None), response_mode="greeting", answer_text="你好！",
        )
        with patch("app.router.run_query", return_value=result) as mocked:
            app = AppTest.from_file(str(APP_FILE)).run()
            app.checkbox[1].set_value(False)
            app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertIs(mocked.call_args.kwargs["thinking_enabled"], False)


if __name__ == "__main__":
    unittest.main()
