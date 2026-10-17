import ast
from copy import deepcopy
import json
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
base = (ROOT / "tests/test_parking_intent_offline.py").read_text(encoding="utf-8")
exec(compile(base.split("kind, strip, sanitize =", 1)[0], "<parking harness>", "exec"))

class ClarifyChange(ValueError):
    pass

r = load(ROOT / "app/orchestration/refinement.py",
         {"snapshot", "apply_changes", "normalize_amenity_changes"},
         {"deepcopy": deepcopy, "ClarifyChange": ClarifyChange, "valid_goals": lambda _: [],
          "AMENITY_KINDS": set(), "DEFAULT_AMENITY_M": 1500, "NEAREST": "nearest:",
          "ATTRS": set(), "SCALARS": {"max_price", "min_price", "bedrooms", "bathrooms", "semantic_query"},
          "LIST_KEYS": {"unsupported_asks": None}, "OPPOSITE": {}, "RELATIVE_FIELDS": set()})
n["refinement"] = types.SimpleNamespace(**r)
n["_extract_json"] = json.loads
n["_context_message"] = lambda _: ""
n["_INTENTS"] = {"new_search", "refine", "clarify", "about_results", "concept"}

def turn(state, query, raw):
    n["_ask"] = lambda *_: json.dumps(raw, ensure_ascii=False)
    return n["parse_intent"]({**state, "user_query": query, "history": [], "lang": "zh"})

def state_with(params):
    return {"params": params, "metrics": [], "ranking": [], "properties": []}

label = "房源车位"
first = turn(state_with({}), "a house with a garage",
             {"intent": "new_search", "semantic_query": "a house with a garage"})
check(first["params"]["unsupported_asks"] == [label], "initial property garage is unsupported")
direct = n["prepare_refinement"]({**first["params"], "max_price": 800000}, first["params"],
                                 user_query="预算改成80万")
check(direct["unsupported_asks"] == [label] and direct["max_price"] == 800000,
      "direct refinement helper retains prior unsupported parking warning")

budget = turn(state_with(first["params"]), "预算改成80万",
              {"intent": "refine", "changes": [{"action": "set", "field": "max_price",
                                                   "value": 800000, "source": "预算改成80万"}]})
check(budget["intent"] == "refine" and budget["params"]["max_price"] == 800000
      and budget["params"]["unsupported_asks"] == [label],
      "budget-only refine retains prior unsupported parking warning")
check(budget["refinement_base"]["params"]["unsupported_asks"] == [label]
      and budget["refinement_base"]["params"].get("max_price") is None,
      "rollback snapshot preserves prior parking state and budget")

cancel = turn(state_with(budget["params"]), "取消车位要求",
              {"intent": "refine", "changes": [{"action": "remove", "field": "unsupported_asks",
                                                   "value": label, "source": "取消车位要求"}]})
check(cancel["intent"] == "refine" and not cancel["params"]["unsupported_asks"]
      and cancel["params"]["max_price"] == 800000,
      "explicit removal clears parking warning without losing budget")
check(cancel["refinement_base"]["params"]["unsupported_asks"] == [label],
      "cancel rollback restores parking warning")

negative_cancel = turn(state_with(budget["params"]), "房子不要车位了",
                       {"intent": "refine", "changes": [{"action": "remove", "field": "unsupported_asks",
                                                            "value": label, "source": "房子不要车位了"}]})
check(n["_parking_kind"]("房子不要车位了") == "property"
      and not negative_cancel["params"]["unsupported_asks"],
      "explicit removal is not re-added by owned-parking words in the same turn")

mixed_prior = {**first["params"], "unsupported_asks": [label, "public facility distance"]}
mixed_budget = turn(state_with(mixed_prior), "预算改成80万",
                    {"intent": "refine", "changes": [{"action": "set", "field": "max_price",
                                                         "value": 800000, "source": "预算改成80万"}]})
check(mixed_budget["params"]["unsupported_asks"] == [label, "public facility distance"],
      "unrelated unsupported facility label survives a budget edit")
mixed_cancel = turn(state_with(mixed_budget["params"]), "取消车位要求",
                    {"intent": "refine", "changes": [{"action": "remove", "field": "unsupported_asks",
                                                         "value": label, "source": "取消车位要求"}]})
check(mixed_cancel["params"]["unsupported_asks"] == ["public facility distance"],
      "explicit property parking removal retains unrelated facility warning")

with_results = {**state_with(first["params"]), "metrics": [{"id": 1}]}
changed_with_results = turn(with_results, "预算改成80万",
                            {"intent": "refine", "changes": [{"action": "set", "field": "max_price",
                                                                 "value": 800000, "source": "预算改成80万"}]})
load(GRAPH, {"_unmatched_refinement"}, n)
rolled_back = n["_unmatched_refinement"]({**with_results, **changed_with_results}, [])
check(rolled_back["params"]["unsupported_asks"] == [label]
      and rolled_back["params"].get("max_price") is None
      and rolled_back["metrics"] == [{"id": 1}],
      "actual no-match rollback restores prior warning, budget and results")

reset = turn(state_with(budget["params"]), "重新搜索靠近公共停车场",
             {"intent": "new_search", "reset_source": "重新搜索",
              "semantic_query": "near a public car park"})
check(reset["intent"] == "new_search" and not reset["params"]["unsupported_asks"]
      and reset["params"]["semantic_query"] == "near a public car park",
      "explicit reset clears inherited warning and preserves public facility")

for query, expected in (("near a public car park", None), ("near The Garage Cafe", None),
                        ("near a public car park and a house with a garage", label)):
    fresh = turn(state_with({}), query, {"intent": "new_search", "semantic_query": query})
    asks = fresh["params"]["unsupported_asks"] or []
    check((expected in asks if expected else not asks), f"fresh search classification: {query}")
    if "public car park" in query:
        check("public car park" in fresh["params"]["semantic_query"],
              f"fresh search keeps public facility: {query}")


invalid = turn(state_with(budget["params"]), "预算改成70万",
               {"intent":"refine","changes":[{"action":"remove","field":"unsupported_asks","value":label,"source":"取消车位要求"}]})
check(invalid["intent"] == "clarify" and invalid["params"]["unsupported_asks"]==[label]
      and invalid["params"]["max_price"]==800000, "invalid cancellation source cannot suppress inherited warning or alter budget")
print(f"RESULT {len(errors)} failed")
sys.exit(bool(errors))
