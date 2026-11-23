"""Fictional order export used by the paired example."""
import csv
import io
import json
from pathlib import Path

ROOT = Path(__file__).parent


def export(rows, selected):
    options = json.loads((ROOT / "options.json").read_text())
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=options["delimiter"])
    writer.writerow(("id", "customer"))
    for order_id, customer in rows:
        if options["scope"] == "selected" and order_id not in selected:
            continue
        if options["encoding"] == "csv":
            writer.writerow((order_id, customer))
        else:
            output.write(options["delimiter"].join((order_id, customer)) + "\n")
    return output.getvalue()

