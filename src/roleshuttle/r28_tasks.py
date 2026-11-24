"""Bounded RoleShuttle web interface for the installed r28 task lifecycle."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess


TASK_FIELDS = {
    "status": {"task_id"},
    "answer": {"task_id", "revision", "request_id", "answer"},
    "diff": {"task_id"},
    "launch": {"task_id", "revision"},
    "cancel": {"task_id", "revision"},
    "reconcile": {"task_id", "revision", "local_stopped", "provider_stopped", "reason"},
    "check": {"task_id", "revision", "fingerprint"},
    "correct": {"task_id", "revision", "fingerprint", "feedback"},
    "accept": {"task_id", "revision", "fingerprint"},
}


class ConsoleTasks:
    def __init__(self, console_home, private_home, history_origin):
        self.console_home = Path(console_home)
        self.registry = Path(private_home) / "r28-panel.sqlite3"
        self.history_origin = history_origin

    def call(self, operation, body):
        runtime = json.loads((self.console_home / "runtime.json").read_text(encoding="utf-8-sig"))
        python, root = Path(runtime["python"]), Path(runtime["source_root"])
        if runtime.get("schema_version") != 1 or not python.is_absolute() or not root.is_absolute():
            raise ValueError("The installed r28 runtime is not configured")
        helper = Path(__file__).with_name("r28_worker_bridge.py").resolve()
        try:
            command = subprocess.run([str(python), "-I", "-B", str(helper), str(root), str(self.console_home), operation],
                                     input=json.dumps(body, ensure_ascii=True), encoding="utf-8", capture_output=True,
                                     env={**os.environ, "AGENT_CONSOLE_HOME": str(self.console_home),
                                          "AGENT_CONSOLE_EXTRA_USAGE": "0"},
                                     timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if command.returncode:
                raise ValueError("r28 could not complete the panel operation")
            result = json.loads(command.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            raise ValueError("r28 did not confirm the operation. Check its history before retrying.") from None
        if result["ok"] is not True:
            raise ValueError(result["error"])
        output = result["result"]
        if isinstance(output, dict):
            output["history_url"] = f"{self.history_origin}/?task={output['task_id']}"
        return output

    def projects(self):
        return self.call("projects", {})

    def action(self, action, packet):
        if not isinstance(packet, dict):
            raise ValueError("Expected a task request")
        if action == "prepare":
            if set(packet) != {"request_id", "contract"} or not isinstance(packet["contract"], dict):
                raise ValueError("Expected a preparation request")
            request_id = packet["request_id"]
            body = packet["contract"]
            fields = {"project_id", "title", "instructions", "type", "complexity", "risk", "mode",
                      "allowed_paths", "check_ids", "context_tokens", "max_wall_time", "role"}
            if (not isinstance(request_id, str)
                    or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", request_id)
                    or set(body) != fields):
                raise ValueError("Expected the bounded model task fields")
            return self.prepare(request_id, body)
        if action not in TASK_FIELDS or set(packet) != TASK_FIELDS[action]:
            raise ValueError("Expected the panel task identity")
        task_id = packet["task_id"]
        if not isinstance(task_id, str) or not re.fullmatch("[0-9a-f]{32}", task_id):
            raise ValueError("Invalid r28 task ID")
        if "revision" in packet and not isinstance(packet["revision"], str):
            raise ValueError("Expected the current task revision")
        if "fingerprint" in packet and (not isinstance(packet["fingerprint"], str)
                                         or not re.fullmatch("[0-9a-f]{64}", packet["fingerprint"])):
            raise ValueError("Expected the reviewed diff fingerprint")
        if action == "correct":
            feedback = packet["feedback"]
            if not isinstance(feedback, str) or not 10 <= len(feedback.strip()) <= 8000:
                raise ValueError("Enter a correction comment of 10 to 8000 characters")
            packet = {**packet, "feedback": feedback.strip()}
        if action == "answer":
            request_id, answer = packet["request_id"], packet["answer"]
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
                raise ValueError("Expected the current question ID")
            if not isinstance(answer, str) or not 1 <= len(answer.strip()) <= 4000:
                raise ValueError("Enter an answer of 1 to 4000 characters")
            packet = {**packet, "answer": answer.strip()}
        if action == "reconcile":
            if packet["local_stopped"] is not True or packet["provider_stopped"] is not True:
                raise ValueError("Confirm both local process and provider request termination")
            reason = packet["reason"]
            if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 2000:
                raise ValueError("Enter an inspection reason of 10 to 2000 characters")
            packet = {**packet, "reason": reason.strip()}
        if not self.registry.is_file():
            raise ValueError("Task was not prepared in this RoleShuttle panel")
        with closing(sqlite3.connect(self.registry.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT 1 FROM requests WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError("Task was not prepared in this RoleShuttle panel")
        return self.call(action, packet)

    def prepare(self, request_id, body):
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
        self.registry.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.registry)) as db:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, digest TEXT, task_id TEXT UNIQUE)")
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute("SELECT digest, task_id FROM requests WHERE id=?", (request_id,)).fetchone()
                if existing is None:
                    db.execute("INSERT INTO requests VALUES(?, ?, NULL)", (request_id, digest))
            if existing is not None:
                if existing[0] != digest or not existing[1]:
                    raise ValueError("Preparation is pending or unconfirmed. Inspect r28 history before retrying.")
                return self.call("status", {"task_id": existing[1]})
            result = self.call("prepare", body)
            with db:
                db.execute("UPDATE requests SET task_id=? WHERE id=?", (result["task_id"], request_id))
            return result
