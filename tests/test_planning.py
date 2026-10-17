import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections import Counter

from app.amenities import planning
from app.core.db import close_pool, get_connection
from app.orchestration.graph import _PLANNING_NEEDS, PARAM_KEYS, RANK_KEYS


info = planning.warm_up()
assert info.get("zone", 0) > 20_000, f"分区只有 {info.get('zone')} 个,数据大概没抓全"
assert info.get("overlay", 0) > 90_000, f"叠加层只有 {info.get('overlay')} 个"


assert planning._family("GRZ3")[2] == "medium"
assert planning._family("NRZ1")[2] == "low"
assert planning._family("RGZ1")[2] == "high"
assert planning._family("PPRZ")[2] == "open"
assert planning._family("HCTZ2")[2] == "high"
assert planning._family("IN1Z")[0] == "IN1Z"
assert planning._family("IN1Z")[2] == "industrial"
assert planning._family("R1Z")[2] == "medium"
assert planning._family("B1Z")[2] == "high"
assert planning._family("PCRZ")[2] == "open"
assert planning._family("PUZ6")[2] == "public"
assert planning._family("PZ")[2] == "industrial"
assert planning._family("ZZZ9")[2] == "unknown"
assert planning._family(None)[2] == "unknown"

assert planning._overlay_family("HO315")[2] == "build"
assert planning._overlay_family("PAO1")[2] == "risk"
assert planning._overlay_family("DCPO1")[2] == "cost"
assert planning._overlay_family("AEO")[1].startswith("机场环境")
assert planning._overlay_family("EAO")[1].startswith("须做环境审计")
assert planning._overlay_family("MAEO1")[1].startswith("墨尔本机场")
assert planning._overlay_family("RFO")[0] == "RFO"
assert planning._overlay_family("RXO")[0] == "RXO"


richmond = planning.for_one(-37.8206, 145.0003)
assert richmond.get("zone"), f"Richmond 查不到分区:{richmond}"
assert richmond.get("nearby_total", 0) > 5, "Richmond 周边分区一块都没查到,空间查询有问题"

assert planning.for_one(None, None) == {}
assert planning.for_batch([], []) == []
assert "无" in planning.describe({}) or "无规划" in planning.describe({})

assert planning.can_redevelop({}) is None
assert planning.can_redevelop({"zone": "GRZ1"}) is True
assert planning.can_redevelop(
    {"zone": "GRZ1", "overlays": [{"code": "HO1", "effect": "build"}]}) is False
assert planning.can_redevelop(
    {"zone": "GRZ1", "overlays": [{"code": "PAO1", "effect": "risk"}]}) is True
assert planning.risks({}) == []

text = planning.describe(richmond)
assert "方形" in text, f"describe 没说明是方形范围:{text}"
assert "分区" in text


with get_connection() as conn:
    cur = conn.cursor()
    cur.execute("SELECT latitude, longitude, suburb FROM properties "
                "WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
    rows = cur.fetchall()

lats = [r[0] for r in rows]
lons = [r[1] for r in rows]
result = planning.for_batch(lats, lons)
assert len(result) == len(rows)

covered = sum(1 for r in result if r.get("zone"))
assert covered / len(rows) > 0.99, f"只有 {covered}/{len(rows)} 套房查到分区,太低"

density = Counter(r.get("density") for r in result)
unknown_share = density.get("unknown", 0) / len(rows)
worst = Counter(r["zone"] for r in result if r.get("density") == "unknown").most_common(5)
assert unknown_share < 0.02, (
    f"{unknown_share:.1%} 的房源落在**未分类**的分区上,最常见的是 {worst} —— "
    "官方多半新增了分区代码。去 app/amenities/planning.py 的 _ZONE_FAMILIES "
    "把它们加上,否则所有基于 density 的筛选会静默失灵。")

top_share = max(density.values()) / len(rows)
assert top_share < 0.75, (
    f"单一 density 档位占了 {top_share:.0%},这个指标区分不出房源:{density.most_common()}")
assert density.get("low", 0) > 1000 and density.get("high", 0) > 500, (
    f"低密度或高密度的房源太少,分类多半有问题:{density.most_common()}")

from app.amenities import planning as _planning
_category = {"no_" + k: fams for k, (_, fams) in _planning.RISK_GROUPS.items()}
for need, (zh, predicate) in _PLANNING_NEEDS.items():
    hits = sum(1 for r in result if predicate(r))
    share = hits / len(rows)
    if need in _category:
        fams = _category[need]
        expect = sum(1 for r in result if "zone" in r and not any(o["family"] in fams for o in r.get("overlays", [])))
        assert hits == expect and 0 < len(rows) - hits < len(rows), (
            f"「{need}」剔除了 {len(rows) - hits} 套,应恰好剔除带 {fams} 叠加层或无规划数据的房源({zh})")
        continue
    assert 0.02 < share < 0.98, (
        f"筛选条件「{need}」命中 {share:.1%} 的房源 —— 接近恒真或恒假,"
        f"等于没筛。({zh})")

with_overlay = sum(1 for r in result if r.get("overlays"))
assert with_overlay / len(rows) > 0.3, "有叠加层的房源不到三成,叠加层大概没接上"
heritage = sum(1 for r in result if any(o["family"] == "HO" for o in r.get("overlays", [])))
assert heritage > 100, f"只有 {heritage} 套落在历史保护叠加层里,墨尔本不该这么少"


assert "planning_needs" in RANK_KEYS
assert "planning_needs" in PARAM_KEYS

import json

from app.orchestration.graph import _PARSE_SYSTEM, PROGRAM_KEYS

examples = [line for line in _PARSE_SYSTEM.splitlines()
            if line.startswith('{"intent"')]
assert len(examples) >= 10, f"提示词里只找到 {len(examples)} 个示例,是不是被改坏了"
model_keys = set(PARAM_KEYS) - set(PROGRAM_KEYS)
refine_examples = []
for line in examples:
    parsed = json.loads(line)
    if parsed["intent"] == "refine":
        assert set(parsed) == {"intent", "changes", "clarification"}, (
            f"refine 示例只能有 intent/changes/clarification,实际:{sorted(parsed)}\n  {line[:120]}")
        assert parsed["changes"] and all({"action", "field", "source"} <= set(c) for c in parsed["changes"]), (
            f"refine 示例的每条 change 都要有 action/field/source:{line[:120]}")
        refine_examples.append(parsed)
        continue
    fields = set(parsed) - {"intent"}
    assert fields == model_keys, (
        f"示例的字段和 PARAM_KEYS 对不上:\n  多了 {fields - model_keys}\n"
        f"  少了 {model_keys - fields}\n  {line[:120]}")
    for value in parsed["planning_needs"]:
        assert value in _PLANNING_NEEDS, f"示例里用了词表外的 planning_needs:{value}"
assert refine_examples, "提示词里没有 refine 示例了 —— 模型会不知道多轮修改该输出 changes"
examples = [line for line in examples if json.loads(line)["intent"] != "refine"]

assert any(json.loads(line)["description_query"] for line in examples), (
    "没有一个示例真的用到 description_query —— 模型会学成永远填 null")

assert any(json.loads(line)["planning_needs"] for line in examples), (
    "没有一个示例真的用到 planning_needs —— 模型会学成永远填空数组")

from app.orchestration.graph import _sanitize

cleaned = _sanitize({"planning_needs": ["no_heritage", "no_such_thing", "no_heritage"]}, "")
assert cleaned["planning_needs"] == ["no_heritage"], cleaned["planning_needs"]
assert _sanitize({"planning_needs": []}, "")["planning_needs"] is None

close_pool()
print(f"规划分区 全部通过。未分类 {unknown_share:.2%},"
      f"density 分布 {dict(density.most_common(4))}")
