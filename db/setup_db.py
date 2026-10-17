import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2

from app.core.config import DB_DSN

SEED = ROOT / "db" / "seed" / "properties.dump"
SCHEMA = ROOT / "db" / "schema.sql"


def _arg(name: str, default: str) -> str:
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def _connect(timeout_s: int = 60):
    deadline = time.time() + timeout_s
    while True:
        try:
            conn = psycopg2.connect(DB_DSN)
            conn.autocommit = True
            return conn
        except psycopg2.OperationalError as exc:
            if time.time() > deadline:
                sys.exit(f"Cannot connect to {DB_DSN.split('@')[-1]}: {exc}\n"
                         "Is the database container running?  docker compose -f db/docker-compose.yml up -d")
            time.sleep(2)


def _properties_rows(cur) -> int | None:
    cur.execute("SELECT to_regclass('public.properties')")
    if cur.fetchone()[0] is None:
        return None
    cur.execute("SELECT count(*) FROM properties")
    return cur.fetchone()[0]


def _restore(container: str, user: str, db: str, data_only: bool) -> None:
    cmd = ["docker", "exec", "-i", container, "pg_restore", "-U", user, "-d", db,
           "--no-owner", "--no-privileges"]
    if data_only:
        cmd.append("--data-only")
    print(f"Restoring {SEED.name} ({SEED.stat().st_size / 1e6:.0f} MB) into container '{container}' ...")
    with SEED.open("rb") as fh:
        result = subprocess.run(cmd, stdin=fh, capture_output=True)
    if result.returncode != 0:
        sys.exit("pg_restore failed:\n" + result.stderr.decode("utf-8", "replace"))


def main() -> None:
    if not SEED.exists():
        sys.exit(f"Seed file not found: {SEED}")
    container = _arg("--container", "capstone_postgres")
    dsn = urlparse(DB_DSN)
    user, db = dsn.username or "capstone", (dsn.path or "/capstone").lstrip("/")

    conn = _connect()
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

    rows = _properties_rows(cur)
    if rows and "--force" not in sys.argv:
        print(f"`properties` already has {rows:,} rows -- skipping restore (use --force to reload).")
    else:
        if rows:
            cur.execute("TRUNCATE properties")
        _restore(container, user, db, data_only=rows is not None)
        cur.execute("SELECT setval(pg_get_serial_sequence('properties', 'id'), (SELECT max(id) FROM properties))")
        print(f"Loaded {_properties_rows(cur):,} properties.")

    cur.execute(SCHEMA.read_text(encoding="utf-8"))
    conn.close()
    print("Database ready.")


if __name__ == "__main__":
    main()
