import asyncio
import json
import math
import sys
import threading
from copy import deepcopy
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from langgraph.types import Overwrite

from app.amenities import context, nearby, planning, registry, suburb_stats, zones
from app.analytics import assumptions
from app.analytics.valuation import warm_up as valuation_warm_up
from app import i18n
from app.analytics.formulas import investment_metrics
from app.orchestration import graph as g
from app.auth import favorites as auth_favorites, routes as auth_routes, store as auth_store
from app.orchestration.graph import GRAPH, new_session
from app.search.search import _get_model

WEB = Path(__file__).resolve().parents[1] / "web"

PROPERTY_TYPE_ZH = {"house": "独立屋", "apartment": "公寓", "townhouse": "联排"}

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_meta_cache: dict | None = None


def _lock_for(thread_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(thread_id, threading.Lock())


def warm_up() -> dict:
    _get_model()
    meta = valuation_warm_up()
    context.warm_up()
    nearby.warm_up()
    zones.warm_up()
    suburb_stats.warm_up()
    planning.warm_up()
    return meta


def _say(text: str) -> None:
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(enc, errors="replace").decode(enc), flush=True)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _say("Loading the model and the map data (about 20 s)...")
    await asyncio.to_thread(auth_store.ensure_schema)
    meta = await asyncio.to_thread(warm_up)
    m = meta["metrics"]
    _say(f"Ready: valuation model R² {m['r2']:.3f}. You can start asking.")
    yield


class _RevalidatingStatic(StaticFiles):

    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        r.headers["Cache-Control"] = "no-cache"
        return r


app = FastAPI(title="Nestwise", lifespan=_lifespan)
app.include_router(auth_routes.router)
app.include_router(auth_favorites.router)
app.mount("/static", _RevalidatingStatic(directory=str(WEB)), name="static")


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})


def _build_meta() -> dict:
    global _meta_cache
    if _meta_cache is None:
        vmeta = valuation_warm_up()
        _meta_cache = {
            "attributes": dict(context.ATTRIBUTE_ZH),
            "attribute_parts": {k: list(v["parts"]) for k, v in context.ATTRIBUTES.items()},
            "attribute_notes": {k: v.get("note", "") for k, v in context.ATTRIBUTES.items()},
            "evidence_labels": {k: list(registry.evidence_label(k))
                                for k in registry.all_evidence_keys()},
            "kinds": dict(nearby.KIND_ZH),
            "sort_labels": dict(g._SORT_LABEL),
            "planning_needs": {k: v[0] for k, v in g._PLANNING_NEEDS.items()},
            "property_types": PROPERTY_TYPE_ZH,
            "valuation_error_by_type": vmeta.get("error_by_type") or {},
            "unsupported": dict(registry.UNSUPPORTED),
            "crime_year": suburb_stats.warm_up().get("year"),
        }
    return {**_meta_cache,
            "assumptions": {"describe": assumptions.describe(),
                            "snapshot": assumptions.snapshot()}}


@app.get("/api/meta")
async def meta(lang: str = "zh"):
    built = _build_meta()
    return i18n.translate_meta(built) if lang == "en" else built


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _snapshot_ok(snap) -> bool:
    return (isinstance(snap, dict) and all(
        isinstance(snap.get(k), (int, float)) and not isinstance(snap.get(k), bool) and math.isfinite(snap[k]) for k in ('opex_rate', 'other_acquisition_costs')))


def _round_assumption_snapshot(state):
    sources = [(state.get("assumption_snap") or {}).get("display") if isinstance(state.get("assumption_snap"), dict) else None]
    sources += [m.get("assumptions") for m in (state.get("metrics") or [])[:1] if isinstance(m, dict)]
    return next((s for s in sources if _snapshot_ok(s)), None)


def _describe_snapshot_zh(snap) -> list[str]:
    return assumptions.describe(snap)


def _done_assumptions(state, lang: str) -> list[str]:
    snap = _round_assumption_snapshot(state)
    if snap is None:
        snap_now = assumptions.snapshot()
        return i18n.assumptions_describe_en(snap_now) if lang == "en" else assumptions.describe(snap_now)
    return i18n.assumptions_describe_en(snap) if lang == "en" else _describe_snapshot_zh(snap)


async def _run(thread_id: str, inputs, config, lang: str = "zh", refinement=None):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def put(item):
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def producer():
        lock = _lock_for(thread_id)
        if not lock.acquire(blocking=False):
            put(("error", {"message": "One request is still running — give it a moment"
                                      if lang == "en" else "上一条还在处理,稍等一下再发"}))
            put(None)
            return
        previous = None
        try:
            previous = deepcopy(GRAPH.get_state(config).values or {})
            if refinement is not None:
                clean = g.prepare_refinement(refinement.params, previous.get("params") or {},
                                             refinement.removed_attributes)
                clean["relative_preferences"] = None
                change = "(通过条件卡修改,以这些条件为准,未列出的旧偏好不再使用): " + json.dumps(clean, ensure_ascii=False)
                GRAPH.update_state(config,
                    {"user_query": change, "intent": "refine", "params": clean,
                     "refinement_base": g.refinement.snapshot(previous), "turn_notice": None,
                     "history": [{"role": "用户", "text": change}], "lang": lang},
                    as_node="parse_intent")
            for mode, chunk in GRAPH.stream(inputs, config=config, stream_mode=["updates", "custom"]):
                if mode == "custom":
                    if isinstance(chunk, dict) and "token" in chunk:
                        put(("token", {"t": chunk["token"]}))
                    continue
                for node, out in chunk.items():
                    out = out or {}
                    put(("node", {"node": node}))
                    if node == "parse_intent":
                        put(("params", {"intent": out.get("intent"), "params": out.get("params")}))
                    elif node == "rank":
                        put(("ranking", {"ranking": out.get("ranking"),
                                         "count": len(out.get("metrics") or [])}))
                        if out.get("more") is not None:
                            put(("more", {"metrics": i18n.apply(out.get("more"), lang)}))
                        if not out.get("metrics"):
                            put(("results", {"metrics": []}))
                    elif node == "present" and out.get("metrics") is not None:
                        put(("results", {"metrics": i18n.apply(out.get("metrics"), lang)}))
                    elif node == "explain":
                        put(("answer", {"answer": out.get("answer")}))
            state = GRAPH.get_state(config).values or {}
            put(("done", {"intent": state.get("intent"), "params": state.get("params"),
                          "notice": state.get("turn_notice"), "batch_offset": state.get("batch_offset") or 0,
                          "ranking": state.get("ranking"),
                          "count": len(state.get("metrics") or []),
                          "assumptions": _done_assumptions(state, lang)}))
        except Exception as exc:
            if previous is not None:
                restore = {key: previous.get(key) for key in g.State.__annotations__}
                restore["history"] = Overwrite(previous.get("history") or [])
                try:
                    GRAPH.update_state(config, restore, as_node="explain")
                except Exception as restore_error:
                    put(("error", {"message": f"{type(exc).__name__}: {exc}; restore: {restore_error}",
                                   "rollback_failed": True}))
                    return
            put(("error", {"message": f"{type(exc).__name__}: {exc}"}))
        finally:
            lock.release()
            put(None)

    loop.run_in_executor(None, producer)
    while True:
        item = await queue.get()
        if item is None:
            break
        yield _sse(*item)


class ChatIn(BaseModel):
    thread_id: str
    message: str
    lang: str = "zh"


class RefineIn(BaseModel):
    thread_id: str
    params: dict
    lang: str = "zh"
    removed_attributes: list[str] = []


class AssumeIn(BaseModel):
    opex_rate: float | None = None
    other_acquisition_costs: float | None = None


@app.post("/api/chat")
async def chat(body: ChatIn):
    message = body.message.strip()
    if not message:
        raise HTTPException(400, "Empty message")
    config = new_session(body.thread_id)
    return StreamingResponse(_run(body.thread_id, {"user_query": message, "lang": body.lang},
                                  config, body.lang),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/refine")
async def refine(body: RefineIn):
    config = new_session(body.thread_id)
    return StreamingResponse(_run(body.thread_id, None, config, body.lang, refinement=body),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class RebatchIn(BaseModel):
    thread_id: str
    offset: int = 0
    lang: str = "zh"


@app.post("/api/rebatch")
async def rebatch(body: RebatchIn):
    config = new_session(body.thread_id)
    prev = (GRAPH.get_state(config).values or {})
    pool = prev.get("more") or prev.get("metrics") or []
    if not pool:
        raise HTTPException(status_code=400, detail="This turn has no candidates to page through")

    limit = g.RESULT_LIMIT
    offset = max(0, int(body.offset))
    if offset >= len(pool):
        offset = 0
    batch = pool[offset:offset + limit]
    if not batch:
        raise HTTPException(status_code=400, detail="No more candidates")

    GRAPH.update_state(
        config,
        {"metrics": batch,
         "batch_offset": offset,
         "user_query": prev.get("user_query") or "找房",
         "intent": "new_search",
         "turn_notice": None, "refinement_base": None,
         "history": [{"role": "用户",
                      "text": f"(换了一批,看第 {offset + 1}–{offset + len(batch)} 套)"}],
         "lang": body.lang},
        as_node="present",
    )
    return StreamingResponse(_run(body.thread_id, None, config, body.lang),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class MeasurePoint(BaseModel):
    q: str | None = None
    lat: float | None = None
    lon: float | None = None
    label: str | None = None


class MeasureIn(BaseModel):
    origin: MeasurePoint
    target: MeasurePoint


def _resolve_point(pt: MeasurePoint, near: tuple | None, side: str) -> dict:
    if pt.lat is not None and pt.lon is not None:
        return {"name": pt.label or f"{pt.lat:.4f}, {pt.lon:.4f}", "kind": None,
                "latitude": float(pt.lat), "longitude": float(pt.lon), "matches": 1}
    if not (pt.q or "").strip():
        raise HTTPException(400, f"{side} is missing")
    found = nearby.place_point(pt.q, near=near)
    if not found:
        raise HTTPException(404, f"Could not find \"{pt.q}\". Place names come from OpenStreetMap and must be in English; "
                                 f"try a fuller name.")
    return found


@app.post("/api/measure")
async def measure(body: MeasureIn):
    o, t = body.origin, body.target
    anchor_o = (o.lat, o.lon) if o.lat is not None and o.lon is not None else None
    anchor_t = (t.lat, t.lon) if t.lat is not None and t.lon is not None else None
    origin = _resolve_point(o, anchor_t, "Origin")
    target = _resolve_point(t, (origin["latitude"], origin["longitude"]), "Destination")
    dist = nearby.distance_between(origin["latitude"], origin["longitude"],
                                   target["latitude"], target["longitude"])
    return {"origin": origin, "target": target, "distance_m": dist}


class RecalcIn(BaseModel):
    price: float | None = None
    annual_rent: float | None = None
    opex_rate: float
    other_acquisition_costs: float


@app.get("/api/session/{thread_id}")
async def session_state(thread_id: str):
    values = (await asyncio.to_thread(GRAPH.get_state, new_session(thread_id))).values or {}
    params = values.get("params") or None
    return {"active": bool(params), "params": params,
            "batch_offset": int(values.get("batch_offset") or 0),
            "ids": [m.get("id") for m in values.get("metrics") or []]}


@app.get("/api/property/{property_id}")
async def property_detail(property_id: int, lang: str = "zh"):
    lang = "en" if lang == "en" else "zh"
    metrics = await asyncio.to_thread(g.detail_metrics, [property_id], lang)
    if not metrics:
        raise HTTPException(404, "property not found")
    return {"metric": i18n.apply(metrics, lang)[0]}


@app.post("/api/recalc")
async def recalc(body: RecalcIn):
    rate = float(body.opex_rate)
    fees = float(body.other_acquisition_costs)
    lo, hi = assumptions.bounds("opex_rate")
    if not (lo <= rate <= hi):
        raise HTTPException(400, f"The operating-cost rate must be between {lo:.0%} and {hi:.0%}")
    lo, hi = assumptions.bounds("other_acquisition_costs")
    if not (lo <= fees <= hi):
        raise HTTPException(400, f"Other acquisition costs must be between {lo:,.0f} and {hi:,.0f}")
    return investment_metrics(body.price, body.annual_rent, rate, fees)


@app.post("/api/assumptions")
async def set_assumptions(body: AssumeIn):
    try:
        if body.opex_rate is not None:
            assumptions.set_rate("opex_rate", float(body.opex_rate))
        if body.other_acquisition_costs is not None:
            assumptions.set_rate("other_acquisition_costs", float(body.other_acquisition_costs))
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"describe": assumptions.describe(), "snapshot": assumptions.snapshot()}
