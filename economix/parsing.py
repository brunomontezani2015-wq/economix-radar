"""Parsers deterministicos para textos brasileiros de oferta.

Todos retornam None quando o texto nao casa com um padrao conhecido -- nunca chutam.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

_PRICE_RE = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*|\d+)(?:,(\d{2}))?")
_SIZE_RE = re.compile(
    r"(?<![\w,])(\d+(?:[.,]\d+)?)\s*(kg|g|gr|mg|ml|l|lt|litros?|un|und|unid(?:ades?)?)\b", re.I
)
_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_UNIT_MAP = {"gr": "g", "lt": "l", "litro": "l", "litros": "l", "und": "un", "unid": "un",
             "unidade": "un", "unidades": "un"}


def parse_price_cents(text: str | None) -> int | None:
    """'R$ 1.234,56' -> 123456. Exige o prefixo R$ para evitar confundir peso com preco."""
    if not text:
        return None
    m = _PRICE_RE.search(text)
    if not m:
        return None
    reais = int(m.group(1).replace(".", ""))
    cents = int(m.group(2)) if m.group(2) else 0
    return reais * 100 + cents


def all_prices_cents(text: str | None) -> list[int]:
    if not text:
        return []
    out = []
    for m in _PRICE_RE.finditer(text):
        out.append(int(m.group(1).replace(".", "")) * 100 + (int(m.group(2)) if m.group(2) else 0))
    return out


def parse_size(text: str | None) -> tuple[float | None, str | None]:
    if not text:
        return None, None
    m = _SIZE_RE.search(text)
    if not m:
        return None, None
    value = float(m.group(1).replace(",", "."))
    unit = m.group(2).lower()
    return value, _UNIT_MAP.get(unit, unit)


def parse_date_br(text: str | None, today: date | None = None) -> date | None:
    """'31/12' ou '31/12/2026'. Sem ano, assume o ano corrente (ou o proximo se a data ja passou ha muito)."""
    if not text:
        return None
    m = _DATE_RE.search(text)
    if not m:
        return None
    today = today or date.today()
    d, mo = int(m.group(1)), int(m.group(2))
    y = m.group(3)
    year = (int(y) + 2000 if len(y) == 2 else int(y)) if y else today.year
    try:
        result = date(year, mo, d)
    except ValueError:
        return None
    if not y and (today - result).days > 180:
        result = date(year + 1, mo, d)
    return result


_PACK_RE = re.compile(
    r"(?:\b(?:pack|kit|caixa|cx|fardo|leve)\s*(?:com|c/)?\s*(\d{1,3})\b"
    r"|\b(\d{1,3})\s*(?:latas|unidades|unid|un|garrafas|long\s*necks?|pacotes|rolos)\b"
    r"|\b(\d{1,3})\s*x\s*\d)", re.I)


def parse_pack_qty(text: str | None) -> int | None:
    """Quantidade de unidades na embalagem, SOMENTE se escrita no nome ('Pack 12 Latas', '8x350ml').
    None = nome nao informa (nao assumimos 1)."""
    if not text:
        return None
    m = _PACK_RE.search(text)
    if not m:
        return None
    n = int(next(g for g in m.groups() if g))
    return n if n > 1 else None


def normalize_name(text: str) -> str:
    """Chave de comparacao: sem acento, minusculo, espacos simples."""
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())
