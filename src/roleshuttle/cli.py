"""Local tools and an explicit bridge to an operator-configured task runtime."""
import argparse
import asyncio
from importlib.resources import files
import json
import os
from pathlib import Path
import subprocess
import sys

from .codex_stage import ROLE_TASKS, codex_stage
from .companion import LABELS, publish, task_state
from .local import MAX_INPUT, MAX_OUTPUT_TOKENS, ROLE_PROMPTS, consult, delegate
from .review import snapshot
from .skill import CLIENT_DIRS, install


def runtime_command():
    config = os.environ.get("ROLESHUTTLE_RUNTIME_ARGV")
    if not config:
        raise ValueError("Set ROLESHUTTLE_RUNTIME_ARGV to your trusted runtime command as a JSON array")
    command = json.loads(config)
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
        raise ValueError("Runtime command must be a non-empty array of strings")
    executable = Path(command[0])
    if not executable.is_absolute() or not executable.is_file():
        raise ValueError("Runtime executable must be an existing absolute path")
    if executable.suffix.lower() in (".bat", ".cmd"):
        raise ValueError("Configure the executable directly")
    return command


def main(argv=None):
    parser = argparse.ArgumentParser(prog="roleshuttle", description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    demo = commands.add_parser("demo", help="Locate the offline interactive walkthrough")
    demo.add_argument("--open", action="store_true", help="Open the demo in your default browser")
    skill = commands.add_parser("skill", help="Print the controller skill or install it in the current project")
    skill.add_argument("--install", choices=tuple(CLIENT_DIRS), help="Install for this client under the current directory")
    preset = commands.add_parser("preset", help="List cloud presets or print a model assignment as JSON")
    preset.add_argument("name", nargs="?", help="Preset name (omit to list)")
    target = preset.add_mutually_exclusive_group()
    target.add_argument("--role", help="Look up one role, such as implementer or reviewer")
    target.add_argument("--lane", help="Look up one task category, such as critical")
    worker = commands.add_parser("delegate", help="Run a text stage on a local Ollama model")
    worker.add_argument("--role", choices=tuple(ROLE_PROMPTS), required=True)
    native = commands.add_parser("codex-stage", help="Run one text stage on the Codex subscription preset")
    native.add_argument("--role", required=True, choices=tuple(ROLE_TASKS))
    native.add_argument("--model", help="Explicitly choose a native Codex model for this stage")
    native.add_argument("--effort", choices=("minimal", "low", "medium", "high", "xhigh", "max", "ultra"))
    native.add_argument("--input", type=argparse.FileType("r", encoding="utf-8-sig"), required=True,
                        help="Approved task text, or - for stdin")
    council = commands.add_parser("consult", help="Send a decision to the configured specialist and reviewer")
    for command in (worker, council):
        command.add_argument("--profile", type=Path, required=True)
        command.add_argument("--max-output-tokens", type=int, default=MAX_OUTPUT_TOKENS,
                             help=f"Maximum generated tokens per call, 1 to {MAX_OUTPUT_TOKENS}")
        command.add_argument("--input", type=argparse.FileType("r", encoding="utf-8-sig"), required=True,
                             help="Task and permitted excerpts, or - for stdin")
    review = commands.add_parser("review", help="Inspect a trusted local Git worktree")
    review.add_argument("--repo", type=Path, required=True)
    review.add_argument("--base", required=True, help="Full pinned commit ID")
    review.add_argument("--allow", action="append", required=True, help="Allowed file or directory ending in /")
    review.add_argument("--task-id", required=True)
    review.add_argument("--read-only", action="store_true")
    pet = commands.add_parser("companion", help="Update the optional AgentWhisker status file")
    source = pet.add_mutually_exclusive_group(required=True)
    source.add_argument("--state", choices=tuple(LABELS))
    source.add_argument("--report", type=argparse.FileType("r", encoding="utf-8"), help="Task report JSON, or - for stdin")
    task = commands.add_parser("task", help="Pass a task command and its exit status to the configured runtime")
    task.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.action == "preset":
            presets = json.loads(files("roleshuttle").joinpath("presets.json").read_text(encoding="utf-8"))
            if args.name is None:
                if args.role is not None or args.lane is not None:
                    preset.error("Specify a preset name for a role or lane lookup")
                result = list(presets)
            else:
                if args.name not in presets:
                    preset.error(f"Unknown preset. Choose from {', '.join(presets)}")
                result = presets[args.name]
                for field, name in (("roles", args.role), ("lanes", args.lane)):
                    if name is not None:
                        if name not in result.get(field, {}):
                            preset.error(f"{args.name} has no {field} entry for {name!r}")
                        result = result[field][name]
            print(json.dumps(result, indent=2))
            return 0
        if args.action == "codex-stage":
            with args.input as stream:
                prompt = stream.read(MAX_INPUT + 1)
            print(json.dumps(asyncio.run(codex_stage(args.role, prompt, model=args.model, effort=args.effort)), ensure_ascii=True))
            return 0
        if args.action == "task":
            if not args.arguments:
                task.error("Specify a task command, for example show --task-id ID")
            return subprocess.run([*runtime_command(), "task", *args.arguments], check=False).returncode
        if args.action in ("delegate", "consult"):
            with args.input as stream:
                prompt = stream.read(MAX_INPUT + 1)
            if args.action == "consult":
                report = consult(args.profile, prompt, max_output_tokens=args.max_output_tokens)
                print(json.dumps(report, ensure_ascii=True))
                return 0 if report["status"] == "returned" else 1
            report = delegate(args.profile, args.role, prompt, max_output_tokens=args.max_output_tokens)
            print(json.dumps(report, ensure_ascii=True))
            return 0 if report["done_reason"] == "stop" else 1
        if args.action == "demo":
            path = Path(str(files("roleshuttle").joinpath("demo.html")))
            if args.open:
                import webbrowser
                webbrowser.open(path.as_uri())
            print(path)
        elif args.action == "skill":
            if args.install:
                print(install(args.install, Path.cwd()))
            else:
                print(files("roleshuttle").joinpath("SKILL.md").read_text(encoding="utf-8"), end="")
        elif args.action == "review":
            contract = {"base_revision": args.base, "task_id": args.task_id, "allowed_paths": args.allow,
                        "mode": "read" if args.read_only else "write"}
            report = snapshot(args.repo, contract)
            print(json.dumps(report, ensure_ascii=True))
            return 0 if report["reviewable"] else 1
        elif args.action == "companion":
            state = args.state
            if args.report:
                with args.report as stream:
                    state = task_state(json.load(stream))
            print(publish(state))
    except (ValueError, OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0
