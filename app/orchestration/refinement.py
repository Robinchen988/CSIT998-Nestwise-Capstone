from copy import deepcopy
from math import isfinite
from statistics import median

from app.amenities import registry

ATTRS = set(registry.ATTRIBUTE_KEYS)
RELATIVE_FIELDS = ATTRS | {"price", "gross_yield", "cap_rate", "roi", "near_place_distance"}
SCALARS = {"max_price", "min_price", "bedrooms", "bathrooms", "property_type", "suburb", "max_distance_cbd_km",
           "min_gross_yield", "near_place", "school_zone", "semantic_query"}
LIST_KEYS = {"amenity_needs": "kind", "planning_needs": None, "unsupported_asks": None}
AMENITY_KINDS = set(registry.USER_FACING_KINDS)
DEFAULT_AMENITY_M = 1500
NEAREST = "nearest:"

PATCH_PROMPT = """
## 多轮修改协议(优先于前面的 refine 参数继承说明)
新增字段 changes(数组)、clarification(字符串或 null)。
new_search 仍填完整参数; refine 必须只用 changes 列出本轮修改,不要重填旧条件。
new_search 另填 description_query: 无法结构化的房子描述(如 balcony),英文短语;无则 null。
已能用价格/房型/属性/设施表达的要求不要放入 description_query,避免删除后残留。
程序会保留没有修改的条件;旧对话中已删除的要求不能恢复。
changes 每项必须带 source: 用户本轮原话中逐字出现的短语(不翻译、不引用历史)。
每项格式 {"action":"set/remove/relative/prioritize", "field":"...", "source":"...", ...}:
- set: 新增或明确修改。field 为 max_price/min_price/bedrooms/bathrooms/property_type/
  suburb/max_distance_cbd_km/min_gross_yield/near_place/school_zone/semantic_query 或注册表属性(如 quiet)。
  value 使用前述参数结构;属性 value 为 {"min_score":60,"operator":"gte",
  "strength":"preferred/required","value_source":"inferred/explicit"}。
  只有用户明确指定数字才为 explicit;模糊要求的默认门槛是 inferred。
  必须/不能低于用 required,最好/希望用 preferred。不要凭空把偏好升级成必须。
  amenity_needs 的 value 是单项 {kind,max_distance_m,strength}(strength 默认 preferred,
  明说必须/一定/不能超过才 required),planning_needs/unsupported_asks
  的 value 是单个字符串;每次只改对应项。semantic_query 只用于无法结构化的新描述。
- remove: 只在用户明确取消该要求时使用,field 同上;列表字段的 value 指明要删除的项。
  “更/少/没这么/再……一点”不是取消。“学校无所谓”删除 school_access,
  如当前还有 primary_school 距离或 school_zone 条件且用户也放弃,需各列一条删除操作。
- relative: “再/稍微/比这些更/没这么/便宜一点”等相对上一轮的调整。
  field 为属性名、price、gross_yield、cap_rate、roi 或 near_place_distance;
  direction 为 increase/decrease; degree 为 slight/normal/strong。
  一点/稍微 -> slight,更 -> normal,明显/大幅 -> strong。不填分数、不改预算数字,
  不附加该属性的默认门槛,不删除反向偏好,不同时生成同属性的 prioritize。
  例如安静两房近公园后说“再热闹一点”:仅 relative lively increase slight;
  “没必要这么安静”:仅 relative quiet decrease slight;“再便宜一些”:relative price decrease slight。
  仍保留原有安静/预算等边界,需要放宽时交给用户明确选择。
- 具体设施类别(小学、幼儿园、火车站、超市……)+“近”:不论说成“离小学近”“最好离小学近一点”
  “最好小学近一点”“小学近点”,一律是 set amenity_needs(value {kind, max_distance_m},没给距离填 1500)。
  这里的“一点/最好”不是相对调整;只有“离小学越近越好/最近的排前面”才再加 prioritize nearest:<kind>。
  只说“学校/教育配套”而没点出哪类学校,才用 school_access 属性。
- prioritize: “最/越……越好/优先/更重要”改变排序侧重,field 为排序口径或 price。
  price 必须提供 direction increase/decrease。不会删除任何筛选条件。
  “越热闹越好”仅 prioritize lively;“不要安静了,改找最热闹的”是 remove quiet + prioritize lively。
  “还是安静更重要”是 prioritize quiet,不是 relative。
- about_results/concept: changes=[]。询问“第二套是不是更吵”不能触发搜索。
一句话可有多个操作。单纯更换房型/预算仍是 refine;仅明确重新开始或完全独立的需求用 new_search。
已有条件时,若要 new_search,必须提供 reset_source(本轮原话中明确重新开始的逐字短语)。
安静与热闹不是逻辑互斥,不能自动删任何一项,不能把热闹擅自改成便利。
有重要歧义或能力缺口时用 intent="clarify",changes=[],clarification 给一句当前界面语言的具体澄清。
例如要求“白天热闹但晚上不能吵”:现有数据没有昼夜噪声,说明限制并问是否改用整体安静度和生活便利度。
普通“再热闹一点”已有明确属性映射,不要无故追问。没有上一轮结果时相对调整不可凭空编基准。
"""


OPPOSITE = {a: b for pair in registry.CONFLICTS for a, b in (pair, pair[::-1])}
ATTR_STEPS = {"slight": (10, 15), "normal": (15, 25), "strong": (25, 40)}
AUTO_STEP, AUTO_FLOOR = 10, 50
PRICE_HOLD = 5


class ClarifyChange(ValueError):
    pass


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def value_of(row, field):
    if field in ATTRS:
        value = (row.get("context_scores") or {}).get(field)
    elif field == "near_place_distance":
        value = (row.get("near_place") or {}).get("distance_m")
    else:
        value = row.get(field)
    return float(value) if finite(value) else None


def baseline_for(rows, field):
    values = [v for row in rows if (v := value_of(row, field)) is not None]
    return median(values) if values else None


def valid_goals(raw):
    goals = []
    for g in raw if isinstance(raw, list) else []:
        if (isinstance(g, dict) and g.get("field") in RELATIVE_FIELDS
                and g.get("direction") in ("increase", "decrease")
                and g.get("degree") in ("slight", "normal", "strong")
                and finite(g.get("baseline"))):
            goals = [old for old in goals if old["field"] != g["field"]]
            goals.append({k: g[k] for k in ("field", "direction", "degree", "baseline")})
    return goals[:6]


def relaxable_need(needs, goals):
    moving = {OPPOSITE.get(g["field"]) for g in goals or []}
    for need in needs or []:
        if (need.get("attribute") in moving and need.get("value_source") == "inferred"
                and need.get("strength") != "required"
                and need.get("operator", "gte") in ("gte", "gt")
                and finite(need.get("min_score")) and need["min_score"] > AUTO_FLOOR):
            return need
    return None


def relaxed_floor(need):
    return max(AUTO_FLOOR, need["min_score"] - AUTO_STEP)


def snapshot(state):
    return deepcopy({key: state.get(key) for key in (
        "params", "metrics", "more", "batch_offset", "ranking", "properties",
        "place_lookup", "zone_lookup", "search_order", "roi_pool", "assumption_snap")})


def normalize_amenity_changes(changes, params):
    if not isinstance(changes, list):
        return changes
    needs = {n.get("kind"): n for n in params.get("amenity_needs") or [] if isinstance(n, dict)}
    out = []
    for change in changes:
        if not isinstance(change, dict):
            out.append(change)
            continue
        action, field, value = change.get("action"), change.get("field"), change.get("value")
        kind = field if field in AMENITY_KINDS else None
        if field == "amenity_needs" and action in ("relative", "prioritize") and isinstance(value, dict):
            kind = value.get("kind") if value.get("kind") in AMENITY_KINDS else None
        if not kind:
            out.append(change)
            continue
        base = {"source": change.get("source"), "field": "amenity_needs"}
        if action == "remove":
            out.append({**base, "action": "remove", "value": {"kind": kind}})
            continue
        given = value.get("max_distance_m") if isinstance(value, dict) else None
        if isinstance(given, (int, float)) and not isinstance(given, bool) and given > 0:
            dist = int(given)
        elif action == "relative" and kind in needs and needs[kind].get("max_distance_m"):
            dist = max(400, int(round(needs[kind]["max_distance_m"] * 2 / 3 / 100) * 100))
        else:
            dist = (needs.get(kind) or {}).get("max_distance_m") or DEFAULT_AMENITY_M
        strength = (value.get("strength") if isinstance(value, dict) and value.get("strength") in ("required", "preferred")
                    else (needs.get(kind) or {}).get("strength") or "preferred")
        if action != "prioritize" or kind not in needs:
            out.append({**base, "action": "set", "value": {"kind": kind, "max_distance_m": dist, "strength": strength}})
            needs[kind] = {"kind": kind, "max_distance_m": dist, "strength": strength}
        if action == "prioritize":
            out.append({"action": "prioritize", "field": NEAREST + kind, "source": change.get("source")})
    return out


def apply_changes(previous, changes, query, rows, sanitize, sort_fields):
    if not isinstance(changes, list) or not changes or len(changes) > 20:
        raise ClarifyChange("missing changes")
    changes = normalize_amenity_changes(changes, previous)
    params = deepcopy(previous)
    params.pop("_conflict", None)
    goals = []
    relative_now = {c.get("field") for c in changes if isinstance(c, dict) and c.get("action") == "relative"}
    for change in changes:
        if not isinstance(change, dict):
            raise ClarifyChange("invalid operation")
        action, field = change.get("action"), change.get("field")
        if not isinstance(action, str) or not isinstance(field, str):
            raise ClarifyChange("invalid operation field")
        source = change.get("source")
        if (not isinstance(source, str) or not source.strip()
                or source.strip().casefold() not in query.casefold()):
            raise ClarifyChange("missing current-turn evidence")
        if action == "relative":
            if (field not in RELATIVE_FIELDS or change.get("direction") not in ("increase", "decrease")
                    or change.get("degree") not in ("slight", "normal", "strong")):
                raise ClarifyChange("invalid relative target")
            baseline = baseline_for(rows, field)
            if baseline is None:
                raise ClarifyChange("no measured baseline")
            goals = [g for g in goals if g["field"] != field]
            goals.append({"field": field, "direction": change["direction"],
                          "degree": change["degree"], "baseline": baseline})
            params["sort_by"] = None
            continue
        if action == "prioritize":
            if OPPOSITE.get(field) in relative_now:
                continue
            sort = field
            if field == "price":
                if change.get("direction") not in ("increase", "decrease"):
                    raise ClarifyChange("price direction missing")
                sort = "price_asc" if change["direction"] == "decrease" else "price_desc"
            if sort not in sort_fields:
                raise ClarifyChange("unknown sort")
            if sort == "near_place_distance" and not params.get("near_place"):
                raise ClarifyChange("missing named place")
            if sort.startswith("nearest:") and not any(
                    n.get("kind") == sort[len("nearest:"):] for n in params.get("amenity_needs") or []):
                raise ClarifyChange("missing amenity requirement")
            params["sort_by"], goals = sort, []
            continue
        if action not in ("set", "remove"):
            raise ClarifyChange("unknown action")
        value = deepcopy(change.get("value"))
        if field in ATTRS:
            needs = [n for n in params.get("abstract_needs") or [] if n["attribute"] != field]
            if action == "set":
                if (not isinstance(value, dict) or not finite(value.get("min_score"))
                        or not 0 <= value["min_score"] <= 100
                        or value.get("operator", "gte") not in ("gte", "gt", "lte", "lt", "eq")):
                    raise ClarifyChange("invalid score")
                needs.append({**value, "attribute": field})
            params["abstract_needs"] = needs or None
            goals = [g for g in goals if g["field"] != field]
            if action == "remove" and params.get("sort_by") == field:
                params["sort_by"] = None
        elif field in LIST_KEYS:
            key = LIST_KEYS[field]
            if key:
                if not isinstance(value, dict) or not value.get(key):
                    raise ClarifyChange("missing list item")
                items = [n for n in params.get(field) or [] if n[key] != value[key]]
            else:
                if not isinstance(value, str) or not value.strip():
                    raise ClarifyChange("missing list item")
                items = [n for n in params.get(field) or [] if n != value]
            if action == "remove" and len(items) == len(params.get(field) or []):
                raise ClarifyChange("nothing to remove")
            if action == "set":
                items.append(value)
            params[field] = items or None
        elif field in SCALARS:
            if action == "set" and value is None:
                raise ClarifyChange("set requires value")
            if field == "semantic_query" and action == "set":
                if not isinstance(value, str):
                    raise ClarifyChange("invalid description")
                value = "; ".join(filter(None, [params.get("description_query"), value]))
                params["description_query"] = value
            elif field == "semantic_query" and action == "remove":
                terms = [t.strip() for t in (params.get("description_query") or "").split(";") if t.strip()]
                if not isinstance(value, str) or value not in terms:
                    raise ClarifyChange("cannot identify the description to remove")
                params["description_query"] = "; ".join(t for t in terms if t != value) or None
            params[field] = value if action == "set" else None
            if field == "near_place":
                goals = [g for g in goals if g["field"] != "near_place_distance"]
                if action == "remove" and params.get("sort_by") == "near_place_distance":
                    params["sort_by"] = None
        else:
            raise ClarifyChange("unknown field")

        clean = sanitize(params, query)
        check_key = "abstract_needs" if field in ATTRS else field
        if action == "set" and not clean.get(check_key) and clean.get(check_key) != 0:
            raise ClarifyChange("value rejected")
        if field == "amenity_needs" and action == "set":
            if not any(n["kind"] == value["kind"] for n in clean.get(field) or []):
                raise ClarifyChange("unknown amenity")
        if field == "planning_needs" and action == "set" and value not in (clean.get(field) or []):
            raise ClarifyChange("unknown planning need")

    if params.get("min_price") and params.get("max_price") and params["min_price"] > params["max_price"]:
        raise ClarifyChange("inverted budget")
    params["relative_preferences"] = goals or None
    return params


def quantile(values, q):
    values = sorted(values)
    pos = (len(values) - 1) * q
    lo = int(pos)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (pos - lo)


def relative_rank(rows, goals, previous_rows, needs):
    targets = []
    for goal in goals:
        field, baseline = goal["field"], goal["baseline"]
        values = [v for row in rows if (v := value_of(row, field)) is not None]
        if not values:
            return []
        factor = {"slight": .25, "normal": .5, "strong": .8}[goal["degree"]]
        spread = quantile(values, .75) - quantile(values, .25)
        if field in ATTRS:
            low, high = ATTR_STEPS[goal["degree"]]
        else:
            scale = max(abs(baseline), max(abs(v) for v in values) * .05, .0001)
            low, high = scale * factor * .1, scale * factor * .6
        step = max(low, min(high, spread * factor))
        sign = 1 if goal["direction"] == "increase" else -1
        minimum = low if field in ATTRS else 0
        targets.append((field, baseline, sign, step, minimum))

    anchors = []
    moving = {g["field"] for g in goals}
    for need in needs or []:
        field = need["attribute"]
        anchor = baseline_for(previous_rows, field)
        if field not in moving and anchor is not None:
            anchors.append((field, anchor, need.get("operator", "gte")))
    price_anchor = None if "price" in moving else baseline_for(previous_rows, "price")
    selected = []
    for index, row in enumerate(rows):
        cost = 0
        for field, baseline, sign, step, minimum in targets:
            value = value_of(row, field)
            delta = None if value is None else (value - baseline) * sign
            if delta is None or delta <= 0 or delta < minimum or delta > step * 1.5:
                break
            cost += abs(delta - step) / step
        else:
            for field, anchor, op in anchors:
                value = value_of(row, field)
                if value is not None:
                    loss = (anchor - value if op in ("gte", "gt") else
                            value - anchor if op in ("lte", "lt") else abs(value - anchor))
                    cost += max(0, loss) / 10
            price = value_of(row, "price")
            if price_anchor and price is not None:
                cost += abs(price - price_anchor) / price_anchor * PRICE_HOLD
            selected.append((cost, index, row))
    return [row for _, _, row in sorted(selected, key=lambda x: (x[0], x[1]))]
