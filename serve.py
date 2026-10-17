import faulthandler
import os
import socket
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

faulthandler.enable()


def _lan_ip() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return None


def _open_when_ready(url: str, probe: str) -> None:
    for _ in range(240):
        try:
            urllib.request.urlopen(probe, timeout=2).read(1)
            print(f"\nBrowser opened: {url}\n")
            webbrowser.open(url)
            return
        except Exception:
            threading.Event().wait(0.5)
    print(f"\nThe server was not ready within two minutes. Try opening it yourself: {url}\n")


def main() -> None:
    import uvicorn

    lan = "--lan" in sys.argv
    host = "0.0.0.0" if lan else "127.0.0.1"
    port = 8000
    for arg in sys.argv[1:]:
        if arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])

    for arg in sys.argv[1:]:
        if arg.startswith("--watchdog="):
            secs = float(arg.split("=", 1)[1])
            faulthandler.dump_traceback_later(secs, repeat=True, exit=False)
            print(f"Watchdog on: thread stacks are printed every {secs:g} s")

    url = f"http://localhost:{port}"
    print("=" * 56)
    print(f"  Web address: {url}")
    if lan:
        ip = _lan_ip()
        if ip:
            print(f"  Phone on the same Wi-Fi: http://{ip}:{port}")
    print("  The first start loads the model and the map data (about 20 s). Please wait...")
    print("  Close this window to stop the server.")
    print("=" * 56)

    if "--no-open" not in sys.argv:
        threading.Thread(target=_open_when_ready,
                         args=(url, f"http://127.0.0.1:{port}/api/meta"),
                         daemon=True).start()

    uvicorn.run("app.api.server:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
