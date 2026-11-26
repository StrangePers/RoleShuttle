"""Start or open the autonomous RoleShuttle desktop panel."""
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import ProxyHandler, Request, build_opener
from urllib.parse import urlencode
import webbrowser

ROOT = Path(__file__).resolve().parent
settings = json.loads((ROOT / "desktop.json").read_bytes())
PORT = settings["port"]
ORIGIN = f"http://127.0.0.1:{PORT}"
CAPABILITY = ROOT / "panel.cap"


def serve():
    from importlib.metadata import version
    if version("roleshuttle") != settings["version"]:
        raise RuntimeError("Unexpected RoleShuttle version")
    os.environ.update(ROLESHUTTLE_HOME=settings["profile"], ROLESHUTTLE_RUNTIME_ARGV="",
                      AGENT_CONSOLE_EXTRA_USAGE="0", ROLESHUTTLE_CODEX_BIN=settings["codex"])
    if "ROLESHUTTLE_OPENCODE_GO_KEY" not in os.environ:
        try:
            result = subprocess.run([settings["opencode"], "auth", "export", "opencode-go"],
                                    capture_output=True, timeout=12, check=True)
            [item] = json.loads(result.stdout)
            os.environ["ROLESHUTTLE_OPENCODE_GO_KEY"] = item["value"]["key"]
            del result, item
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            raise RuntimeError("Could not load the configured OpenCode Go key") from None
    from roleshuttle.profile import write_json
    from roleshuttle.settings_server import SettingsServer
    with SettingsServer(PORT, autonomous_tasks=True) as server:
        write_json(CAPABILITY, {"pid":os.getpid(), "token":server.token})
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
