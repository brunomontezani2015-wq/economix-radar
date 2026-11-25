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
import logging
import time
import urllib.parse as up
from typing import Iterator

from ..config import settings
from ..models import Offer
from ..parsing import parse_size
from .base import BaseAdapter, NeedsHuman
from .webcommon import cents, load_basket, make_client, polite_request

log = logging.getLogger("economix")
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
        self._apply_cep()

    def _apply_cep(self) -> None:
        """Aplica o CEP do markets.yaml (ex.: Rio Preto). O preco e o estoque mudam por regiao/loja,
        entao sem isso o site devolve a loja padrao (Maringa)."""
        cep = str(self.market.extra.get("cep") or "").strip()
        if not cep:
            return
        digits = cep.replace("-", "")
        r = self.client.get("https://amigao.vtexcommercestable.com.br/api/checkout/pub/regions",
                            params={"country": "BRA", "postalCode": digits})
        regions = r.json() if r.status_code == 200 else []
        if not regions or not regions[0].get("sellers"):
            raise NeedsHuman(f"O Amigao nao atende o CEP {cep} pelo site; coleta interrompida para nao usar outra regiao.")
        self._region_id = regions[0]["id"]
        self._cep = f"{digits[:5]}-{digits[5:]}"

    def _with_cep(self, v: dict) -> dict:
        if getattr(self, "_region_id", None):
            v["input"].update({"postalCode": self._cep, "cmsPostalCode": self._cep, "regionId": self._region_id})
        return v

    def _fetch_full(self, term: str, page: int, facets: list | None = None, sort: str | None = None) -> dict | None:
        v = self._with_cep(json.loads(json.dumps(self.query["variables"])))
        v["input"].update({"term": term, "page": page})
        if facets is not None:
            v["input"]["selectedFacets"] = facets
        if sort:
            v["input"]["sort"] = sort
        url = f"{BASE}/api/graphql?" + up.urlencode({
            "operationName": "GetProductsQuery", "operationHash": self.query["hash"],
            "variables": json.dumps(v, separators=(",", ":"))}, quote_via=up.quote)
        r = polite_request(self.client, "GET", url)
        if r is None:
            return None
        js = r.json()
        data = ((js.get("data") or {}).get("getProducts") or {}).get("data")
        if js.get("errors") or not data:
            return None
        self._last_raw = r.content
        return data

    def _fetch(self, term: str, page: int) -> dict | None:
        d = self._fetch_full(term, page)
        return d["products"] if d else None

    # ---------- modo catalogo ----------
    PER_PAGE = 20
    MAX_PAGES = 49   # o site deixa de responder a partir da pagina 50 (~1.000 itens por consulta)

    def _collect_catalog(self) -> Iterator[Offer]:
        cep = self.query["variables"]["input"].get("postalCode")
        self._store = (f"CEP {self._cep} ({self.market.extra.get('cidade', '')})".replace(" ()", "") if getattr(self, "_cep", None) else (f"site (CEP padrão {cep})" if cep else "site (região padrão)"))
        self._seen: set = set()
        depts = list(self.market.extra.get("departments") or [])
        # descobre departamentos extras pelos filtros que o proprio site devolve em algumas buscas
        for term in self.market.extra.get("discover_terms") or []:
            d = self._fetch_full(term, 1, facets=[])
            for f in (d or {}).get("facets") or []:
                if f.get("key") == "category-1":
                    depts += [x["value"] for x in f.get("values", []) if x["value"] not in depts]
        log.info("amigao: departamentos %s", depts)
        for dept in depts:
            yield from self._crawl([{"key": "category-1", "value": dept}], level=1)

    def _crawl(self, facets: list, level: int) -> Iterator[Offer]:
        first = self._fetch_full("", 1, facets=facets)
        if first is None:
            return
        total = (first["products"].get("pageInfo") or {}).get("totalCount") or 0
        limit = self.PER_PAGE * self.MAX_PAGES
        if total > limit and level < 4:
            sub = next((f for f in first.get("facets") or [] if f.get("key") == f"category-{level + 1}"), None)
            if sub and sub.get("values"):
                for val in sub["values"]:
                    yield from self._crawl(facets + [{"key": f"category-{level + 1}", "value": val["value"]}], level + 1)
                return
        label = "_".join(f["value"] for f in facets)
        yield from self._emit(first["products"], f"{label}_p1")
        sorts = [None] if total <= limit else [None, "price_asc", "price_desc"]
        if total > limit:
            log.warning("amigao: %s tem %s itens, acima do limite de paginacao; lido em parte", label, total)
        for sort in sorts:
            pages = min(self.MAX_PAGES, -(-total // self.PER_PAGE))
            for page in range(2 if sort is None else 1, pages + 1):
                d = self._fetch_full("", page, facets=facets, sort=sort)
                if d is None:
                    break
                yield from self._emit(d["products"], f"{label}_{sort or 'score'}_p{page}")

    def _emit(self, prods: dict, label: str) -> Iterator[Offer]:
        ev = str(self.evidence.save_json(self._last_raw, label))
        for o in offers_from_edges(self.market.id, prods.get("edges", []), ev, self._store):
            key = json.loads(o.raw_text)["sku"]
            if key in self._seen:
                continue
            self._seen.add(key)
            yield o

    def collect(self) -> Iterator[Offer]:
        if self.market.extra.get("crawl") == "catalog":
            # garante uma consulta valida (redescobre o hash se o site mudou)
            if self._fetch_full("", 1, facets=[]) is None and not self._refreshed:
                self.query, self._refreshed = discover_query(), True
            yield from self._collect_catalog()
            return
        cep = self.query["variables"]["input"].get("postalCode")
        store = (f"CEP {self._cep}" if getattr(self, "_cep", None) else (f"site (CEP padrão {cep})" if cep else "site (região padrão)"))
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
