import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import routes, store
from app.core.db import get_connection

router = APIRouter(prefix='/api/favorites')
LIMIT = 500
FIELDS = ('suburb', 'address', 'price', 'bedrooms', 'bathrooms', 'car_spaces', 'property_type',
          'latitude', 'longitude', 'sale_date')


def _user(request: Request):
    return store.user_for_session(request.cookies.get(routes.COOKIE))


def _unauthorized():
    return JSONResponse({'error': 'login_required'}, status_code=401)


@router.get('')
def list_favorites(request: Request):
    user = _user(request)
    if not user:
        return _unauthorized()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT property_id, snapshot, created_at FROM favorites WHERE user_id = %s "
                    "ORDER BY created_at DESC", (user['id'],))
        rows = cur.fetchall()
    return {'items': [{'id': pid, 'snapshot': snap, 'saved_at': at.isoformat()} for pid, snap, at in rows]}


@router.put('/{property_id}')
def add_favorite(property_id: int, request: Request):
    user = _user(request)
    if not user:
        return _unauthorized()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {', '.join(FIELDS)} FROM properties WHERE id = %s", (property_id,))
        row = cur.fetchone()
        if not row:
            return JSONResponse({'error': 'not_found'}, status_code=404)
        cur.execute("SELECT count(*) FROM favorites WHERE user_id = %s", (user['id'],))
        if cur.fetchone()[0] >= LIMIT:
            return JSONResponse({'error': 'limit_reached'}, status_code=409)
        snap = {k: (v.isoformat() if hasattr(v, 'isoformat') else v) for k, v in zip(FIELDS, row)}
        cur.execute("INSERT INTO favorites (user_id, property_id, snapshot) VALUES (%s, %s, %s) "
                    "ON CONFLICT (user_id, property_id) DO NOTHING", (user['id'], property_id, json.dumps(snap)))
    return {'ok': True, 'id': property_id}


@router.delete('/{property_id}')
def remove_favorite(property_id: int, request: Request):
    user = _user(request)
    if not user:
        return _unauthorized()
    with get_connection() as conn:
        conn.cursor().execute("DELETE FROM favorites WHERE user_id = %s AND property_id = %s", (user['id'], property_id))
    return {'ok': True, 'id': property_id}
