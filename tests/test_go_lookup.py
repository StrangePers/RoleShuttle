import io
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from roleshuttle.go_stage import COMPLETIONS, STATUS, GoStage


class GoLookupTests(unittest.TestCase):
    def setUp(self):
        self.stage = GoStage("fixture-key")
        self.stage.opener = Mock()

    def test_refuses_lookup_when_extra_usage_is_on(self):
        self.stage.opener.open.return_value = io.BytesIO(b'{"useBalance":true}')
        self.assertEqual(self.stage.lookup_completion("fixture-id", "fixture"),
                         {"outcome": "extra_usage_unverified"})
        self.assertEqual(self.stage.opener.open.call_count, 1)

    def test_denied_lookup_uses_only_get_and_preserves_http_status(self):
        self.stage.opener.open.side_effect = [
            io.BytesIO(b'{"useBalance":false}'),
            HTTPError(COMPLETIONS, 403, "Forbidden", {}, None),
        ]
        self.assertEqual(self.stage.lookup_completion("fixture/id", "fixture"),
                         {"outcome": "http_error", "http_status": 403})
        requests = [call.args[0] for call in self.stage.opener.open.call_args_list]
        self.assertEqual([request.full_url for request in requests],
                         [STATUS, COMPLETIONS + "/fixture%2Fid"])
        self.assertTrue(all(request.get_method() == "GET" and request.data is None
                            for request in requests))

    def test_only_matching_terminal_completion_is_evidence(self):
        for returned_id, reason, expected in [
            ("fixture-id", "stop", {"outcome": "terminal_completion", "finish_reason": "stop"}),
            ("different-id", "stop", {"outcome": "no_terminal_evidence"}),
            ("fixture-id", None, {"outcome": "no_terminal_evidence"}),
        ]:
            with self.subTest(returned_id=returned_id, reason=reason):
                result = dict(object="chat.completion", id=returned_id, model="fixture",
                              choices=[{"finish_reason": reason, "message":{"content":"discard"}}])
                self.stage.opener.open.side_effect = [
                    io.BytesIO(b'{"useBalance":false}'), io.BytesIO(json.dumps(result).encode()),
                ]
                self.assertEqual(self.stage.lookup_completion("fixture-id", "fixture"), expected)
