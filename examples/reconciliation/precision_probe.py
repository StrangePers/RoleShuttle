"""Post-hoc diagnostic, separate from the original 21 hidden groups."""
import json
import sys
from decimal import localcontext
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from importer import reconcile

cases = [
    ("default precision, long amount", 28, "12345678901234567890123456789.12"),
    ("application precision, ordinary amount", 6, "12345.67"),
]
results = {}
for label, precision, amount in cases:
    with localcontext() as context:
        context.prec = precision
        got = reconcile([{"id":"a", "seq":1, "state":"paid", "total":amount}], [])
    expected = {"north/a":int(amount.replace(".", ""))}
    results[label] = {"passed":got == expected, "expected":expected, "observed":got}
print(json.dumps(results))
raise SystemExit(0 if all(r["passed"] for r in results.values()) else 1)
