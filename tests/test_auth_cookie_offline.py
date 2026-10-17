import os
import sys
import types
from contextlib import contextmanager
from pathlib import Path

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _Cur:
    def __init__(self, db):
        self.db = db
        self.row = None

    def execute(self, sql, params=None):
        self.db.queries.append((" ".join(sql.split())[:60], params))
        if sql.lstrip().upper().startswith("SELECT U.ID"):
            self.row = self.db.sessions.get(params[0])

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


class _Db:
    def __init__(self):
        self.queries, self.sessions = [], {}

    @contextmanager
    def get_connection(self):
        conn = types.SimpleNamespace(cursor=lambda: _Cur(self))
        yield conn


DB = _Db()
stub = types.ModuleType("app.core.db")
stub.get_connection = DB.get_connection
sys.modules["app.core.db"] = stub

from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.auth import favorites, routes, store

failures = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


BAD = ["é", "令牌", "abcÿdef", "Ã©"]
for tok in BAD:
    DB.queries.clear()
    try:
        got = store.user_for_session(tok)
        check(got is None and not DB.queries, f"user_for_session({tok!r}) -> None without touching the DB")
    except Exception as exc:
        check(False, f"user_for_session({tok!r}) raised {type(exc).__name__}")
    try:
        store.delete_session(tok)
        check(not DB.queries, f"delete_session({tok!r}) is a no-op")
    except Exception as exc:
        check(False, f"delete_session({tok!r}) raised {type(exc).__name__}")

DB.sessions[store._token_hash("valid-token_123")] = (7, "alice", "a@example.test")
check(store.user_for_session("valid-token_123") == {"id": 7, "username": "alice", "email": "a@example.test"},
      "valid ASCII token still resolves its user")
check(store.user_for_session("another-valid-but-unknown") is None, "unknown ASCII token -> None")
check(store.user_for_session("") is None and store.user_for_session(None) is None, "empty / None token -> None")
DB.queries.clear()
store.delete_session("valid-token_123")
check(len(DB.queries) == 1 and DB.queries[0][1] == (store._token_hash("valid-token_123"),),
      "delete_session(valid) still issues exactly one DELETE by hash")
tok, exp = None, None
check(store._token_hash("x") == "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
      "token hashing unchanged (sha256 of ascii token)")

app = FastAPI()
app.include_router(routes.router)
app.include_router(favorites.router)
client = TestClient(app, raise_server_exceptions=False)
for tok in BAD[:3]:
    hdr = {"cookie": f"{routes.COOKIE}={tok}".encode("utf-8")}
    r = client.get("/api/auth/me", headers=hdr)
    check(r.status_code == 200 and r.json() == {"user": None}, f"GET /api/auth/me bad cookie {tok!r} -> 200 user:null (got {r.status_code})")
    r = client.post("/api/auth/logout", headers=hdr)
    check(r.status_code == 200 and r.json() == {"ok": True}, f"POST /api/auth/logout bad cookie {tok!r} -> 200 (got {r.status_code})")
    r = client.get("/api/favorites", headers=hdr)
    check(r.status_code == 401 and r.json() == {"error": "login_required"}, f"GET /api/favorites bad cookie {tok!r} -> 401 login_required (got {r.status_code})")
    r = client.put("/api/favorites/1", headers=hdr)
    check(r.status_code == 401, f"PUT /api/favorites/1 bad cookie {tok!r} -> 401 (got {r.status_code})")
r = client.get("/api/auth/me", headers={"cookie": f"{routes.COOKIE}=valid-token_123"})
check(r.status_code == 200 and r.json()["user"]["username"] == "alice", "GET /api/auth/me valid cookie -> user")
r = client.get("/api/favorites")
check(r.status_code == 401, "GET /api/favorites without cookie -> 401")

print("FAILED: %d" % len(failures) if failures else "ALL PASSED")
sys.exit(1 if failures else 0)
