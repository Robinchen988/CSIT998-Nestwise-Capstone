import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.amenities import nearby
from app.core.db import get_connection
from app.orchestration import graph as g
from app.search.search import search_properties

with get_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT id, latitude, longitude, distance_cbd FROM properties")
    rows = cur.fetchall()
shown = {i: nearby.distance_to_cbd_km(lat, lon, d) for i, lat, lon, d in rows}
assert any(lat is None for _, lat, _, _ in rows), "库里应有缺坐标的房源,否则 NULL 分支没被测到"

checks = 0
for limit in (2.3, 5, 10, 12.5, 20, 35):
    expect = {i for i, d in shown.items() if d is not None and d <= limit}
    got = {p["id"] for p in search_properties("house", max_distance_cbd_km=limit, limit=100_000)}
    assert got == expect, f"≤ {limit} km: 漏 {len(expect - got)} 套、多 {len(got - expect)} 套"
    checks += 1

assert len(search_properties("house", limit=100_000)) == len(rows)
assert len(search_properties("house", max_distance_cbd_km=None, limit=100_000)) == len(rows)
checks += 2

for raw, want in ((20, 20.0), (12.34, 12.3), (0, None), (-5, None), (True, None), (500, None), ("20", None)):
    assert g._sanitize({"intent": "new_search", "max_distance_cbd_km": raw}, "q")["max_distance_cbd_km"] == want, raw
    checks += 1
from app.amenities import registry
assert "到 CBD 的距离要求" not in registry.UNSUPPORTED and "步行/驾车时间" in registry.UNSUPPORTED
checks += 1

print(f"距 CBD 筛选 全部通过:{checks} 项检查(全库 {len(rows)} 套逐套核对,与页面显示同一口径)")
