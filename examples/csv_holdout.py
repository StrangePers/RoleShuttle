"""Held-out examples for the two-setting CSV repair. Run outside the checkout."""
import csv
import io
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from orders import export

samples = [("x", 'Alfa; "Beta"'), ("y", "First\r\nSecond"), ("z","Клиент"), ("empty-name","")]
cases = [("empty source", [], []), ("empty choice", samples, []),
         ("unknown", samples, ["unknown"]), ("preserve order", samples, ["z","x"]),
         ("newline", samples, ["y"]), ("all", samples, [r[0] for r in samples]),
         ("repeated selection", samples, ["x","x"])]
for name, rows, selected in cases:
    actual=list(csv.reader(io.StringIO(export(rows,set(selected)),newline=""),delimiter=";",strict=True))
    expected=[["id","customer"],*[list(row) for row in rows if row[0] in selected]]
    assert actual==expected,name
print(f"{len(cases)}/{len(cases)} held-out cases passed")
