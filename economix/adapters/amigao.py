"""Amigao (VTEX FastStore) via consulta GraphQL persistida usada pelo proprio site (GetProductsQuery).

- O site usa um "operationHash" que pode mudar quando a loja publica nova versao. Se a consulta falhar,
  o adapter redescobre o hash abrindo a busca no Chromium (Playwright) como um visitante e guarda em
  data/state/amigao.json. Se a pagina pedir verificacao humana, para (NeedsHuman).
- hasClubPrice=true sem valor no payload: registramos a condicao, nunca um valor de preco clube.
- gtin do Amigao e codigo interno (6 digitos) -> o validador de EAN descarta.
- Regiao: CEP padrao do site (capturado junto com o hash) e registrado em `store`.
"""
from __future__ import annotations

import json
import time
import urllib.parse as up
from typing import Iterator

from ..config import settings
from ..models import Offer
from ..parsing import parse_size
from .base import BaseAdapter, NeedsHuman
from .webcommon import cents, load_basket, make_client, polite_request

BASE = "https://www.amigao.com"
STATE = settings.data_dir / "state" / "amigao.json"


def discover_query() -> dict:
    from playwright.sync_api import sync_playwright
    found: dict = {}
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(locale="pt-BR")

        def on_req(req):
            if "operationName=GetProductsQuery" in req.url and not found:
                q = up.parse_qs(up.urlparse(req.url).query)
                found["hash"] = q["operationHash"][0]
                found["variables"] = json.loads(q["variables"][0])

        page.on("request", on_req)
        page.goto(f"{BASE}/s?q=arroz", wait_until="domcontentloaded", timeout=45000)
        for _ in range(6):
            if found:
                break
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(2000)
        title = page.title()
        b.close()
    if not found:
        raise NeedsHuman(f"Nao foi possivel descobrir a consulta de produtos do Amigao (titulo: {title!r}).")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
    return found


def offers_from_edges(market_id: str, edges: list[dict], evidence: str, store: str) -> Iterator[Offer]:
    for e in edges:
        n = e.get("node") or {}
        offers = (n.get("offers") or {}).get("offers") or []
        if not offers or "InStock" not in (offers[0].get("availability") or ""):
            continue
        o = offers[0]
        price, listp = cents(o.get("price")), cents(o.get("listPrice"))
        name = (n.get("name") or (n.get("isVariantOf") or {}).get("name") or "").strip()
        if price is None or not name:
            continue
        promo = price if (listp is not None and price < listp) else None
        conds = []
        if n.get("hasClubPrice"):
            conds.append("Possui preço Clube Amigão (valor não exibido sem login)")
        conds += [f"Destaque: {h['name']}" for h in (n.get("clusterHighlights") or []) if h.get("name")]
        mult, unit = n.get("unitMultiplier"), n.get("measurementUnit")
        size_v, size_u = parse_size(name)
        yield Offer(
            market_id=market_id, product=name, brand=(n.get("brand") or {}).get("name"),
            size_value=size_v, size_unit=size_u, ean=n.get("gtin"),
            regular_price_cents=listp if promo is not None else price, promo_price_cents=promo,
            sale_unit=(f"{unit} x{mult:g}" if unit and mult and mult != 1 else unit),
            conditions="; ".join(conds) or None, store=store, evidence_path=evidence,
            raw_text=json.dumps({"sku": n.get("sku"), "slug": n.get("slug")}, ensure_ascii=False),
        )


class Adapter(BaseAdapter):
    def __init__(self, market, evidence, delay: float = 1.0, max_pages: int = 3):
        super().__init__(market, evidence)
        self.delay, self.max_pages = delay, max_pages
        self.client = make_client(Referer=BASE + "/")
        self.query = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else discover_query()
        self._refreshed = not STATE.exists()

    def _fetch(self, term: str, page: int) -> dict | None:
        v = json.loads(json.dumps(self.query["variables"]))
        v["input"].update({"term": term, "page": page})
        url = f"{BASE}/api/graphql?" + up.urlencode({
            "operationName": "GetProductsQuery", "operationHash": self.query["hash"],
            "variables": json.dumps(v, separators=(",", ":"))}, quote_via=up.quote)
        r = polite_request(self.client, "GET", url)
        if r is None:
            return None
        js = r.json()
        if js.get("errors") or not (js.get("data") or {}).get("getProducts"):
            return None
        self._last_raw = r.content
        return js["data"]["getProducts"]["data"]["products"]

    def collect(self) -> Iterator[Offer]:
        cep = self.query["variables"]["input"].get("postalCode")
        store = f"site (CEP padrão {cep})" if cep else "site (região padrão)"
        seen: set[str] = set()
        fails = 0
        for term in load_basket()["terms"]:
            for page in range(1, self.max_pages + 1):
                prods = self._fetch(term, page)
                if prods is None and not self._refreshed:
                    self.query, self._refreshed = discover_query(), True
                    prods = self._fetch(term, page)
                time.sleep(self.delay)
                if prods is None:
                    fails += 1
                    if fails >= 3:
                        raise NeedsHuman("Consulta de produtos do Amigao falhando (3x), mesmo apos redescoberta.")
                    break
                ev = str(self.evidence.save_json(self._last_raw, f"{term}_p{page}"))
                for o in offers_from_edges(self.market.id, prods.get("edges", []), ev, store):
                    key = json.loads(o.raw_text)["sku"]
                    if key in seen:
                        continue
                    seen.add(key)
                    yield o
                total = (prods.get("pageInfo") or {}).get("totalCount") or 0
                if page * 20 >= total:
                    break
