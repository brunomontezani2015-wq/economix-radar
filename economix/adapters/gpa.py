"""Pao de Acucar e Extra (GPA) via API de busca usada pelos proprios sites (api.vendas.gpa.digital).

Configuracao em markets.yaml:  gpa_chain: pa | ex   gpa_store_id: 461 | 483
- price = preco atual exibido; priceFrom = preco "de" (so existe quando ha promocao).
- productPromotion.startDate/endDate NAO sao usados: observamos que formam uma janela movel
  (ontem->amanha no horario da consulta), logo nao representam a validade real da oferta.
- priceType (P/M) e guardado bruto em raw_text; o significado nao foi confirmado.
"""
from __future__ import annotations

import json
import time
from typing import Iterator

from ..models import Offer
from ..parsing import parse_size
from .base import BaseAdapter, NeedsHuman
from .webcommon import cents, load_basket, make_client, polite_request

API = "https://api.vendas.gpa.digital/{chain}/search/search"


def _conditions(p: dict) -> str | None:
    out = []
    promo = p.get("productPromotion") or {}
    buy, pay = promo.get("promotionQuantityBuy"), promo.get("promotionQuantityPayFor")
    if buy and pay:
        out.append(f"Leve {buy} pague {pay}")
    if promo.get("promotionPercentOff"):
        out.append(f"{promo['promotionPercentOff']}% off (informado pelo site)")
    if promo.get("appExclusive"):
        out.append("Exclusivo app")
    if promo.get("paymentFormName"):
        out.append(f"Pagamento: {promo['paymentFormName']}")
    if promo.get("subscriptionPrice"):
        out.append(f"Preço assinatura: {promo['subscriptionPrice']}")
    if p.get("variableWeight"):
        out.append("Peso variável")
    return "; ".join(out) or None


def offers_from_search(market_id: str, products: list[dict], evidence: str, store: str, origin: str) -> Iterator[Offer]:
    for p in products:
        if not p.get("stock"):
            continue
        price = cents(p.get("price"))
        name = (p.get("name") or "").strip()
        if price is None or not name:
            continue
        frm = cents(p.get("priceFrom"))
        promo = price if (frm is not None and price < frm) else None
        size_v, size_u = parse_size(name)
        yield Offer(
            market_id=market_id, product=name, brand=p.get("brand") or None,
            size_value=size_v, size_unit=size_u, ean=None,
            regular_price_cents=frm if promo is not None else price, promo_price_cents=promo,
            sale_unit="kg" if p.get("variableWeight") else None,
            conditions=_conditions(p), store=store, evidence_path=evidence,
            raw_text=json.dumps({"id": p.get("id"), "sku": p.get("sku"), "priceType": p.get("priceType"),
                                 "url": p.get("urlDetails") or f"{origin}/produto/{p.get('id')}"},
                                ensure_ascii=False),
        )


class Adapter(BaseAdapter):
    def __init__(self, market, evidence, delay: float = 1.0, max_pages: int = 3):
        super().__init__(market, evidence)
        extra = market.extra
        if not (extra.get("gpa_chain") and extra.get("gpa_store_id") and market.web_base):
            raise NeedsHuman("Configurar gpa_chain, gpa_store_id e web_base em markets.yaml")
        self.chain, self.store_id = extra["gpa_chain"], int(extra["gpa_store_id"])
        self.origin = market.web_base.rstrip("/")
        self.delay, self.max_pages = delay, max_pages
        self.client = make_client(Origin=self.origin, Referer=self.origin + "/")
        self.store_label = f"site (storeId {self.store_id})"
        cep = str(market.extra.get("cep") or "").replace("-", "").strip()
        if cep:
            self._apply_cep(cep)

    def _apply_cep(self, cep: str) -> None:
        """Escolhe a loja que entrega no CEP, pela mesma consulta de entrega que o site usa.
        O preco e o estoque mudam por loja; sem isso o site usa a loja padrao (outra cidade)."""
        r = self.client.get(f"https://api.vendas.gpa.digital/{self.chain}/delivery/ecom/{cep}",
                            params={"isNal": "false"},
                            headers={"Accept": "application/json, text/plain, */*"})
        types = ((r.json().get("content") or {}).get("deliveryTypes") or []) if r.status_code == 200 else []
        if not types:
            raise NeedsHuman(f"Nenhuma loja da rede entrega no CEP {cep[:5]}-{cep[5:]} pelo site.")
        first = types[0]
        self.store_id = int(first["storeid"])
        cidade = self.market.extra.get("cidade")
        self.store_label = f"CEP {cep[:5]}-{cep[5:]}" + (f" ({cidade})" if cidade else "") + f" · loja {first.get('storeName', self.store_id)}"

    def collect(self) -> Iterator[Offer]:
        if self.market.extra.get("crawl") == "catalog":
            yield from self._collect_catalog()
            return
        yield from self._collect_basket()

    def _collect_catalog(self) -> Iterator[Offer]:
        """Percorre cada departamento do site pela mesma rota que o site usa nas paginas de categoria."""
        seen: set = set()
        fails = 0
        store = self.store_label
        url = API.format(chain=self.chain).replace("/search/search", "/search/category-page")
        for dept in self.market.extra.get("departments") or []:
            page, pages = 1, 1
            while page <= pages:
                body = {"partner": "linx", "page": page, "resultsPerPage": 100, "multiCategory": dept,
                        "sortBy": "relevance", "department": "ecom", "storeId": self.store_id,
                        "customerPlus": True, "filters": []}
                r = polite_request(self.client, "POST", url, json=body)
                if r is None:
                    fails += 1
                    if fails >= 5:
                        raise NeedsHuman("API GPA recusando requisicoes (5 falhas).")
                    break
                js = r.json()
                pages = int(js.get("totalPages") or 0)
                ev = str(self.evidence.save_json(r.content, f"{dept}_p{page}"))
                for o in offers_from_search(self.market.id, js.get("products", []), ev, store, self.origin):
                    key = json.loads(o.raw_text)["id"]
                    if key in seen:
                        continue
                    seen.add(key)
                    yield o
                page += 1

    def _collect_basket(self) -> Iterator[Offer]:
        seen: set[str] = set()
        fails = 0
        store = self.store_label
        for term in load_basket()["terms"]:
            for page in range(1, self.max_pages + 1):
                body = {"terms": term, "page": page, "sortBy": "relevance", "resultsPerPage": 21,
                        "allowRedirect": True, "storeId": self.store_id, "department": "ecom",
                        "customerPlus": True, "partner": "fallback"}
                r = polite_request(self.client, "POST", API.format(chain=self.chain), json=body)
                time.sleep(self.delay)
                if r is None:
                    fails += 1
                    if fails >= 3:
                        raise NeedsHuman("API GPA recusando requisicoes (3 falhas).")
                    break
                js = r.json()
                ev = str(self.evidence.save_json(r.content, f"{term}_p{page}"))
                for o in offers_from_search(self.market.id, js.get("products", []), ev, store, self.origin):
                    key = json.loads(o.raw_text)["id"]
                    if key in seen:
                        continue
                    seen.add(key)
                    yield o
                if page >= int(js.get("totalPages") or 1):
                    break
