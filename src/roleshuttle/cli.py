"""Local tools and an explicit bridge to an operator-configured task runtime."""
import argparse
import asyncio
from importlib.resources import files
import json
from pathlib import Path
import subprocess
import sys
import sqlite3

from .codex_stage import ROLE_TASKS, codex_stage
from .companion import LABELS, follow, publish, publish_sources, report_snapshot, runtime_task_id
from .local import MAX_INPUT, MAX_OUTPUT_TOKENS, ROLE_PROMPTS, consult, delegate
from .local_tasks import prepare_task, register_project, resolve_project, show_project, show_task
from .local_acceptance import accept_task, check_task, reconcile_checks
from .local_execution import execute_patch, inspect_result, reconcile_patch
from .local_model import answer_question, cancel_model, propose_patch, revise_patch
from .local_worktrees import create_worktree, reconcile_worktree
from .model_settings import import_console, preview_console, read_settings
from .profile import profile_home, runtime_command
from .project_import import import_projects, preview_projects, project_readiness
from .recommended import preview_snapshot
from .review import snapshot
from .skill import CLIENT_DIRS, install
from .stages import read_stage, run_stage


def runtime_reports(task_ids):
    ids = [runtime_task_id(value) for value in task_ids]
    command = runtime_command()
    reports = []
    for task_id in ids:
        result = subprocess.run(
            [*command, "task", "show", "--task-id", task_id, "--wait-seconds", "0"],
            capture_output=True, encoding="utf-8", timeout=15,
        )
        if result.returncode:
            raise ValueError("Configured runtime could not read a requested task")
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or report.get("task_id") != task_id:
            raise ValueError("Configured runtime returned a different task identity")
        reports.append(report)
    return reports


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
    native = commands.add_parser("codex-stage", help="Run one text stage on the Codex subscription")
    native.add_argument("--role", required=True, choices=tuple(ROLE_TASKS))
    native.add_argument("--model", help="Explicitly choose a native Codex model for this stage")
    native.add_argument("--effort", choices=("minimal", "low", "medium", "high", "xhigh", "max", "ultra"))
    native.add_argument("--use-profile", action="store_true", help="Use this role's saved Codex model and effort")
    native.add_argument("--input", type=argparse.FileType("r", encoding="utf-8-sig"), required=True,
                        help="Approved task text, or - for stdin")
    council = commands.add_parser("consult", help="Send a decision to the configured specialist and reviewer")
    for command in (worker, native, council):
        command.add_argument("--record", action="store_true", help="Save the result in the private RoleShuttle profile")
    stage = commands.add_parser("stage", help="Read a recorded standalone stage")
    stage.add_argument("id")
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
    source.add_argument("--state", choices=("idle", "running", "waiting", "failed"))
    source.add_argument("--report", action="append", help="Task report JSON, or - for stdin. Repeat for a complete snapshot.")
    source.add_argument("--task-id", action="append", help="Read a task from the configured runtime. Repeat for a snapshot.")
    source.add_argument("--snapshot", action="store_true", help="Publish recorded stages and optional runtime status once.")
    source.add_argument("--follow", action="store_true", help="Refresh this snapshot every two seconds until interrupted.")
    pet.add_argument("--runtime-status", type=Path, help="Absolute path to an existing Console bridge status file.")
    task = commands.add_parser("task", help="Pass a task command and its exit status to the configured runtime")
    task.add_argument("arguments", nargs=argparse.REMAINDER)
    dashboard = commands.add_parser("dashboard", help="Open the configured runtime's local dashboard")
    dashboard.add_argument("--port", type=int)
    dashboard.add_argument("--task-id", type=runtime_task_id, help="Open a known runtime task")
    dashboard.add_argument("--no-browser", action="store_true")
    models = commands.add_parser("models", help="Open or inspect private model settings")
    model_actions = models.add_subparsers(dest="model_action", required=True)
    model_panel = model_actions.add_parser("serve", help="Open the local model settings panel")
    model_panel.add_argument("--port", type=int, default=0)
    model_preview = model_actions.add_parser("preview-console", help="Preview assignments from an Agent Console config directory")
    model_preview.add_argument("config", type=Path)
    model_import = model_actions.add_parser("import-console", help="Apply compatible assignments from a reviewed preview")
    model_import.add_argument("config", type=Path)
    model_import.add_argument("--source-revision", required=True)
    model_import.add_argument("--target-revision", required=True)
    routing = commands.add_parser("recommend", help="Preview Recommended from a saved snapshot without running a task")
    routing.add_argument("snapshot", type=Path)
    routing.add_argument("--project", help="Apply a registered private project policy")
    local_project = commands.add_parser("local-project", help="Register or inspect a private RoleShuttle project")
    project_actions = local_project.add_subparsers(dest="project_action", required=True)
    register = project_actions.add_parser("register")
    register.add_argument("profile", type=Path)
    register.add_argument("--revision", default="empty")
    project_actions.add_parser("audit", help="Inspect checkout and check executable readiness")
    project_show = project_actions.add_parser("show")
    project_show.add_argument("id")
    resolve = project_actions.add_parser("resolve", help="Resolve or inherit an authorized linked worktree")
    resolve.add_argument("repository", type=Path)
    for name in ("preview-console", "import-console"):
        project_import = project_actions.add_parser(name, help="Migrate private profiles with Extra Usage off")
        project_import.add_argument("config", type=Path)
        if name == "import-console":
            project_import.add_argument("--source-revision", required=True)
            project_import.add_argument("--target-revision", required=True)
    local_task = commands.add_parser("local-task", help="Prepare or inspect a private RoleShuttle task")
    task_actions = local_task.add_subparsers(dest="local_task_action", required=True)
    prepare = task_actions.add_parser("prepare")
    prepare.add_argument("contract", type=Path)
    prepare.add_argument("--request-id")
    task_show = task_actions.add_parser("show")
    task_show.add_argument("id")
    worktree = task_actions.add_parser("worktree", help="Create a detached worktree at the pinned commit")
    worktree.add_argument("id")
    reconcile = task_actions.add_parser("reconcile-worktree", help="Inspect an interrupted worktree creation")
    reconcile.add_argument("id")
    reconcile.add_argument("--confirm-git-stopped", action="store_true")
    execute = task_actions.add_parser("execute", help="Apply a local text patch in the ready worktree")
    execute.add_argument("id")
    execute.add_argument("--patch", type=Path, required=True)
    propose = task_actions.add_parser("propose-patch", help="Request one bounded subscription model patch")
    propose.add_argument("id")
    propose.add_argument("--prompt", type=Path, required=True)
    propose.add_argument("--snapshot", type=Path, required=True)
    answer = task_actions.add_parser("answer-model-question", help="Answer a saved model question")
    answer.add_argument("id")
    answer.add_argument("--question-id", required=True)
    answer.add_argument("--prompt", type=Path, required=True)
    answer.add_argument("--answer", type=Path, required=True)
    answer.add_argument("--snapshot", type=Path, required=True)
    revise = task_actions.add_parser("revise-model-patch", help="Request changes to a reviewed model patch")
    revise.add_argument("id")
    revise.add_argument("--fingerprint", required=True)
    revise.add_argument("--prompt", type=Path, required=True)
    revise.add_argument("--comment", type=Path, required=True)
    revise.add_argument("--snapshot", type=Path, required=True)
    cancel = task_actions.add_parser("cancel-model", help="Stop an active native model response")
    cancel.add_argument("id")
    cancel.add_argument("--operation-id", required=True)
    apply_model = task_actions.add_parser("apply-model-patch", help="Apply the saved model proposal")
    apply_model.add_argument("id")
    inspect = task_actions.add_parser("review", help="Inspect the current diff and compare it with the stored receipt")
    inspect.add_argument("id")
    recover = task_actions.add_parser("reconcile-execution", help="Bind an interrupted attempt to an inspected result")
    recover.add_argument("id")
    recover.add_argument("--fingerprint", required=True)
    recover.add_argument("--confirm-git-stopped", action="store_true")
    for name in ("check", "accept"):
        action = task_actions.add_parser(name)
        action.add_argument("id")
        action.add_argument("--fingerprint", required=True)
        if name == "accept":
            action.add_argument("--project-proof", type=Path)
    recover_checks = task_actions.add_parser("reconcile-checks")
    recover_checks.add_argument("id")
    recover_checks.add_argument("--fingerprint", required=True)
    recover_checks.add_argument("--confirm-checks-stopped", action="store_true")
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
            model, effort = args.model, args.effort
            if args.use_profile:
                assignment = read_settings()["settings"]["roles"].get(args.role)
                if not assignment or assignment["provider"] != "codex":
                    raise ValueError("The selected role needs a saved Codex assignment")
                model, effort = model or assignment["model_id"], effort or assignment["effort"]
            report = run_stage("codex-stage",
                               lambda: asyncio.run(codex_stage(args.role, prompt, model=model, effort=effort)),
                               record=args.record,
                               outcome=lambda result: "waiting" if result["status"] == "needs_input" else "review")
            print(json.dumps(report, ensure_ascii=True))
            return 0
        if args.action == "stage":
            print(json.dumps(read_stage(args.id), ensure_ascii=True))
            return 0
        if args.action == "task":
            if not args.arguments:
                task.error("Specify a task command, for example show --task-id ID")
            return subprocess.run([*runtime_command(), "task", *args.arguments], check=False).returncode
        if args.action == "dashboard":
            command = [*runtime_command(), "serve"]
            if args.port is not None:
                if not 1 <= args.port <= 65535:
                    dashboard.error("--port must be between 1 and 65535")
                command.extend(("--port", str(args.port)))
            if args.task_id:
                command.extend(("--task-id", args.task_id))
            if args.no_browser:
                command.append("--no-browser")
            return subprocess.run(command, check=False).returncode
        if args.action == "models":
            if args.model_action == "preview-console":
                print(json.dumps(preview_console(args.config), indent=2))
            elif args.model_action == "import-console":
                print(json.dumps(import_console(args.config, args.source_revision, args.target_revision), indent=2))
            else:
                if not 0 <= args.port <= 65535:
                    model_panel.error("--port must be between 0 and 65535")
                from .settings_server import serve_settings
                serve_settings(args.port)
            return 0
        if args.action == "recommend":
            snapshot_input = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
            print(json.dumps(preview_snapshot(snapshot_input, project_id=args.project), indent=2, ensure_ascii=True))
            return 0
        if args.action == "local-project":
            if args.project_action == "register":
                result = register_project(json.loads(args.profile.read_text(encoding="utf-8-sig")), args.revision)
            elif args.project_action == "resolve":
                result = resolve_project(args.repository)
            elif args.project_action == "audit":
                result = project_readiness()
            elif args.project_action == "preview-console":
                result = preview_projects(args.config)
            elif args.project_action == "import-console":
                result = import_projects(args.config, args.source_revision, args.target_revision)
            else:
                result = show_project(args.id)
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0
        if args.action == "local-task":
            if args.local_task_action == "prepare":
                result = prepare_task(json.loads(args.contract.read_text(encoding="utf-8-sig")),
                                      request_id=args.request_id)
            elif args.local_task_action == "worktree":
                result = create_worktree(args.id)
            elif args.local_task_action == "reconcile-worktree":
                result = reconcile_worktree(args.id, confirm_git_stopped=args.confirm_git_stopped)
            elif args.local_task_action == "propose-patch":
                result = propose_patch(args.id, args.prompt.read_text(encoding="utf-8-sig"),
                                       json.loads(args.snapshot.read_text(encoding="utf-8-sig")))
            elif args.local_task_action == "answer-model-question":
                result = answer_question(args.id, args.prompt.read_text(encoding="utf-8-sig"),
                                         json.loads(args.snapshot.read_text(encoding="utf-8-sig")),
                                         args.question_id, args.answer.read_text(encoding="utf-8-sig"))
            elif args.local_task_action == "revise-model-patch":
                result = revise_patch(args.id, args.prompt.read_text(encoding="utf-8-sig"),
                                      json.loads(args.snapshot.read_text(encoding="utf-8-sig")),
                                      args.fingerprint, args.comment.read_text(encoding="utf-8-sig"))
            elif args.local_task_action == "cancel-model":
                result = cancel_model(args.id, args.operation_id)
            elif args.local_task_action == "apply-model-patch":
                result = execute_patch(args.id)
            elif args.local_task_action == "execute":
                result = execute_patch(args.id, args.patch)
            elif args.local_task_action == "review":
                result = inspect_result(args.id)
            elif args.local_task_action == "reconcile-execution":
                result = reconcile_patch(args.id, args.fingerprint, confirm_git_stopped=args.confirm_git_stopped)
            elif args.local_task_action == "check":
                result = check_task(args.id, args.fingerprint)
                print(json.dumps(result, indent=2, ensure_ascii=True))
                return 0 if result["check_receipt"]["passed"] else 1
            elif args.local_task_action == "accept":
                proof = json.loads(args.project_proof.read_text(encoding="utf-8-sig")) if args.project_proof else None
                result = accept_task(args.id, args.fingerprint, proof=proof)
            elif args.local_task_action == "reconcile-checks":
                result = reconcile_checks(args.id, args.fingerprint, confirm_checks_stopped=args.confirm_checks_stopped)
            else:
                result = show_task(args.id)
            print(json.dumps(result, indent=2, ensure_ascii=True))
            return 0
        if args.action in ("delegate", "consult"):
            with args.input as stream:
                prompt = stream.read(MAX_INPUT + 1)
            if args.action == "consult":
                report = run_stage("consult", lambda: consult(args.profile, prompt, max_output_tokens=args.max_output_tokens),
                                   record=args.record,
                                   outcome=lambda result: "review" if result["status"] == "returned" else "failed")
                print(json.dumps(report, ensure_ascii=True))
                return 0 if report["status"] == "returned" else 1
            report = run_stage("delegate", lambda: delegate(args.profile, args.role, prompt, max_output_tokens=args.max_output_tokens),
                               record=args.record,
                               outcome=lambda result: "review" if result["done_reason"] == "stop" else "failed")
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
            if args.runtime_status is not None:
                if not (args.snapshot or args.follow):
                    pet.error("--runtime-status requires --snapshot or --follow")
                if (not args.runtime_status.is_absolute() or
                        args.runtime_status.resolve() == (profile_home() / "agentwhisker-status.json").resolve()):
                    pet.error("--runtime-status must name a separate absolute path")
            if args.follow:
                follow(args.runtime_status)
            elif args.snapshot:
                print(publish_sources(args.runtime_status))
            elif args.task_id:
                state, items = report_snapshot(runtime_reports(args.task_id))
                print(publish(state, items))
            elif args.report:
                if args.report.count("-") > 1:
                    raise ValueError("Specify stdin at most once")
                reports = [json.load(sys.stdin) if name == "-" else
                           json.loads(Path(name).read_text(encoding="utf-8-sig")) for name in args.report]
                state, items = report_snapshot(reports)
                print(publish(state, items))
            else:
                print(publish(args.state))
    except subprocess.TimeoutExpired:
        print("Configured runtime timed out reading a task", file=sys.stderr)
        return 2
    except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0
