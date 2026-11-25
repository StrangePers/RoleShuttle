"""Run a registered local check with a time limit and process cleanup."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
from threading import Thread

SAFE_ENV = {"PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR"}
LOG_LIMIT = 32 * 1024
GATE = """
import json, subprocess, sys
argv = json.loads(sys.stdin.readline())
raise SystemExit(subprocess.call(argv, stdin=subprocess.DEVNULL))
"""


def executable_hash(executable):
    with Path(executable).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _stop(process, job):
    if job is not None:
        job.close()
    elif os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()
    process.wait(timeout=5)


def check_directory(check, root):
    from .review import _inside

    cwd = _inside(root, check["cwd"]) if "cwd" in check else root
    if not cwd.is_dir():
        raise ValueError("Registered check working directory is unavailable")
    return cwd


def _run_process(check, cwd, environment):
    read_fd, write_fd = os.pipe()
    tail = bytearray()
    truncated = False
    with os.fdopen(read_fd, "rb", buffering=0) as output, os.fdopen(write_fd, "wb") as sink:
        def drain_output():
            nonlocal truncated
            try:
                while chunk := output.read(LOG_LIMIT):
                    tail.extend(chunk)
                    if len(tail) > LOG_LIMIT:
                        del tail[:-LOG_LIMIT]
                        truncated = True
            except (OSError, ValueError):
                truncated = True

        with subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", GATE], cwd=cwd, env=environment,
                              start_new_session=os.name != "nt", stdin=subprocess.PIPE,
                              stdout=sink, stderr=subprocess.STDOUT) as process:
            sink.close()
            reader = Thread(target=drain_output, daemon=True)
            job = None
            try:
                if os.name == "nt":
                    from .windows_job import WindowsJob
                    job = WindowsJob(process.pid)
                reader.start()
                process.communicate(input=(json.dumps(check["argv"]) + "\n").encode("ascii"), timeout=check["timeout"])
                status = "passed" if process.returncode == 0 else "failed"
                result = {"id": check["id"], "status": status, "exit_code": process.returncode}
            except subprocess.TimeoutExpired:
                result = {"id": check["id"], "status": "timed_out", "exit_code": None}
            finally:
                _stop(process, job)
                if reader.ident is not None:
                    reader.join(timeout=5)
    if result["status"] != "passed":
        encoded = bytes(tail).decode("utf-8", errors="replace").encode("utf-8")
        result["log"] = {"text": encoded[-LOG_LIMIT:].decode("utf-8", errors="ignore"),
                         "truncated": truncated or reader.is_alive() or len(encoded) > LOG_LIMIT}
    return result


def run_check(check, root):
    if executable_hash(check["argv"][0]) != check["executable_sha256"]:
        raise ValueError("Registered check executable changed")
    environment = {key: value for key, value in os.environ.items() if key.upper() in SAFE_ENV}
    # Give tools a writable home without loading the operator's real profile.
    with tempfile.TemporaryDirectory(prefix="roleshuttle-check-home-") as home:
        cache = shlex.quote(f"cache_dir={Path(home) / 'pytest-cache'}")
        environment.update(HOME=home, USERPROFILE=home, PYTHONDONTWRITEBYTECODE="1",
                           PYTEST_ADDOPTS=f"-o {cache}")
        return _run_process(check, check_directory(check, root), environment)
