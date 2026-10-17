import ast
import json
import re
import sys
import types
from pathlib import Path

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv)>1 and sys.argv[1] != "--legacy" else Path(__file__).resolve().parents[1]
GRAPH = ROOT / "app/orchestration/graph.py"
errors = []

def check(ok, name):
    print(("ok   " if ok else "FAIL ") + name)
    if not ok:
        errors.append(name)

def load(path, names, ns):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names or (
            isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)
        ):
            exec(compile(ast.Module([node], []), str(path), "exec"), ns)
    return ns

if sys.argv[1:] == ["--legacy"]:
    old = Path(r"D:\projects\capstone-test-20260930-1340\wave07\work\parking_visibility_candidate\app\orchestration\graph.py")
    n = load(old, {"_PARKING_OVERLAY_RE", "_PARKING_ASK_RE", "_asks_parking", "_without_property_parking"}, {"re": re})
    for text in ("near a public car park", "靠近公共停车场", "near The Garage Cafe"):
        check(not n["_asks_parking"](text) and n["_without_property_parking"](text) == text,
              f"legacy should preserve public/place query: {text}")
    print(f"RESULT {len(errors)} failed")
    sys.exit(bool(errors))

source = GRAPH.read_text(encoding="utf-8")
n = load(GRAPH, {"_PROPERTY_PARKING_ASK", "_PROPERTY_PARKING_LABELS", "_PUBLIC_PARKING_RE", "_PARKING_TERM_RE", "_PROPERTY_PARKING_RE",
                 "_DERIVED_BARE_SPACE_RE", "_DERIVED_AFTER_HOME_RE", "_property_parking_spans", "_parking_kind",
                 "_without_property_parking", "_without_derived_property_parking", "_unsupported_en",
                 "_parking_notice", "_sanitize", "_unsupported_list", "prepare_refinement", "parse_intent", "explain",
                 "_explain_fact", "_EXPLAIN_HIDDEN", "area_suspect",
                 "_TRAVEL_TIME_ASK", "_TRAVEL_TIME_RE", "_asks_travel_time", "_travel_time_notice", "_FIRST_TURN_RETRY",
                 "_ignored_notes", "RESULT_LIMIT"},
         {"re": re, "json": json, "State": dict,
          "_t": lambda state, zh, en: en if state.get("lang") == "en" else zh,
          "_en": lambda state: state.get("lang") == "en",
          "i18n": types.SimpleNamespace(UNSUPPORTED_KEY_EN={}),
          "PARAM_KEYS": ("max_price", "min_price", "bedrooms", "bathrooms", "property_type", "suburb", "sort_by",
                         "min_gross_yield", "amenity_needs", "near_place", "abstract_needs", "unsupported_asks",
                         "school_zone", "planning_needs", "relative_preferences", "description_query", "semantic_query"),
          "_INT_KEYS": ("max_price", "min_price", "bedrooms", "bathrooms"), "_PROPERTY_TYPES": ("house", "apartment", "townhouse"),
          "_SORT_FIELDS": (), "_ABSTRACT_KEYS": (), "_SCORE_OPERATORS": {}, "_PLANNING_NEEDS": (), "NEAREST_PREFIX": "nearest:",
          "nearby": types.SimpleNamespace(KINDS=()), "zones": types.SimpleNamespace(LEVELS=()),
          "refinement": types.SimpleNamespace(valid_goals=lambda _: [], baseline_for=lambda *_: None),
          "_valuation_direction": lambda _q, sort: sort, "_typo_source": lambda *_: None,
          "_no_result_answer": lambda *_: "NO RESULTS", "_assumption_lines": lambda *_: [],
          "_EXPLAIN_SYSTEM": "FAKE", "_EXPLAIN_LANG_EN": "", "answer_guard": __import__("types").SimpleNamespace(**__import__("runpy").run_path(str(__import__("pathlib").Path(__file__).resolve().parents[1] / "app/orchestration/answer_guard.py"))), "_dataset_suburbs": lambda: [], "_context_message": lambda *_: "", "_PARSE_SYSTEM": "FAKE"})
kind, strip, sanitize = n["_parking_kind"], n["_without_property_parking"], n["_sanitize"]

public = ("near a public car park", "靠近公共停车场", "附近有停车位", "near The Garage Cafe", "Parking Overlay houses")
for text in public:
    check(kind(text) is None and strip(text) == text, f"public/place preserved: {text}")
    got = sanitize({"semantic_query": text, "description_query": text,
                    "unsupported_asks": ["房源车位", "public facility distance"]}, text)
    check(got["semantic_query"] == text and got["description_query"] == text
          and got["unsupported_asks"] == ["public facility distance"],
          f"no false unsupported ask or query damage: {text}")

owned = (("a house with a garage", "a house"), ("property without parking", "property"),
         ("房子要有车位", "房子"), ("房子不要车位", "房子"), ("需要停车位", ""))
for text, expected in owned:
    check(kind(text) == "property" and strip(text) == expected, f"owned-space detection: {text}")
    got = sanitize({"semantic_query": text, "description_query": text, "bedrooms": 3}, text)
    check(got["unsupported_asks"] == ["房源车位"] and got["bedrooms"] == 3
          and got["semantic_query"] == (expected or "property") and got["description_query"] == (expected or None),
          f"owned phrase removed, other filter kept: {text}")

for text in ("garage", "parking", "车位"):
    got = sanitize({"semantic_query": text, "description_query": text}, text)
    check(kind(text) == "ambiguous" and strip(text) == text and got["semantic_query"] == text and got["description_query"] == text,
          f"ambiguous query retained: {text}")

refined = n["prepare_refinement"]({"semantic_query": "house with garage"}, {}, user_query="house with garage")
check(refined["semantic_query"] == "house" and refined["unsupported_asks"] == ["房源车位"],
      "refine second sanitation still uses the original user query and retains the unsupported warning")

for text, kept in (("near a public car park and a house with a garage", "public car park"),
                   ("靠近公共停车场，但房子要有车位", "公共停车场")):
    got = sanitize({"semantic_query": text, "description_query": text,
                    "unsupported_asks": ["public facility distance"]}, text)
    check(kind(text) == "property" and kept in got["semantic_query"] and kept in got["description_query"]
          and got["unsupported_asks"] == ["房源车位", "public facility distance"] and "garage" not in got["semantic_query"].lower()
          and "车位" not in got["semantic_query"], f"mixed query keeps public facility, drops only owned phrase: {text}")

for query, semantic, description, expected_semantic, expected_description in (
    ("靠近公园，房子要有停车位", "house near park with parking", "house with parking", "house near park", "house"),
    ("靠近公园的房子要有停车位", "house near park with parking", "house with parking", "house near park", "house"),
    ("靠近商场的公寓自带停车位", "apartment near shopping with parking", "apartment with parking", "apartment near shopping", "apartment"),
    ("三房要有车位", "three bedroom house garage", "garage", "three bedroom house", None),
):
    result = sanitize({"semantic_query": semantic, "description_query": description, "bedrooms": 3}, query)
    check(kind(query) == "property" and result["unsupported_asks"] == ["房源车位"]
          and result["semantic_query"] == expected_semantic and result["description_query"] == expected_description
          and result["bedrooms"] == 3, f"[V3 review] owned-space intent removes derived term, keeps other needs: {query}")

query = "near The Garage Cafe and a house with a garage"
result = sanitize({"semantic_query": "near The Garage Cafe and a house with a garage",
                   "description_query": "near The Garage Cafe"}, query)
check(result["unsupported_asks"] == ["房源车位"] and "The Garage Cafe" in result["semantic_query"]
      and result["description_query"] == "near The Garage Cafe", "[V3 review] explicit owned-space request does not strip the named place")
result = sanitize({"semantic_query": "near The Garage Cafe and a house with a garage",
                   "description_query": "garage"}, query)
check(result["description_query"] == "garage" and result["unsupported_asks"] == ["房源车位"],
      "[V3 review] ambiguous bare garage from a mixed named-place request is retained with warning")

check("要带车位的" not in source and "The Garage Cafe" in source, "parser prompt distinguishes public venue from owned space")
def unavailable(*_args, **_kwargs):
    raise RuntimeError("synthetic LLM outage")
n.update({"_ask": unavailable, "_ask_streaming": unavailable})
parsed = n["parse_intent"]({"user_query": "房子要有车位", "params": {}, "metrics": [], "history": [], "lang": "zh"})
check(parsed["intent"] == "clarify" and "无法核验" in parsed["turn_notice"], "parser outage: clear owned-space notice")
parsed = n["parse_intent"]({"user_query": "near The Garage Cafe", "params": {}, "metrics": [], "history": [], "lang": "en"})
check(parsed["intent"] == "new_search" and parsed["params"]["semantic_query"] == "near The Garage Cafe",
      "parser outage: place search is intact")

for text, expected in (("near a public car park", None), ("near The Garage Cafe", None),
                       ("a house with a garage", "cannot currently be verified"), ("garage", "If you mean")):
    answer = n["explain"]({"user_query": text, "intent": "new_search", "metrics": [], "params": {}, "lang": "en"})["answer"]
    check((expected is None and answer == "NO RESULTS") or (expected is not None and expected in answer),
          f"no-result explanation has the right conditional notice: {text}")

captured = {}
def fake_stream(_system, user, **_kwargs):
    captured["user"] = user
    return "FAKE LLM TEXT"
n["_ask_streaming"] = fake_stream
metric = {"id": 1, "price": 500000, "car_spaces": 3}
for text, expected in (("near a public car park", None), ("a house with a garage", "cannot currently be verified"),
                       ("garage", "If you mean")):
    state = {"user_query": text, "intent": "about_results", "metrics": [metric], "params": {}, "lang": "en"}
    answer = n["explain"](state)["answer"]
    check((expected is None and answer == "FAKE LLM TEXT") or (expected is not None and expected in answer),
          f"with results, only owned/ambiguous queries receive a notice: {text}")
    check("car_spaces" not in captured["user"] and metric["car_spaces"] == 3,
          f"LLM payload excludes car_spaces without changing metric: {text}")


query = "a house near a public car park with a garage"
result = sanitize({"semantic_query":"house near public parking with garage","description_query":"near public parking"}, query)
check(result["semantic_query"] == "house near public parking" and result["description_query"]=="near public parking" and result["unsupported_asks"]==["房源车位"], "[reviewer] mixed derived public parking survives owned-garage removal")
translation = next(node for node in ast.parse((ROOT/"app/i18n.py").read_text(encoding="utf-8")).body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=="UNSUPPORTED_KEY_EN" for t in node.targets))
lookup=ast.literal_eval(translation.value)
check(lookup.get("房源车位")=="property car spaces", "[reviewer] canonical key is translated for the English conditions card")
print(f"RESULT {len(errors)} failed")
sys.exit(bool(errors))
