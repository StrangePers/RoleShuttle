"""Dispatch one bounded text stage to an operator-selected loopback Ollama server."""
from contextlib import closing
from http.client import HTTPConnection, HTTPException
import json
from pathlib import Path
import re


ROLE_PROMPTS = {
    "research": "Find relevant facts in the supplied material. Return a concise summary and unresolved questions.",
    "implement": "Propose a scoped change based only on the supplied material. Describe how to verify it.",
    "specialist": "Resolve the supplied design or implementation decision. Cite the deciding evidence. Give a concise recommendation and a check.",
    "review": "Independently review the supplied change. Give findings, evidence and unresolved questions.",
}
MAX_INPUT = 64 * 1024
MAX_RESPONSE = 2 * 1024 * 1024
MAX_OUTPUT_TOKENS = 2048


def request(port, path, payload):
    try:
        with closing(HTTPConnection("127.0.0.1", port, timeout=120)) as connection:
            connection.request("POST", path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"Ollama returned HTTP {response.status} at {path}")
            data = response.read(MAX_RESPONSE + 1)
    except HTTPException as exc:
        raise ValueError("Invalid Ollama HTTP response") from exc
    if len(data) > MAX_RESPONSE:
        raise ValueError("Ollama response exceeds the local stage limit")
    result = json.loads(data)
    if not isinstance(result, dict) or "error" in result:
        raise ValueError("Ollama did not return a successful object")
    return result


def read_profile(profile_path):
    profile = json.loads(Path(profile_path).read_text(encoding="utf-8-sig"))
    if not isinstance(profile, dict) or set(profile) - {"port", "roles"}:
        raise ValueError("Expected a local role profile")
    port = profile.get("port", 11434)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Invalid loopback port")
    roles = profile.get("roles")
    return port, roles


def model_for(roles, role):
    model = roles.get(role) if isinstance(roles, dict) else None
    if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+:[a-zA-Z0-9_.-]+", model):
        raise ValueError("Configure a tagged, locally installed model for this role")
    if model.lower().endswith((":cloud", "-cloud")):
        raise ValueError("Remote models are outside this adapter")
    return model


def _call(port, model, role, prompt, max_output_tokens):
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
        raise ValueError(f"Output token limit must be between 1 and {MAX_OUTPUT_TOKENS}")
    if not prompt.strip() or len(prompt.encode("utf-8")) > MAX_INPUT:
        raise ValueError("Supply a non-empty task and excerpts within 64 KiB")
    metadata = request(port, "/api/show", {"model": model})
    if metadata.get("remote_model") or metadata.get("remote_host") or not metadata.get("model_info"):
        raise ValueError("Expected metadata for a locally installed model")
    result = request(port, "/api/chat", {
        "model": model, "stream": False, "options": {"num_predict": max_output_tokens},
        "messages": [{"role": "system", "content": ROLE_PROMPTS[role]},
                     {"role": "user", "content": prompt}],
    })
    message = result.get("message")
    if (result.get("done") is not True or result.get("model") != model
            or not isinstance(message, dict) or message.get("role") != "assistant"
            or not isinstance(message.get("content"), str)
            or (not message["content"].strip() and result.get("done_reason") != "length")):
        raise ValueError("Incomplete or mismatched model response")
    usage = {}
    for key in ("prompt_eval_count", "eval_count", "total_duration", "load_duration",
                "prompt_eval_duration", "eval_duration"):
        value = result.get(key)
        usage[key] = value if type(value) is int and value >= 0 else None
    return {"role": role, "model": model, "source": "ollama", "result": message["content"],
            "done_reason": result.get("done_reason"), "usage": usage}


def delegate(profile_path, role, prompt, *, max_output_tokens=MAX_OUTPUT_TOKENS):
    port, roles = read_profile(profile_path)
    return _call(port, model_for(roles, role), role, prompt, max_output_tokens)


def consult(profile_path, prompt, *, max_output_tokens=MAX_OUTPUT_TOKENS):
    port, roles = read_profile(profile_path)
    specialist = model_for(roles, "specialist")
    reviewer = model_for(roles, "review")
    draft = _call(port, specialist, "specialist", prompt, max_output_tokens)
    calls = [draft]
    if draft["done_reason"] != "stop":
        return {"status": "specialist_incomplete", "calls": calls}
    review_input = json.dumps({"task": prompt, "proposal_to_review": draft["result"]}, ensure_ascii=False)
    try:
        review = _call(port, reviewer, "review", review_input, max_output_tokens)
    except (ValueError, OSError, RuntimeError) as exc:
        return {"status": "review_failed", "calls": calls, "error": str(exc)}
    calls.append(review)
    status = "returned" if review["done_reason"] == "stop" else "review_incomplete"
    return {"status": status, "calls": calls}
