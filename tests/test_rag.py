"""D5 tests use a fake model: no key, fee, or network required."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import URLError

from app.knowledge import DEFAULT_CATALOG, NoEvidence, search
from app.rag import DEFAULT_MODEL, DEEPSEEK_URL, DeepSeekClient, LLMRequestError, UnsupportedAnswer, answer_knowledge, render_answer


class FakeClient:
    model = "fake-for-tests"

    def __init__(self, *, bad_citation: bool = False, invented_number: bool = False, bad_quote: bool = False):
        self.calls = 0
        self.bad_citation = bad_citation
        self.invented_number = invented_number
        self.bad_quote = bad_quote

    def complete(self, *, question, evidence):
        self.calls += 1
        if evidence.record["concept"] == "internal_watch":
            quote = next(line for line in evidence.excerpt.splitlines() if "1 Watch" in line)
            explanation = "项目内部 Watch 的门槛是 4.20 m，尚未到 4.43 m。"
        else:
            quote = next(line for line in evidence.excerpt.splitlines() if "达到Watch" in line)
            explanation = "达到 Watch 阈值的实测目标点为 0，因此不能证明召回率。"
        if self.invented_number:
            explanation += "准确率为 99%。"
        if self.bad_quote:
            quote = "这是模型虚构的引文。"
        return json.dumps(
            {
                "explanation": explanation,
                "supporting_quote": quote,
                "citations": ["wrong-id" if self.bad_citation else evidence.record["id"]],
            },
            ensure_ascii=False,
        )


class RAGTests(unittest.TestCase):
    def test_deepseek_adapter_builds_expected_request_without_network(self) -> None:
        evidence = search("项目内部Watch")[0]
        response = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "{\"explanation\":\"test\",\"supporting_quote\":\"test\",\"citations\":[]}"},
            }]
        }
        with patch("app.rag.urlopen") as mocked:
            mocked.return_value.__enter__.return_value = BytesIO(json.dumps(response).encode("utf-8"))
            raw = DeepSeekClient(api_key="test-only-key").complete(question="项目内部 Watch 是什么", evidence=evidence)
        self.assertIn('"explanation"', raw)
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, DEEPSEEK_URL)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-only-key")
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], DEFAULT_MODEL)
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_network_permission_error_explains_how_to_restart(self) -> None:
        evidence = search("项目内部Watch")[0]
        denied = PermissionError(13, "permission denied", None, 10013)
        with patch("app.rag.urlopen", side_effect=URLError(denied)):
            with self.assertRaises(LLMRequestError) as caught:
                DeepSeekClient(api_key="test-only-key").complete(question="项目内部 Watch 是什么", evidence=evidence)
        self.assertIn("WinError 10013", str(caught.exception))
        self.assertIn("PowerShell", str(caught.exception))
        self.assertNotIn("test-only-key", str(caught.exception))

    def test_two_supported_questions_have_verified_citations(self) -> None:
        client = FakeClient()
        internal = answer_knowledge("项目内部 Watch 是什么", client=client)
        limit = answer_knowledge("模型局限是什么", client=client)
        self.assertEqual(client.calls, 2)
        self.assertIn("不是 Environment Agency 的官方 Flood Alert", render_answer(internal))
        self.assertTrue("README.md:13-20" in internal.locator or "internal_watch.md:1-8" in internal.locator)
        self.assertIn("不能据此证明", limit.fact)
        self.assertTrue("HIGH_WATER_EVALUATION_REPORT.md:71-78" in limit.locator or "model_limit.md:1-8" in limit.locator)

    def test_removed_catalog_record_refuses_before_model_call(self) -> None:
        data = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
        data["records"] = [r for r in data["records"] if r["concept"] != "internal_watch"]
        with TemporaryDirectory() as directory:
            catalog = Path(directory) / "without-watch.json"
            catalog.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            client = FakeClient()
            with self.assertRaises(NoEvidence):
                answer_knowledge("项目内部 Watch 是什么", client=client, catalog=catalog)
            self.assertEqual(client.calls, 0)

    def test_removed_source_file_refuses_before_model_call(self) -> None:
        data = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
        data["records"] = [r for r in data["records"] if r["concept"] == "highwater_evaluation_limit"]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = root / "catalog.json"
            catalog.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            client = FakeClient()
            with self.assertRaises(FileNotFoundError):
                answer_knowledge("模型局限是什么", client=client, catalog=catalog, root=root)
            self.assertEqual(client.calls, 0)

    def test_fake_or_unsupported_citation_is_rejected(self) -> None:
        with self.assertRaises(UnsupportedAnswer):
            answer_knowledge("项目内部 Watch 是什么", client=FakeClient(bad_citation=True))
        with self.assertRaises(UnsupportedAnswer):
            answer_knowledge("项目内部 Watch 是什么", client=FakeClient(bad_quote=True))
        with self.assertRaises(UnsupportedAnswer):
            answer_knowledge("模型局限是什么", client=FakeClient(invented_number=True))


if __name__ == "__main__":
    unittest.main()
