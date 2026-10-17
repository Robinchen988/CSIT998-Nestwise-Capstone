# Development Guide

For anyone who wants to change or extend the code. The project rules: every number is labelled by
origin, nothing is fabricated, distances are straight-line, and the dataset itself is
never edited to fix data problems.

---

## Project layout

```
app/
  api/server.py          FastAPI app: HTTP endpoints, server-sent-event streaming, static files
  orchestration/
    graph.py             LangGraph pipeline (parse_intent → search → analyze → enrich → rank → present → explain)
    refinement.py        multi-turn "changes" protocol (cheaper / quieter / drop X …)
  search/search.py       hybrid search: exact SQL filters + pgvector semantic similarity
  analytics/
    formulas.py          stamp duty, gross yield, NOI, cap rate, ROI (pure functions)
    assumptions.py       the two editable assumptions, labelled on every output
    valuation.py         XGBoost valuation model (inference) + 80% ranges
    train_valuation.py   training; crossfit_/calibrate_valuation.py = out-of-fold estimates
  amenities/
    registry.py          single source of truth: 30 data sources, 15 lifestyle attributes, unsupported asks
    nearby.py            straight-line distances to OSM amenities (spatial index)
    context.py           lifestyle scores (percentile against the whole city)
    zones.py             school catchments + LGA crime
    planning.py          planning zones and overlays
  auth/                  accounts (scrypt passwords, cookie sessions) and favourites
  core/config.py         reads .env; refuses to start without required settings
  core/db.py             Postgres connection pool
  i18n.py                backend strings (zh/en)
  web/                   the frontend (no build step)
    index.html, app.css, app.js   main single-page app
    i18n.js              all UI text, Chinese + English
    auth.js, favorites.js         login dialog, favourites drawer
    map-glass.js         vector basemap (OpenFreeMap + MapLibre GL) with raster fallback
    showroom/            homepage 3D scene (three.js module + baked GLB/textures, ~19 MB)
db/
  docker-compose.yml     Postgres 16 + pgvector on host port 15432
  schema.sql             tables: properties, users, sessions, favorites
  setup_db.py            one-command setup: restores db/seed/properties.dump
  seed/properties.dump   20,800 properties with embeddings (pg_dump custom format)
data/                    pinned geographic snapshots (OSM, planning, school zones, crime, rents)
models/                  trained valuation model + metadata + out-of-fold estimates
pipeline/                the loader and rent-join scripts the tests check
eval/                    evaluation question set and runner
tests/                   plain test scripts (no pytest)
docs/en/                 these guides
serve.py                 start the web app
```

## How one question flows

```
browser ──POST /api/chat──▶ graph:
  parse_intent   LLM turns the sentence into structured params (filters, attributes, sort)
  search         SQL hard filters + pgvector similarity → candidate pool
  analyze        valuation model + investment formulas for every candidate
  enrich         distances, lifestyle scores, school zone, planning, crime
  rank           filter by attribute scores, sort, take the top 5 (rest kept for "next batch")
  present        build the cards/details payload (pure code, no LLM)
  explain        LLM writes the explanation from the computed facts only (streamed)
◀── server-sent events: node, params, results, ranking, token…, answer, done
```

The LLM touches only `parse_intent` and `explain`. Every number in `results` comes from
SQL or Python. When there are no results, `explain` is skipped and a fixed message is used.

## HTTP API

All endpoints are under `http://localhost:8000`. The streaming endpoints return
`text/event-stream`. Event types are `node`, `params`, `results`, `ranking`, `more`,
`token`, `answer`, `done` and `error`.

| Method & path | Body / params | Purpose |
|---|---|---|
| `GET /api/meta?lang=en` | — | labels, attribute names, assumptions, model metrics for the UI |
| `POST /api/chat` | `{thread_id, message, lang}` | new question or follow-up (stream) |
| `POST /api/refine` | `{thread_id, params, lang, removed_attributes}` | re-run with edited filters, no LLM parsing (stream) |
| `POST /api/rebatch` | `{thread_id, offset, lang}` | next batch of the same shortlist; only re-explains (stream) |
| `GET /api/property/{id}?lang=` | — | live detail for one property (used by favourites/compare) |
| `GET /api/session/{thread_id}` | — | is this conversation still on the server? (`active`, `params`, shown ids) — used to restore after a page refresh |
| `POST /api/measure` | `{origin:{q\|lat,lon,label}, target:{…}}` | resolve place names and measure straight-line distance |
| `POST /api/recalc` | `{price, annual_rent, opex_rate, other_acquisition_costs}` | recompute costs/returns for one property without changing global settings |
| `POST /api/assumptions` | `{opex_rate?, other_acquisition_costs?}` | change the server-wide assumptions |
| `POST /api/auth/register` | `{username, email?, password}` | create account + session cookie |
| `POST /api/auth/login` | `{login, password}` | username or email |
| `POST /api/auth/logout`, `GET /api/auth/me` | — | |
| `GET /api/favorites` | — | list (login required) |
| `PUT /api/favorites/{property_id}` | — | add (snapshot taken server-side) |
| `DELETE /api/favorites/{property_id}` | — | remove |

`thread_id` is any string the client generates. Conversation state is kept in memory per thread.

The search function can also be used directly from Python:

```python
from app.search.search import search_properties
search_properties(semantic_query="quiet apartment close to the city", max_price=800_000, bedrooms=2,
                  max_distance_cbd_km=10)   # optional: straight-line km to the CBD
```

## Tests

Plain scripts. Run them from the project root:

```bash
# all Python tests (Windows Git Bash / macOS / Linux)
for f in tests/test_*.py; do python "$f" || echo "FAILED: $f"; done
```

| Needs | Tests |
|---|---|
| nothing (offline) | `test_formulas`, `test_amenities`, `test_context`, `test_registry`, `test_refinement`, `test_*_offline` (incl. paraphrases, exclusions, explanation facts) |
| the database | `test_api`, `test_auth`, `test_cbd_distance_filter`, `test_orchestration`, `test_planning`, `test_property_detail`, `test_refinement_api`, `test_zones_crime` |
| an LLM key | none. LLM calls are replaced with fixed responses. |

Node checks (`node tests/test_*.mjs` / `.cjs`) cover number formatting, i18n, the homepage
scene and some UI flows. `tests/test_*_browser.mjs` drive a real Chrome with real mouse events
against a running server (`node tests/test_add_condition_browser.mjs 8000 zh`): compare,
distance sort, CBD filter, map/card sync, detail docking, refresh restore, add-filter menu. A few browser fixtures (`test_conversation_browser.mjs`,
`test_relative_browser.mjs`) are manual harnesses that need extra environment variables
(`CONVERSATION_FIXTURE`, `CONVERSATION_URL` or `CONVERSATION_TEST_PORT`, and `RELATIVE_FIXTURE`).

On a Chinese-locale Windows console, set `PYTHONIOENCODING=utf-8` before running tests.

## Retraining the valuation model (optional)

The data in `data/` and `db/seed/` is pinned so that results stay reproducible. Retrain the
valuation model with `python -m app.analytics.train_valuation`.

## Conventions

- **Frontend**: no framework, no bundler. Edit `app/web/*` and refresh the page; static
  files are served with `Cache-Control: no-cache`. Python changes need a server restart.
- **All UI text** goes through `app/web/i18n.js` (both languages) and backend text through `app/i18n.py`.
- **Visibility must never depend on an animation finishing.** If an animation doesn't run,
  the element must still be visible.
- **Never commit `.env`.**
