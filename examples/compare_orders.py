"""Compare two local model routes on disposable copies of the orders fixture."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from prepare_orders import prepare
from roleshuttle.local import consult, delegate


TASKS = {
    "patch": "Change only the delimiter to a comma. Preserve other options.",
    "export": "Fix the export of selected orders. Preserve input order and the delimiter. "
              "Names with quotes and newlines must round-trip. Empty selections give only the header.",
    "lookup": "Which team owns selected-order exports under the active policy? Answer with the team name.",
}


def task_input(root, task):
    if task == "lookup":
        cards = sorted((root / "policy").glob("*.md"))
        return TASKS[task] + "\n" + "\n".join(p.name + ": " + p.read_text().strip() for p in cards)
    return (TASKS[task] + "\norders.py:\n" + (root / "orders.py").read_text()
            + "\nCurrent options.json:\n" + (root / "options.json").read_text())


def apply_answer(root, task, text):
    text = text.strip()
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1] == "```":
        text = "\n".join(lines[1:-1]).strip()
    if task == "lookup":
        (root / "answer.txt").write_text(text, encoding="utf-8")
        return
    data = json.loads(text)
    original = json.loads((root / "options.json").read_text())
    required = {"scope", "encoding"} if task == "export" else {"delimiter"}
    if not isinstance(data, dict) or data.keys() != original.keys():
        raise ValueError("Expected the three existing options")
    for key in data:
        if not isinstance(data[key], str) or (key not in required and data[key] != original[key]):
            raise ValueError("Changed an out-of-scope option or type")
    (root / "options.json").write_text(json.dumps(data) + "\n", encoding="utf-8")


def run(root, task, mode, roles, chat, limit):
    base = prepare(root)
    prompt = task_input(root, task)
    instruction = ("Reply with only the team name." if task == "lookup"
                   else "Reply with only the complete replacement options.json object.")
    started = time.monotonic()
    record = {"task": task, "mode": mode, "base": base, "calls": [], "passed": False,
              "retries": 0}
    try:
        if mode == "routed":
            if task == "export":
                report = consult(roles, prompt + "\nGive the option values and one check in at most 35 words.",
                                 max_output_tokens=limit)
                record["calls"].extend(report["calls"])
                if report["status"] != "returned":
                    raise ValueError(report["status"])
            else:
                worker = delegate(roles, "research", prompt, max_output_tokens=limit)
                record["calls"].append(worker)
                if worker["done_reason"] != "stop":
                    raise ValueError("research_incomplete")
            if task == "lookup":
                prompt = TASKS[task]
            prompt += "\nWorker advice (verify):\n" + "\n".join(c["result"] for c in record["calls"])
        response = delegate(chat, "implement", prompt + "\n" + instruction, max_output_tokens=limit)
        response["role"] = "chat"
        record["calls"].append(response)
        if response["done_reason"] != "stop":
            raise ValueError("chat_incomplete")
        apply_answer(root, task, response["result"])
        checked = subprocess.run([sys.executable, "-B", "checks.py", task], cwd=root, capture_output=True)
        record["passed"] = checked.returncode == 0
        if not record["passed"]:
            record["error"] = "acceptance_check_failed"
    except (ValueError, OSError) as exc:
        record["error"] = str(exc)
    record["seconds"] = round(time.monotonic() - started, 2)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--chat-model", required=True)
    parser.add_argument("--output", type=Path, required=True, help="New local results directory")
    parser.add_argument("--max-output-tokens", type=int, default=192)
    parser.add_argument("--routed-first", action="store_true")
    args = parser.parse_args()
    args.output.mkdir()
    profile = json.loads(args.profile.read_text(encoding="utf-8-sig"))
    chat = args.output / "chat.json"
    chat.write_text(json.dumps({"port": profile.get("port", 11434), "roles": {"implement": args.chat_model}}))
    modes = ["routed", "direct"] if args.routed_first else ["direct", "routed"]
    for task in TASKS:
        for mode in modes:
            name = task + "-" + mode
            result = run(args.output / name, task, mode, args.profile, chat, args.max_output_tokens)
            (args.output / (name + ".json")).write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(name, result["passed"], result["seconds"], result.get("error", ""), flush=True)
        modes.reverse()


if __name__ == "__main__":
    main()
