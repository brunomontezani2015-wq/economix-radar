"""Agrupa o mesmo produto entre redes.

1) EAN igual (+ mesma quantidade de embalagem lida do nome) -> mesmo produto, certeza.
2) Sem EAN (Amigao, Pao de Acucar, Extra), casamento por nome CONSERVADOR. Exige, ao mesmo tempo:
   - mesma marca (normalizada, nao vazia)
   - mesmo tamanho convertido para g/ml/un (lido do nome; sem tamanho, nao casa)
   - mesma quantidade de embalagem (pack)
   - similaridade das palavras descritivas >= LIMIAR (Jaccard)
   Cada grupo guarda o metodo e a menor similaridade usada, para o leitor saber o quao seguro e.
Na duvida, NAO casa: um produto sem par e melhor que uma comparacao errada.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .parsing import normalize_name, parse_pack_qty, parse_size

LIMIAR = 0.8

# embalagens: ignoradas na similaridade, mas duas embalagens declaradas e diferentes impedem o casamento
_PACKAGING = {"pacote", "pct", "pote", "lata", "garrafa", "caixa", "cx", "frasco", "sache", "sachet",
              "pouch", "refil", "bisnaga", "balde", "galao", "vidro", "tetra", "squeeze", "bandeja"}
_STOP = {
    "de", "da", "do", "das", "dos", "e", "para", "em", "a", "o", "c", "s", "embalagem", "economica",
    "unidade", "unidades", "un", "und", "kg", "g", "gr", "ml", "l", "lt", "litro", "litros",
    "vacuo", "tipo", "pack", "leve", "pague", "cada", "x", "mais", "gratis",
} | _PACKAGING
_BOUND = {"sem", "com", "zero"}  # "sem sal" vira um token so: "sem_sal" != "com_sal"


def size_key(name: str) -> tuple[float, str] | None:
    v, u = parse_size(name)
    if v is None or u is None:
        return None
    if u == "kg":
        return round(v * 1000, 1), "g"
    if u == "mg":
        return round(v / 1000, 3), "g"
    if u == "l":
        return round(v * 1000, 1), "ml"
    return round(v, 1), u


def tokens(name: str, brand: str | None) -> frozenset[str]:
    """Palavras descritivas. Numeros soltos ficam (ex.: 'Espaguete 8', '252 graus');
    tokens de medida ('500g', '2l') saem, pois o tamanho ja e comparado a parte."""
    bt = set(normalize_name(brand or "").split())
    words = normalize_name(name).split()
    out = set()
    i = 0
    while i < len(words):
        t = words[i]
        if t in _BOUND and i + 1 < len(words):
            out.add(f"{t}_{words[i + 1]}")
            i += 2
            continue
        i += 1
        if t in _STOP or t in bt or (any(ch.isdigit() for ch in t) and not t.isdigit()):
            continue
        out.add(t)
    return frozenset(out)


def packaging(name: str) -> frozenset[str]:
    return frozenset(t for t in normalize_name(name).split() if t in _PACKAGING and t not in ("pacote", "pct", "caixa", "cx"))


def jaccard(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


@dataclass
class Item:
    market: str
    product: str
    brand: str | None
    ean: str | None
    price_cents: int
    ref: object = None  # linha original

    def __post_init__(self):
        self.brand_n = normalize_name(self.brand or "")
        self.size = size_key(self.product)
        self.pack = parse_pack_qty(self.product)
        self.toks = tokens(self.product, self.brand)
        self.pkg = packaging(self.product)

    def compatible(self, other: "Item") -> bool:
        return not (self.pkg and other.pkg and not (self.pkg & other.pkg))

    @property
    def block(self):
        if not self.brand_n or self.size is None:
            return None
        return (self.brand_n, self.size, self.pack)


@dataclass
class Group:
    key: str
    product: str
    ean: str | None
    method: str                      # "EAN" | "nome"
    min_sim: float = 1.0
    members: dict[str, Item] = field(default_factory=dict)  # market -> item (mais barato)

    def add(self, it: Item, sim: float = 1.0) -> None:
        cur = self.members.get(it.market)
        if cur is None or it.price_cents < cur.price_cents:
            self.members[it.market] = it
        self.min_sim = min(self.min_sim, sim)


def group_items(items: list[Item]) -> list[Group]:
    groups: list[Group] = []
    by_ean: dict[tuple, Group] = {}
    loose: list[Item] = []
    for it in items:
        if it.ean:
            k = (it.ean, it.pack)
            g = by_ean.get(k)
            if g is None:
                g = by_ean[k] = Group(f"{it.ean}-{it.pack or 1}", it.product, it.ean, "EAN")
                groups.append(g)
            g.add(it)
        else:
            loose.append(it)

    # indice por bloco (marca, tamanho, pack) dos grupos existentes
    index: dict[tuple, list[Group]] = {}
    for g in groups:
        for m in g.members.values():
            if m.block:
                index.setdefault(m.block, [])
                if g not in index[m.block]:
                    index[m.block].append(g)

    for it in sorted(loose, key=lambda i: i.price_cents):
        if it.block is None:
            continue
        best, best_sim = None, 0.0
        for g in index.get(it.block, []):
            if it.market in g.members and g.members[it.market].product != it.product:
                continue  # a rede ja tem outro produto neste grupo: ambiguo
            peers = [m for m in g.members.values() if m.block == it.block]
            if not peers or not all(it.compatible(m) for m in g.members.values()):
                continue
            # o item precisa ser parecido com TODOS do grupo (evita encadear A~B~C com A!~C)
            sim = min(jaccard(it.toks, m.toks) for m in peers)
            if sim > best_sim:
                best, best_sim = g, sim
        if best is not None and best_sim >= LIMIAR:
            if best.method == "EAN":
                best.method = "EAN + nome"
            best.add(it, best_sim)
        else:
            g = Group(f"nome-{len(groups)}", it.product, None, "nome")
            g.add(it)
            groups.append(g)
            index.setdefault(it.block, []).append(g)
    return groups
