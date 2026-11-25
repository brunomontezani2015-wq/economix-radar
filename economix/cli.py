"""CLI do Economix Radar.  Uso: python -m economix <comando>"""
from __future__ import annotations

import subprocess
import sys

import typer

from .adapters import load_markets
from .config import settings
from .db import Database

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def doctor(appium: bool = False):
    """Verifica ferramentas, emulador/dispositivo e (opcional) sessao Appium."""
    args = [sys.executable, str(settings.root / "scripts" / "doctor.py")] + (["--appium"] if appium else [])
    raise typer.Exit(subprocess.call(args))


@app.command()
def markets():
    """Lista mercados e o status de cada um (pacote, instalado, fluxo)."""
    from .device import Device, DeviceError
    try:
        dev = Device()
    except DeviceError:
        dev = None
    for mid, m in load_markets().items():
        installed = "?" if not (dev and m.android_package) else ("sim" if dev.is_installed(m.android_package) else "nao")
        flow = "sim" if (settings.flows_dir / f"{mid}.yaml").exists() else "nao"
        typer.echo(f"{mid:15} ativo={'sim' if m.enabled else 'nao':3} pacote={m.android_package or '(nao confirmado)':32} "
                   f"instalado={installed:3} fluxo={flow}")


@app.command()
def discover():
    """Lista apps de terceiros instalados no dispositivo (para confirmar pacotes Android)."""
    from .device import Device
    for p in Device().packages():
        typer.echo(p)


@app.command()
def explore(market_id: str, label: str = ""):
    """Salva screenshot + arvore compacta da tela atual em data/exploration/<mercado>/."""
    from .device import Device
    from .explorer import snapshot
    typer.echo(str(snapshot(Device(), market_id, label)))


@app.command()
def collect(market_id: list[str] = typer.Argument(None), notify: bool = False):
    """Coleta mercados (todos os ativos, ou os informados) e opcionalmente envia relatorio."""
    from .report import build_report
    from .runner import collect_all
    db = Database(settings.db_path)
    status = collect_all(db, market_id or None)
    for m, s in status.items():
        typer.echo(f"{m}: {s}")
    if notify:
        from .notify import send_telegram
        send_telegram(build_report(db, status))
        typer.echo("relatorio enviado ao Telegram")


@app.command()
def buscar(termo: str, limite: int = 30):
    """Busca preco de produto por nome/marca/EAN na ultima coleta de todas as redes (mais barato primeiro)."""
    from .parsing import normalize_name
    from .report import brl
    db = Database(settings.db_path)
    names = {mid: m.name for mid, m in load_markets().items()}
    words = normalize_name(termo).split()
    hits = []
    for mid, rows in db.latest_by_market().items():
        for r in rows:
            hay = normalize_name(f"{r['product']} {r['brand'] or ''} {r['ean'] or ''}")
            if all(w in hay for w in words):
                eff = r["promo_price_cents"] if r["promo_price_cents"] is not None else r["regular_price_cents"]
                hits.append((eff if eff is not None else 10**12, mid, r))
    hits.sort(key=lambda h: h[0])
    typer.echo(f"{len(hits)} resultado(s) para '{termo}'" + (f" (mostrando {limite})" if len(hits) > limite else ""))
    for _, mid, r in hits[:limite]:
        promo = f"  PROMO {brl(r['promo_price_cents'])} (de {brl(r['regular_price_cents'])})" if r["promo_price_cents"] else ""
        price = brl(r["regular_price_cents"]) if not r["promo_price_cents"] else ""
        typer.echo(f"{names.get(mid, mid):14} {price:>11}{promo}  {r['product'][:60]}")


@app.command()
def oportunidades(enviar: bool = False, sempre: bool = False, limite: int = 40):
    """Produtos que baixaram de preco desde a coleta anterior (por rede). Salva em data/reports/.
    Com --enviar, so manda ao Telegram se houver queda (use --sempre para mandar mesmo sem queda)."""
    from datetime import date, datetime
    from html import escape

    from .analysis import changes
    from .report import brl
    db = Database(settings.db_path)
    markets = load_markets()
    drops = [c for mid in markets for c in changes(db, mid) if c.kind == "caiu"]
    drops.sort(key=lambda c: c.pct or 0)
    lines = [f"<b>Economix Radar — Baixaram de preço ({datetime.now():%d/%m %H:%M})</b>",
             f"{len(drops)} produto(s) baixaram de preço desde a coleta anterior."]
    for c in drops[:limite]:
        lines.append(f"🔻 <i>{escape(markets[c.market_id].name)}</i> {escape(c.product, quote=False)}: "
                     f"{brl(c.before_cents)} → <b>{brl(c.after_cents)}</b> ({c.pct:.0f}%)"
                     + (" ⚠ conferir no site" if (c.pct or 0) < -50 else ""))
    if len(drops) > limite:
        lines.append(f"… e mais {len(drops) - limite}. Veja o painel.")
    if not drops:
        lines.append("Nenhuma queda (é preciso ter duas coletas de cada rede).")
    text = "\n".join(lines)
    out = settings.data_dir / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"oportunidades_{datetime.now():%Y-%m-%d_%H%M}.txt").write_text(text, encoding="utf-8")
    typer.echo(text)
    if enviar and (drops or sempre):
        from .notify import send_telegram
        send_telegram(text)
        typer.echo("enviado ao Telegram")
    elif enviar:
        typer.echo("sem quedas novas: nada enviado")


@app.command("telegram-config")
def telegram_config():
    """Descobre o chat do Telegram (mande /start ao seu bot antes), grava TELEGRAM_CHAT_ID no .env e envia um teste."""
    import os

    import httpx
    env = settings.root / ".env"
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        typer.echo("Falta TELEGRAM_BOT_TOKEN no arquivo .env (copie .env.example para .env e cole o token do @BotFather).")
        raise typer.Exit(1)
    r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20)
    if r.status_code == 401:
        typer.echo("Token recusado pelo Telegram. Confira o token copiado do @BotFather.")
        raise typer.Exit(1)
    r.raise_for_status()
    chats = {}
    for upd in r.json().get("result", []):
        msg = upd.get("message") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            chats[chat["id"]] = chat.get("title") or chat.get("first_name") or chat.get("username") or "?"
    if not chats:
        typer.echo("Nenhuma conversa encontrada. Abra o seu bot no Telegram, mande /start e rode este comando de novo.")
        raise typer.Exit(1)
    chat_id, name = list(chats.items())[-1]
    lines = [l for l in env.read_text(encoding="utf-8").splitlines() if not l.startswith("TELEGRAM_CHAT_ID=")]
    lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
               data={"chat_id": chat_id, "text": "Economix Radar conectado. Você vai receber aqui as quedas de preço todo dia às 08:00."}).raise_for_status()
    typer.echo(f"Conectado ao chat '{name}'. Mensagem de teste enviada.")


@app.command()
def report(send: bool = False):
    """Mostra (ou envia) o relatorio de alteracoes e comparacao."""
    from .report import build_report
    text = build_report(Database(settings.db_path))
    if send:
        from .notify import send_telegram
        send_telegram(text)
    typer.echo(text)


if __name__ == "__main__":
    app()
