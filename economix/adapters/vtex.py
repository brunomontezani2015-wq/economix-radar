"""Adapter para lojas VTEX via API publica de catalogo (a mesma que o site da loja usa).

- Respeita robots.txt (verificado na configuracao) e limita a ~1 requisicao/segundo.
- Evidencia: a resposta JSON bruta de cada busca e salva com hash.
- Preco promocional so e registrado quando Price < ListPrice. Item indisponivel ou com preco 0 nao e registrado.
- PriceValidUntil da VTEX e uma validade tecnica do cache de preco, NAO a validade da promocao: nao e usado.
- Precos sem CEP/regiao refletem o canal padrao do site; isso e registrado em `store`.
"""
from __future__ import annotations

import json
import time
from typing import Iterator
from urllib.parse import quote

import httpx
import yaml

from ..config import settings
from ..models import Offer
from ..parsing import parse_size
from .base import BaseAdapter, NeedsHuman

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EconomixRadar/0.1"


def _cents(v) -> int | None:
    if v is None:
        return None
    c = round(float(v) * 100)
    return c if c > 0 else None


def offers_from_products(market_id: str, products: list[dict], evidence_path: str, store: str) -> Iterator[Offer]:
    for p in products:
        for it in p.get("items", []):
            sellers = it.get("sellers", [])
            # marketplace (ex.: Americanas): usa o vendedor que o site marca como padrao; senao o primeiro
            chosen = [s for s in sellers if s.get("sellerDefault")][:1] or sellers[:1]
            for seller in chosen:
                co = seller.get("commertialOffer") or {}
                if not co.get("IsAvailable"):
                    continue
                price, listp = _cents(co.get("Price")), _cents(co.get("ListPrice"))
                if price is None:
                    continue
                promo = price if (listp is not None and price < listp) else None
                regular = listp if promo is not None else price
                name = (it.get("nameComplete") or p.get("productName") or "").strip()
                if not name:
                    continue
                teasers = [t.get("<Name>k__BackingField") or t.get("Name") for t in
                           (co.get("Teasers") or []) + (co.get("PromotionTeasers") or [])]
                teasers = [t for t in teasers if t]
                if len(sellers) > 1 and seller.get("sellerName"):
                    teasers.insert(0, f"Vendido por {seller['sellerName']}")
                brand = p.get("brand")
                if brand and brand.strip().lower() in ("não disponível", "nao disponivel", "n/a", "-"):
                    brand = None
                size_v, size_u = parse_size(name)
                mult = it.get("unitMultiplier")
                unit = it.get("measurementUnit")
                yield Offer(
                    market_id=market_id, product=name, brand=brand or None,
                    size_value=size_v, size_unit=size_u, ean=(it.get("ean") or None),
                    regular_price_cents=regular, promo_price_cents=promo,
                    sale_unit=(f"{unit} x{mult:g}" if unit and mult and mult != 1 else unit),
                    conditions="; ".join(dict.fromkeys(teasers)) or None,
                    store=store, evidence_path=evidence_path,
                    raw_text=json.dumps({"productId": p.get("productId"), "itemId": it.get("itemId"),
                                         "link": p.get("link"), "seller": seller.get("sellerName"),
                                         "Price": co.get("Price"),
                                         "ListPrice": co.get("ListPrice")}, ensure_ascii=False),
                )


class Adapter(BaseAdapter):
    def __init__(self, market, evidence, delay: float = 1.0):
        super().__init__(market, evidence)
        if not market.web_base:
            raise NeedsHuman("web_base nao configurado em markets.yaml")
        self.base = market.web_base.rstrip("/")
        self.delay = delay
        self.basket = yaml.safe_load((settings.root / "config" / "basket.yaml").read_text(encoding="utf-8"))
        self.client = httpx.Client(headers={"User-Agent": UA, "Accept": "application/json"},
                                   timeout=30, follow_redirects=True)
        # CEP opcional (markets.yaml: cep: "15015-000"): a loja aplica a regiao e marca como
        # indisponivel o que nao entrega naquele CEP. Itens indisponiveis nao sao registrados.
        self.cep = str(market.extra.get("cep") or "").replace("-", "").strip() or None
        self.store_label = "site (canal padrao, sem CEP)"
        if self.cep:
            self._apply_region()

    def _apply_region(self) -> None:
        import base64
        r = self.client.get(f"{self.base}/api/checkout/pub/regions",
                            params={"country": "BRA", "postalCode": self.cep})
        regions = r.json() if r.status_code == 200 else []
        if not regions or not regions[0].get("id"):
            raise NeedsHuman(f"A loja nao retornou regiao para o CEP {self.cep}; coleta interrompida para nao misturar regioes.")
        seg = {"regionId": regions[0]["id"], "channel": "1", "countryCode": "BRA", "postalCode": self.cep}
        self.client.cookies.set("vtex_segment", base64.b64encode(json.dumps(seg).encode()).decode())
        cidade = self.market.extra.get("cidade")
        self.store_label = f"CEP {self.cep[:5]}-{self.cep[5:]}" + (f" ({cidade})" if cidade else "")

    def collect(self) -> Iterator[Offer]:
        per = int(self.basket.get("per_term", 50))
        seen: set[str] = set()
        failures = 0
        for term in self.basket["terms"]:
            r = self._get("/api/catalog_system/pub/products/search", {"ft": term, "_from": 0, "_to": per - 1})
            if r is None:
                failures += 1
                if failures >= 3:
                    raise NeedsHuman("API do catalogo recusando requisicoes (3 falhas). Coleta interrompida.")
                continue
            ev = str(self.evidence.save_json(r.content, term))
            for o in offers_from_products(self.market.id, r.json(), ev, self.store_label):
                key = o.ean or o.product
                if key in seen:
                    continue
                seen.add(key)
                yield o
            time.sleep(self.delay)

    def _get(self, path: str, params: dict) -> httpx.Response | None:
        for attempt in range(3):
            try:
                # VTEX/WAF de algumas lojas rejeita '+' como espaco (400); usar %20.
                qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
                r = self.client.get(f"{self.base}{path}?{qs}")
            except httpx.HTTPError:
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code in (200, 206) and r.headers.get("content-type", "").startswith("application/json"):
                return r
            if r.status_code in (403, 429):  # bloqueio/limite: nao insistir
                return None
            time.sleep(3 * (attempt + 1))
        return None
