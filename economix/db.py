"""Persistencia SQLite: execucoes, observacoes de oferta (historico) e consultas de alteracao."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .models import Offer
from .parsing import normalize_name

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',   -- running|ok|partial|failed
    offers_count INTEGER DEFAULT 0,
    error TEXT,
    meta TEXT
);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    market_id TEXT NOT NULL,
    product_key TEXT NOT NULL,       -- EAN se houver, senao nome normalizado + tamanho
    product TEXT NOT NULL,
    brand TEXT,
    size_value REAL,
    size_unit TEXT,
    ean TEXT,
    regular_price_cents INTEGER,
    promo_price_cents INTEGER,
    sale_unit TEXT,
    valid_from TEXT,
    valid_until TEXT,
    conditions TEXT,
    store TEXT,
    evidence_path TEXT NOT NULL,
    raw_text TEXT,
    collected_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_obs_key ON observations(market_id, product_key, collected_at);
CREATE INDEX IF NOT EXISTS ix_obs_ean ON observations(ean);
"""


def product_key(o: Offer) -> str:
    if o.ean:
        return f"ean:{o.ean}"
    size = f"{o.size_value:g}{o.size_unit}" if o.size_value is not None and o.size_unit else ""
    return "name:" + " ".join(p for p in [normalize_name(o.product), normalize_name(o.brand or ""), size] if p)


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.path, timeout=60)  # coletas em paralelo: espera a vez de gravar
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def start_run(self, market_id: str, meta: dict | None = None) -> int:
        with self.conn() as c:
            cur = c.execute("INSERT INTO runs(market_id, started_at, meta) VALUES (?,?,?)",
                            (market_id, _now(), json.dumps(meta or {})))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, count: int, error: str | None = None) -> None:
        with self.conn() as c:
            c.execute("UPDATE runs SET finished_at=?, status=?, offers_count=?, error=? WHERE id=?",
                      (_now(), status, count, error, run_id))

    def add_offers(self, run_id: int, offers: list[Offer]) -> int:
        rows = [(
            run_id, o.market_id, product_key(o), o.product, o.brand, o.size_value, o.size_unit, o.ean,
            o.regular_price_cents, o.promo_price_cents, o.sale_unit,
            o.valid_from.isoformat() if o.valid_from else None,
            o.valid_until.isoformat() if o.valid_until else None,
            o.conditions, o.store, o.evidence_path, o.raw_text, o.collected_at.isoformat(),
        ) for o in offers]
        with self.conn() as c:
            c.executemany("""INSERT INTO observations(run_id, market_id, product_key, product, brand,
                size_value, size_unit, ean, regular_price_cents, promo_price_cents, sale_unit, valid_from,
                valid_until, conditions, store, evidence_path, raw_text, collected_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        return len(rows)

    def last_two_runs(self, market_id: str) -> list[int]:
        with self.conn() as c:
            rows = c.execute("SELECT id FROM runs WHERE market_id=? AND status IN ('ok','partial') "
                             "ORDER BY id DESC LIMIT 2", (market_id,)).fetchall()
        return [r["id"] for r in rows]

    def hours_since_last_ok(self, market_id: str) -> float | None:
        """Horas desde o fim da ultima coleta boa (ok/partial) da rede; None se nunca coletou."""
        with self.conn() as c:
            row = c.execute("SELECT finished_at FROM runs WHERE market_id=? AND status IN ('ok','partial') "
                            "AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 1", (market_id,)).fetchone()
        if not row:
            return None
        return (datetime.now(timezone.utc) - datetime.fromisoformat(row["finished_at"])).total_seconds() / 3600

    def run_pair_since(self, market_id: str, hours: float) -> list[int]:
        """[ultima coleta, coleta-base]: base = mais recente com fim <= agora-hours; senao a mais antiga da janela."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.conn() as c:
            ok = "market_id=? AND status IN ('ok','partial')"
            last = c.execute(f"SELECT id FROM runs WHERE {ok} ORDER BY id DESC LIMIT 1", (market_id,)).fetchone()
            if not last:
                return []
            base = c.execute(f"SELECT id FROM runs WHERE {ok} AND finished_at <= ? ORDER BY id DESC LIMIT 1",
                             (market_id, cutoff)).fetchone()
            if not base:
                base = c.execute(f"SELECT id FROM runs WHERE {ok} AND id < ? ORDER BY id ASC LIMIT 1",
                                 (market_id, last["id"])).fetchone()
        return [last["id"], base["id"]] if base and base["id"] != last["id"] else []

    def observations(self, run_id: int) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute("SELECT * FROM observations WHERE run_id=?", (run_id,)).fetchall()

    def latest_by_market(self) -> dict[str, list[sqlite3.Row]]:
        out: dict[str, list[sqlite3.Row]] = {}
        with self.conn() as c:
            runs = c.execute("SELECT market_id, MAX(id) id FROM runs WHERE status IN ('ok','partial') "
                             "GROUP BY market_id").fetchall()
            for r in runs:
                out[r["market_id"]] = c.execute("SELECT * FROM observations WHERE run_id=?",
                                                (r["id"],)).fetchall()
        return out

    def history(self, market_id: str, key: str) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute("SELECT collected_at, regular_price_cents, promo_price_cents, evidence_path "
                             "FROM observations WHERE market_id=? AND product_key=? ORDER BY collected_at",
                             (market_id, key)).fetchall()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
