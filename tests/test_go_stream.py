import io
import json
import unittest

from roleshuttle.go_stage import parse_stream


def event(**values):
    return b"data: "+json.dumps(values).encode()+b"\n\n"


class GoStreamTests(unittest.TestCase):
    def test_complete_stream_requires_terminal_and_preserves_usage(self):
        signal=[]
        data=(event(id="go-123",model="fixture",choices=[{"delta":{"role":"assistant"}}])
              +event(id="go-123",model="fixture",choices=[{"delta":{"content":"part"}}])
              +event(id="go-123",model="fixture",choices=[{"delta":{"content":" two"}}])
              +event(id="go-123",model="fixture",choices=[{"finish_reason":"stop","delta":{}}])
              +event(id="go-123",model="fixture",choices=[],usage={"cost":0})
              +b"data: [DONE]\n\n")
        out=parse_stream(io.BytesIO(data),"fixture",signal.append)
        self.assertEqual(signal,["go-123"])
        self.assertEqual(out["choices"][0]["message"]["content"],"part two")
        self.assertEqual(out["usage"],{"cost":0})

    def test_incomplete_stream_is_never_a_result(self):
        data=event(id="go-123",model="fixture",choices=[{"delta":{"content":"partial"}}])
        with self.assertRaisesRegex(ValueError,"terminal"):
            parse_stream(io.BytesIO(data),"fixture",lambda _:None)

    def test_model_mismatch_stops_before_active_confirmation(self):
        signal=[]
        data=event(id="go-123",model="other",choices=[{"delta":{"content":"invalid"}}])
        with self.assertRaises(ValueError):
            parse_stream(io.BytesIO(data),"fixture",signal.append)
        self.assertEqual(signal,[])

    def test_cancel_closes_only_active_socket_without_provider_ack(self):
        from threading import Event
        from unittest.mock import patch
        from roleshuttle.go_stage import GoClientDisconnected, GoStage
        stopped=Event()
        active=Event()
        frame=event(id="go-456",model="fixture",choices=[{"delta":{"content":"first"}}])
        class Socket:
            def shutdown(self,_):
                stopped.set()
        class Response(io.BytesIO):
            status=200
            def getheader(self,*args):
                return "text/event-stream"
            def readline(self,*args):
                line=super().readline(*args)
                if not line:
                    if not stopped.wait(5):
                        raise ValueError("No local cancellation")
                return line
        class Connection:
            sock=Socket()
            def request(self,*args,**kwargs):
                pass
            def getresponse(self):
                return Response(frame)
            def close(self):
                pass
        stage=GoStage("fixture-key")
        with patch("roleshuttle.go_stage.HTTPSConnection",return_value=Connection()):
            with self.assertRaises(GoClientDisconnected) as raised:
                stage.stream_request({},"s","fixture",lambda _:active.set(),active.is_set)
        self.assertEqual(raised.exception.response_id,"go-456")
        self.assertTrue(stopped.is_set())

    def test_normal_terminal_with_pending_cancel_preserves_result(self):
        from unittest.mock import patch
        from roleshuttle.go_stage import GoStage
        data=(event(id="go-789",model="fixture",choices=[{"delta":{"content":"full"}}])
              +event(id="go-789",model="fixture",choices=[{"delta":{},"finish_reason":"stop"}])
              +event(id="go-789",model="fixture",choices=[],usage={"cost":0})
              +b"data: [DONE]\n\n")
        class Response(io.BytesIO):
            status=200
            def getheader(self,*args):
                return "text/event-stream"
            def readline(self,*args):
                line=super().readline(*args)
                if b'"usage"' in line:
                    import time
                    time.sleep(.3)
                return line
        from unittest.mock import Mock
        connection=Mock()
        connection.getresponse.return_value=Response(data)
        stage=GoStage("fixture-key")
        with patch("roleshuttle.go_stage.HTTPSConnection",return_value=connection):
            response=stage.stream_request({},"s","fixture",lambda _:None,lambda: True)
        self.assertEqual(response["choices"][0]["message"]["content"],"full")
        connection.sock.shutdown.assert_not_called()
