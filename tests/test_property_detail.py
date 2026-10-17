import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient

from app.api import server
from app.orchestration import graph as g
from app.search.search import properties_by_ids, search_properties

checks = 0


def check(ok, msg):
    global checks
    assert ok, msg
    checks += 1


with patch.object(g, '_ask', side_effect=AssertionError('detail must not call the LLM')), \
     patch.object(g, '_ask_streaming', side_effect=AssertionError('detail must not call the LLM')), \
     TestClient(server.app) as client:
    rows = search_properties('townhouse near a park', limit=5)
    ids = [r['id'] for r in rows]
    check(len(ids) == 5, 'search returned candidates')

    by_id = properties_by_ids(list(reversed(ids)) + [999999999])
    check([r['id'] for r in by_id] == list(reversed(ids)), 'order kept, unknown id skipped')
    check(all(a == b for a, b in zip(reversed(rows), by_id)), 'same columns and values as search')

    state = {'properties': rows, 'params': {}, 'lang': 'zh', 'user_query': 'x'}
    for node in (g.analyze, g.enrich, g.present):
        state.update(node(state))
    via_search = {m['id']: m for m in state['metrics']}
    via_detail = {m['id']: m for m in g.detail_metrics(ids)}
    check(set(via_search) == set(via_detail), 'same properties')
    for pid in ids:
        a, b = via_search[pid], via_detail[pid]
        diff = [k for k in set(a) | set(b) if a.get(k) != b.get(k)]
        check(not diff, f'property {pid} differs in {diff}')
    check(via_detail[ids[0]].get('predicted_price') is not None and 'gross_yield' in via_detail[ids[0]],
          'detail carries model and formula numbers')

    r = client.get(f'/api/property/{ids[0]}')
    check(r.status_code == 200 and r.json()['metric']['id'] == ids[0], f'api ok: {r.text[:200]}')
    check(r.json()['metric']['price'] == via_detail[ids[0]]['price'], 'api returns same numbers')
    en = client.get(f'/api/property/{ids[0]}?lang=en').json()['metric']
    check(en['id'] == ids[0], 'english variant')
    check(client.get('/api/property/999999999').status_code == 404, 'unknown property -> 404')
    check(client.get('/api/property/abc').status_code == 422, 'non-numeric id rejected')

print(f'按编号取详情 全部通过:{checks} 项检查(与搜索结果同一口径,不调用 LLM)')
