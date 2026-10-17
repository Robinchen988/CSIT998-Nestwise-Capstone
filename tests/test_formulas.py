import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analytics.formulas import cap_rate, gross_yield, noi, roi, stamp_duty_vic

assert gross_yield(32760, 780000) == 0.042
assert gross_yield(None, 780000) is None
assert gross_yield(32760, 0) is None
assert noi(32760, None) is None

assert gross_yield(32760, None) is None
assert noi(None, 8000) is None
assert cap_rate(None, 780000) is None
assert roi(None, 100000) is None

assert cap_rate(24760, 0) is None
assert cap_rate(24760, None) is None
assert roi(5000, 0) is None
assert roi(5000, None) is None

assert gross_yield(50000, 1000000) == 0.05
assert cap_rate(40000, 1000000) == 0.04
assert roi(20000, 100000) == 0.2

assert noi(32760, 8000) == 24760
assert cap_rate(noi(32760, 8000), 780000) == 24760 / 780000

oe = None
assert noi(32760, oe) is None
assert cap_rate(noi(32760, oe), 780000) is None

rent, price, opex_rate = 32760, 780000, 0.28
opex = round(rent * opex_rate)
assert opex == 9173
assert noi(rent, opex) == 23587
assert abs(cap_rate(noi(rent, opex), price) - 23587 / 780000) < 1e-12

assert stamp_duty_vic(25_000) == 350
assert stamp_duty_vic(130_000) == 2_870
assert stamp_duty_vic(240_000) == 9_470
assert stamp_duty_vic(960_000) == 52_670
assert stamp_duty_vic(1_300_000) == 71_500
assert stamp_duty_vic(0) is None
assert stamp_duty_vic(None) is None
assert stamp_duty_vic(-5) is None
assert stamp_duty_vic(130_125) == 2_877
assert stamp_duty_vic(130_175) == 2_880
assert stamp_duty_vic(130_126) == 2_878
assert stamp_duty_vic(25_025) == 351
assert stamp_duty_vic(960_010) == 52_801

for boundary in (25_000, 130_000):
    assert stamp_duty_vic(boundary) == stamp_duty_vic(boundary + 1)

assert stamp_duty_vic(240_000) / 240_000 < 0.04
assert abs(stamp_duty_vic(1_300_000) / 1_300_000 - 0.055) < 1e-9
assert round(240_000 * 0.055) - stamp_duty_vic(240_000) > 3_000

total_cost = price + stamp_duty_vic(price) + 2_000
assert roi(noi(rent, opex), total_cost) < cap_rate(noi(rent, opex), price)

print("formulas: all tests passed.")
