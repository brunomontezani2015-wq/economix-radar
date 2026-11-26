"""Orquestracao da coleta: um mercado por vez, falha de um nao derruba os outros."""
from __future__ import annotations

import logging
import traceback

from .adapters import NeedsHuman, get_adapter, load_markets
from .config import settings
from .db import Database
from .evidence import EvidenceStore

log = logging.getLogger("economix")


def collect_market(db: Database, market_id: str, **adapter_kw) -> str:
    market = load_markets()[market_id]
    run_id = db.start_run(market_id, {"package": market.android_package, "strategy": market.strategy.value})
    evidence = EvidenceStore(settings.evidence_dir, market_id, run_id)
    batch, total = [], 0
    try:
        adapter = get_adapter(market, evidence, **adapter_kw)
        for offer in adapter.collect():
            batch.append(offer)
            if len(batch) >= 25:
                total += db.add_offers(run_id, batch)
                batch.clear()
        total += db.add_offers(run_id, batch)
        db.finish_run(run_id, "ok", total)
        return f"ok ({total} ofertas)"
    except NeedsHuman as e:
        total += db.add_offers(run_id, batch)
        db.finish_run(run_id, "partial" if total else "failed", total, f"needs_human: {e}")
        return f"⚠ intervenção humana: {e}" + (f" — {total} ofertas salvas" if total else "")
    except Exception as e:  # noqa: BLE001 - isolamento por mercado
        total += db.add_offers(run_id, batch)
        db.finish_run(run_id, "partial" if total else "failed", total, traceback.format_exc()[-2000:])
        log.exception("falha em %s", market_id)
        return f"✖ erro: {type(e).__name__}: {e}"


def collect_all(db: Database, only: list[str] | None = None, parallel: int = 8) -> dict[str, str]:
    """Coleta as redes em paralelo (uma thread por rede). Cada site continua recebendo no maximo
    ~1 requisicao/segundo, porque o limite de ritmo fica dentro de cada adapter."""
    from concurrent.futures import ThreadPoolExecutor

    import os
    from datetime import datetime
    hour = datetime.now().hour
    force = os.environ.get("ECONOMIX_RUN_ALL") == "1"
    mids = []
    for mid, m in load_markets().items():
        if only:
            if mid in only:
                mids.append(mid)
            continue
        if not m.enabled:
            continue
        hours = m.extra.get("run_hours")
        if hours and hour not in hours and not force:
            log.info("%s: fora do horario desta rede (%s), pulando", mid, hours)
            continue
        # "uma vez a cada N horas": nao depende da hora exata em que o agendador do GitHub disparou
        gap = m.extra.get("min_interval_hours")
        if gap and not force:
            age = db.hours_since_last_ok(mid)
            if age is not None and age < float(gap):
                log.info("%s: coletada ha %.1fh (intervalo minimo %sh), pulando", mid, age, gap)
                continue
        mids.append(mid)
    if parallel <= 1 or len(mids) <= 1:
        return {mid: collect_market(db, mid) for mid in mids}
    with ThreadPoolExecutor(max_workers=min(parallel, len(mids))) as pool:
        futures = {mid: pool.submit(collect_market, db, mid) for mid in mids}
        return {mid: f.result() for mid, f in futures.items()}
