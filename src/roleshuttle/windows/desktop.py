"""Start or open the autonomous RoleShuttle desktop panel."""
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from threading import Thread
import time
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import urlencode
import webbrowser

ROOT = Path(__file__).resolve().parent
settings = json.loads((ROOT / "desktop.json").read_bytes())
PORT = settings["port"]
ORIGIN = f"http://127.0.0.1:{PORT}"
CAPABILITY = ROOT / "panel.cap"


def load_go_key(opencode):
    from roleshuttle.go_stage import configure_key
    for delay in (0, 2, 5):
        if delay:
            time.sleep(delay)
        try:
            result = subprocess.run([opencode, "auth", "export", "opencode-go"],
                                    capture_output=True, timeout=12, check=True)
            [item] = json.loads(result.stdout)
            configure_key(item["value"]["key"])
            return
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            continue
    print("OpenCode Go is unavailable: could not load its configured key.", file=sys.stderr, flush=True)


def serve():
    from importlib.metadata import version
    if version("roleshuttle") != settings["version"]:
        raise RuntimeError("Unexpected RoleShuttle version")
    os.environ.update(ROLESHUTTLE_HOME=settings["profile"], ROLESHUTTLE_RUNTIME_ARGV="",
                      AGENT_CONSOLE_EXTRA_USAGE="0", ROLESHUTTLE_CODEX_BIN=settings["codex"])
    from roleshuttle.go_stage import configured
    from roleshuttle.profile import write_json
    from roleshuttle.settings_server import SettingsServer
    with SettingsServer(PORT, autonomous_tasks=True) as server:
        write_json(CAPABILITY, {"pid":os.getpid(), "token":server.token})
        if not configured():
            Thread(target=load_go_key, args=(settings.get("opencode"),), daemon=True).start()
        server.serve_forever()


def ready():
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            cap = json.loads(CAPABILITY.read_bytes())
            auth = {"Authorization": "Bearer " + cap["token"]}
            with opener.open(Request(ORIGIN + "/api/local-tasks", headers=auth), timeout=2) as response:
                panel = json.load(response)
            if panel.get("enabled") is True and panel.get("history") is None:
                return cap
        except (OSError, ValueError, KeyError):
            time.sleep(.2)
    raise RuntimeError("Autonomous RoleShuttle did not become ready")


if __name__ == "__main__":
    if sys.argv[1:] == ["serve"]:
        serve()
    else:
        cap = ready()
        action = sys.argv[1:]
        if action == ["health"]:
            raise SystemExit(0)
        params = {"token":cap["token"]}
        if len(action) == 2 and action[0] == "archive-open" and re.fullmatch(r"[0-9a-f]{32}", action[1]):
            os.environ["ROLESHUTTLE_HOME"] = settings["profile"]
            from roleshuttle.r28_archive import archive_card
            try:
                archive_card(action[1])
            except ValueError:
                from roleshuttle.local_tasks import show_task
                show_task(action[1])
                params["task"] = action[1]
            else:
                params["archive"] = action[1]
        elif action != ["open"]:
            raise ValueError("Invalid RoleShuttle launcher action")
        if not webbrowser.open(ORIGIN + "/#" + urlencode(params)):
            raise RuntimeError("RoleShuttle panel could not open")
