import torch
from sentence_transformers import SentenceTransformer

from app.analytics.formulas import roi_order_sql
from app.core.config import EMBED_MODEL
from app.core.db import get_connection

MODEL_NAME = EMBED_MODEL

_model = None


def _pick_device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME, trust_remote_code=True, device=_pick_device())
    return _model


_COLUMNS = """id, suburb, address, property_type, price, bedrooms, bathrooms,
           car_spaces, land_size, building_area, distance_cbd,
           latitude, longitude, annual_rent, description"""

_CBD_LAT, _CBD_LON = -37.8136, 144.9631
_CBD_KM_SQL = """CASE WHEN latitude IS NULL OR longitude IS NULL THEN distance_cbd::float8
        ELSE 2 * 6371.0::float8 * asin(sqrt(least(1.0::float8,
          power(sin(radians(latitude::float8 - %(cbd_lat)s::float8) / 2), 2)
          + cos(radians(%(cbd_lat)s::float8)) * cos(radians(latitude::float8))
            * power(sin(radians(longitude::float8 - %(cbd_lon)s::float8) / 2), 2)))) END"""

_SEARCH_SQL = """
    SELECT """ + _COLUMNS + """
    FROM properties
    WHERE (%(max_price)s IS NULL OR price <= %(max_price)s)
      AND (%(min_price)s IS NULL OR price >= %(min_price)s)
      AND (%(bedrooms)s IS NULL OR bedrooms = %(bedrooms)s)
      AND (%(bathrooms)s IS NULL OR bathrooms = %(bathrooms)s)
      AND (%(property_type)s IS NULL OR property_type = %(property_type)s)
      AND (%(max_distance_cbd_km)s IS NULL OR round((""" + _CBD_KM_SQL + """)::numeric, 1) <= %(max_distance_cbd_km)s)
      AND (%(suburb)s IS NULL OR lower(suburb) = lower(%(suburb)s))
    ORDER BY {order}embedding <=> %(query_vector)s, id
    LIMIT %(limit)s
"""

_ORDER_BY = {
    None: "",
    "price_asc": "CASE WHEN price > 0 THEN 0 ELSE 1 END, price ASC, ",
    "price_desc": "price DESC, ",
    "gross_yield": "annual_rent::float / NULLIF(price, 0) DESC NULLS LAST, ",
    "roi": roi_order_sql() + " DESC NULLS LAST, ",
}


def _order_params(order_by, opex_rate, other_costs) -> dict:
    if order_by == "roi":
        if opex_rate is None or other_costs is None:
            raise ValueError("order_by='roi' needs both opex_rate and other_costs")
        return {"opex_rate": float(opex_rate), "other_costs": float(other_costs)}
    return {"opex_rate": None, "other_costs": None}


def search_properties(
    semantic_query: str,
    max_price: int | None = None,
    min_price: int | None = None,
    bedrooms: int | None = None,
    bathrooms: int | None = None,
    property_type: str | None = None,
    suburb: str | None = None,
    limit: int = 10,
    order_by: str | None = None,
    opex_rate: float | None = None,
    other_costs: float | None = None,
    max_distance_cbd_km: float | None = None,
) -> list[dict]:
    if order_by not in _ORDER_BY:
        raise ValueError(f"order_by must be one of {sorted(k for k in _ORDER_BY if k)} or None")
    order_args = _order_params(order_by, opex_rate, other_costs)
    query_vector = _get_model().encode("search_query: " + semantic_query, normalize_embeddings=True)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(_SEARCH_SQL.format(order=_ORDER_BY[order_by]), {
            "max_price": max_price,
            "min_price": min_price,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "property_type": property_type,
            "suburb": suburb,
            "query_vector": query_vector,
            "limit": limit,
            "max_distance_cbd_km": max_distance_cbd_km,
            "cbd_lat": _CBD_LAT, "cbd_lon": _CBD_LON,
            **order_args,
        })
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]


def properties_by_ids(ids: list[int]) -> list[dict]:
    ids = [int(i) for i in ids]
    if not ids:
        return []
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {_COLUMNS} FROM properties WHERE id = ANY(%s)", (ids,))
        columns = [d[0] for d in cur.description]
        rows = {r[0]: dict(zip(columns, r)) for r in cur.fetchall()}
    return [rows[i] for i in ids if i in rows]
