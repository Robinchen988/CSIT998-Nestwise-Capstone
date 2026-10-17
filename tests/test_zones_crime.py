import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.amenities import nearby, suburb_stats, zones
from app.amenities.registry import ATTRIBUTES, UNSUPPORTED
from app.core.db import close_pool, get_connection


info = zones.warm_up()
assert info.get("primary", 0) > 500, f"小学学区只有 {info.get('primary')} 个,数据大概没抓全"
assert info.get("secondary", 0) > 100, f"中学学区只有 {info.get('secondary')} 个"

for label, lat, lon in (("Richmond", -37.8197, 144.9976),
                        ("Balwyn", -37.8100, 145.0800),
                        ("Point Cook", -37.9000, 144.7500)):
    z = zones.zone_for(lat, lon)
    assert "primary" in z and "secondary" in z, f"{label} 没落在学区内:{z}"
    assert z["primary"] and z["secondary"]

assert zones.zone_for(None, None) == {}
assert zones.zone_for_batch([], []) == []
assert "无" in zones.describe({}) or "不在" in zones.describe({})

hits = zones.find_zone("Balwyn Primary School", "primary")
assert hits and all(h["level"] == "primary" for h in hits)
assert any("balwyn" in h["school"].lower() for h in hits)
assert all(h["level"] == "secondary" for h in zones.find_zone("Balwyn", "secondary"))
assert zones.find_zone("完全不存在的学校") == []
assert zones.find_zone("") == []

inside = zones.in_zone_batch([-37.8100], [145.0800], zones.find_zone("Balwyn Primary School", "primary"))
assert len(inside) == 1
assert zones.in_zone_batch([-37.81], [145.08], []) == [False]


with get_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT suburb, latitude, longitude FROM properties "
                "WHERE latitude IS NOT NULL ORDER BY id LIMIT 600")
    rows = cur.fetchall()

lats = [r[1] for r in rows]
lons = [r[2] for r in rows]
zone_rows = zones.zone_for_batch(lats, lons)
_, nearest_names = nearby.nearest_batch(lats, lons, "primary_school")

covered = sum(1 for z in zone_rows if z.get("primary"))
assert covered / len(rows) > 0.9, f"只有 {covered}/{len(rows)} 套房落在小学学区内,太低"

same = sum(1 for z, name in zip(zone_rows, nearest_names)
           if z.get("primary") and name
           and z["primary"].lower().split()[0] == name.lower().split()[0])
ratio = same / covered
assert 0.30 < ratio < 0.85, (
    f"「最近的小学」与「所属学区小学」一致率是 {ratio:.0%} —— "
    "实测应在 54% 上下。太高说明两者被算成了同一回事,太低说明匹配有问题")


crime_info = suburb_stats.warm_up()
assert crime_info["suburbs"] > 1000, f"只有 {crime_info['suburbs']} 个区有罪案数据"
assert crime_info["year"]

melbourne = suburb_stats.crime_for("Melbourne")
brighton = suburb_stats.crime_for("Brighton")
assert melbourne and brighton
assert melbourne["rate_per_100k"] > brighton["rate_per_100k"] * 2, (
    f"CBD {melbourne['rate_per_100k']:,.0f} vs Brighton {brighton['rate_per_100k']:,.0f},"
    "两者关系不合常理")
assert melbourne["lga"] and brighton["lga"]

assert suburb_stats.crime_for("完全不存在的区") is None
assert suburb_stats.crime_for(None) is None
assert suburb_stats.crime_for("") is None

text = suburb_stats.describe(melbourne)
assert "LGA" in text, "罪案率的说明里没提粒度"
assert "同区所有房源相同" in text
assert "无" in suburb_stats.describe(None)

with get_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT suburb FROM properties")
    suburbs = [r[0] for r in cur.fetchall()]
missing = [s for s in suburbs if suburb_stats.crime_for(s) is None]
assert not missing, f"这些区查不到罪案数据:{missing[:10]}"


assert "low_crime" in ATTRIBUTES
assert "crime_rate_per_100k" in ATTRIBUTES["low_crime"]["parts"]
assert "police_m" not in ATTRIBUTES["low_crime"]["parts"]
assert "LGA" in ATTRIBUTES["low_crime"]["note"], "属性说明里必须写明粒度"

assert any("街道" in k for k in UNSUPPORTED)
assert all("low_crime" in v or "治安" in v or "犯罪" in v
           for k, v in UNSUPPORTED.items() if "治安" in k or "犯罪" in k)

close_pool()
print("学区归属 + LGA 罪案率 全部通过。")
