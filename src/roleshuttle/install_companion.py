"""Install the packaged companion entry points into an existing Windows desktop."""
import argparse
import os
from importlib.resources import files
from pathlib import Path
from tempfile import NamedTemporaryFile


def install(desktop):
    if not all((desktop / name).is_file() for name in ("desktop.json", "desktop.py", "Start.ps1")):
        raise ValueError("Expected an existing RoleShuttle Windows desktop installation")
    resources = files("roleshuttle").joinpath("windows")
    for name in ("companion-follow.py", "Start-AgentWhisker.ps1"):
        payload = resources.joinpath(name).read_bytes()
        target = desktop / name
        if target.exists() and target.read_bytes() == payload:
            continue
        with NamedTemporaryFile(dir=desktop, delete=False) as temp:
            temp.write(payload)
        try:
            os.replace(temp.name, target)
        finally:
            Path(temp.name).unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("desktop", type=Path)
    install(parser.parse_args().desktop)
