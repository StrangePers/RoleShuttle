"""Locate the private user profile and its explicit runtime configuration."""

import json
import os
from pathlib import Path
import tempfile
import time


def write_json(destination, value):
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stream = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            json.dump(value, stream)
            stream.write("\n")
        for delay in (0.02, 0.05, 0.1, None):
            try:
                os.replace(temporary, destination)
                break
            except OSError as error:
                if delay is None or getattr(error, "winerror", None) not in {5, 32, 33}:
                    raise
                time.sleep(delay)
    finally:
        temporary.unlink(missing_ok=True)


def profile_home():
    configured = os.environ.get("ROLESHUTTLE_HOME")
    if configured is None:
        return Path.home() / ".roleshuttle"
    path = Path(configured)
    if not path.is_absolute():
        raise ValueError("ROLESHUTTLE_HOME must be an absolute path")
    return path


def runtime_command():
    configured = os.environ.get("ROLESHUTTLE_RUNTIME_ARGV")
    if configured is not None:
        command = json.loads(configured) if configured else None
    else:
        path = profile_home() / "runtime.json"
        try:
            profile = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            profile = {"schema_version": 1}
        if not isinstance(profile, dict) or type(profile.get("schema_version")) is not int or profile["schema_version"] != 1:
            raise ValueError("Unsupported RoleShuttle runtime profile")
        command = profile.get("argv")
    if command is None:
        raise ValueError("Configure the trusted runtime in runtime.json or ROLESHUTTLE_RUNTIME_ARGV")
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
        raise ValueError("Runtime command must be a non-empty array of strings")
    executable = Path(command[0])
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError("Runtime executable must be an existing absolute path")
    if executable.suffix.lower() in (".bat", ".cmd"):
        raise ValueError("Configure the executable directly")
    return command
