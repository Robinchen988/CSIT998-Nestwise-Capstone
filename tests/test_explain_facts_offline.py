import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.orchestration import graph as g

ROOT = Path(__file__).resolve().parents[1]
checks = 0

base = {"suburb": "Dallas", "address": "1 Test St", "price": 500_000, "bedrooms": 3, "bathrooms": 1,
        "property_type": "house", "gross_yield": 0.04, "latitude": -37.6, "longitude": 144.9, "id": 42,
        "car_spaces": 2, "distance_cbd": 15.0}
metrics = [{**base, "land_size": 588.0, "building_area": 94.0},
           {**base, "land_size": 0, "building_area": None},
           {**base, "land_size": None, "building_area": 0.0}]

seen = {}
def fake_stream(system, user, temperature=0.0):
    seen["system"], seen["user"] = system, user
    return "ok"
g._ask_streaming = fake_stream
g.explain({"metrics": metrics, "params": {}, "user_query": "q", "lang": "zh", "intent": "new_search",
           "ranking": "按语义相关度排序", "batch_offset": 0})
facts = json.loads(seen["user"].split("已算好的房源数据:\n", 1)[1])

for f in facts:
    for hidden in ("car_spaces", "latitude", "longitude", "id"):
        assert hidden not in f, f"{hidden} 不该交给模型"
        checks += 1
assert (facts[0]["land_size"], facts[0]["building_area"]) == (588.0, 94.0)
assert (facts[1]["land_size"], facts[1]["building_area"]) == (None, None), "0 是没记录,要当缺失"
assert (facts[2]["land_size"], facts[2]["building_area"]) == (None, None)
assert [f["display_no"] for f in facts] == [1, 2, 3]
checks += 4
assert "提到排序方式时只能照这条口径说" in seen["system"]
assert "null 表示**没有记录**" in seen["system"]
assert "本次结果的排序口径:按语义相关度排序" in seen["user"]
checks += 3

app_js = (ROOT / "app/web/app.js").read_text(encoding="utf-8")
assert re.search(r"T\.landArea\(area\(m\.land_size\)\)", app_js) and re.search(r"T\.buildingArea\(area\(m\.building_area\)\)", app_js)
assert "v > 0" in app_js.split("const area = v =>", 1)[1].split("\n", 1)[0]
i18n_js = (ROOT / "app/web/i18n.js").read_text(encoding="utf-8")
assert i18n_js.count("landArea:") == 2 and i18n_js.count("buildingArea:") == 2, "中英文都要有"
checks += 3

print(f"说明事实与页面一致 全部通过:{checks} 项检查(离线,不调用 LLM)")
