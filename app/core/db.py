from contextlib import contextmanager

from pgvector.psycopg2 import register_vector
from psycopg2.pool import SimpleConnectionPool

from app.core.config import DB_DSN

_pool = SimpleConnectionPool(minconn=1, maxconn=5, dsn=DB_DSN)

_vector_registered: set[int] = set()

HNSW_ITERATIVE_SCAN = "relaxed_order"


@contextmanager
def get_connection():
    conn = _pool.getconn()
    try:
        if id(conn) not in _vector_registered:
            register_vector(conn)
            _vector_registered.add(id(conn))
        conn.cursor().execute(f"SET LOCAL hnsw.iterative_scan = {HNSW_ITERATIVE_SCAN}")
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        _pool.putconn(conn)


def close_pool() -> None:
    _pool.closeall()
    _vector_registered.clear()
