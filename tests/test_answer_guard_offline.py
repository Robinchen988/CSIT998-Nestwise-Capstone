import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.orchestration import answer_guard as ag
from app.orchestration import refinement as r
from app.orchestration import graph as g

errors = []


def check(ok, label):
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        errors.append(label)


ROWS = [
    {"display_no": 1, "price": 391000, "distance_cbd": 14.0, "land_size": 215, "gross_yield": 0.040,
     "context_scores": {"quiet": 60}, "predicted_gap": -0.30},
    {"display_no": 2, "price": 500000, "distance_cbd": 9.0, "land_size": 0, "gross_yield": 0.035,
     "context_scores": {"quiet": 70}, "predicted_gap": 0.05},
    {"display_no": 3, "price": 605000, "distance_cbd": 20.0, "land_size": 620, "gross_yield": 0.030,
     "context_scores": {"quiet": 80}, "predicted_gap": 0.20},
    {"display_no": 4, "price": 551000, "distance_cbd": 11.0, "land_size": 697, "gross_yield": 0.033,
     "context_scores": {"quiet": 75}},
    {"display_no": 5, "price": 386000, "distance_cbd": 12.0, "land_size": None, "gross_yield": 0.045,
     "context_scores": {"quiet": 65}},
]

wrong = [
    "- Property 1 is the cheapest at $391,000",
    "- 第 1 套是唯一有土地记录的",
    "- Property 1 is the quietest but furthest out",
    "- 第 3 套土地最大",
    "- Property 5 is the priciest",
    "- 第 2 套离 CBD 最远",
    "- 第 3 套回报率最高",
]
right = [
    "第 5 套最便宜,适合预算有限的人。",
    "- Property 3 is the most expensive and the quietest",
    "- 第 4 套土地最大,第 2 套离 CBD 最近",
    "- 第 2 套不是最便宜的,但离市区最近",
    "- 前三套里第 1 套最便宜",
    "- Property 5 has the highest yield and is among the cheapest",
    "- 第 1、3、4 套记录了土地面积",
    "- 第 5 套回报最高、离市区不算远",
]
answer = "\n".join(right[:1] + [""] + wrong + right[1:])
clean, removed = ag.strip_false_comparisons(answer, ROWS)
check(sorted(removed) == sorted(wrong), f"every wrong comparison is removed ({len(removed)}/{len(wrong)})")
check(all(line in clean for line in right), "correct, negated and subset comparisons are kept")
check(ag.strip_false_comparisons("Property 2 sits in the middle on every count.", ROWS) ==
      ("Property 2 sits in the middle on every count.", []), "an answer with no superlatives is untouched")
_, rm = ag.strip_false_comparisons("- Property 2 is the cheapest\n- Property 9 is the cheapest", ROWS)
check(rm == ["- Property 2 is the cheapest"], "references to cards not shown are not judged")
tie = [{**ROWS[0], "price": 386000}, *ROWS[1:]]
check(ag.strip_false_comparisons("- 第 1、5 套并列最便宜", tie)[1] == [], "ties count as cheapest")

more = ["- Property 5 is the only one with no land record", "- Property 4 costs the most",
        "- 第 3 套是唯一没有土地记录的", "- Property 3 has the largest recorded land",
        "- Property 4 is the smallest recorded land"]
check(sorted(ag.strip_false_comparisons("\n".join(more), ROWS)[1]) == sorted(more),
      "'only one without land' and 'costs the most' are checked too")
check(ag.strip_false_comparisons("- Property 3 is the dearest", ROWS)[1] == [], "'dearest' that is right is kept")

facts = "\n".join(ag.comparison_facts(ROWS))
check("价格最低:第 5 套($386,000);最高:第 3 套($605,000)" in facts, "facts: cheapest and priciest")
check("离 CBD 距离最近:第 2 套(9.0 km);最远:第 3 套(20.0 km)" in facts, "facts: closest and furthest from the CBD")
check("土地面积最小:第 1 套(215 m²);最大:第 4 套(697 m²)" in facts, "facts: land extremes ignore 0 and missing")
check("5 套中有土地面积记录的 3 套:第 1、3、4 套" in facts, "facts: which properties have land recorded")
check("第 1 套:估值**低于**售价 30%" in facts and "第 2 套:估值与售价基本相符" in facts
      and "第 3 套:估值**高于**售价 20%" in facts, "facts: valuation direction is spelled out per property")
en = "\n".join(ag.comparison_facts(ROWS, en=True))
check("Price: lowest Property 5 ($386,000); highest Property 3 ($605,000)" in en
      and "Property 1: estimate BELOW the sale price by 30%" in en, "facts in English")
suspect = [{**ROWS[3], "area_suspect": {"land": True}}, *ROWS[:3]]
check("最大:第 3 套" in "\n".join(ag.comparison_facts(suspect)), "a land record flagged as suspect is not used for comparison")

prev_rows = [{"price": p, "context_scores": {"quiet": q}} for p, q in
             [(380000, 82), (390000, 80), (393000, 80), (400000, 79), (410000, 78)]]
base_goal = {"field": "price", "direction": "decrease", "degree": "slight", "baseline": 520000.0}
previous = {"max_price": 1000000, "bedrooms": 3, "relative_preferences": [base_goal],
            "abstract_needs": [{"attribute": "quiet", "min_score": 60}]}
sanitize = lambda params, query: params
out = r.apply_changes(previous, [{"action": "set", "field": "bedrooms", "value": 4, "source": "one more bedroom"}],
                      "one more bedroom", prev_rows, sanitize, ())
check(out.get("relative_preferences") is None,
      "a later turn does not inherit the earlier 'cheaper' goal or its $520,000 baseline")
out = r.apply_changes(previous, [{"action": "relative", "field": "quiet", "direction": "increase", "degree": "slight",
                                  "source": "a bit quieter"}], "a bit quieter", prev_rows, sanitize, ())
goals = out["relative_preferences"]
check(len(goals) == 1 and goals[0]["field"] == "quiet" and goals[0]["baseline"] == 80,
      "a new relative request is measured from the results just shown, alone")

cands = [{"id": "far", "price": 610000, "context_scores": {"quiet": 91}},
         {"id": "near", "price": 395000, "context_scores": {"quiet": 91}}]
ranked = r.relative_rank(cands, goals, prev_rows, [])
check([c["id"] for c in ranked] == ["near", "far"], "a quieter request keeps price close to the previous results")
price_goal = [{"field": "price", "direction": "decrease", "degree": "slight", "baseline": 393000.0}]
cheap = [{"id": "a", "price": 385000, "context_scores": {"quiet": 80}}]
check([c["id"] for c in r.relative_rank(cheap, price_goal, prev_rows, [])] == ["a"],
      "a cheaper request is not held back by the price anchor")

after_rows = [{"price": p, "context_scores": {"quiet": q}} for p, q in
              [(395000, 79), (405000, 78), (410500, 78), (420000, 77), (430000, 76)]]
state = {"intent": "refine", "lang": "zh", "refinement_base": {"metrics": prev_rows},
         "params": {"abstract_needs": [{"attribute": "quiet", "min_score": 60}]}}
note = "".join(g._change_notes(state, {"metrics": after_rows}))
check("价格中位 $393,000→$410,500(上升)" in note, f"price change direction is computed: {note}")
check("「安静」中位 80→78(下降)" in note, "score change direction is computed")
same = "".join(g._change_notes(state, {"metrics": prev_rows}))
check("(不变)" in same and "上升" not in same and "下降" not in same, "no change is reported as unchanged")
en_note = "".join(g._change_notes({**state, "lang": "en"}, {"metrics": after_rows}))
check("median price $393,000→$410,500 (up)" in en_note, "English change note")
check(g._change_notes({**state, "intent": "new_search"}, {"metrics": after_rows}) == [],
      "a new search has no before/after note")

SUBS = ["Toorak", "Carlton", "Carlton North", "Footscray", "Box Hill", "Box Hill North", "Brunswick",
        "Sunshine", "Plenty", "Melbourne", "Richmond", "Coburg"]


def ignored(query, params):
    return [zh for zh, _ in ag.ignored_conditions(query, params, SUBS)]


n = ignored("4 beds in Toorak or Carlton under $3M", {"suburb": "Toorak", "bedrooms": 4, "max_price": 3000000})
check(len(n) == 1 and "只搜了「Toorak」" in n[0] and "「Carlton」没有搜索" in n[0] and "不代表那里没有房源" in n[0],
      "second suburb: says it was not searched")
n = ignored("2 or 3 bedroom unit in Footscray", {"suburb": "Footscray", "property_type": "apartment"})
check(len(n) == 1 and "2–3 房没有用上" in n[0], "bedroom range: says it was not applied")
n = ignored("Brunswick 一房或两房的公寓,60 万以内", {"suburb": "Brunswick", "max_price": 600000, "bedrooms": 2})
check(len(n) == 1 and "1–2 房只按 2 房搜了" in n[0], "Chinese bedroom range searched as one number")
n = ignored("house under $0", {"property_type": "house"})
check(n == ["价格上限 $0 不成立,没有使用"], "a $0 budget is called out")
n = ignored("3-bedroom house under $500k and over $2M", {"bedrooms": 3, "max_price": 500000})
check(len(n) == 1 and "价格下限 $2,000,000 没有用上" in n[0] and "互相矛盾" in n[0],
      "a contradictory minimum price is called out")
check(ignored("x", {"min_price": 900000, "max_price": 500000}) == ["价格下限 $900,000 高于上限 $500,000,两个条件互相矛盾"],
      "contradictory parsed bounds are called out")
for query, params in [
    ("80万以下的两房公寓", {"bedrooms": 2, "max_price": 800000, "property_type": "apartment"}),
    ("Coburg 50 万到 80 万之间的房子", {"suburb": "Coburg", "min_price": 500000, "max_price": 800000}),
    ("离火车站 800m 内、1.5m以下的 townhouse", {"max_price": 1500000}),
    ("plenty of sunshine, 3 bed near Melbourne Uni", {"bedrooms": 3, "near_place": {"name": "University of Melbourne"}}),
    ("3 bedroom house at least 600 m2 land under $900k", {"bedrooms": 3, "max_price": 900000}),
    ("Box Hill North 三房", {"suburb": "Box Hill North", "bedrooms": 3}),
    ("租金每周 $500 以下的两房", {"bedrooms": 2}),
    ("one more bedroom", {}),
    ("3 bed 2 bath house within 10 km of the CBD", {"bedrooms": 3, "max_distance_cbd_km": 10}),
    ("Between $500k and $800k in Richmond", {"suburb": "Richmond", "min_price": 500000, "max_price": 800000}),
]:
    got = ignored(query, params)
    check(got == [], f"no false alarm: {query} -> {got}")
n = ignored("2-bed apartment, Toorak or Carlton", {"bedrooms": 2})
check(len(n) == 1 and "没有作为区域条件使用" in n[0], "suburbs mentioned but none used")
en = [e for _, e in ag.ignored_conditions("4 beds in Toorak or Carlton", {"suburb": "Toorak", "bedrooms": 4}, SUBS)]
check(en and "Carlton was not searched" in en[0] and "does not mean there are no homes there" in en[0], "English notice")

state = {"intent": "new_search", "lang": "zh", "user_query": "house under $0", "params": {"property_type": "house"}}
original = g._dataset_suburbs
g._dataset_suburbs = lambda: SUBS
try:
    check(g._ignored_notes(state) == ["价格上限 $0 不成立,没有使用"], "graph wires the notice for new searches")
    check(g._ignored_notes({**state, "intent": "refine"}) == [], "refinements are not re-audited")
finally:
    g._dataset_suburbs = original

print(f"RESULT {len(errors)} failed")
sys.exit(bool(errors))
