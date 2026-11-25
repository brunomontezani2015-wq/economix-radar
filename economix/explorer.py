"""Exploracao assistida por IA.

Salva, para cada passo, screenshot + arvore compacta em data/exploration/<mercado>/.
A IA (Claude via skill) le esses arquivos, entende a navegacao e escreve o fluxo deterministico
em config/flows/<mercado>.yaml. Depois disso a coleta roda sem IA.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import settings
from .device import Device, compact_tree


def snapshot(device: Device, market_id: str, label: str = "") -> Path:
    base = settings.exploration_dir / market_id
    base.mkdir(parents=True, exist_ok=True)
    n = len(list(base.glob("*.png"))) + 1
    stem = f"{n:03d}_{datetime.now():%H%M%S}" + (f"_{label}" if label else "")
    png = device.screenshot()
    (base / f"{stem}.png").write_bytes(png)
    nodes = device.ui_nodes()
    (base / f"{stem}.txt").write_text(
        f"# pkg={device.current_package()} nodes={len(nodes)}\n"
        "# idx|flags(C=clicavel,S=rolavel)|classe|resource-id|centro x,y|texto\n"
        + compact_tree(nodes), encoding="utf-8")
    return base / f"{stem}.txt"


def log_action(market_id: str, action: dict) -> None:
    base = settings.exploration_dir / market_id
    base.mkdir(parents=True, exist_ok=True)
    with open(base / "actions.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(), **action}, ensure_ascii=False) + "\n")
