"""Stop a running Lan.Assistant instance on its current dynamic port."""
from __future__ import annotations

import time
import urllib.request

import config


def main() -> int:
    ports: list[int] = []
    runtime = config.read_runtime_port()
    if runtime:
        ports.append(runtime)
    for port in (8888, 9999, 6666, 8765):
        if port not in ports:
            ports.append(port)
    for port in ports:
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/shutdown",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=4) as response:
                if response.status == 200:
                    print("Lan.Assistant stopped.")
                    time.sleep(1.5)
                    return 0
        except Exception:
            continue
    print("Lan.Assistant is not running.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
