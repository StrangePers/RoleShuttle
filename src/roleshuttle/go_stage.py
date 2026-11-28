"""Bounded OpenCode Go patch requests through one explicitly configured credential."""
from http.client import HTTPSConnection, HTTPException
import json
import os
import re
import socket
from threading import Event, Thread
import time
from urllib.parse import quote, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

KEY_ENV = "ROLESHUTTLE_OPENCODE_GO_KEY"
USAGE = "https://opencode.ai/inference/go/v1/usage"
STATUS = "https://opencode.ai/console/api/go/status"
COMPLETIONS = "https://opencode.ai/inference/go/openai/v1/chat/completions"
MAX_RESPONSE = 4 * 1024 * 1024
DEFAULT_TASK_TIMEOUT = 150
_key = os.environ.pop(KEY_ENV, None)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def configure_key(key):
    global _key
    GoStage(key)
    _key = key


def configured():
    return bool(_key)


def configured_stage():
    return GoStage(_key)


class GoStreamLimitError(ValueError):
    messages = {
        "size_limit": "OpenCode Go stream exceeded the 4 MiB response size limit",
        "timeout": "OpenCode Go stream timed out",
    }

    def __init__(self, reason):
        super().__init__(self.messages[reason])
        self.reason = reason


class GoClientDisconnected(ValueError):
    def __init__(self, response_id):
        super().__init__("Go connection closed locally; provider cancellation is unconfirmed")
        self.response_id = response_id


def stream_events(response, deadline):
    remaining = MAX_RESPONSE
    data = []
    while True:
        if time.monotonic() >= deadline:
            raise GoStreamLimitError("timeout")
        line = response.readline(remaining + 1)
        if time.monotonic() >= deadline:
            raise GoStreamLimitError("timeout")
        if not line:
            return
        remaining -= len(line)
        if remaining < 0:
            raise GoStreamLimitError("size_limit")
        line = line.rstrip(b"\r\n")
        if line.startswith(b"data:"):
            data.append(line[5:].lstrip())
        elif not line and data:
            yield b"\n".join(data)
            data.clear()


def parse_stream(response, model, on_active, *, deadline=None, on_terminal=None):
    deadline = time.monotonic() + DEFAULT_TASK_TIMEOUT if deadline is None else deadline
    response_id, usage, finish = None, None, None
    active = False
    text = []
    for data in stream_events(response,deadline):
        if data == b"[DONE]":
            if finish and usage is not None:
                return {"model":model,"usage":usage,"choices":[
                    {"finish_reason":finish,"message":{"content":"".join(text)}}]}
            break
        frame = json.loads(data)
        if not isinstance(frame,dict) or frame.get("error") or frame.get("model") not in {None,model}:
            raise ValueError("Unexpected OpenCode Go stream event")
        if frame.get("id"):
            seen = frame["id"]
            if not isinstance(seen,str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,200}",seen) or (
                    response_id is not None and response_id!=seen):
                raise ValueError("Unexpected OpenCode Go response identifier")
            response_id=seen
        if frame.get("usage") is not None:
            usage=frame["usage"]
        choices=frame.get("choices",[])
        if not isinstance(choices,list) or len(choices)>1:
            raise ValueError("Unexpected OpenCode Go choice")
        for choice in choices:
            if not isinstance(choice,dict) or choice.get("index",0)!=0:
                raise ValueError("Unexpected OpenCode Go choice")
            delta=choice.get("delta") or {}
            if not isinstance(delta,dict) or delta.get("tool_calls") or delta.get("function_call"):
                raise ValueError("Unexpected tool call in OpenCode Go response")
            part=delta.get("content")
            if part is not None and not isinstance(part,str):
                raise ValueError("Unexpected OpenCode Go text")
            if part:
                text.append(part)
            if choice.get("finish_reason"):
                finish=choice["finish_reason"]
                if on_terminal:
                    on_terminal()
            if not active and response_id and (part or delta.get("reasoning_content") or delta.get("reasoning")):
                on_active(response_id)
                active=True
    raise ValueError("OpenCode Go stream ended without terminal result and usage")




class GoStage:
    def __init__(self, key):
        if not isinstance(key, str) or not re.fullmatch(r"[!-~]+", key):
            raise ValueError("Configure an OpenCode Go credential for RoleShuttle")
        self.key = key
        self.authorized_at = None
        self.health_timeout = 10
        self.task_timeout = DEFAULT_TASK_TIMEOUT
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, endpoint, body=None, session=None):
        headers = {"Authorization": "Bearer " + self.key, "Accept": "application/json",
                   "User-Agent": "RoleShuttle/1.0"}
        data = None
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-OpenCode-Session": session})
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            with self.opener.open(Request(endpoint, data=data, headers=headers),
                                  timeout=self.task_timeout if data else self.health_timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
            if len(raw) <= MAX_RESPONSE:
                value = json.loads(raw)
                if isinstance(value, dict):
                    return value
        except (URLError, OSError, UnicodeError, ValueError):
            pass
        raise ValueError("OpenCode Go returned no verifiable response")

    def lookup_completion(self, response_id, model):
        # Go documents inference but not stored Chat Completion retrieval.
        # Probe the compatible read endpoint without starting a generation.
        if (not re.fullmatch(r"[A-Za-z0-9._:/-]{1,200}", response_id)
                or response_id in {".", ".."}):
            return {"outcome": "invalid_response_id"}
        try:
            if self.request(STATUS).get("useBalance") is not False:
                return {"outcome": "extra_usage_unverified"}
        except ValueError:
            return {"outcome": "extra_usage_unverified"}

        endpoint = COMPLETIONS + "/" + quote(response_id, safe="")
        request = Request(endpoint, method="GET", headers={
            "Authorization": "Bearer " + self.key, "Accept": "application/json",
            "User-Agent": "RoleShuttle/1.0",
        })
        try:
            with self.opener.open(request, timeout=self.health_timeout) as response:
                raw = response.read(MAX_RESPONSE + 1)
                value = json.loads(raw) if len(raw) <= MAX_RESPONSE else None
        except HTTPError as error:
            error.close()
            return {"outcome": "http_error", "http_status": error.code}
        except (URLError, OSError, HTTPException, UnicodeError, ValueError):
            return {"outcome": "unavailable"}

        if (isinstance(value, dict) and value.get("object") == "chat.completion"
                and value.get("id") == response_id and value.get("model") == model):
            choices = value.get("choices")
            if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict):
                reason = choices[0].get("finish_reason")
                if reason in ("stop", "length", "content_filter", "tool_calls", "function_call"):
                    return {"outcome": "terminal_completion", "finish_reason": reason}
        return {"outcome": "no_terminal_evidence"}

    def check_model(self, model):
        catalog = self.request("https://opencode.ai/inference/go/v1/models").get("data", [])
        if not any(isinstance(row,dict) and row.get("id")==model for row in catalog):
            raise ValueError("OpenCode Go model was not found in the live catalog")

    def authorize(self):
        self.authorized_at = None
        usage = self.request(USAGE).get("usage")
        if not isinstance(usage, dict):
            raise ValueError("OpenCode Go quota could not be verified")
        for name in ("rolling", "weekly", "monthly"):
            meter = usage.get(name)
            if (not isinstance(meter, dict) or meter.get("status") != "ok"
                    or type(meter.get("percent")) is not int or not 0 <= meter["percent"] < 100):
                raise ValueError("OpenCode Go quota is unavailable or exhausted")
        if self.request(STATUS).get("useBalance") is not False:
            raise ValueError("OpenCode Go Extra Usage must be confirmed off for the executor credential")

        self.authorized_at = time.monotonic()

    def stream_request(self, body, session, model, on_active, cancel_requested, *, on_request=None):
        target=urlsplit(COMPLETIONS)
        connection=HTTPSConnection(target.hostname,timeout=self.task_timeout)
        active, done, disconnected, terminal=Event(),Event(),Event(),Event()
        response_id=None
        result=None
        limit_error = None
        timed_out = Event()
        watcher = None

        def publish(identity):
            nonlocal response_id
            on_active(identity)
            response_id=identity
            active.set()

        def watch(sock):
            while not done.wait(.15):
                expired = time.monotonic() >= deadline
                cancelled = not expired and not terminal.is_set() and active.is_set() and cancel_requested()
                if not expired and not cancelled:
                    continue
                if done.is_set():
                    return
                if expired:
                    timed_out.set()
                else:
                    if terminal.is_set():
                        continue
                    disconnected.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    if not expired:
                        disconnected.clear()
                return

        try:
            headers={"Authorization":"Bearer "+self.key,"Accept":"text/event-stream",
                     "Accept-Encoding":"identity","Content-Type":"application/json",
                     "X-OpenCode-Session":session,"User-Agent":"RoleShuttle/1.0"}
            deadline=time.monotonic()+self.task_timeout
            connection.request("POST",target.path,body=json.dumps(body,ensure_ascii=False).encode("utf-8"),
                               headers=headers)
            sock=connection.sock
            if sock is None:
                raise ValueError("OpenCode Go connection was unavailable")
            watcher=Thread(target=watch,args=(sock,),daemon=True)
            watcher.start()
            with connection.getresponse() as response:
                log_id = response.getheader("x-opencode-log-id", "")
                if on_request and re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", log_id, re.IGNORECASE
                ):
                    on_request(log_id.lower())
                if response.status!=200 or response.getheader("Content-Type","").split(";",1)[0]!="text/event-stream":
                    raise ValueError("OpenCode Go did not return a successful event stream")
                try:
                    result=parse_stream(response,model,publish,deadline=deadline,on_terminal=terminal.set)
                except GoStreamLimitError as error:
                    limit_error = error
                except TimeoutError:
                    limit_error = GoStreamLimitError("timeout")
                except (OSError, HTTPException, ValueError):
                    pass
        except TimeoutError:
            raise GoStreamLimitError("timeout") from None
        except (OSError, HTTPException):
            if timed_out.is_set():
                raise GoStreamLimitError("timeout") from None
            raise ValueError("OpenCode Go stream could not be verified") from None
        finally:
            done.set()
            if watcher is not None:
                watcher.join(timeout=5)
            connection.close()
        if result is not None:
            return result
        if disconnected.is_set():
            raise GoClientDisconnected(response_id)
        if limit_error is not None:
            raise limit_error
        if timed_out.is_set():
            raise GoStreamLimitError("timeout")
        raise ValueError("OpenCode Go stream ended without a verified result")



    def complete(self, task_id, model, prompt, *, on_active=None, cancel_requested=None, on_request=None):
        authorized = self.authorized_at
        self.authorized_at = None
        if authorized is None or time.monotonic() - authorized > 15:
            raise ValueError("OpenCode Go needs a fresh subscription check")
        body={"model":model,"messages":[{"role":"user","content":prompt}],
              "max_tokens":8192,"stream":on_active is not None}
        if on_active is None:
            value=self.request(COMPLETIONS,body,task_id)
        else:
            body["stream_options"]={"include_usage":True}
            value = self.stream_request(body, task_id, model, on_active, cancel_requested, on_request=on_request)
        try:
            usage = value["usage"]
            billing = usage["opencode"]["billing_source"]
            cost = usage["cost"]
            if billing not in {"go", "go-plus", "go-teams"} or type(cost) not in {int, float} or cost != 0:
                raise ValueError("OpenCode Go billing was not confirmed as subscription-only")
            choice, = value["choices"]
            if choice.get("finish_reason") == "length":
                raise ValueError("OpenCode Go reached its response token limit")
            text = choice["message"]["content"]
            if (value["model"] != model or choice.get("finish_reason") != "stop"
                    or not isinstance(text, str) or choice["message"].get("tool_calls")):
                raise ValueError("OpenCode Go returned an unexpected model or response")
        except (KeyError, TypeError):
            raise ValueError("OpenCode Go response is missing model or billing evidence") from None
        status = "returned"
        if text.startswith("QUESTION:"):
            status, text = "needs_input", text.removeprefix("QUESTION:").strip()
        evidence = {"billing_source": billing, "balance_cost": cost}
        for name in ("prompt_tokens", "completion_tokens"):
            if type(usage.get(name)) is int and usage[name] >= 0:
                evidence[name] = usage[name]
        return {"model": model, "effort": None, "status": status, "result": text, "usage": evidence}
