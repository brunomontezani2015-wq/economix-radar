"""Adapter generico dirigido por YAML (config/flows/<mercado>.yaml).

O YAML e escrito apos a exploracao assistida por IA e descreve, de forma deterministica:
  - como chegar na lista de ofertas (steps)
  - como provar que a sessao esta autenticada (session.require_any) e detectar bloqueios (session.block_if_any)
  - como agrupar nos da tela em cartoes de oferta e extrair campos (collect.card)

Exemplo minimo:

    steps:
      - launch
      - tap_text: Ofertas
    session:
      require_any: ["Clube", "Ola,"]
      block_if_any: ["Entrar", "Fazer login", "Atualize o aplicativo"]
    collect:
      max_scrolls: 40
      stop_after_unchanged: 2
      card:
        container_rid: offer_card          # resource-id do cartao
        fields:                            # cada campo: rid OU regex sobre o texto do cartao
          product: {rid: tv_title}
          promo_price: {rid: tv_price}
          regular_price: {rid: tv_old_price}
          conditions: {regex: "(Leve \\d+.*|Limite.*|Exclusivo.*)"}
          valid_until: {regex: "at[eé] (\\d{1,2}/\\d{1,2}(?:/\\d{2,4})?)"}
"""
from __future__ import annotations

import re
import time
from typing import Iterator

import yaml

from ..config import settings
from ..device import Device, UiNode
from ..models import Offer
from ..parsing import all_prices_cents, parse_date_br, parse_price_cents, parse_size
from .base import BaseAdapter, NeedsHuman


class FlowAdapter(BaseAdapter):
    def __init__(self, market, evidence, device: Device | None = None):
        super().__init__(market, evidence)
        path = settings.flows_dir / f"{market.id}.yaml"
        if not path.exists():
            raise NeedsHuman(f"Fluxo ainda nao criado: {path.name}. Rode a exploracao primeiro.")
        self.flow = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.device = device or Device()

    # ---------------------------------------------------------------
    def collect(self) -> Iterator[Offer]:
        pkg = self.market.android_package
        if not pkg or not self.device.is_installed(pkg):
            raise NeedsHuman(f"App {self.market.app_name} nao instalado ({pkg}).")
        self.device.stop(pkg)
        for step in self.flow.get("steps", []):
            self._run_step(step, pkg)
        self._check_session()

        cfg = self.flow["collect"]
        seen: set[tuple] = set()
        unchanged = 0
        for _ in range(int(cfg.get("max_scrolls", 30)) + 1):
            png = self.device.screenshot()
            nodes = self.device.ui_nodes()
            shot = str(self.evidence.save_png(png, "offers"))
            new = 0
            for offer in self._parse_screen(nodes, cfg["card"], shot):
                key = (offer.product, offer.promo_price_cents, offer.regular_price_cents, offer.conditions)
                if key in seen:
                    continue
                seen.add(key)
                new += 1
                yield offer
            unchanged = unchanged + 1 if new == 0 else 0
            if unchanged >= int(cfg.get("stop_after_unchanged", 2)):
                break
            self.device.scroll_down(float(cfg.get("scroll_fraction", 0.55)))

    # ---------------------------------------------------------------
    def _run_step(self, step, pkg: str) -> None:
        if step == "launch":
            self.device.launch(pkg, wait=float(self.flow.get("launch_wait", 6)))
            return
        (op, arg), = step.items()
        if op == "tap_text":
            if not self.device.tap_text(str(arg)):
                self._shot("step_falhou")
                raise NeedsHuman(f"Elemento '{arg}' nao encontrado. A interface do app pode ter mudado.")
        elif op == "tap_xy":
            self.device.tap(int(arg[0]), int(arg[1]))
        elif op == "wait":
            time.sleep(float(arg))
        elif op == "back":
            self.device.back()
        elif op == "scroll":
            for _ in range(int(arg)):
                self.device.scroll_down()
        else:
            raise ValueError(f"Passo desconhecido no fluxo: {op}")

    def _check_session(self) -> None:
        cfg = self.flow.get("session", {})
        labels = " | ".join(n.label for n in self.device.ui_nodes() if n.label)
        for bad in cfg.get("block_if_any", []):
            if bad.lower() in labels.lower():
                self._shot("bloqueio")
                raise NeedsHuman(f"Tela de bloqueio detectada ('{bad}'). Intervencao humana necessaria.")
        req = cfg.get("require_any", [])
        if req and not any(r.lower() in labels.lower() for r in req):
            self._shot("sessao_nao_confirmada")
            raise NeedsHuman("Nao foi possivel confirmar sessao autenticada.")

    def _shot(self, label: str) -> None:
        try:
            self.evidence.save_png(self.device.screenshot(), label)
        except Exception:  # noqa: BLE001 - evidencia e best-effort em caminho de erro
            pass

    def _parse_screen(self, nodes: list[UiNode], card: dict, shot: str) -> Iterator[Offer]:
        containers = [n for n in nodes if n.rid == card["container_rid"]]
        _, screen_h = self.device.screen_size()
        for c in containers:
            x1, y1, x2, y2 = c.bounds
            if y1 < 0 or y2 > screen_h:  # cartao cortado na borda: sera lido inteiro apos rolar
                continue
            inner = [n for n in nodes if n is not c and _inside(n.bounds, c.bounds) and n.label]
            text = " | ".join(n.label for n in inner)
            fields = {k: _extract(spec, inner, text) for k, spec in card["fields"].items()}
            if not fields.get("product"):
                continue  # sem nome legivel => nao registra
            promo = parse_price_cents(fields.get("promo_price"))
            regular = parse_price_cents(fields.get("regular_price"))
            if promo is None and regular is None:
                prices = all_prices_cents(text)
                if len(prices) == 1:
                    regular = prices[0]  # um unico preco, sem indicacao de promocao
            size_v, size_u = parse_size(fields.get("size") or fields["product"])
            yield Offer(
                market_id=self.market.id, product=fields["product"], brand=fields.get("brand"),
                size_value=size_v, size_unit=size_u, ean=fields.get("ean"),
                regular_price_cents=regular, promo_price_cents=promo, sale_unit=fields.get("sale_unit"),
                valid_until=parse_date_br(fields.get("valid_until")), conditions=fields.get("conditions"),
                store=fields.get("store"), evidence_path=shot, raw_text=text,
            )


def _inside(a, b) -> bool:
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def _extract(spec: dict, inner: list[UiNode], text: str) -> str | None:
    if "rid" in spec:
        for n in inner:
            if n.rid == spec["rid"]:
                return n.label or None
        return None
    if "regex" in spec:
        m = re.search(spec["regex"], text, re.I)
        if m:
            return (m.group(1) if m.groups() else m.group(0)).strip()
    return None
