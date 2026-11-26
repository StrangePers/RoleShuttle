"""Publish the desktop companion snapshot from private RoleShuttle data."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from threading import Event, Thread

ROOT = Path(__file__).resolve().parent
config = json.loads((ROOT/"desktop.json").read_bytes())
os.environ.update(ROLESHUTTLE_HOME=config["profile"], ROLESHUTTLE_RUNTIME_ARGV="")
from roleshuttle.companion import publish, task_state
import sqlite3
from contextlib import closing
from roleshuttle.r28_archive import list_archive
from roleshuttle.stages import stage_items

stop=Event()
Thread(target=lambda:(os.read(sys.stdin.fileno(), 1),stop.set()),daemon=True).start()
while not stop.is_set():
    now=datetime.now(timezone.utc)
    stamp=now.isoformat()
    rows=list(stage_items(now))
    # Terminal records keep their transition time. A live publisher heartbeat
    # must not make yesterday's failure active again.
    database = Path(config["profile"]) / "local-tasks.sqlite3"
    if database.is_file():
        with closing(sqlite3.connect(database.as_uri()+"?mode=ro", uri=True)) as db:
            recent = db.execute("SELECT record FROM tasks ORDER BY rowid DESC LIMIT 200").fetchall()
        for index, (encoded,) in enumerate(recent):
            record=json.loads(encoded)
            status=record["status"]
            if index >= 4 and status not in {"running","waiting"}:
                continue
            terminal=status in {"accepted","completed","cancelled","closed_manually","failed","rejected"}
            updated=record.get("updated_at") if terminal else stamp
            if terminal and not updated:
                continue
            if status in {"failed","rejected"} and (now-datetime.fromisoformat(updated)).total_seconds()>1200:
                continue
            if status in {"accepted","completed","cancelled","closed_manually"}:
                state="review"
            elif status=="waiting":
                state="waiting"
            else:
                state=task_state(record)
            rows.append({"id":record["task_id"],"source":"roleshuttle","title":record["contract"]["title"],
                         "state":"review" if state=="idle" else state,"updatedAt":updated})
    for record in list_archive()["items"][:3]:
        rows.append({"id":record["task_id"],"source":"agent-console","title":record["title"],
                     "state":"review","updatedAt":stamp})
    priority={"waiting":0,"running":1,"failed":2,"review":3,"idle":4}
    rows.sort(key=lambda row:priority.get(row["state"],5))
    publish(rows[0]["state"] if rows else "idle",rows[:8],observed=stamp)
    stop.wait(2)
