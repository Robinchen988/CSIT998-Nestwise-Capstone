import re


_GAP_SAME = 0.10


def _land(m):
    v = m.get("land_size")
    suspect = (m.get("area_suspect") or {}).get("land")
    return float(v) if isinstance(v, (int, float)) and v > 0 and not suspect else None


def _num(m, key):
    v = m.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _score(m, attr):
    v = (m.get("context_scores") or {}).get(attr)
    return float(v) if isinstance(v, (int, float)) else None


_FIELDS = {
    "price": lambda m: _num(m, "price"),
    "cbd": lambda m: _num(m, "distance_cbd"),
    "land": _land,
    "yield": lambda m: _num(m, "gross_yield"),
    "quiet": lambda m: _score(m, "quiet"),
}


def _extremes(rows, field):
    values = {r["display_no"]: v for r in rows if (v := _FIELDS[field](r)) is not None}
    if len(values) < 2:
        return None
    lo, hi = min(values.values()), max(values.values())
    tol = 1e-9 if field != "cbd" else 0.05
    return {"min": {k for k, v in values.items() if v - lo <= tol},
            "max": {k for k, v in values.items() if hi - v <= tol}, "values": values}


def _nos(nos, en):
    nos = sorted(nos)
    if en:
        return ("Property " if len(nos) == 1 else "Properties ") + ", ".join(map(str, nos))
    return "第 " + "、".join(map(str, nos)) + " 套"


def comparison_facts(rows, en=False):
    lines = []
    fmt = {"price": lambda v: f"${v:,.0f}", "cbd": lambda v: f"{v:.1f} km",
           "land": lambda v: f"{v:,.0f} m²", "yield": lambda v: f"{v * 100:.2f}%", "quiet": lambda v: f"{v:.0f}"}
    names = {"price": ("价格", "price"), "cbd": ("离 CBD 距离", "distance to the CBD"),
             "land": ("土地面积", "land size"), "yield": ("毛租金回报率", "gross yield"),
             "quiet": ("安静评分", "quiet score")}
    words = {"price": (("最低", "最高"), ("lowest", "highest")), "cbd": (("最近", "最远"), ("closest", "furthest")),
             "land": (("最小", "最大"), ("smallest", "largest")), "yield": (("最低", "最高"), ("lowest", "highest")),
             "quiet": (("最低", "最高"), ("lowest", "highest"))}
    for field in _FIELDS:
        ext = _extremes(rows, field)
        if not ext:
            continue
        (zlo, zhi), (elo, ehi) = words[field]
        v = ext["values"]
        lo_v, hi_v = v[min(ext["min"])], v[min(ext["max"])]
        if en:
            lines.append(f"{names[field][1].capitalize()}: {elo} {_nos(ext['min'], True)} ({fmt[field](lo_v)}); "
                         f"{ehi} {_nos(ext['max'], True)} ({fmt[field](hi_v)})")
        else:
            lines.append(f"{names[field][0]}{zlo}:{_nos(ext['min'], False)}({fmt[field](lo_v)});"
                         f"{zhi}:{_nos(ext['max'], False)}({fmt[field](hi_v)})")
    land_rec = sorted(r["display_no"] for r in rows if _land(r) is not None)
    total = len(rows)
    if en:
        lines.append(f"Land size recorded for {len(land_rec)} of {total}: " + (_nos(land_rec, True) if land_rec else "none"))
    else:
        lines.append(f"{total} 套中有土地面积记录的 {len(land_rec)} 套:" + (_nos(land_rec, False) if land_rec else "无"))
    for r in rows:
        gap = _num(r, "predicted_gap")
        if gap is None:
            continue
        no = r["display_no"]
        if abs(gap) < _GAP_SAME:
            text = (f"Property {no}: estimate roughly in line with the sale price ({gap * 100:+.0f}%)" if en
                    else f"第 {no} 套:估值与售价基本相符({gap * 100:+.0f}%)")
        elif gap > 0:
            text = (f"Property {no}: estimate ABOVE the sale price by {gap * 100:.0f}%" if en
                    else f"第 {no} 套:估值**高于**售价 {gap * 100:.0f}%")
        else:
            text = (f"Property {no}: estimate BELOW the sale price by {-gap * 100:.0f}%" if en
                    else f"第 {no} 套:估值**低于**售价 {-gap * 100:.0f}%")
        lines.append(text)
    return lines


_CLAIMS = [
    (r"最便宜|价格最低|总价最低|售价最低|最低价", "price", "min"),
    (r"最贵|价格最高|总价最高|售价最高|最高价", "price", "max"),
    (r"(?:离|距|靠近)\s*(?:CBD|市区|市中心|城区)\s*最近|最靠近\s*(?:CBD|市区|市中心)|最近市区", "cbd", "min"),
    (r"(?:离|距)\s*(?:CBD|市区|市中心|城区)\s*最远|最偏远|位置最远|最远离市区", "cbd", "max"),
    (r"(?:土地|占地|地块)(?:面积)?最大|地最大", "land", "max"),
    (r"(?:土地|占地|地块)(?:面积)?最小", "land", "min"),
    (r"唯一(?:一套)?(?:有|记录了?)\s*(?:土地|占地)|只有.{0,8}(?:有|记录了?)\s*(?:土地|占地)", "land", "only"),
    (r"回报(?:率)?最高|回报最好|收益(?:率)?最高", "yield", "max"),
    (r"回报(?:率)?最低|回报最差|收益(?:率)?最低", "yield", "min"),
    (r"最安静", "quiet", "max"),
    (r"(?i)\bcheapest\b|\blowest[- ]priced\b|\blowest price\b|\bleast expensive\b", "price", "min"),
    (r"(?i)\bpriciest\b|\bdearest\b|\bmost expensive\b|\bmost costly\b|\bcosts the most\b|\bhighest[- ]priced\b"
     r"|\bhighest price\b", "price", "max"),
    (r"(?i)\b(?:closest|nearest) to (?:the )?(?:cbd|city)\b|\bclosest in\b", "cbd", "min"),
    (r"(?i)\b(?:furthest|farthest) (?:out|from (?:the )?(?:cbd|city))\b", "cbd", "max"),
    (r"(?i)\b(?:largest|biggest)(?:\s+\w+){0,2}\s+(?:land|block|lot|parcel)\b|\bmost land\b", "land", "max"),
    (r"(?i)\bsmallest(?:\s+\w+){0,2}\s+(?:land|block|lot|parcel)\b|\bleast land\b", "land", "min"),
    (r"(?i)\bonly (?:one|property) (?:with|that has|to have) (?:a )?(?:land|block)", "land", "only"),
    (r"(?i)\bonly (?:one|property) (?:with no|without(?: a)?|that lacks|missing) (?:land|block)", "land", "only_none"),
    (r"唯一(?:一套)?(?:没有|缺少?)\s*(?:土地|占地)|只有.{0,8}(?:没有|缺少?)\s*(?:土地|占地)", "land", "only_none"),
    (r"(?i)\b(?:highest|best|top|strongest) (?:gross )?(?:rental )?yield\b", "yield", "max"),
    (r"(?i)\b(?:lowest|weakest|worst) (?:gross )?(?:rental )?yield\b", "yield", "min"),
    (r"(?i)\bquietest\b", "quiet", "max"),
]
_CLAIMS = [(re.compile(p), f, k) for p, f, k in _CLAIMS]

_SKIP = re.compile(r"不是|并非|不算|第二|次便宜|次贵|之一|前[两二三四]套|[两二三四]套(?:里|中|之中|之间)|这几套里|其中"
                   r"|(?i:\bnot\b|n't\b|\bsecond\b|\bone of\b|\bamong\b|\bbetween\b|\bof the (?:two|three|four)\b)")
_REF_ZH = re.compile(r"第\s*(\d+(?:\s*(?:、|,|，|和|与|及|-|–|~|至|到)\s*\d+)*)\s*套")
_REF_EN = re.compile(r"(?i)\bpropert(?:y|ies)\s+(\d+(?:\s*(?:,|and|&|-|–|to)\s*\d+)*)")
_CLAUSE = re.compile(r"[，,;；。!！?？]|(?<!\d)\.(?!\d)")


def _refs(text):
    out = []
    for m in list(_REF_ZH.finditer(text)) + list(_REF_EN.finditer(text)):
        body = m.group(1)
        nums = [int(n) for n in re.findall(r"\d+", body)]
        if len(nums) == 2 and re.search(r"-|–|~|至|到|\bto\b", body):
            nums = list(range(nums[0], nums[1] + 1))
        out.extend(nums)
    return out


def _wrong(clause, refs, rows):
    if not refs or _SKIP.search(clause):
        return False
    shown = {r["display_no"] for r in rows}
    refs = [n for n in refs if n in shown]
    if not refs:
        return False
    for pattern, field, kind in _CLAIMS:
        if not pattern.search(clause):
            continue
        if kind in ("only", "only_none"):
            recorded = {r["display_no"] for r in rows if _land(r) is not None}
            target = recorded if kind == "only" else shown - recorded
            if len(target) != 1 or set(refs) != target:
                return True
            continue
        ext = _extremes(rows, field)
        if ext and not set(refs) <= ext[kind]:
            return True
    return False


def strip_false_comparisons(answer, rows):
    if not answer or not rows:
        return answer, []
    kept, removed = [], []
    for line in answer.split("\n"):
        carry = []
        bad = False
        for clause in _CLAUSE.split(line):
            refs = _refs(clause) or carry
            if refs:
                carry = refs
            if _wrong(clause, refs, rows):
                bad = True
                break
        (removed if bad else kept).append(line)
    if not removed:
        return answer, []
    text = "\n".join(kept).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text, removed


_AMBIGUOUS = {"officer", "plenty", "research", "emerald", "sunshine", "hillside", "brooklyn", "chelsea",
              "vermont", "newport", "dallas", "windsor", "brighton", "hampton", "albion", "wildwood", "seaford"}
_ALWAYS_SKIP = {"melbourne"}


def mentioned_suburbs(query, suburbs, exclude_texts=()):
    text = query or ""
    blocked = [t.lower() for t in exclude_texts if t]
    found, taken = [], []
    for name in sorted(suburbs, key=len, reverse=True):
        low = name.lower()
        if low in _ALWAYS_SKIP:
            continue
        flags = 0 if low in _AMBIGUOUS else re.I
        for m in re.finditer(r"(?<![A-Za-z])" + re.escape(name) + r"(?![A-Za-z])", text, flags):
            span = m.span()
            if any(a < span[1] and span[0] < b for a, b in taken):
                continue
            if any(low in t for t in blocked):
                continue
            taken.append(span)
            if name not in found:
                found.append(name)
    return found


_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
_BED = r"(?:\s*-?\s*(?:bed(?:room)?s?\b|br\b|b/r\b|卧室|卧|居室|房(?![子源价型东贷产屋间])))"
_BED_RANGE = re.compile(r"(?i)(\d|one|two|three|four|five|[一两二三四五])" + _BED + r"?\s*(?:or|to|-|–|~|或|到|至|/)\s*"
                        r"(\d|one|two|three|four|five|six|[一两二三四五六])" + _BED)
_BED_ONE = re.compile(r"(?i)(?<![\d.])(\d{1,2}|one|two|three|four|five|six|[一两二三四五六])" + _BED)
_BED_MORE = re.compile(r"(?i)(?:more|another|additional|extra|多|再加|加)\s*(?:一|1|one)?\s*" + _BED)


def _n(token):
    token = token.lower()
    return int(token) if token.isdigit() else _NUM_WORDS.get(token)


_MONEY = re.compile(r"(?i)(\$\s*)?(\d+(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k\b|m\b(?![²2])|mil\b|million\b|thousand\b|万|百万|千万|亿)?")
_UPPER_BEFORE = re.compile(r"(?i)(?:under|below|less than|up to|max(?:imum)?|no more than|within|at most|cheaper than|"
                           r"budget(?: of| is)?|不超过|最多|低于|少于|小于|预算)\s*$")
_LOWER_BEFORE = re.compile(r"(?i)(?:over|above|more than|at least|min(?:imum)?|starting(?: from| at)?|upwards of|from|"
                           r"不低于|至少|超过|高于|大于)\s*$")
_UPPER_AFTER = re.compile(r"^\s*(?:以下|以内|之内|内(?!容)|or less|and under|max)")
_LOWER_AFTER = re.compile(r"(?i)^\s*(?:以上|起|及以上|or more|and up|plus|\+)")
_RENT_CONTEXT = re.compile(r"(?i)^\s*(?:per\s*week|/\s*w(?:ee)?k|pw\b|a week|weekly|每周|周租|/周)|rent|租金")


def _money_mentions(query):
    q = query or ""
    out = []
    for m in _MONEY.finditer(q):
        dollar, num, unit = m.group(1), m.group(2), (m.group(3) or "").lower()
        if not dollar and not unit:
            continue
        value = float(num.replace(",", ""))
        mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mil": 1e6, "million": 1e6,
                "万": 1e4, "百万": 1e6, "千万": 1e7, "亿": 1e8}.get(unit, 1)
        amount = value * mult
        if unit == "m" and value > 50:
            continue
        if unit == "k" and value > 50_000:
            continue
        before, after = q[max(0, m.start() - 24):m.start()], q[m.end():m.end() + 12]
        if _RENT_CONTEXT.search(after) or re.search(r"(?i)rent|租金", before[-10:]):
            continue
        if 0 < amount < 10_000:
            continue
        if _UPPER_BEFORE.search(before) or _UPPER_AFTER.search(after):
            out.append((round(amount), "upper"))
        elif _LOWER_BEFORE.search(before) or _LOWER_AFTER.search(after):
            out.append((round(amount), "lower"))
    rng = (re.search(r"(?i)between\s*(\$?\s*[\d.,]+\s*(?:k|m|million)?)\s*(?:and|to|-)\s*(\$?\s*[\d.,]+\s*(?:k|m|million)?)", q)
           or re.search(r"([\d.]+\s*万?)\s*(?:到|至|-|–|~)\s*([\d.]+\s*万)", q))
    if rng:
        low_text, high_text = rng.group(1), rng.group(2)
        unit = _unit(high_text)
        vals = [_parse_amount(low_text, unit), _parse_amount(high_text, unit)]
        if all(vals):
            lo, hi = sorted(vals)
            out = [x for x in out if x[0] not in (lo, hi)] + [(lo, "lower"), (hi, "upper")]
    return out


_UNIT_MULT = {"k": 1e3, "m": 1e6, "million": 1e6, "万": 1e4}


def _unit(text):
    m = re.search(r"(?i)(k|m|million|万)\s*$", text.strip())
    return m.group(1).lower() if m else ""


def _parse_amount(text, fallback_unit=""):
    m = re.search(r"([\d.,]+)", text)
    if not m:
        return None
    unit = _unit(text) or fallback_unit
    if not unit and "$" not in text:
        return None
    try:
        return round(float(m.group(1).replace(",", "")) * _UNIT_MULT.get(unit, 1))
    except ValueError:
        return None


def _money(v):
    return f"${v:,.0f}"


def ignored_conditions(query, params, suburbs=()):
    params = params or {}
    q = query or ""
    notes = []

    exclude = [(params.get("near_place") or {}).get("name") or "", (params.get("school_zone") or {}).get("school") or ""]
    used = (params.get("suburb") or "").strip()
    names = [s for s in mentioned_suburbs(q, suburbs, exclude)
             if s.lower() != used.lower() and not (used and s.lower() in used.lower())]
    if names and used:
        zh_list, en_list = "、".join(f"「{n}」" for n in names), ", ".join(names)
        notes.append((f"一次只能按一个区搜索:这次只搜了「{used}」,{zh_list}没有搜索(不代表那里没有房源),可以单独再问一次",
                      f"Only one suburb can be searched at a time: this search covered {used} only; {en_list} "
                      f"{'was' if len(names) == 1 else 'were'} not searched (that does not mean there are no homes there). "
                      f"Ask again for {en_list} separately"))
    elif names:
        zh_list, en_list = "、".join(f"「{n}」" for n in names), ", ".join(names)
        notes.append((f"你提到的{zh_list}没有作为区域条件使用,这次结果不限区域",
                      f"{en_list} {'was' if len(names) == 1 else 'were'} not used as a suburb filter; these results are not limited by suburb"))

    bed = params.get("bedrooms")
    if not _BED_MORE.search(q):
        rng = _BED_RANGE.search(q)
        if rng and _n(rng.group(1)) and _n(rng.group(2)) and _n(rng.group(1)) != _n(rng.group(2)):
            lo, hi = sorted((_n(rng.group(1)), _n(rng.group(2))))
            if bed:
                notes.append((f"卧室数一次只能按一个数筛选:你说的 {lo}–{hi} 房只按 {bed} 房搜了",
                              f"Bedrooms can only be filtered by one number: your {lo}–{hi} bedrooms was searched as {bed} only"))
            else:
                notes.append((f"卧室数一次只能按一个数筛选:你说的 {lo}–{hi} 房没有用上,这次结果不限卧室数",
                              f"Bedrooms can only be filtered by one number: your {lo}–{hi} bedrooms was not applied; "
                              f"these results are not limited by bedrooms"))
        elif not bed and (one := _BED_ONE.search(q)) and _n(one.group(1)):
            n = _n(one.group(1))
            notes.append((f"你提到的 {n} 房没有作为卧室数条件使用,这次结果不限卧室数",
                          f"Your {n}-bedroom requirement was not applied; these results are not limited by bedrooms"))

    max_p, min_p = params.get("max_price"), params.get("min_price")
    for amount, side in _money_mentions(q):
        if side == "upper":
            if amount <= 0:
                notes.append((f"价格上限 {_money(amount)} 不成立,没有使用",
                              f"A price limit of {_money(amount)} is not meaningful and was not used"))
            elif not max_p:
                notes.append((f"你提到的价格上限 {_money(amount)} 没有用上",
                              f"Your price limit of {_money(amount)} was not applied"))
        elif amount > 0 and not min_p:
            clash = max_p and amount > max_p
            notes.append((f"你提到的价格下限 {_money(amount)} 没有用上"
                          + (f"(它高于价格上限 {_money(max_p)},两个条件互相矛盾)" if clash else ""),
                          f"Your minimum price of {_money(amount)} was not applied"
                          + (f" (it is above the {_money(max_p)} limit, so the two conditions contradict each other)" if clash else "")))
    if max_p and min_p and min_p > max_p:
        notes.append((f"价格下限 {_money(min_p)} 高于上限 {_money(max_p)},两个条件互相矛盾",
                      f"The minimum price {_money(min_p)} is above the limit {_money(max_p)}; the two conditions contradict each other"))
    seen, unique = set(), []
    for zh, en in notes:
        if zh not in seen:
            seen.add(zh)
            unique.append((zh, en))
    return unique
