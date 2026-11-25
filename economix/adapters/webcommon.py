"""Utilidades compartilhadas pelos adapters web/API: cesta, cliente HTTP educado, ritmo por servidor."""
from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import httpx
import yaml

from ..config import settings

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36 EconomixRadar/0.1"

# Ritmo por SERVIDOR (nao por rede): Pao de Acucar e Extra usam o mesmo servidor do GPA e,
# coletados em paralelo, dobravam o ritmo e eram recusados. Intervalo minimo entre consultas ao mesmo host.
MIN_INTERVAL = 1.0
_host_locks: dict[str, threading.Lock] = {}
_host_last: dict[str, float] = {}
_registry_lock = threading.Lock()


def wait_turn(url: str, interval: float = MIN_INTERVAL) -> None:
    host = urlparse(url).netloc
    with _registry_lock:
        lock = _host_locks.setdefault(host, threading.Lock())
    with lock:
        wait = _host_last.get(host, 0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _host_last[host] = time.monotonic()


def load_basket() -> dict:
    return yaml.safe_load((settings.root / "config" / "basket.yaml").read_text(encoding="utf-8"))


def make_client(**headers) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA, "Accept": "application/json", **headers},
                        timeout=40, follow_redirects=True)


def polite_request(client: httpx.Client, method: str, url: str, **kw) -> httpx.Response | None:
    """Ate 3 tentativas, respeitando o ritmo do servidor. 403 = bloqueio: nao insiste.
    429 (limite) = espera mais e tenta de novo."""
    for attempt in range(3):
        wait_turn(url)
        try:
            r = client.request(method, url, **kw)
        except httpx.HTTPError:
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code in (200, 206) and "json" in r.headers.get("content-type", ""):
            return r
        if r.status_code == 403:
            return None
        time.sleep((20 if r.status_code == 429 else 3) * (attempt + 1))
    return None


def cents(v) -> int | None:
    if v is None:
        return None
    c = round(float(v) * 100)
    return c if c > 0 else None
