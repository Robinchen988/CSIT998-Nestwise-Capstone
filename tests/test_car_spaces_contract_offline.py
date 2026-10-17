import ast
import builtins
import math
import re
import sys
from pathlib import Path

import pandas

WORK = Path(__file__).resolve().parents[1]
failures = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


class _Stub:
    def __getattr__(self, n): return _Stub()
    def __call__(self, *a, **k): return _Stub()


tree = ast.parse((WORK / "pipeline/load_properties.py").read_text(encoding="utf-8"))
top = {}
for n in tree.body:
    if isinstance(n, ast.FunctionDef):
        top[n.name] = n
    elif isinstance(n, ast.Assign):
        for t in n.targets:
            if isinstance(t, ast.Name):
                top[t.id] = n
ns = {"__builtins__": builtins, "math": math, "pd": pandas}
for nm in ("TYPE_LABEL", "to_pynone", "row_to_record"):
    exec(compile(ast.Module([top[nm]], []), "load_properties.py", "exec"), ns)
rec = ns["row_to_record"]
Row = type("Row", (), {})


def make(**kw):
    base = dict(Suburb="A", Address="1 X St", Type="h", Price=800000.0, Bedroom2=3.0, Bathroom=1.0, Car=1.0, Landsize=300.0,
                BuildingArea=float("nan"), YearBuilt=float("nan"), Distance=5.0, Lattitude=-37.8, Longtitude=145.0,
                annual_rent=20800.0, description="d", Date="15/03/2017", rent_source="precinct_exact_sheet")
    base.update(kw)
    r = Row()
    r.__dict__.update(base)
    return r


check(rec(make(Car=float("nan")))["car_spaces"] is None, "missing Car -> car_spaces None (unknown), NOT 0")
check(rec(make(Car=0.0))["car_spaces"] == 0 and rec(make(Car=0.0))["car_spaces"] is not None, "a recorded Car of 0 stays 0 (genuinely no car space)")
check(rec(make(Car=2.0))["car_spaces"] == 2, "a recorded Car of 2 stays 2")

schema = (WORK / "db/schema.sql").read_text(encoding="utf-8")
line = next(l for l in schema.splitlines() if re.match(r"\s*car_spaces\b", l))
check("NOT NULL" not in line.upper() and "DEFAULT 0" not in line.upper(), f"schema.sql car_spaces column is nullable with no default-0 (got: {line.strip()})")

mig = WORK / "db/migrate_car_spaces_nullable.sql"
text = mig.read_text(encoding="utf-8") if mig.exists() else ""
check("DROP NOT NULL" in text.upper() and "DROP DEFAULT" in text.upper(), "migration note exists and relaxes the existing column (ALTER ... DROP NOT NULL / DROP DEFAULT)")
check(not re.search(r"\bUPDATE\b", text, re.I) and re.search(r"reload|重新导入|re-?import", text, re.I),
      "migration does NOT guess which existing 0s were unknown (no UPDATE) and says a reload + retrain is required")
print("FAILED: %d" % len(failures) if failures else "ALL PASSED")
sys.exit(1 if failures else 0)
