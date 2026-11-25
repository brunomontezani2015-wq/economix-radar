from datetime import date

from economix.analysis import changes, compare
from economix.db import Database, product_key
from economix.device import compact_tree, parse_nodes
from economix.models import Offer
from economix.parsing import parse_date_br, parse_price_cents, parse_size


def test_price_parsing():
    assert parse_price_cents("R$ 1.234,56") == 123456
    assert parse_price_cents("por R$12,90 un") == 1290
    assert parse_price_cents("R$ 5") == 500
    assert parse_price_cents("500g") is None          # sem R$ nao e preco
    assert parse_price_cents("") is None


def test_size_and_date():
    assert parse_size("Arroz Tio Joao 5kg") == (5.0, "kg")
    assert parse_size("Leite 1 L") == (1.0, "l")
    assert parse_size("Cafe 500 gr") == (500.0, "g")
    assert parse_size("Detergente") == (None, None)
    assert parse_date_br("valido ate 31/12", today=date(2026, 11, 24)) == date(2026, 12, 31)
    assert parse_date_br("ate 05/01", today=date(2026, 11, 24)) == date(2027, 1, 5)
    assert parse_date_br("31/02/2026") is None


def test_pack_qty():
    from economix.parsing import parse_pack_qty
    assert parse_pack_qty("Cerveja Amstel Sleek Pack 12 Latas 350ml") == 12
    assert parse_pack_qty("Cerveja Stella Artois Lata 8x350ml") == 8
    assert parse_pack_qty("Cerveja Amstel 350ml") is None
    assert parse_pack_qty("Papel Higienico Neve 12 rolos") == 12
    assert parse_pack_qty("Arroz Camil 5kg") is None


def test_invalid_ean_is_dropped():
    o = Offer(market_id="x", product="p", ean="7891234567890", evidence_path="e.png")
    assert o.ean is None  # checksum invalido
    o = Offer(market_id="x", product="p", ean="7891000315507", evidence_path="e.png")
    assert o.ean == "7891000315507"


def _offer(m, name, price, ean=None, brand="Camil"):
    return Offer(market_id=m, product=name, brand=brand, promo_price_cents=price, ean=ean, evidence_path="e.png")


def test_history_changes_and_compare(tmp_path):
    db = Database(tmp_path / "t.db")
    for prices in ([1000, 500], [1100, None]):
        r = db.start_run("a")
        offers = [_offer("a", "Arroz 5kg", prices[0])]
        if prices[1]:
            offers.append(_offer("a", "Feijao 1kg", prices[1]))
        db.add_offers(r, offers)
        db.finish_run(r, "ok", len(offers))
    ch = {c.product: c for c in changes(db, "a")}
    assert ch["Arroz 5kg"].kind == "subiu" and round(ch["Arroz 5kg"].pct) == 10
    assert ch["Feijao 1kg"].kind == "removida"

    r = db.start_run("b")
    db.add_offers(r, [_offer("b", "ARROZ 5kg", 990)])
    db.finish_run(r, "ok", 1)
    comp = compare(db)
    assert len(comp) == 1 and comp[0].cheapest == "b"


def test_name_matching_is_conservative():
    from economix.matching import Item, group_items
    items = [
        Item("pa", "Manteiga com Sal Aviação Pote 200g", "Aviação", None, 1590),
        Item("ex", "Manteiga com Sal Aviação Pote 200g", "AVIAÇÃO", None, 1490),
        Item("am", "Manteiga sem Sal Aviação 200g", "Aviação", None, 1698),      # sem sal != com sal
        Item("mu", "Manteiga Aviação com Sal Lata 200g", "Aviação", None, 2110),  # lata != pote
        Item("sa", "Sabonete Barra Karité Dove 90g", "Dove", None, 500),
        Item("am", "Sabonete em Barra Karité e Baunilha Dove 90g", "Dove", None, 529),  # outro produto
        Item("pa", "Cafe Pilao Tradicional 500g", "Pilão", None, 2000),
        Item("ex", "Cafe Pilao Tradicional 250g", "Pilão", None, 1100),          # tamanho diferente
    ]
    multi = [g for g in group_items(items) if len(g.members) > 1]
    assert len(multi) == 1 and set(multi[0].members) == {"pa", "ex"}


def test_changes_since_window(tmp_path):
    db = Database(tmp_path / "t.db")
    for price in (1000, 900, 950):          # 3 coletas seguidas
        r = db.start_run("a")
        db.add_offers(r, [_offer("a", "Arroz 5kg", price)])
        db.finish_run(r, "ok", 1)
    last = {c.product: c for c in changes(db, "a")}                  # 900 -> 950: subiu
    assert last["Arroz 5kg"].kind == "subiu"
    day = {c.product: c for c in changes(db, "a", since_hours=24)}   # base = mais antiga da janela: 1000 -> 950
    assert day["Arroz 5kg"].kind == "caiu" and day["Arroz 5kg"].before_cents == 1000


def test_product_key_prefers_ean():
    assert product_key(_offer("a", "X", 1, ean="7891000315507")) == "ean:7891000315507"


def test_ui_tree_compact():
    xml = ('<hierarchy><node text="Ofertas" resource-id="app:id/tab" class="android.widget.TextView" '
           'content-desc="" clickable="true" scrollable="false" bounds="[0,0][100,50]"/></hierarchy>')
    nodes = parse_nodes(xml)
    assert nodes[0].center == (50, 25)
    assert "Ofertas" in compact_tree(nodes)
