"""Historico, deteccao de alteracoes e comparacao entre concorrentes."""
from __future__ import annotations

from dataclasses import dataclass

from .db import Database


@dataclass
class Change:
    market_id: str
    product: str
    kind: str                      # nova | removida | subiu | caiu
    before_cents: int | None
    after_cents: int | None
    evidence: str | None

    @property
    def pct(self) -> float | None:
        if self.before_cents and self.after_cents is not None:
            return (self.after_cents - self.before_cents) / self.before_cents * 100
        return None


def _eff(row) -> int | None:
    return row["promo_price_cents"] if row["promo_price_cents"] is not None else row["regular_price_cents"]


def changes(db: Database, market_id: str, since_hours: float | None = None) -> list[Change]:
    """Compara a ultima coleta com:
    - a coleta imediatamente anterior (since_hours=None) -> o que mudou desde a ultima rodada (alerta de hora em hora);
    - a coleta mais recente feita ha pelo menos `since_hours` horas, ou a mais antiga disponivel
      dentro da janela -> o que mudou no periodo (ex.: 24h, para o painel)."""
    if since_hours is None:
        runs = db.last_two_runs(market_id)
    else:
        runs = db.run_pair_since(market_id, since_hours)
    if len(runs) < 2:
        return []
    cur = {r["product_key"]: r for r in db.observations(runs[0])}
    prev = {r["product_key"]: r for r in db.observations(runs[1])}
    out: list[Change] = []
    for k, r in cur.items():
        if k not in prev:
            out.append(Change(market_id, r["product"], "nova", None, _eff(r), r["evidence_path"]))
            continue
        a, b = _eff(prev[k]), _eff(r)
        if a is not None and b is not None and a != b:
            out.append(Change(market_id, r["product"], "subiu" if b > a else "caiu", a, b, r["evidence_path"]))
    for k, r in prev.items():
        if k not in cur:
            out.append(Change(market_id, r["product"], "removida", _eff(r), None, r["evidence_path"]))
    return out


@dataclass
class Comparison:
    product_key: str
    product: str
    prices: dict[str, int]        # market_id -> centavos (preco efetivo)
    method: str = "EAN"           # EAN | EAN + nome | nome
    min_sim: float = 1.0

    @property
    def cheapest(self) -> str:
        return min(self.prices, key=self.prices.get)  # type: ignore[arg-type]

    @property
    def spread_pct(self) -> float:
        lo, hi = min(self.prices.values()), max(self.prices.values())
        return (hi - lo) / lo * 100 if lo else 0.0


MAX_PLAUSIBLE_SPREAD = 150.0  # acima disso quase sempre e embalagem diferente (pack x unidade)


def compare(db: Database, min_markets: int = 2, include_suspect: bool = False,
            only: set[str] | None = None) -> list[Comparison]:
    """Cruza produtos pela chave (EAN; na falta, nome+marca+tamanho normalizados) E pela quantidade
    de unidades da embalagem lida do nome (lojas reutilizam o EAN da unidade em packs).
    Casamento aproximado NAO e feito de proposito: so comparamos o que e certamente o mesmo produto.
    Diferencas implausiveis sao separadas (include_suspect=True para ve-las) em vez de reportadas."""
    from .matching import Item, group_items
    items = []
    for market_id, rows in db.latest_by_market().items():
        if only is not None and market_id not in only:
            continue
        for r in rows:
            price = _eff(r)
            if price is not None:
                items.append(Item(market_id, r["product"], r["brand"], r["ean"], price, ref=r))
    out = []
    for g in group_items(items):
        if len(g.members) < min_markets:
            continue
        out.append(Comparison(g.key, g.product, {m: it.price_cents for m, it in g.members.items()},
                              g.method, g.min_sim))
    out = [c for c in out if (c.spread_pct > MAX_PLAUSIBLE_SPREAD) == include_suspect]
    return sorted(out, key=lambda c: c.spread_pct, reverse=True)
