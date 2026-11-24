"""Create a pinned worktree once and reconcile interrupted creation."""

from contextlib import contextmanager
import os
from pathlib import Path
from uuid import uuid4

from .local_tasks import common_dir, database, load_project, load_task, show_task, stamp, update_task
from .profile import profile_home
from .review import _git, repository_head


@contextmanager
def _operation_lock(root, task_id):
    folder = root / "worktree-locks"
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.resolve() != folder:
        raise ValueError("Worktree operation lock directory must stay in the private profile")
    with (folder / (task_id + ".lock")).open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError("Another worktree operation is in progress") from None
        # Closing the handle releases the lock, including after process exit.
        yield


def _destination(root, task_id):
    folder = root / "worktrees"
    if folder.resolve() != folder or folder.is_symlink() or getattr(folder, "is_junction", lambda: False)():
        raise ValueError("Worktree directory must stay in the private profile")
    return folder / task_id


def _registered(root, target):
    rows = _git(root, "worktree", "list", "--porcelain", "-z").split(b"\0\0")
    for row in rows:
        fields = row.split(b"\0")
        if fields[0].startswith(b"worktree ") and Path(os.fsdecode(fields[0][9:])).resolve() == target:
            return fields
    return None


def _verify_worktree(repository, target, commit, fields, *, clean=True):
    if (not fields or b"detached" not in fields or b"HEAD " + commit.encode("ascii") not in fields
            or any(field.startswith((b"prunable", b"locked")) for field in fields)
            or not target.is_dir() or target.resolve() != target
            or target.is_symlink() or getattr(target, "is_junction", lambda: False)()
            or repository_head(target) != commit or common_dir(repository) != common_dir(target)):
        raise ValueError("Worktree cannot be verified against the pinned commit and repository")
    admin = Path(os.fsdecode(_git(target, "rev-parse", "--absolute-git-dir").rstrip(b"\r\n")))
    if any(os.path.lexists(admin / name) for name in ("index.lock", "HEAD.lock")):
        raise ValueError("Git left an administrative lock. Inspect it before reconciliation.")
    if clean and _git(target, "--no-optional-locks", "-c", "core.fsmonitor=false", "status",
            "--porcelain=v1", "--untracked-files=all", "--ignored=matching"):
        raise ValueError("Worktree contains changes. Inspect it before reconciliation.")


def _make_worktree(repository, target, commit):
    # Disable repository hooks for the automatic checkout.
    no_hooks = target.parent / (target.name + ".no-hooks")
    if os.path.lexists(no_hooks):
        raise ValueError("Reserved worktree hook path already exists")
    _git(repository, "-c", "core.hooksPath=" + str(no_hooks), "worktree", "add",
         "--detach", "--", str(target), commit)


def _finish(record, phase, state, *, worktree):
    with database(write=True) as db:
        current = load_task(db, record["task_id"])
        if current["revision"] != record["revision"]:
            raise ValueError("Task revision changed during worktree operation")
        operation = {**current["worktree_operation"], "state": state, "finished_at": stamp()}
        return update_task(db, current, phase=phase, worktree=worktree, worktree_operation=operation)


def verify_task_worktree(home, record, *, clean=True):
    target = _destination(home, record["task_id"])
    if record.get("worktree") != str(target):
        raise ValueError("Task has no verified worktree in the current profile")
    contract = record["contract"]
    repository = Path(contract["repository"])
    _verify_worktree(repository, target, contract["base_revision"], _registered(repository, target), clean=clean)
    return target


def create_worktree(task_id):
    show_task(task_id)
    home = profile_home().resolve()
    with _operation_lock(home, task_id):
        target = _destination(home, task_id)
        with database(write=True) as db:
            record = load_task(db, task_id)
            if record["phase"] == "worktree_ready":
                return record
            if record["phase"] != "prepared":
                raise ValueError("Worktree creation is unresolved. Reconcile it before retrying.")
            contract = record["contract"]
            project = load_project(db, contract["project_id"])
            repository = Path(contract["repository"])
            if project["revision"] != contract["project_revision"]:
                raise ValueError("Project revision changed. Prepare a new task.")
            if repository_head(repository) != contract["base_revision"]:
                raise ValueError("Repository HEAD changed. Prepare a new task.")
            if os.path.lexists(target) or _registered(repository, target):
                raise ValueError("The worktree destination is already occupied")
            record = update_task(db, record, phase="creating_worktree",
                                 worktree_operation={"id": uuid4().hex, "state": "creating",
                                                     "started_at": stamp(), "destination": str(target)})
        target.parent.mkdir(mode=0o700, exist_ok=True)
        _make_worktree(repository, target, contract["base_revision"])
        _verify_worktree(repository, target, contract["base_revision"], _registered(repository, target))
        return _finish(record, "worktree_ready", "created", worktree=str(target))


def reconcile_worktree(task_id, *, confirm_git_stopped):
    if confirm_git_stopped is not True:
        raise ValueError("Confirm the interrupted Git process has stopped before reconciliation")
    show_task(task_id)
    home = profile_home().resolve()
    with _operation_lock(home, task_id):
        record = show_task(task_id)
        if record["phase"] != "creating_worktree":
            raise ValueError("Task has no interrupted worktree creation to reconcile")
        target = _destination(home, task_id)
        if record["worktree_operation"]["destination"] != str(target):
            raise ValueError("The profile path changed after worktree creation started")
        repository = Path(record["contract"]["repository"])
        repository_head(repository)
        fields = _registered(repository, target)
        if not fields and not os.path.lexists(target):
            return _finish(record, "prepared", "absent", worktree=None)
        _verify_worktree(repository, target, record["contract"]["base_revision"], fields)
        return _finish(record, "worktree_ready", "reconciled", worktree=str(target))
