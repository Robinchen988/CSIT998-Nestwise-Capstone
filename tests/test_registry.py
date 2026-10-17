import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.amenities.registry import (
    ATTRIBUTES, BY_TYPE_KEYS, CONFLICTS, PROPERTY_EVIDENCE, RESAMPLE_M, SOURCES,
    SUBURB_EVIDENCE, UNSUPPORTED, USER_FACING_KINDS, all_evidence_keys, amenity_kinds_block,
    evidence_key, evidence_label, prompt_block, unsupported_block, used_evidence_keys,
)


REQUIRED = {"match", "zh", "geometry", "measure", "user_facing"}
for kind, source in SOURCES.items():
    missing = REQUIRED - set(source)
    assert not missing, f"数据源 {kind} 缺字段:{missing}"
    assert source["geometry"] in ("point", "line", "area"), kind
    assert source["measure"] in ("nearest", "count"), kind
    if source["measure"] == "count":
        assert source.get("radius_m", 0) > 0, f"{kind} 是密度类却没给半径"
    key, values = source["match"]
    assert values == "*" or (isinstance(values, tuple) and values), f"{kind} 的 match 取值有问题"

assert 20 <= RESAMPLE_M <= 100, "重采样间距不合理"

keys = [evidence_key(k) for k in SOURCES]
assert len(keys) == len(set(keys)), "两个数据源产出了同一个证据键"
assert set(all_evidence_keys()) == set(keys) | set(PROPERTY_EVIDENCE) | set(SUBURB_EVIDENCE)

for key in all_evidence_keys():
    label, unit = evidence_label(key)
    assert label and label != key, f"证据 {key} 没有中文标签"
    assert unit, f"证据 {key} 没有单位"


used = used_evidence_keys()
known = set(all_evidence_keys()) | {"bedrooms_many"}

for name, spec in ATTRIBUTES.items():
    assert spec.get("zh"), f"属性 {name} 没有中文名"
    assert spec.get("note"), f"属性 {name} 没有说明 —— 说不清依据什么的属性不该存在"
    assert spec.get("synonyms"), f"属性 {name} 没有同义词,LLM 映射不上"
    assert spec.get("parts"), f"属性 {name} 没有任何证据依据"

    total = 0.0
    for key, value in spec["parts"].items():
        assert key in known, f"属性 {name} 引用了不存在的证据 {key} —— 它会永远算不出分"
        direction, weight = value
        assert direction in ("near", "far", "many", "few"), f"{name}.{key} 方向 {direction} 非法"
        assert 0 < weight <= 1, f"{name}.{key} 权重 {weight} 不合理"
        total += weight
    assert abs(total - 1.0) < 1e-9, f"属性 {name} 的权重和是 {total:.3f},应为 1"

for kind, source in SOURCES.items():
    count = source.get("count")
    if count is not None and evidence_key(kind) in used:
        assert count >= 50, (
            f"{kind} 只有 {count} 个却被用于打分 —— 分位数会全是噪声。"
            "要么别用它打分,要么换个数据源。")

for key, meta in SUBURB_EVIDENCE.items():
    assert "LGA" in meta["zh"] or "区" in meta["zh"], f"{key} 的标签没体现粒度"

waste = [k for k in SOURCES
         if evidence_key(k) not in used and not SOURCES[k]["user_facing"]]
assert not waste, f"这些源既不参与打分也不对用户可见,要么用起来要么删掉:{waste}"

seen = {}
for name, spec in ATTRIBUTES.items():
    for word in spec["synonyms"]:
        assert word not in seen, f"同义词「{word}」同时属于 {seen[word]} 和 {name}"
        seen[word] = name

for a, b in CONFLICTS:
    assert a in ATTRIBUTES and b in ATTRIBUTES, f"互斥对 {a}/{b} 引用了不存在的属性"


assert len(UNSUPPORTED) >= 8, "明确列出'没有这项数据'的类别太少"
assert all(v.strip() for v in UNSUPPORTED.values()), "每一项都要写清为什么没有"
assert not (set(UNSUPPORTED) & {s["zh"] for s in ATTRIBUTES.values()})
assert any("治安" in k for k in UNSUPPORTED)
assert "police" in SOURCES, "警察局位置本身是可以显示的事实,只是不能当治安指标"


attrs_block = prompt_block()
for name, spec in ATTRIBUTES.items():
    assert name in attrs_block and spec["zh"] in attrs_block, f"{name} 没出现在生成的提示词里"
    assert spec["synonyms"][0] in attrs_block

kinds_block = amenity_kinds_block()
for kind in USER_FACING_KINDS:
    assert kind in kinds_block, f"{kind} 没出现在设施清单里"
for kind, source in SOURCES.items():
    if not source["user_facing"]:
        assert f"{kind:<18}" not in kinds_block, f"{kind} 不该出现在给用户的设施清单里"

unsup_block = unsupported_block()
for name in UNSUPPORTED:
    assert name in unsup_block

assert set(BY_TYPE_KEYS) <= set(all_evidence_keys())

from app import i18n as _i18n

_missing = _i18n.check()
assert not _missing, "英文标签缺这些键(见 app/i18n.py):\n  " + "\n  ".join(_missing)

print(f"注册表自洽 —— {len(SOURCES)} 个数据源 · {len(ATTRIBUTES)} 个属性 · "
      f"{len(UNSUPPORTED)} 类明确不支持 · 英文标签无缺口,全部通过。")
