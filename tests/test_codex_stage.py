import asyncio
from collections import deque
import json
import time
import unittest

from roleshuttle.codex_stage import stage, subscription_ready


def quota():
    return {"ordinaryUsageAllowed": True, "accountId": "example", "rateLimits": {
        "spendControlReached": False, "rateLimitReachedType": None,
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
        "primary": {"usedPercent": 12, "resetsAt": time.time() + 3600}}}


ACCOUNT = {"account": {"type": "chatgpt"}, "workspaceRouting": {"chatgptAccountId": "example"}}
ASSIGNMENT = {"model": "example-model", "effort": "low"}


class FakeClient:
    def __init__(self, limits=None):
        self.methods = []
        self.pending = deque()
        self.results = {"initialize": {}, "account/read": ACCOUNT,
                        "account/rateLimits/read": quota() if limits is None else limits,
                        "config/read": {"config": {"mcp_servers": {"example": {"enabled": True}}}},
                        "thread/start": {"thread": {"id": "t"}, "modelProvider": "openai",
                                         "sandbox": {"type": "readOnly"}, "model": "example-model",
                                         "reasoningEffort": "low"},
                        "turn/start": {"turn": {"id": "u"}}}
        self.events = deque()

    async def request(self, method, params):
        self.methods.append(method)
        if method == "thread/start":
            assert params["config"]["mcp_servers"] == {"example": {"enabled": False}}
            assert params["allowProviderModelFallback"] is False
        return self.results[method]

    async def send(self, message):
        pass

    async def read(self):
        return self.events.popleft()

    def event(self, name, **params):
        self.events.append({"method": name, "params": {"threadId": "t", "turnId": "u", **params}})

    def run(self):
        return asyncio.run(stage(self, "implementer", "A short specification", ".", ASSIGNMENT))


class CodexStageTests(unittest.TestCase):
    def test_signed_out_client_receives_login_instructions_before_quota_lookup(self):
        client = FakeClient()
        client.results["account/read"] = {"account": None}
        with self.assertRaisesRegex(ValueError, "codex login"):
            client.run()
        self.assertEqual(client.methods, ["initialize", "account/read"])

    def test_subscription_guard(self):
        self.assertTrue(subscription_ready(ACCOUNT, quota()))
        for field, value in (("ordinaryUsageAllowed", False), ("accountId", "different"),
                             ("rateLimitsByLimitId", []), ("rateLimitsByLimitId", {"other": {}})):
            with self.subTest(field=field):
                client = FakeClient({**quota(), field: value})
                with self.assertRaises(ValueError):
                    client.run()
                self.assertNotIn("thread/start", client.methods)
        for field, value in (("credits", {"hasCredits": True}), ("spendControlReached", True),
                             ("rateLimitReachedType", "limit"), ("primary", None),
                             ("primary", {"usedPercent": 100, "resetsAt": time.time() + 60}),
                             ("primary", {"usedPercent": 12, "resetsAt": 0})):
            limits = quota()
            limits["rateLimits"][field] = value
            self.assertFalse(subscription_ready(ACCOUNT, limits))
        self.assertFalse(subscription_ready({"account": {"type": "apiKey"}}, quota()))

    def test_model_mismatch_stops_before_turn(self):
        client = FakeClient()
        client.results["thread/start"]["model"] = "other"
        with self.assertRaises(ValueError):
            client.run()
        self.assertNotIn("turn/start", client.methods)

    def test_reject_tool_and_reroute(self):
        for name, params in (("model/rerouted", {}), ("item/started", {"item": {"type": "commandExecution"}})):
            client = FakeClient()
            client.event(name, **params)
            with self.assertRaises(ValueError):
                client.run()

    def test_question_and_usage_return_to_controller(self):
        client = FakeClient()
        client.event("thread/tokenUsage/updated", tokenUsage={"total": {
            "inputTokens": 20, "cachedInputTokens": 6, "outputTokens": 3, "cacheWriteInputTokens": 2}})
        client.event("item/completed", item={"type": "agentMessage", "text": json.dumps({
            "status": "needs_input", "text": "Which orders?"})})
        client.event("turn/completed", turn={"id": "u", "status": "completed"})
        report = client.run()
        self.assertEqual(report["status"], "needs_input")
        self.assertEqual(report["result"], "Which orders?")
        self.assertEqual(report["usage"]["inputTokens"], 20)
        self.assertEqual(report["usage"]["cacheWriteInputTokens"], 2)
        self.assertEqual(client.methods.count("turn/start"), 1)
