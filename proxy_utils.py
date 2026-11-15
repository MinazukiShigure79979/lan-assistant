"""Use Windows' system/PAC proxy for setup downloads.

urllib does not evaluate an AutoConfigURL (PAC) by itself.  ShadowsocksR and
several other Windows proxies use a PAC file instead of ProxyEnable, so a
direct urlopen appears to have no network even though the browser works.
"""
from __future__ import annotations

import os
import re
import sys
import urllib.request

_APPLIED = False


def _registry_proxy() -> tuple[str, str]:
    if sys.platform != "win32":
        return "", ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            def read(name: str, default: object) -> object:
                try:
                    return winreg.QueryValueEx(key, name)[0]
                except Exception:
                    return default

            auto = str(read("AutoConfigURL", "") or "")
            enabled = int(read("ProxyEnable", 0) or 0)
            server = str(read("ProxyServer", "") or "")
        return auto, server if enabled else ""
    except Exception:
        return "", ""


def _pac_proxy(url: str) -> str:
    try:
        # PAC is served by the local proxy; fetch it without using itself.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=5) as response:
            text = response.read().decode("utf-8", "replace")
        match = re.search(r"\bPROXY\s+([A-Za-z0-9_.\-\[\]:]+)", text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return ""


def _normalize(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if "://" in value:
        return value
    return "http://" + value


def apply_system_proxy() -> None:
    """Populate HTTP(S)_PROXY when the user has a system PAC/proxy configured."""
    global _APPLIED
    if _APPLIED or sys.platform != "win32":
        return
    _APPLIED = True
    auto, server = _registry_proxy()
    proxy = ""
    if auto:
        proxy = _pac_proxy(auto)
    if not proxy and server:
        # ProxyServer can be "host:port" or "http=host:port;https=host:port".
        first = server.split(";")[0]
        if "=" in first:
            first = first.split("=", 1)[1]
        proxy = first
    value = _normalize(proxy)
    if not value:
        return
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.setdefault(name, value)
    current = os.environ.get("NO_PROXY", "")
    local = "127.0.0.1,localhost"
    if local not in current:
        os.environ["NO_PROXY"] = (current + "," + local).strip(",")
        os.environ["no_proxy"] = os.environ["NO_PROXY"]
