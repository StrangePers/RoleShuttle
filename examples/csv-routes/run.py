"""Repeat the nine live CSV trials using the checkout's Codex subscription adapter."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

EXAMPLES = Path(__file__).resolve().parents[1]
LUNA = "gpt-6-luna"
ASTRA = "gpt-6.2-astra"
TASK = 'The exporter supports scope values "all" or "selected" and encoding values "plain" or "csv". Export selected rows only. Names with quotes, semicolons or newlines must survive parsing as one field. Keep the current delimiter. Initial settings: {"scope":"all","encoding":"plain","delimiter":";"}. Return just the corrected JSON object as your text, with no Markdown.'
REVIEW = 'Independently review the proposed JSON settings against the original requirements and exporter source. Start your result text with ACCEPT or REVISE and give a concrete edge case. Do not run anything.'
ORDER = [("luna", "astra", "reviewed"), ("astra", "reviewed", "luna"), ("reviewed", "luna", "astra")]


def call(attempt, role, model, prompt, work):
    command = [sys.executable, "-B", "-m", "roleshuttle", "codex-stage", "--role", role,
               "--model", model, "--effort", "low" if model == LUNA else "medium", "--input", "-"]
    env = os.environ.copy()
    env.pop("ROLESHUTTLE_RUNTIME_ARGV", None)
    env["PYTHONPATH"] = str(EXAMPLES.parent / "src")
    started = time.monotonic()
    record = {"role": role, "model": model}
    attempt["calls"].append(record)
    try:
        reply = subprocess.run(command, input=prompt.encode(), capture_output=True, cwd=work, env=env, timeout=210)
    finally:
        record["wall_s"] = round(time.monotonic() - started, 4)
    record["exit"] = reply.returncode
    if reply.returncode:
        raise RuntimeError("Codex stage failed. Its token usage is unknown.")
    receipt = json.loads(reply.stdout)
    record.update(receipt)
    if receipt["status"] != "returned":
        raise ValueError("Incomplete proposal")
    return receipt["result"]


def passes(work):
    command = [sys.executable, "-I", "-B", EXAMPLES / "csv_holdout.py", work]
    return subprocess.run(command, capture_output=True).returncode == 0


def run(route, attempt, output):
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        work = Path(tmp) / "orders"
        shutil.copytree(EXAMPLES / "orders", work)
        if passes(work):
            raise RuntimeError("The original fixture should fail its check")
        started = time.monotonic()
        try:
            model = ASTRA if route == "astra" else LUNA
            proposal = json.loads(call(attempt, "implementer", model, TASK, output))
            if not isinstance(proposal, dict) or set(proposal) != {"scope", "encoding", "delimiter"}:
                raise ValueError("Unexpected settings")
            approved = True
            if route == "reviewed":
                material = {"requirements": TASK, "proposal": proposal,
                            "exporter_source": (work / "orders.py").read_text(), "request": REVIEW}
                verdict = call(attempt, "reviewer", ASTRA, json.dumps(material), output)
                approved = verdict.strip().split(maxsplit=1)[0].strip(".:").upper() == "ACCEPT"
            (work / "options.json").write_text(json.dumps(proposal))
            attempt["gate_passed"] = passes(work)
            attempt["accepted"] = attempt["gate_passed"] and approved
        except ValueError as exc:
            attempt["failure_type"] = type(exc).__name__
        finally:
            attempt["wall_s"] = round(time.monotonic() - started, 4)
            filename = f"{attempt['round']}-{route}.json"
            (output / filename).write_text(json.dumps(attempt, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory for local receipts")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    for number, routes in enumerate(ORDER, 1):
        for position, name in enumerate(routes, 1):
            attempt = {"round": number, "position": position, "route": name, "calls": [], "accepted": False}
            run(name, attempt, output)
            print(number, name, attempt["accepted"], attempt["wall_s"], flush=True)
