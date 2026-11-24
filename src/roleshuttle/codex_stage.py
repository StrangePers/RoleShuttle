"""Run one bounded subscription stage through the native Codex stdio client."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager, nullcontext
from importlib.resources import files
import json
import math
import os
from pathlib import Path
import shutil
import signal
import tempfile
import time

from .codex_cancel import cancellation_evidence, interruption_confirmed, open_responses
from .local import MAX_INPUT

ROLE_TASKS = {
    "assistant": "Find the requested facts in the supplied material.",
    "coordinator": "Propose bounded stages and acceptance checks.",
    "implementer": "Return a scoped implementation proposal that meets the task.",
    "specialist": "Resolve the technical question and give a verification step.",
    "architect": "Compare design choices and state how to verify the decision.",
    "reviewer": "Independently check the supplied proposal and report concrete errors or acceptance evidence.",
}
OUTPUT = {
    "type": "object", "additionalProperties": False,
    "properties": {"status": {"type": "string", "enum": ["returned", "needs_input"]},
                   "text": {"type": "string"}},
    "required": ["status", "text"],
}


def executable():
    name = "codex.exe" if os.name == "nt" else "codex"
    found = os.environ.get("ROLESHUTTLE_CODEX_BIN") or shutil.which(name)
    if not found or not Path(found).is_absolute() or not Path(found).is_file():
        raise ValueError("Install Codex CLI or set ROLESHUTTLE_CODEX_BIN to its absolute executable path")
    if os.name == "nt" and Path(found).suffix.lower() != ".exe":
        raise ValueError("Select the native Codex .exe")
    return found


def subscription_ready(account, limits):
    ident = (account.get("account") or {}).get("type")
    account_id = (account.get("workspaceRouting") or {}).get("chatgptAccountId")
    if ident != "chatgpt" or (limits.get("accountId") and limits["accountId"] != account_id):
        return False
    extra = limits.get("rateLimitsByLimitId")
    if extra is None:
        extra = {}
    if limits.get("ordinaryUsageAllowed") is not True or not isinstance(extra, dict):
        return False
    buckets = [limits.get("rateLimits"), *extra.values()]
    if not any(isinstance(bucket, dict) and bucket.get("limitId") in (None, "codex") for bucket in buckets):
        return False
    for bucket in buckets:
        if not isinstance(bucket, dict) or bucket.get("spendControlReached") is not False or bucket.get("rateLimitReachedType") is not None:
            return False
        credits = bucket.get("credits") or {}
        if not isinstance(credits, dict) or credits.get("hasCredits") is not False or credits.get("unlimited") is not False:
            return False
        if credits.get("balance") is not None:
            try:
                if isinstance(credits["balance"], bool) or float(credits["balance"]) != 0:
                    return False
            except (TypeError, ValueError):
                return False
        windows = [(field, bucket[field]) for field in ("primary", "secondary", "individualLimit") if bucket.get(field) is not None]
        if not windows:
            return False
        for field, window in windows:
            if not isinstance(window, dict):
                return False
            remaining = field == "individualLimit"
            percent = window.get("remainingPercent" if remaining else "usedPercent")
            reset = window.get("resetsAt")
            if type(reset) not in (float, int) or not math.isfinite(reset) or reset <= time.time():
                return False
            if type(percent) not in (int, float) or not 0 <= percent <= 100 or (percent == 0 if remaining else percent == 100):
                return False
    return True


class Client:
    def __init__(self, process):
        self.process = process
        self.sequence = 0
        self.pending = deque()

    async def send(self, message):
        self.process.stdin.write(json.dumps(message).encode() + b"\n")
        await self.process.stdin.drain()

    async def read(self):
        line = await self.process.stdout.readline()
        if not line:
            raise ValueError("Native Codex disconnected before the stage completed")
        event = json.loads(line)
        if not isinstance(event, dict) or ("method" in event and "id" in event):
            raise ValueError("Native Codex requested an interactive action or returned an invalid event")
        return event

    async def begin_request(self, method, params):
        self.sequence += 1
        await self.send({"id": self.sequence, "method": method, "params": params})
        return self.sequence

    async def request(self, method, params):
        request_id = await self.begin_request(method, params)
        while True:
            msg = await self.read()
            if msg.get("id") == request_id:
                if not isinstance(msg.get("result"), dict):
                    raise ValueError(f"Native Codex rejected {method}")
                return msg["result"]
            if method == "turn/start":
                if len(self.pending) >= 256:
                    raise ValueError("Too many notifications before turn acknowledgement")
                self.pending.append(msg)


async def subscription_limits(client):
    await client.request("initialize", {"clientInfo": {"name": "roleshuttle", "version": "1"},
                                        "capabilities": {"experimentalApi": True}})
    await client.send({"method": "initialized"})
    account = await client.request("account/read", {"refreshToken": False})
    if (account.get("account") or {}).get("type") != "chatgpt":
        raise ValueError("Sign in to ChatGPT with 'codex login' (headless: 'codex login --device-auth'), "
                         "then run 'codex login status' in the same account as your coding agent")
    limits = await client.request("account/rateLimits/read", {})
    if not subscription_ready(account, limits):
        raise ValueError("Codex must report ChatGPT sign-in, available quota and no extra credits")
    return limits


async def stage(client, role, prompt, directory, assignment, *, cancellation=None, evidence=None):
    await subscription_limits(client)
    model, effort = assignment["model"], assignment["effort"]
    # Disable configured MCP servers for this worker only. Keep native authentication and policy.
    config = (await client.request("config/read", {"cwd": directory})).get("config")
    if not isinstance(config, dict):
        raise ValueError("Native Codex did not return its configuration")
    servers = config.get("mcp_servers") or {}
    if not isinstance(servers, dict):
        raise ValueError("Invalid native MCP configuration")
    overrides = {"model_reasoning_effort": effort, "web_search": "disabled",
                 "mcp_servers": {name: {"enabled": False} for name in servers}}
    start = await client.request("thread/start", {
        "model": model, "modelProvider": "openai", "allowProviderModelFallback": False,
        "approvalPolicy": "never", "sandbox": "read-only", "ephemeral": True, "cwd": directory,
        "environments": [], "selectedCapabilityRoots": [], "config": overrides,
        "developerInstructions": ROLE_TASKS[role] + " Use only the supplied text. Do not call tools. "
        "Return needs_input with one question if a requirement is missing. Return the result to the main agent.",
    })
    if (start.get("model") != model or start.get("modelProvider") != "openai"
            or start.get("reasoningEffort") != effort or (start.get("sandbox") or {}).get("type") != "readOnly"):
        raise ValueError("Native Codex did not confirm the requested model, effort and read-only sandbox")
    thread = start["thread"]["id"]
    turn = (await client.request("turn/start", {"threadId": thread, "input": [{"type": "text", "text": prompt}],
                                                "environments": [], "outputSchema": OUTPUT}))["turn"]["id"]
    return await collect_turn(client, role, model, effort, thread, turn, cancellation, evidence)


async def collect_turn(client, role, model, effort, thread, turn, cancellation, evidence):
    usage, final = None, None
    interrupt_id, before, terminal, acknowledged = None, None, None, False
    reader = None
    try:
        while True:
            if terminal == "interrupted" and acknowledged:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    after = evidence()
                    if interruption_confirmed(before, after):
                        return {"role": role, "model": model, "effort": effort, "usage": usage, "status": "cancelled",
                                "interruption": {"thread_id": thread, "turn_id": turn, "native_terminal": terminal,
                                                 "native_acknowledged": True, "before": dict(before), "after": dict(after)}}
                    await asyncio.sleep(.05)
                raise ValueError("Cancelled turn has no matching provider interruption confirmation")
            if cancellation and interrupt_id is None:
                current = evidence()
                active = current["response.completed"] >= 1 and open_responses(current) == 1
                if cancellation(thread, turn, active) and active:
                    before = current
                    interrupt_id = await client.begin_request("turn/interrupt", {"threadId": thread, "turnId": turn})
            if client.pending:
                msg = client.pending.popleft()
            elif cancellation:
                if reader is None:
                    reader = asyncio.create_task(client.read())
                ready, _ = await asyncio.wait({reader}, timeout=.05)
                if not ready:
                    continue
                msg = reader.result()
                reader = None
            else:
                msg = await client.read()
            if interrupt_id is not None and msg.get("id") == interrupt_id:
                if not isinstance(msg.get("result"), dict):
                    raise ValueError("Native Codex rejected interruption")
                acknowledged = True
                continue
            method, params = msg.get("method"), msg.get("params") or {}
            if params.get("threadId") != thread or params.get("turnId", turn) != turn:
                continue
            if method == "model/rerouted":
                raise ValueError("Codex rerouted the model; stage result rejected")
            if method in ("item/started", "item/completed"):
                item = params["item"]
                if item["type"] not in ("userMessage", "agentMessage", "reasoning"):
                    raise ValueError("Unexpected tool or action in a text stage")
                if method == "item/completed" and item["type"] == "agentMessage" and item.get("phase") != "commentary":
                    final = item.get("text")
            if method == "thread/tokenUsage/updated":
                counters = params["tokenUsage"]["total"]
                usage = {key: counters.get(key) for key in ("inputTokens", "cachedInputTokens", "outputTokens")}
                usage["cacheWriteInputTokens"] = counters.get("cacheWriteInputTokens", 0)
            if method == "turn/completed" and params["turn"]["id"] == turn:
                terminal = params["turn"]["status"]
                if terminal != "completed":
                    if terminal != "interrupted" or before is None:
                        raise ValueError("Codex did not complete the requested stage")
                    continue
                if not final:
                    raise ValueError("Codex did not complete the requested stage")
                result = json.loads(final)
                if (not isinstance(result, dict) or result.get("status") not in ("returned", "needs_input")
                        or not isinstance(result.get("text"), str) or not result["text"].strip()):
                    raise ValueError("Codex returned an invalid stage result")
                return {"role": role, "model": model, "effort": effort, "usage": usage,
                        "status": result["status"], "result": result["text"]}

    finally:
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


async def codex_stage(role, prompt, *, model=None, effort=None, cancellation=None):
    if not prompt.strip() or len(prompt.encode("utf-8")) > MAX_INPUT:
        raise ValueError("Supply a non-empty stage within 64 KiB")
    presets = json.loads(files("roleshuttle").joinpath("presets.json").read_text(encoding="utf-8"))
    assignment = dict(presets["codex"]["roles"][role])
    if model is not None:
        if not model.strip() or len(model) > 160:
            raise ValueError("Specify a valid native model ID")
        assignment["model"] = model
    if effort is not None:
        assignment["effort"] = effort
    try:
        async with native_client(cancellation) as (client, directory, evidence), asyncio.timeout(180):
            return await stage(client, role, prompt, directory, assignment,
                               cancellation=cancellation, evidence=evidence)
    except TimeoutError:
        raise ValueError("Codex stage timed out; no retry was requested") from None
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError):
        raise ValueError("Unsupported native Codex response") from None


@asynccontextmanager
async def native_client(cancellation=None):
    collector = cancellation_evidence() if cancellation else nullcontext((None, [], None))
    with tempfile.TemporaryDirectory(prefix="roleshuttle-") as directory, collector as (evidence, flags, env):
        process = await asyncio.create_subprocess_exec(
            executable(), *flags, "app-server", "--listen", "stdio://", "--disable", "apps", "--disable", "plugins",
            "--disable", "multi_agent", "--disable", "shell_tool", cwd=directory, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            start_new_session=os.name != "nt", limit=4 * 1024 * 1024)
        job = None
        try:
            if os.name == "nt":
                from .windows_job import WindowsJob
                job = WindowsJob(process.pid)
            yield Client(process), directory, evidence
        finally:
            if job is not None:
                job.close()
            elif os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            elif process.returncode is None:
                process.kill()
            await process.communicate()
