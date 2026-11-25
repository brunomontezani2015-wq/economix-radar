"""Relatorio em texto (Markdown/HTML simples do Telegram)."""
from __future__ import annotations

from datetime import datetime
from html import escape

from .adapters import load_markets
from .analysis import changes, compare
from .db import Database


def brl(cents: int | None) -> str:
    if cents is None:
        return "—"
    s = f"{cents / 100:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def build_report(db: Database, run_status: dict[str, str] | None = None, top: int = 10) -> str:
    markets = load_markets()
    name = lambda mid: markets[mid].name if mid in markets else mid  # noqa: E731
    lines = [f"<b>Economix Radar</b> — {datetime.now():%d/%m/%Y %H:%M}"]
    if run_status:
        lines.append("\n<b>Coleta</b>")
        lines += [f"• {escape(name(m))}: {escape(s)}" for m, s in run_status.items()]
    lines.append("\n<b>Alterações</b>")
    any_change = False
    for mid in markets:
        ch = changes(db, mid)
        moved = [c for c in ch if c.kind in ("subiu", "caiu")]
        if not ch:
            continue
        any_change = True
        novas = sum(c.kind == "nova" for c in ch)
        rem = sum(c.kind == "removida" for c in ch)
        lines.append(f"<i>{escape(name(mid))}</i>: {novas} novas, {rem} encerradas, {len(moved)} com preço alterado")
        for c in sorted(moved, key=lambda c: abs(c.pct or 0), reverse=True)[:top]:
            arrow = "🔺" if c.kind == "subiu" else "🔻"
            lines.append(f"  {arrow} {escape(c.product)}: {brl(c.before_cents)} → {brl(c.after_cents)} ({c.pct:+.1f}%)")
    if not any_change:
        lines.append("Sem histórico suficiente (são necessárias 2 coletas por mercado).")
    comp = compare(db)[:top]
    lines.append("\n<b>Comparação entre concorrentes</b>")
    if not comp:
        lines.append("Nenhum produto idêntico (mesmo EAN ou mesmo nome+marca+tamanho) em 2+ mercados ainda.")
    for c in comp:
        parts = ", ".join(f"{escape(name(m), quote=False)} {brl(p)}"
                          for m, p in sorted(c.prices.items(), key=lambda x: x[1]))
        lines.append(f"• {escape(c.product, quote=False)} — {parts} (dif. {c.spread_pct:.0f}%)")
    suspect = compare(db, include_suspect=True)
    if suspect:
        lines.append(f"\n<i>{len(suspect)} cruzamentos descartados por diferença implausível "
                     f"(provável embalagem diferente com mesmo EAN).</i>")
    return "\n".join(lines)
