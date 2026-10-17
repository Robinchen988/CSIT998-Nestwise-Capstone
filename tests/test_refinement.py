import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.orchestration import graph as g, refinement as r

checks = 0
def check(condition, message):
    global checks
    assert condition, message
    checks += 1

def row(id, quiet, lively, price=700000):
    return {"id": id, "price": price, "bedrooms": 2, "property_type": "townhouse",
            "context_scores": {"quiet": quiet, "lively": lively, "green": 80}}

previous = {"params": g._sanitize({"bedrooms": 2, "property_type": "townhouse", "max_price": 1000000,
            "sort_by": "quiet", "abstract_needs": [{"attribute": "quiet", "min_score": 60},
            {"attribute": "green", "min_score": 60}]}, "quiet home"),
            "metrics": [row(1, 85, 20), row(2, 83, 22), row(3, 81, 24)],
            "more": [], "ranking": "previous ranking", "batch_offset": 5}
saved = deepcopy(previous)

def parse(query, changes=None, **reply):
    with patch.object(g, "_ask", return_value=json.dumps({"intent": "refine", "changes": changes, **reply})):
        return g.parse_intent({**previous, "user_query": query})

goal = {"action": "relative", "field": "lively", "direction": "increase", "degree": "slight", "source": "再热闹一点"}
parsed = parse("再热闹一点", [goal], abstract_needs=[{"attribute": "lively", "min_score": 60}], sort_by="lively", bedrooms=9)
params = parsed["params"]
check(params["abstract_needs"] == previous["params"]["abstract_needs"], "Full model output must not replace old preferences")
check(params["bedrooms"] == 2 and params["max_price"] == 1000000, "Unmentioned filters survive")
check(params["sort_by"] is None and params["relative_preferences"][0]["baseline"] == 22, "Relative mode uses current displayed results")
check(previous == saved, "Parsing must not mutate previous state")

pool = previous["metrics"] + [row(10, 83, 26), row(11, 82, 29), row(12, 80, 33),
        row(13, 65, 29), row(14, 22, 100), row(15, 75, 100)]
ranked = g.rank({**previous, **parsed, "metrics": pool})
check(ranked["metrics"] and all(m["context_scores"]["quiet"] >= 60 for m in ranked["metrics"]), "Quiet floor remains")
check(all(22 < m["context_scores"]["lively"] < 50 for m in ranked["metrics"]), "Moderate increase excludes extremes")
check(ranked["metrics"][0]["id"] != 13, "Avoid unnecessary deterioration in retained quiet preference")
with patch.object(g, "_ask", return_value=json.dumps({"intent": "refine", "changes": [goal]})):
    again = g.parse_intent({**previous, **ranked, "params": params, "user_query": "再热闹一点"})
check(again["params"]["relative_preferences"][0]["baseline"] > 22, "Repeated requests advance baseline")

failed = g.rank({**previous, **parsed, "metrics": [row(14, 22, 100), row(15, 75, 100)]})
check(failed["params"] == previous["params"] and failed["metrics"] == previous["metrics"], "No compatible adjustment restores filters and results")
check(failed["batch_offset"] == 5 and bool(failed["turn_notice"]), "Restoration preserves displayed numbering")
with patch.object(g, "_ask_streaming", side_effect=AssertionError("must not call LLM")):
    check("保留" in g.explain({**previous, **failed})["answer"], "No-match explanation is deterministic")

removed = parse("不要安静了，越热闹越好", [
    {"action": "remove", "field": "quiet", "source": "不要安静了"},
    {"action": "prioritize", "field": "lively", "source": "越热闹越好"}])["params"]
check([n["attribute"] for n in removed["abstract_needs"]] == ["green"] and removed["sort_by"] == "lively", "Explicit replacement works")
prioritized = parse("安静更重要", [{"action": "prioritize", "field": "quiet", "source": "安静更重要"}])["params"]
check(prioritized["abstract_needs"] == previous["params"]["abstract_needs"], "Priority never deletes filters")
both = parse("预算八十万，再热闹一点", [{"action": "set", "field": "max_price", "value": 800000, "source": "预算八十万"}, goal])["params"]
check(both["max_price"] == 800000 and both["relative_preferences"], "One utterance supports multiple changes")
price = parse("便宜一点", [{"action": "relative", "field": "price", "direction": "decrease", "degree": "slight", "source": "便宜一点"}])["params"]
check(price["max_price"] == 1000000 and price["relative_preferences"][0]["direction"] == "decrease", "Cheaper does not invent a new budget")

for changes in (None, [], [dict(goal, source="unspoken")], [dict(goal, field="unknown")],
                [dict(goal, direction="sideways")], [{"action":"set", "field":"bedrooms", "value":-2, "source":"再热闹一点"}],
                [goal, {"action":"set", "field":"max_price", "value":None, "source":"再热闹一点"}]):
    out = parse("再热闹一点", changes)
    check(out["intent"] == "clarify" and out["params"] == previous["params"], "Invalid patch leaves all conditions intact")
for intent in ("about_results", "concept"):
    out = parse("第二套是不是更吵", [goal], intent=intent)
    check(g._route(out) == "explain" and out["params"] == previous["params"], "Questions never run search")
check(parse("再热闹一点", None, intent="new_search")["intent"] == "clarify", "Accidental new_search cannot wipe previous filters")
out = parse("重新开始，只找公寓", None, intent="new_search", reset_source="重新开始", property_type="apartment")
check(out["params"]["bedrooms"] is None and out["params"]["property_type"] == "apartment", "Explicit restart resets old conditions")
with patch.object(g, "_ask", side_effect=RuntimeError("offline failure")):
    out = g.parse_intent({**previous, "user_query": "再热闹一点"})
check(out["intent"] == "clarify" and out["params"] == previous["params"], "Parser failure never broadens a live search")

needs = [{"attribute": "quiet", "min_score": 80}, {"attribute": "lively", "min_score": 80}]
incompatible = [row(1, 90, 10), row(2, 10, 90)]
for order in (needs, list(reversed(needs))):
    check(len(g._sanitize({"abstract_needs": order}, "x")["abstract_needs"]) == 2, "Both preferences survive sanitization")
    required = [{**n, "strength": "required"} for n in order]
    out = g.rank({"params": {"abstract_needs": required}, "metrics": incompatible})
    check(out["metrics"] == [] and "已忽略" not in out["ranking"], "No intersection never drops a required condition")
    out = g.rank({"params": {"abstract_needs": order}, "metrics": incompatible})
    check(len(out["metrics"]) == 2 and "已忽略" not in out["ranking"]
          and "优先「安静」" in out["ranking"] and "优先「热闹」" in out["ranking"],
          "Preferred conditions are all kept and stated, none silently dropped")
    out = g.rank({"params": {"abstract_needs": required}, "metrics": incompatible + [row(3, 85, 85)]})
    check([m["id"] for m in out["metrics"]] == [3], "Real compatible candidate is retained (required)")
    out = g.rank({"params": {"abstract_needs": order}, "metrics": incompatible + [row(3, 85, 85)]})
    check([m["id"] for m in out["metrics"]][0] == 3 and len(out["metrics"]) == 3, "Compatible candidate ranks first (preferred)")

fresh = g.prepare_refinement({**params, "sort_by": "price_asc"}, params)
check(fresh["relative_preferences"] is None, "Explicit sort clears relative goals")
fresh = g.prepare_refinement({**params, "abstract_needs": []}, params, ["lively"])
check(fresh["relative_preferences"] is None, "Deleted target clears relative goal")
check('第 6 套' in g._context_message({**previous, "user_query": "看看"}), "Parser context preserves pagination numbering")

with_description = deepcopy(previous['params'])
with_description['description_query'] = 'balcony; private garden'
changed = r.apply_changes(with_description, [goal], '再热闹一点', previous['metrics'], g._sanitize, g._SORT_FIELDS)
clean = g.prepare_refinement(changed, with_description)
check('balcony' in clean['semantic_query'] and 'private garden' in clean['semantic_query'], 'Unstructured details survive unrelated refinement')
changed = r.apply_changes(with_description, [{'action':'remove','field':'semantic_query','value':'balcony','source':'不要阳台'}],
                          '不要阳台', previous['metrics'], g._sanitize, g._SORT_FIELDS)
clean = g.prepare_refinement(changed, with_description)
check('balcony' not in clean['semantic_query'] and 'private garden' in clean['semantic_query'], 'Removing one descriptive requirement preserves the other')
edited = deepcopy(params)
edited['abstract_needs'].append({'attribute':'lively','min_score':30,'operator':'gte'})
edited['relative_preferences'] = params['relative_preferences']
edited2 = deepcopy(edited); edited2['abstract_needs'][-1]['min_score'] = 40
check(g.prepare_refinement(edited2, edited)['relative_preferences'] is None, 'Editing a target score explicitly clears its old relative objective')
down = r.relative_rank([row(1,80,20,690000),row(2,80,20,650000),row(3,80,20,200000)],
                      [{'field':'price','baseline':700000,'direction':'decrease','degree':'slight'}], [], [])
check(down and all(600000 < m['price'] < 700000 for m in down), 'Cheaper means a local downward price step rather than the cheapest extreme')
with patch.object(g, '_ask_streaming', return_value='解释测试') as explain_model:
    g.explain({**previous, **ranked, 'params':params, 'user_query':'再热闹一点', 'intent':'refine'})
    sent_context = explain_model.call_args.args[1]
    check('previous_displayed_median' in sent_context and 'current_displayed_median' in sent_context,
          'Explanation receives actual previous and current baselines')
    check('不能说满足它的房源与本轮诉求相反' in sent_context and 'quiet' in sent_context,
          'Explanation treats retained quiet as part of the request')

def lively_state(need):
    return {"params": g._sanitize({"bedrooms": 2, "abstract_needs": [need]}, "lively flat"),
            "metrics": [row(1, 20, 95), row(2, 22, 96), row(3, 24, 97)], "more": [], "ranking": "", "batch_offset": 0}
quieter = {"action": "relative", "field": "quiet", "direction": "increase", "degree": "slight", "source": "再安静一点"}
def quieter_turn(need, pool):
    base = lively_state(need)
    with patch.object(g, "_ask", return_value=json.dumps({"intent": "refine", "changes": [quieter]})):
        parsed = g.parse_intent({**base, "user_query": "再安静一点"})
    return base, g.rank({**base, **parsed, "metrics": pool})
inferred = {"attribute": "lively", "min_score": 60, "operator": "gte", "strength": "preferred", "value_source": "inferred"}
roomy = [row(20 + i, 33 + i % 3, 90 - i) for i in range(8)] + [row(40, 80, 30)]
base, out = quieter_turn(inferred, roomy)
check("params" not in out, "Enough candidates inside the original floor: no relaxation")
check(all(m["context_scores"]["quiet"] >= 22 + 10 for m in out["metrics"]), "A slight step moves the score by at least 10")
check(all(m["context_scores"]["lively"] >= 60 for m in out["metrics"]), "Opposite preference is retained as a floor")
check("「热闹」" in out["ranking"] and "「安静」" in out["ranking"], "Both score movements are reported")
tight = [row(50, 33, 70), row(51, 34, 55), row(52, 34, 54), row(53, 35, 52), row(54, 33, 51), row(55, 34, 40)]
base, out = quieter_turn(inferred, tight)
needs = {n["attribute"]: n for n in out["params"]["abstract_needs"]}
check(needs["lively"]["min_score"] == 50, "Inferred opposite floor relaxes by one step to 50")
check(len(out["metrics"]) == 5 and all(m["context_scores"]["lively"] >= 50 for m in out["metrics"]), "Relaxed floor is still enforced")
check("放宽到 ≥50" in out["ranking"].split(";")[0], "Relaxation is stated first in the ranking notes")
check(not out.get("turn_notice"), "New results still get normal presentation and explanation")
at_floor = dict(inferred, min_score=50)
base, out = quieter_turn(at_floor, tight[:3] + [row(55, 34, 40)])
check("params" not in out and len(out["metrics"]) == 3, "Floor at 50 is not relaxed further")
check("只有 3 套" in out["ranking"] and "需要你确认" in out["ranking"], "Below 50 needs the user's confirmation")
for need in (dict(inferred, min_score=70, value_source="explicit"), dict(inferred, strength="required"),
             {"attribute": "lively", "min_score": 60}):
    base, out = quieter_turn(need, tight + [row(60, 33, 75)])
    check("params" not in out, f"Non-inferred floor is never relaxed: {need}")
    check(all(m["context_scores"]["lively"] >= need["min_score"] for m in out["metrics"]), "Original floor enforced")
base, out = quieter_turn(dict(inferred, min_score=70, value_source="explicit"), tight + [row(60, 33, 75)])
check("明确要求" in out["ranking"], "Explicit floor explains why it was not lowered")

reaffirm = {"action": "prioritize", "field": "lively", "source": "还是要热闹"}
slightly = dict(quieter, source="稍微安静点")
for order in ([slightly, reaffirm], [reaffirm, slightly]):
    base = lively_state(inferred)
    with patch.object(g, "_ask", return_value=json.dumps({"intent": "refine", "changes": order})):
        out = g.parse_intent({**base, "user_query": "稍微安静点，但还是要热闹"})
    goals = out["params"]["relative_preferences"] or []
    check(out["intent"] == "refine" and [x["field"] for x in goals] == ["quiet"], "Reaffirming the opposite keeps the quieter goal")
    check(out["params"]["sort_by"] is None and out["params"]["abstract_needs"] == base["params"]["abstract_needs"],
          "Reaffirmation neither re-sorts by lively nor changes the lively floor")

print(f"Relative refinement: {checks} checks passed (offline, no LLM calls).")
