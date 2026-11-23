"""Run one task's acceptance checks against this checkout."""
import csv
import io
from pathlib import Path
import sys

from orders import export

ROWS = [("42", 'Ada, "A"'), ("23", "Bo"), ("87", "Line\nbreak")]


def records(data, delimiter):
    return list(csv.reader(io.StringIO(data, newline=""), delimiter=delimiter, strict=True))


def check(task):
    if task == "patch":
        assert export([], set()).splitlines() == ["id,customer"]
    elif task == "export":
        for selected in (set(), {"87", "42"}, {"missing"}):
            data = records(export(ROWS, selected), ";")
            assert data == [["id", "customer"], *[list(row) for row in ROWS if row[0] in selected]]
    elif task == "lookup":
        assert (Path(__file__).parent / "answer.txt").read_text().strip() == "dispatch"
    else:
        raise ValueError(task)


if __name__ == "__main__":
    check(sys.argv[1])
    print("passed")

