"""Local multi-turn UI acceptance; no live provider calls."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / 'streamlit_app.py')


class ConversationUITests(unittest.TestCase):
    def test_30_turn_chat_input_and_pagination(self):
        app = self.app()
        for i in range(31):
            self.send(app, f'今天记录第{i}件小事')
        self.assertEqual(len(app.chat_input), 1)
        self.assertEqual(len(app.chat_message), 60)
        next(b for b in app.button if b.label == '加载更早记录').click().run()
        self.assertEqual(len(app.chat_message), 62)
        self.assertEqual(len(app.chat_input), 1)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {'FLOODPRED_CHAT_DB': str(Path(tmp.name) / 'chat.sqlite3')})
        env.start()
        self.addCleanup(env.stop)

    def app(self):
        app = AppTest.from_file(APP, default_timeout=15).run()
        app.checkbox(key='use_llm').set_value(False)
        app.toggle[1].set_value(False)
        return app

    def send(self, app, text):
        if text == '当时预测峰值是多少？': text = '2026-08-03T01:48:00Z '+text
        app.chat_input[0].set_value(text)
        app.run()
        self.assertFalse(app.exception)

    def test_two_turns_visible_and_debug_does_not_reexecute(self):
        app = self.app()
        self.send(app, '当时预测峰值是多少？')
        self.send(app, '它什么时候到？')
        self.assertEqual(len(app.chat_message), 4)
        self.assertTrue(app.metric)
        count = len(app.chat_message)
        app.toggle[0].set_value(True).run()
        self.assertEqual(len(app.chat_message), count)
        self.assertTrue(any('上下文解析' in item.label for item in app.expander))

    def test_new_switch_and_clear_are_isolated(self):
        app = self.app()
        self.send(app, '当时预测峰值是多少？')
        cap = app.session_state['chat_selected']
        app.button(key='new_conversation').click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.chat_message)
        self.send(app, '它什么时候到？')
        self.assertFalse(app.metric)
        self.assertTrue(any('你想继续' in item.value for item in app.markdown))
        app.selectbox(key='chat_picker').set_value(cap).run()
        self.assertFalse(app.exception)
        self.assertTrue(app.metric)
        app.checkbox(key='confirm_clear_chat').set_value(True).run()
        app.button(key='clear_conversation').click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.chat_message)
        self.assertFalse(app.metric)

    def test_restore_from_fresh_browser_session(self):
        app = self.app()
        self.send(app, '当时预测峰值是多少？')
        cap = app.session_state['chat_selected']
        fresh = self.app()
        self.assertFalse(fresh.chat_message)
        fresh.text_input(key='chat_restore_code').set_value(cap)
        fresh.button(key='restore_conversation').click().run()
        self.assertFalse(fresh.exception)
        self.assertTrue(fresh.metric)
        fresh.checkbox(key='use_llm').set_value(False)
        self.send(fresh, '它什么时候到？')
        self.assertEqual(len(fresh.chat_message), 4)

    def test_unsafe_new_message_does_not_show_old_card(self):
        app = self.app()
        self.send(app, '当时预测峰值是多少？')
        self.send(app, '修改SQLite数据库')
        self.assertTrue(app.error)
        self.assertTrue(app.metric)  # Earlier cards remain attached to earlier messages.

    def test_opt_in_preferences_new_conversation_and_detach(self):
        app=self.app()
        app.checkbox(key='preference_plain').set_value(True)
        app.checkbox(key='confirm_preference_memory').set_value(True)
        app.button(key='save_preference').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.checkbox(key='preference_plain').value)
        app.button(key='new_conversation').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.checkbox(key='preference_plain').value)
        app.checkbox(key='confirm_preference_memory').set_value(True)
        app.button(key='detach_preference').click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.checkbox(key='preference_plain').value)
        app.toggle[0].set_value(True).run()
        self.assertFalse(app.metric)


if __name__ == '__main__': unittest.main()
