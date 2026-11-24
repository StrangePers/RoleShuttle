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

SAFE_ENV = {"PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR"}
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


def run_check(check, root):
    if executable_hash(check["argv"][0]) != check["executable_sha256"]:
        raise ValueError("Registered check executable changed")
    environment = {key: value for key, value in os.environ.items() if key.upper() in SAFE_ENV}
    # Give tools a writable home without loading the operator's real profile.
    with tempfile.TemporaryDirectory(prefix="roleshuttle-check-home-") as home:
        cache = shlex.quote(f"cache_dir={Path(home) / 'pytest-cache'}")
        environment.update(HOME=home, USERPROFILE=home, PYTHONDONTWRITEBYTECODE="1",
                           PYTEST_ADDOPTS=f"-o {cache}")
        with subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", GATE], cwd=check_directory(check, root), env=environment,
                              start_new_session=os.name != "nt", stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
            job = None
            try:
                if os.name == "nt":
                    from .windows_job import WindowsJob
                    job = WindowsJob(process.pid)
                process.communicate(input=(json.dumps(check["argv"]) + "\n").encode("ascii"), timeout=check["timeout"])
                return {"id": check["id"], "status": "passed" if process.returncode == 0 else "failed",
                        "exit_code": process.returncode}
            except subprocess.TimeoutExpired:
                return {"id": check["id"], "status": "timed_out", "exit_code": None}
            finally:
                _stop(process, job)
