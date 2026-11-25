"""Modelos de dados.

Regra central: campo ilegivel ou indisponivel fica None. Nunca preencher por inferencia.
Precos sao guardados em centavos (int) para evitar erro de ponto flutuante.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Strategy(str, Enum):
    API = "api"          # API oficial/autorizada
    UI = "ui"            # automacao de interface (ADB/Appium)


class Offer(BaseModel):
    market_id: str
    product: str = Field(min_length=1)
    brand: str | None = None
    size_value: float | None = None       # ex.: 500
    size_unit: str | None = None          # ex.: "g", "kg", "ml", "l", "un"
    ean: str | None = None
    regular_price_cents: int | None = None
    promo_price_cents: int | None = None
    sale_unit: str | None = None          # ex.: "un", "kg", "pct"
    valid_from: date | None = None
    valid_until: date | None = None
    conditions: str | None = None         # ex.: "Leve 3 pague 2", "Exclusivo clube", "Limite 5 un"
    store: str | None = None              # loja/regiao exibida no app, se houver
    evidence_path: str                    # screenshot obrigatorio
    raw_text: str | None = None           # texto bruto lido da tela, para auditoria
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("ean")
    @classmethod
    def _valid_ean(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v.isdigit() or len(v) not in (8, 12, 13, 14) or not ean_checksum_ok(v):
            return None  # EAN invalido nao e registrado
        return v

    @property
    def effective_price_cents(self) -> int | None:
        return self.promo_price_cents if self.promo_price_cents is not None else self.regular_price_cents

    @property
    def missing_fields(self) -> list[str]:
        keys = ["brand", "size_value", "ean", "regular_price_cents", "promo_price_cents", "valid_until"]
        return [k for k in keys if getattr(self, k) is None]


def ean_checksum_ok(code: str) -> bool:
    digits = [int(c) for c in code]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10 == check
