"""Exporta a ultima coleta de cada rede para os arquivos do painel (data/dashboard/*.json).

Cada arquivo e {"rows": [...], "meta": [...]}; meta leva este proprio codigo para auditoria.
Precos saem em reais (float com 2 casas). Campos ausentes ficam null (nunca preenchidos).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from economix.adapters import load_markets  # noqa: E402
from economix.analysis import MAX_PLAUSIBLE_SPREAD, changes, compare  # noqa: E402
from economix.db import Database  # noqa: E402

OUT = ROOT / "data" / "dashboard"
CODE = Path(__file__).read_text(encoding="utf-8")


def reais(c):
    return None if c is None else round(c / 100, 2)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    markets = load_markets()
    db = sqlite3.connect(ROOT / "data" / "economix.db")
    db.row_factory = sqlite3.Row
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta = [{"label": "export_dashboard.py", "value": CODE, "type": "code", "lang": "python"},
            {"label": "Input", "value": "data/economix.db (última execução de cada rede)"},
            {"label": "Exportado em", "value": now}]

    last = {r["market_id"]: r for r in db.execute(
        "SELECT * FROM runs WHERE id IN (SELECT MAX(id) FROM runs WHERE status IN ('ok','partial') GROUP BY market_id)")}
    latest_any = {r["market_id"]: r for r in db.execute(
        "SELECT * FROM runs WHERE id IN (SELECT MAX(id) FROM runs GROUP BY market_id)")}

    # 1) situacao por rede (todas as 10, inclusive as sem coleta)
    status_rows = []
    for mid, m in markets.items():
        run = last.get(mid)
        la = latest_any.get(mid)
        stats = db.execute(
            "SELECT COUNT(*) n, SUM(ean IS NOT NULL) ean, SUM(promo_price_cents IS NOT NULL) promo "
            "FROM observations WHERE run_id=?", (run["id"],)).fetchone() if run else None
        if run:
            situacao = "Coletando" if la and la["status"] == "running" else "OK"
        elif la and la["status"] == "running":
            situacao = "Coletando"
        elif not m.enabled:
            situacao = "Bloqueado" if "NAO contornar" in m.notes else "Pendente"
        else:
            situacao = "Falhou"
        status_rows.append({
            "rede": m.name, "situacao": situacao, "fonte": "API do site" if m.strategy.value == "api" else "App",
            "produtos": stats["n"] if stats else None, "com_ean": stats["ean"] if stats else None,
            "em_promocao": stats["promo"] if stats else None,
            "coletado_em": run["finished_at"][:16].replace("T", " ") + " UTC" if run else None,
            "observacao": _note(mid, m),
        })
    _write("coleta", status_rows, meta)

    # 2) ofertas da ultima coleta, cada uma com o grupo (mesmo produto em outras redes), se houver
    from economix.matching import Item, group_items
    obs = {mid: db.execute("SELECT * FROM observations WHERE run_id=?", (run["id"],)).fetchall()
           for mid, run in last.items()}
    items = []
    for mid, rows in obs.items():
        for o in rows:
            eff = o["promo_price_cents"] if o["promo_price_cents"] is not None else o["regular_price_cents"]
            if eff is not None:
                items.append(Item(mid, o["product"], o["brand"], o["ean"], eff, ref=f"{mid}-{o['id']}"))
    grupo_de = {}
    for g in group_items(items):
        prices = [it.price_cents for it in g.members.values()]
        if len(g.members) >= 2 and (max(prices) - min(prices)) / min(prices) * 100 <= MAX_PLAUSIBLE_SPREAD:
            for it in g.members.values():
                grupo_de[it.ref] = g.key
    offers = []
    for mid, rows in obs.items():
        name = markets[mid].name if mid in markets else mid
        for o in rows:
            reg, pro = o["regular_price_cents"], o["promo_price_cents"]
            offers.append({
                "id": f"{mid}-{o['id']}", "grupo": grupo_de.get(f"{mid}-{o['id']}"), "rede": name, "produto": o["product"], "marca": o["brand"], "ean": o["ean"],
                "preco_normal": reais(reg), "preco_promo": reais(pro),
                "desconto_pct": round((1 - pro / reg) * 100, 1) if (pro and reg) else None,
                "condicoes": o["conditions"], "loja": o["store"],
            })
    _write("ofertas", offers, meta)

    # 3) comparacao: EAN, ou nome com regras conservadoras (economix/matching.py)
    edb = Database(ROOT / "data" / "economix.db")
    comp = []
    for c in compare(edb):
        name = lambda m: markets[m].name if m in markets else m  # noqa: E731
        lo, hi = min(c.prices.values()), max(c.prices.values())
        row = {"id": c.product_key, "produto": c.product, "redes": len(c.prices),
               "casamento": c.method if c.method != "nome" else f"nome ({c.min_sim:.0%} igual)".replace(".", ","),
               "mais_barato": name(c.cheapest), "menor_preco": reais(lo), "maior_preco": reais(hi),
               "diferenca_pct": round(c.spread_pct, 1)}
        for mid in markets:
            row[markets[mid].name] = reais(c.prices.get(mid))
        comp.append(row)
    _write("comparacao", comp, meta)

    # 4) quedas de preco nas ultimas 24h: ultima coleta x coleta de ~24h atras (ou a mais antiga da janela)
    quedas = []
    for mid in markets:
        for ch in changes(edb, mid, since_hours=24):
            if ch.kind != "caiu":
                continue
            quedas.append({"id": f"{mid}-{len(quedas)}", "rede": markets[mid].name, "produto": ch.product,
                           "antes": reais(ch.before_cents), "agora": reais(ch.after_cents),
                           "queda_pct": round(-ch.pct, 1), "economia": reais(ch.before_cents - ch.after_cents),
                           "alerta": "Queda acima de 50%: conferir no site" if -ch.pct > 50 else None})
    quedas.sort(key=lambda r: -r["queda_pct"])
    _write("quedas", quedas, meta)
    print({"coleta": len(status_rows), "ofertas": len(offers), "comparacao": len(comp), "quedas": len(quedas)})


def _note(mid: str, m) -> str | None:
    notes = {
        "carrefour": "Site exige verificação humana (anti-bot). Coleta só pelo app.",
        "assai": "Site bloqueia acesso automatizado às ofertas. Coleta só pelo app.",
        "max_atacadista": "Ofertas só em encartes (imagem) por loja.",
        "tonin": "Site oficial não confirmado.",
        "amigao": "Preço Clube Amigão não aparece sem login.",
        "atacadao": "Site não mostra preço 'de/por'.",
        "americanas": "Disponibilidade para o CEP 15015-000 (Rio Preto). Marketplace: vendedor indicado nas condições.",
    }
    return notes.get(mid)


def _write(name: str, rows: list, meta: list) -> None:
    (OUT / f"{name}.json").write_text(json.dumps({"rows": rows, "meta": meta}, ensure_ascii=False),
                                      encoding="utf-8")


if __name__ == "__main__":
    main()
