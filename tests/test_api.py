import json
import sys
import os
from copy import deepcopy
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.analytics import assumptions
from app.api import server
from app.orchestration import graph as g


_PARSE_REPLY = json.dumps({
    "intent": "new_search", "semantic_query": "quiet three bedroom house",
    "max_price": 900000, "min_price": None, "bedrooms": 3, "bathrooms": None,
    "property_type": "house", "suburb": None, "sort_by": "quiet", "min_gross_yield": None,
    "amenity_needs": [{"kind": "train_station", "max_distance_m": 1500}], "near_place": None,
    "abstract_needs": [{"attribute": "quiet", "min_score": 60}],
    "unsupported_asks": ["采光"], "school_zone": None, "planning_needs": ["no_risk_overlay"],
})


def _fake_ask(system, user, temperature=0.0):
    return _PARSE_REPLY


def _fake_ask_streaming(system, user, temperature=0.0):
    from langgraph.config import get_stream_writer
    writer = get_stream_writer()
    for piece in ("这是", "一段", "测试说明"):
        writer({"token": piece})
    return "这是一段测试说明"


g._ask = _fake_ask
g._ask_streaming = _fake_ask_streaming


def _events(response_text: str) -> list[tuple[str, dict]]:
    out = []
    for block in response_text.split("\n\n"):
        event, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            out.append((event, json.loads(data)))
    return out


with TestClient(server.app) as client:
    meta = client.get("/api/meta").json()
    assert "quiet" in meta["attributes"] and meta["attributes"]["quiet"] == "安静"
    assert "gross_yield" in meta["sort_labels"] and "quiet" in meta["sort_labels"]
    assert "train_station" in meta["kinds"]
    assert "no_heritage" in meta["planning_needs"]
    assert meta["assumptions"]["snapshot"]["opex_rate"] == assumptions.opex_rate()
    assert "major_road_m" in meta["evidence_labels"]
    assert meta["property_types"]["house"] == "独立屋"

    from app.analytics.formulas import investment_metrics

    before_recalc = assumptions.snapshot()
    body = {"price": 555000, "annual_rent": 20748,
            "opex_rate": 0.28, "other_acquisition_costs": 2000}
    r = client.post("/api/recalc", json=body)
    assert r.status_code == 200
    assert r.json() == investment_metrics(555000, 20748, 0.28, 2000)

    r2 = client.post("/api/recalc", json={**body, "other_acquisition_costs": 5000}).json()
    assert r2["total_cost"] == r.json()["total_cost"] + 3000
    assert r2["noi"] == r.json()["noi"]
    assert r2["roi"] < r.json()["roi"]

    r3 = client.post("/api/recalc", json={**body, "opex_rate": 0.20}).json()
    assert r3["noi"] > r.json()["noi"] and r3["roi"] > r.json()["roi"]
    assert r3["total_cost"] == r.json()["total_cost"]

    assert client.post("/api/recalc", json={**body, "opex_rate": 1.5}).status_code == 400
    assert client.post("/api/recalc", json={**body,
                                            "other_acquisition_costs": 999_999}).status_code == 400

    assert assumptions.snapshot() == before_recalc

    from app.amenities import nearby as _nb

    house = {"lat": -37.87, "lon": 144.83, "label": "Altona Meadows"}
    r = client.post("/api/measure", json={"origin": house,
                                          "target": {"q": "Flinders Street Station"}})
    assert r.status_code == 200
    j = r.json()
    assert j["distance_m"] == _nb.distance_between(
        house["lat"], house["lon"], j["target"]["latitude"], j["target"]["longitude"])
    assert j["target"]["kind"] == "train_station", j["target"]

    r = client.post("/api/measure", json={"origin": house, "target": {"q": "Chadstone"}})
    assert r.json()["target"]["kind"] == "mall", r.json()["target"]

    r = client.post("/api/measure", json={"origin": {"q": "Werribee"},
                                          "target": {"q": "Melbourne Airport"}})
    assert r.status_code == 200 and r.json()["distance_m"] > 20_000

    assert client.post("/api/measure", json={"origin": house,
                                             "target": {"q": "zzzz no such place"}}).status_code == 404
    assert client.post("/api/measure", json={"origin": {}, "target": {"q": "Werribee"}}).status_code == 400

    original = assumptions.snapshot()
    r = client.post("/api/assumptions", json={"opex_rate": 0.30})
    assert r.status_code == 200 and any("30.0%" in s for s in r.json()["describe"])
    r = client.post("/api/assumptions", json={"opex_rate": 1.5})
    assert r.status_code == 400 and assumptions.opex_rate() == 0.30
    assumptions.set_rate("opex_rate", original["opex_rate"])

    thread = "test-thread-1"
    r = client.post("/api/chat", json={"thread_id": thread, "message": "90万以内安静的三房独栋"})
    assert r.status_code == 200
    events = _events(r.text)
    names = [e for e, _ in events]
    assert "params" in names and "results" in names and "ranking" in names and "answer" in names
    assert names[-1] == "done", names[-3:]
    assert names.index("params") < names.index("results") < names.index("answer")
    tokens = [d["t"] for e, d in events if e == "token"]
    assert tokens == ["这是", "一段", "测试说明"], tokens
    answer = next(d for e, d in events if e == "answer")["answer"]
    assert answer == "这是一段测试说明"

    params = next(d for e, d in events if e == "params")["params"]
    assert params["bedrooms"] == 3 and params["max_price"] == 900000 and params["sort_by"] == "quiet"
    assert params["planning_needs"] == ["no_risk_overlay"]

    metrics = next(d for e, d in events if e == "results")["metrics"]
    assert 1 <= len(metrics) <= 5
    for m in metrics:
        assert m["bedrooms"] == 3 and m["price"] <= 900000 and m["property_type"] == "house"
        for key in ("planning", "school_zones", "crime", "amenities", "context_scores", "context_evidence",
                    "stamp_duty", "predicted_price", "valuation_range", "valuation_interval",
                    "valuation_position", "assumptions", "rent_source"):
            assert key in m, f"结果里缺 {key}"
        assert m["rent_source"] in ("precinct_exact_sheet", "precinct_all_properties",
                                    "region_exact_sheet", "region_all_properties")
        assert m["context_scores"]["quiet"] >= 60
        assert m["amenities"]["train_station"]["distance_m"] <= 1500
    done = next(d for e, d in events if e == "done")
    assert done["intent"] == "new_search" and done["count"] == len(metrics)

    before_delete = deepcopy(params)
    before_delete["abstract_needs"].append({"attribute": "school_access", "min_score": 60})
    response = client.post("/api/refine", json={"thread_id": thread, "params": before_delete})
    base_events = _events(response.text)
    assert base_events[-1][0] == "done", base_events[-1]
    baseline = next(d for e, d in base_events if e == "done")["params"]
    removed = deepcopy(baseline)
    removed["abstract_needs"] = [n for n in removed["abstract_needs"] if n["attribute"] != "quiet"]
    removed["semantic_query"] = "peaceful quiet three bedroom house away from traffic"
    with patch.object(g, "search_properties", wraps=g.search_properties) as queried:
        response = client.post("/api/refine", json={"thread_id": thread, "params": removed})
        assert queried.call_count == 1, "删除条件没有重新检索数据库"
        args = queried.call_args.kwargs
        assert args["limit"] == g.CANDIDATE_LIMIT and args["bedrooms"] == 3
        assert not any(word in args["semantic_query"].lower() for word in ("quiet", "peaceful", "traffic"))
    deletion_events = _events(response.text)
    assert deletion_events[-1][0] == "done", deletion_events[-1]
    deleted = next(d for e, d in deletion_events if e == "done")
    assert deleted["params"]["sort_by"] is None
    assert deleted["params"]["abstract_needs"] == [{"attribute": "school_access", "min_score": 60}]
    assert deleted["params"]["max_price"] == 900000 and deleted["params"]["property_type"] == "house"
    assert "按语义相关度排序" in deleted["ranking"] and "安静" not in deleted["ranking"]
    assert [d["node"] for e, d in deletion_events if e == "node"] == ["search", "analyze", "enrich", "rank", "present", "explain"]
    if os.environ.get("REFINE_FIXTURE_PATH"):
        fixture = {"initial": {"params": baseline,
                    "metrics": next(d for e, d in base_events if e == "results")["metrics"]},
                   "removed": {"params": deleted["params"],
                    "metrics": next(d for e, d in deletion_events if e == "results")["metrics"]}}
        Path(os.environ["REFINE_FIXTURE_PATH"]).write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")

    other_sort = {**removed, "sort_by": "price_asc"}
    assert g.prepare_refinement(other_sort, baseline)["sort_by"] == "price_asc"
    assert g.prepare_refinement({**deleted["params"], "sort_by": "quiet"}, deleted["params"])["sort_by"] == "quiet"
    assert g.prepare_refinement(removed, {}, ["quiet"])["sort_by"] is None, "旧页面在服务重启后删除也必须生效"

    config = g.new_session(thread)
    committed = deepcopy(server.GRAPH.get_state(config).values)
    original_stream = server.GRAPH.stream
    def stream_then_fail(*args, **kwargs):
        for event in original_stream(*args, **kwargs):
            yield event
            if event[0] == "updates" and "present" in event[1]:
                raise RuntimeError("intentional failure after results")
    for failure in (patch.object(g, "search_properties", side_effect=RuntimeError("intentional search failure")),
                    patch.object(server.GRAPH, "stream", side_effect=stream_then_fail)):
        with failure:
            response = client.post("/api/refine", json={"thread_id": thread, "params": other_sort})
        failed_events = _events(response.text)
        assert failed_events[-1][0] == "error" and not failed_events[-1][1].get("rollback_failed"), failed_events[-1]
        restored = server.GRAPH.get_state(config).values
        for key in ("params", "metrics", "more", "ranking", "history", "user_query", "batch_offset"):
            assert restored.get(key) == committed.get(key), f"失败后没有恢复 {key}"
    lock = server._lock_for(thread)
    lock.acquire()
    try:
        response = client.post("/api/refine", json={"thread_id": thread, "params": other_sort})
        assert _events(response.text)[0][0] == "error"
        assert server.GRAPH.get_state(config).values["params"] == committed["params"], "繁忙会话的条件被提前覆盖"
    finally:
        lock.release()

    below = deepcopy(deleted["params"])
    below["abstract_needs"] = [{"attribute": "quiet", "min_score": 60, "operator": "lt"}]
    below["amenity_needs"] = []
    below["planning_needs"] = []
    events = _events(client.post("/api/refine", json={"thread_id": thread, "params": below}).text)
    assert events[-1][0] == "done", events[-1]
    below_metrics = next(d for e, d in events if e == "results")["metrics"]
    assert below_metrics and all(m["context_scores"]["quiet"] < 60 for m in below_metrics)
    assert events[-1][1]["params"]["abstract_needs"] == below["abstract_needs"]
    assert "评分 < 60" in events[-1][1]["ranking"]

    edited = dict(params)
    edited["bedrooms"] = 2
    edited["sort_by"] = "gross_yield"
    edited["abstract_needs"] = []
    edited["amenity_needs"] = []
    edited["planning_needs"] = []
    r = client.post("/api/refine", json={"thread_id": thread, "params": edited})
    assert r.status_code == 200
    events = _events(r.text)
    names = [e for e, _ in events]
    assert "params" not in names, "refine 不该再走 parse_intent"
    assert names[0] == "node" and events[0][1]["node"] == "search"
    metrics2 = next(d for e, d in events if e == "results")["metrics"]
    assert metrics2 and all(m["bedrooms"] == 2 for m in metrics2)
    yields = [m["gross_yield"] for m in metrics2]
    assert yields == sorted(yields, reverse=True), "没按毛回报排序"
    done2 = next(d for e, d in events if e == "done")
    assert done2["intent"] == "refine" and done2["params"]["bedrooms"] == 2

    junk = dict(params)
    junk.update({"sort_by": "drop table", "bedrooms": -3, "property_type": "castle",
                 "planning_needs": ["no_such_need"], "max_price": "很多"})
    r = client.post("/api/refine", json={"thread_id": thread, "params": junk})
    done3 = next(d for e, d in _events(r.text) if e == "done")
    p3 = done3["params"]
    assert p3["sort_by"] is None and p3["bedrooms"] is None and p3["property_type"] is None
    assert p3["planning_needs"] is None and p3["max_price"] is None

    assert client.post("/api/chat", json={"thread_id": thread, "message": "   "}).status_code == 400

print("web API: all tests passed (metadata, assumptions, recalculation, distance tool, streamed chat events, condition-card refine, parameter checks)")
