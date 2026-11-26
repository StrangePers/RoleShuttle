"""Read a private, offline r28 archive without starting the old runtime."""

from contextlib import closing, contextmanager
import json
import re
import sqlite3

from .profile import profile_home


def archive_path():
    return profile_home().resolve() / "r28-archive.sqlite3"


@contextmanager
def archive_database(path):
    if path.resolve() != path or not path.is_file():
        raise ValueError("The local r28 archive must be a regular profile file")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != 1:
            raise ValueError("Unsupported r28 archive format")
        yield db


def list_archive():
    path = archive_path()
    if not path.exists():
        return {"items": []}
    with archive_database(path) as db:
        return {"items": [{"task_id": key, "title": title, "status": status}
                          for key, title, status in db.execute(
                              "SELECT id,title,status FROM archived_tasks ORDER BY updated_at DESC,id")]}


def archive_card(task_id):
    if not isinstance(task_id, str) or not re.fullmatch(r"[0-9a-f]{32}", task_id):
        raise ValueError("Invalid archived task ID")
    with archive_database(archive_path()) as db:
        row = db.execute("SELECT payload FROM archived_tasks WHERE id=?", (task_id,)).fetchone()
    if row is None:
        raise ValueError("Archived task not found")
    return json.loads(row[0])
