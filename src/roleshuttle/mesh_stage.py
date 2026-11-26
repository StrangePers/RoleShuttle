"""Use Mesh's registry and load-admission policy to execute bounded local text requests."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import Request

MAX_RESPONSE = 512 * 1024
MAX_OUTPUT = 8192


def configured_server(route_model, servers):
    model = route_model.removeprefix("mac-omlx/")
    matches = [server for server in servers if model in server["models"]]
    if len(matches) != 1 or model == "auto":
        raise ValueError("Select exactly one saved Mesh server for this model")
    server = matches[0]
    return server["id"], server["base_url"].rstrip("/"), model


def mesh_python():
    executable = shutil.which("mesh")
    if not executable:
        raise ValueError("Mesh executable is unavailable")
    folder = Path(executable).resolve().parent
    name = "python.exe" if sys.platform == "win32" else "python"
    interpreter = folder / name
    if not (folder.parent / "pyvenv.cfg").is_file() or not interpreter.is_file():
        raise ValueError("Mesh Python environment is unavailable")
    return interpreter


class MeshStage:
    def __init__(self, route_model, settings):
        self.route_model = route_model
        self.server_id, self.base_url, self.model = configured_server(route_model, settings["local_servers"])
        self.interpreter = mesh_python()
        self.health_timeout = settings["timeouts"]["local_health_seconds"]
        self.task_timeout = settings["timeouts"]["task_default_seconds"]
        self.authorized_at = None

    def exchange(self, prompt=None):
        values = {"server":self.server_id,"base_url":self.base_url,"model":self.model,
                  "health_timeout":self.health_timeout,"task_timeout":self.task_timeout,"prompt":prompt}
        timeout = self.health_timeout + 10
        if prompt is not None:
            timeout += self.task_timeout
        try:
            result = subprocess.run([str(self.interpreter),"-I","-B",str(Path(__file__).resolve())],
                                    input=json.dumps(values,ensure_ascii=False).encode("utf-8"),
                                    capture_output=True,timeout=timeout,check=True)
            if len(result.stdout) > MAX_RESPONSE:
                raise ValueError("Mesh response exceeded the local limit")
            return json.loads(result.stdout)
        except (OSError, subprocess.SubprocessError, ValueError):
            raise ValueError("Mesh could not confirm the local model operation") from None

    def authorize(self):
        result = self.exchange()
        if result != {"admitted":True,"model":self.model}:
            raise ValueError("Mesh did not admit the exact local model")
        self.authorized_at = time.monotonic()

    def complete(self, task_id, model, prompt):
        authorized = self.authorized_at
        self.authorized_at = None
        if (authorized is None or time.monotonic() - authorized > 15 or model != self.route_model):
            raise ValueError("Mesh needs a fresh check for the same local model")
        result = self.exchange(prompt)
        text = result.get("result")
        if result.get("model") != self.model or not isinstance(text, str):
            raise ValueError("Unexpected local model response")
        status = "returned"
        if text.startswith("QUESTION:"):
            status, text = "needs_input", text.removeprefix("QUESTION:").strip()
        return {"model":model,"effort":None,"status":status,"result":text,
                "usage":result.get("usage"),"server_id":self.server_id}


def mesh_request(values):
    # Only this helper runs under the installed Mesh interpreter.
    from agent_mesh.models import _open, DEFAULT_REGISTRY, fetch_status, load_registry, select_model

    server = load_registry(DEFAULT_REGISTRY.expanduser())[values["server"]]
    if server.base_url != values["base_url"]:
        raise ValueError("Registered Mesh server differs from the saved address")
    choice = select_model(server,fetch_status(server,timeout=values["health_timeout"]),values["model"])
    if choice.model != values["model"]:
        raise ValueError("Mesh selected a different model")
    prompt = values["prompt"]
    if prompt is None:
        return {"admitted":True,"model":choice.model}
    # Reserve space for the output while treating the input byte size as an upper bound.
    limit=min(choice.output,MAX_OUTPUT)
    if len(prompt.encode("utf-8")) + limit + 256 > choice.context:
        raise ValueError("The approved prompt exceeds the admitted context")
    body=json.dumps({"model":choice.model,"messages":[{"role":"user","content":prompt}],
                     "max_tokens":limit,"temperature":0,"stream":False},ensure_ascii=False).encode("utf-8")
    headers={"Content-Type":"application/json","Accept":"application/json"}
    if choice.api_key:
        headers["Authorization"]="Bearer "+choice.api_key
    with _open(Request(choice.base_url+"/chat/completions",data=body,headers=headers),
               timeout=values["task_timeout"]) as response:
        raw=response.read(MAX_RESPONSE + 1)
    if len(raw)>MAX_RESPONSE:
        raise ValueError("Mesh response exceeded the local limit")
    result=json.loads(raw)
    row,=result["choices"]
    text=row["message"]["content"]
    if (result.get("model")!=choice.model or row.get("finish_reason")!="stop"
            or not isinstance(text,str) or row["message"].get("tool_calls")):
        raise ValueError("Unexpected local completion")
    usage=result.get("usage") or {}
    return {"model":choice.model,"result":text,"usage":{
        key:usage[key] for key in ("prompt_tokens","completion_tokens")
        if type(usage.get(key)) is int and usage[key]>=0}}


if __name__=="__main__":
    try:
        values=json.loads(sys.stdin.buffer.read(192 * 1024))
        answer=json.dumps(mesh_request(values),ensure_ascii=False).encode("utf-8")
        sys.stdout.buffer.write(answer)
    except Exception:
        raise SystemExit("Mesh local operation failed") from None
