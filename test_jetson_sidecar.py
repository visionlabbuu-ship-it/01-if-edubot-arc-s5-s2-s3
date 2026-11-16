import unittest
from unittest.mock import patch

from arc_runtime.config import normalize_openai_base_url
from arc_runtime.jetson_sidecar import CollectionRun, describe_loaded_model
from arc_runtime.sim_llm_chat import assemble_chat_completion_from_sse


class NormalizeBaseUrlTest(unittest.TestCase):
    def test_strips_trailing_v1(self):
        self.assertEqual(
            normalize_openai_base_url("http://127.0.0.1:4000/v1/"),
            "http://127.0.0.1:4000",
        )


class SseAssemblyTest(unittest.TestCase):
    def test_assembles_stream_content_and_tool_calls(self):
        lines = [
            'data: {"id":"chatcmpl-1","model":"local-llama","choices":[{"index":0,"delta":{"role":"assistant","content":"สวัสดี"}}]}',
            'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"scan","arguments":"{\\"color\\":\\"green\\"}"}}]}}]}',
            'data: {"choices":[{"finish_reason":"tool_calls","index":0,"delta":{}}]}',
            "data: [DONE]",
        ]
        completion = assemble_chat_completion_from_sse(lines)
        message = completion["choices"][0]["message"]
        self.assertEqual(completion["id"], "chatcmpl-1")
        self.assertEqual(completion["model"], "local-llama")
        self.assertEqual(message["content"], "สวัสดี")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "scan")
        self.assertEqual(
            message["tool_calls"][0]["function"]["arguments"],
            '{"color":"green"}',
        )
        self.assertEqual(completion["choices"][0]["finish_reason"], "tool_calls")


class DescribeLoadedModelTest(unittest.TestCase):
    def test_reads_q_from_condition_and_gguf_path(self):
        loaded = describe_loaded_model(
            {
                "ok": True,
                "condition_id": "UD-Q2_K_XL",
                "litellm_model": "local-llama",
                "model_path": "/mnt/ssd/arcllm/models/Qwen3.8-27B-UD-Q2_K_XL.gguf",
                "llama_ready": True,
                "litellm_ready": True,
            }
        )
        self.assertEqual(loaded["quantization_label"], "UD-Q2_K_XL")
        self.assertEqual(loaded["api_alias"], "local-llama")
        self.assertEqual(loaded["model_filename"], "Qwen3.8-27B-UD-Q2_K_XL.gguf")
        self.assertEqual(loaded["model_family"], "Qwen3.8-27B")
        self.assertIsNone(loaded["ctx_size"])

    def test_nested_status_payload(self):
        loaded = describe_loaded_model(
            {
                "ok": True,
                "status": {
                    "condition_id": "UD-Q4_K_XL",
                    "model_path": "/models/Qwen3.8-27B-UD-Q4_K_XL.gguf",
                    "litellm_model": "local-llama",
                    "locked_policy": {"ctx_size": 120000},
                },
            }
        )
        self.assertEqual(loaded["quantization_label"], "UD-Q4_K_XL")
        self.assertEqual(loaded["model_filename"], "Qwen3.8-27B-UD-Q4_K_XL.gguf")
        self.assertEqual(loaded["ctx_size"], 120000)


class CollectionRunMarkerTest(unittest.TestCase):
    def test_posts_run_and_request_markers_in_order(self):
        posted = []

        def fake_status(timeout=5.0):
            return {
                "ok": True,
                "llama_ready": True,
                "litellm_ready": True,
                "condition_id": "UD-Q2_K_XL",
            }

        def fake_marker(payload):
            posted.append(dict(payload))
            return {"ok": True, "ack": True}

        with patch("arc_runtime.jetson_sidecar.fetch_status", side_effect=fake_status), patch(
            "arc_runtime.jetson_sidecar.post_marker", side_effect=fake_marker
        ):
            with CollectionRun(task_id="sim-chat", run_id="arc-test-1", wait_bundle=False) as run:
                with run.request() as first:
                    self.assertEqual(first["turn_index"], 0)
                with run.request() as second:
                    self.assertEqual(second["turn_index"], 1)

        types = [item["marker_type"] for item in posted]
        self.assertEqual(
            types,
            [
                "run_started",
                "request_started",
                "request_finished",
                "request_started",
                "request_finished",
                "run_finished",
            ],
        )
        self.assertEqual(posted[0]["condition_id"], "UD-Q2_K_XL")
        self.assertEqual(posted[0]["task_id"], "sim-chat")
        self.assertEqual(posted[1]["turn_index"], 0)
        self.assertEqual(posted[3]["turn_index"], 1)
        self.assertNotIn("X-Arc-Run-Id", posted[0])

    def test_disabled_run_posts_nothing(self):
        with patch("arc_runtime.jetson_sidecar.post_marker") as posted:
            with CollectionRun(task_id="sim-chat", enabled=False) as run:
                with run.request():
                    pass
            posted.assert_not_called()
            self.assertFalse(run.summary()["markers_enabled"])


if __name__ == "__main__":
    unittest.main()
