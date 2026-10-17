import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.orchestration import graph as g

errors = []


def check(ok, label):
    print(("ok   " if ok else "FAIL ") + label)
    if not ok:
        errors.append(label)


EMPTY = {k: None for k in g.PARAM_KEYS}


def reply(intent, **fields):
    return json.dumps({**EMPTY, "intent": intent, "amenity_needs": [], "abstract_needs": [],
                       "unsupported_asks": [], "planning_needs": [], "changes": [], **fields}, ensure_ascii=False)


def run_parse(query, answers, **state):
    calls = []
    def fake_ask(system, message, *a, **k):
        calls.append(message)
        return answers[min(len(calls) - 1, len(answers) - 1)]
    original = g._ask
    g._ask = fake_ask
    try:
        return g.parse_intent({"user_query": query, **state}), calls
    finally:
        g._ask = original


q = "60 万以下哪套的年租金最高,租金和回报率各是多少"
out, calls = run_parse(q, [reply("about_results"), reply("new_search", max_price=600000, semantic_query="investment property")])
check(len(calls) == 2 and g._FIRST_TURN_RETRY in calls[1], "first-turn about_results is re-asked once with the first-turn note")
check(out["intent"] == "new_search" and out["params"].get("max_price") == 600000,
      "re-asked parse keeps the $600k ceiling instead of searching with empty conditions")

out, calls = run_parse(q, [reply("about_results"), reply("about_results")])
check(out["intent"] == "clarify" and out.get("turn_notice") and "没有检索" in out["turn_notice"],
      "still unclassifiable: no search, the user is told why")
out, _ = run_parse(q, [reply("about_results"), "not json"])
check(out["intent"] == "clarify", "retry that returns garbage also stops instead of searching")

out, _ = run_parse("Which home under $600k has the highest rent?", [reply("about_results"), reply("about_results")], lang="en")
check(out["intent"] == "clarify" and "nothing was searched" in (out.get("turn_notice") or ""), "English notice for the stop")

out, calls = run_parse("这几套哪个回报最高", [reply("about_results")], metrics=[{"id": 1}], params={"max_price": 800000})
check(len(calls) == 1 and out["intent"] == "about_results", "a real follow-up with results is not re-asked")

out, calls = run_parse("帮我找 80 万以下的两房", [reply("new_search", max_price=800000, bedrooms=2)])
check(len(calls) == 1 and out["params"].get("max_price") == 800000, "normal first-turn search makes a single call")

price_only = g._no_result_answer({"max_price": 50000, "property_type": "house"}, False)
check("其他城市" not in price_only and "放宽" in price_only, "price-only no-result does not blame the city")
rooms_only = g._no_result_answer({"bedrooms": 25}, True)
check("Melbourne only" not in rooms_only and "relaxing" in rooms_only, "English bedroom-only no-result does not blame the city")
city = g._no_result_answer({"suburb": "Sydney"}, False)
check("不含其他城市" in city, "a suburb outside the data still gets the Melbourne-only note")
city_en = g._no_result_answer({"suburb": "Adelaide", "property_type": "apartment"}, True)
check("Melbourne only" in city_en, "English Melbourne-only note when a suburb was named")

for text in ("走路到火车站要几分钟", "开车多久能到 CBD", "步行 5 分钟到车站的两房", "坐车到市区多长时间",
             "How many minutes' walk to the station?", "how long is the drive to the CBD", "walking time to school"):
    check(g._asks_travel_time(text), f"travel-time question detected: {text}")
for text in ("离火车站 800 米内的两房", "近车站 3房", "Houses within 1 km of a hospital", "walk-in robe apartment",
             "公寓几房", "driveway with two car spaces"):
    check(not g._asks_travel_time(text), f"not a travel-time question: {text}")

out, _ = run_parse("走路到火车站要几分钟", [reply("concept")])
check(out["intent"] == "concept" and "直线距离" in (out.get("turn_notice") or ""),
      "concept-routed travel-time question gets the deterministic straight-line notice")
out, _ = run_parse("How many minutes' walk to the station?", [reply("concept")], lang="en")
check("straight-line" in (out.get("turn_notice") or ""), "English straight-line notice")
out, _ = run_parse("这几套走路到车站几分钟", [reply("about_results")], metrics=[{"id": 1}], params={"bedrooms": 2})
check(out["intent"] == "about_results" and "直线距离" in (out.get("turn_notice") or ""),
      "follow-up travel-time question about current results gets the notice too")
out, _ = run_parse("什么是资本化率", [reply("concept")])
check(out.get("turn_notice") is None, "ordinary concept question is left to the concept answer")

out, _ = run_parse("步行 5 分钟到车站、80 万以下的两房",
                   [reply("new_search", max_price=800000, bedrooms=2, unsupported_asks=["步行时间"])])
asks = out["params"].get("unsupported_asks") or []
check(asks.count(g._TRAVEL_TIME_ASK) == 1 and "步行时间" not in asks and out["params"].get("max_price") == 800000,
      "search with a travel-time wish registers the canonical unsupported ask once and keeps other conditions")
out, _ = run_parse("开车 10 分钟到 CBD 的三房", [reply("new_search", bedrooms=3)])
check(g._TRAVEL_TIME_ASK in (out["params"].get("unsupported_asks") or []),
      "travel-time ask is registered even when the model forgot it")

print(f"RESULT {len(errors)} failed")
sys.exit(bool(errors))
