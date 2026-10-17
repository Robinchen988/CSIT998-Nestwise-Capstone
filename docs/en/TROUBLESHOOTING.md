# Troubleshooting

### `RuntimeError: 缺少必需的配置项 DB_DSN` (missing required setting)

`.env` is missing or incomplete. Copy `.env.example` to `.env` in the project root.
Start the server from the project root, or use `serve.py`, which changes to the root itself.

### Cannot connect to the database / `connection refused` on port 15432

1. Is Docker Desktop running?
2. `docker compose -f db/docker-compose.yml up -d`, then `docker ps` should list
   `capstone_postgres` as *Up*.
3. Did you run `python db/setup_db.py`? Without it the tables are empty, so every
   search returns nothing.

### `docker compose` says the container name `capstone_postgres` is already in use

A container with that name already exists, for example from an older setup.
Check it with `docker ps -a`. If it is an old copy you don't need, run
`docker rm -f capstone_postgres` (its data volume is kept) and run `up -d` again.

### Port already in use / refused

- **Port 8000** is taken: `python serve.py --port=8010`.
- **Windows reserves port ranges dynamically** (Hyper-V / WSL), and a port can refuse to bind
  even when nothing uses it. Check the ranges with:
  ```bash
  netsh interface ipv4 show excludedportrange protocol=tcp
  ```
  Then pick a port outside them. The ranges change after a reboot.
- **Port 15432** is taken: change it in `db/docker-compose.yml` *and* in `DB_DSN` in `.env`.

### The first start is slow or seems stuck

The first start downloads the embedding model (~550 MB) from Hugging Face. Later starts take
about 20 s to load the models and geographic data. The terminal prints
`就绪:估值模型 R² … · 可以开始提问` ("ready") when it is done.

### `UnicodeEncodeError` or garbled Chinese in the terminal

Windows consoles with a Chinese locale use the GBK code page. `serve.py` handles
this already. For other scripts and tests, run first:

```bash
set PYTHONIOENCODING=utf-8        # cmd
$env:PYTHONIOENCODING="utf-8"     # PowerShell
export PYTHONIOENCODING=utf-8     # Git Bash / macOS / Linux
```

### The chat shows an error such as `AuthenticationError` or `model not found`

The LLM key, model name or endpoint is wrong. Check the three `LLM_*` lines in `.env`
([CONFIGURATION.md](CONFIGURATION.md)), then restart the server.

### `pip install` fails

- Use Python 3.12–3.14, 64-bit.
- `psycopg2-binary` or `shapely` failing usually means an unsupported Python version.
- On a slow connection, the `torch` download (pulled in by `sentence-transformers`) can
  take a while. CPU-only torch is enough.

### The homepage shows a flat illustrated city instead of the 3D scene

This is expected when the window is narrower than 900px or the browser has no WebGL
(remote desktops, some VMs, or hardware acceleration turned off in browser settings).
Chrome or Edge with hardware acceleration shows the 3D scene.

### The map looks like a standard colourful street map

The frosted vector basemap needs WebGL and access to `tiles.openfreemap.org`. Without
them it falls back to OpenStreetMap raster tiles, and everything else still works.

### Search returns nothing for everything

The `properties` table is empty. Run `python db/setup_db.py`, which should report
`Loaded 20,800 properties`.
