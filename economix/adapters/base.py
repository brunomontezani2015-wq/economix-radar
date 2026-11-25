"""Contrato de um adapter de mercado.

Cada mercado e independente: um item em config/markets.yaml + (opcional) um fluxo em
config/flows/<id>.yaml + (opcional) uma classe propria em economix/adapters/<id>.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator

from ..evidence import EvidenceStore
from ..models import Offer, Strategy


class NeedsHuman(RuntimeError):
    """Sessao expirada, login, captcha, atualizacao obrigatoria etc. Nunca contornamos: paramos e avisamos."""


@dataclass
class MarketInfo:
    id: str
    name: str
    app_name: str
    strategy: Strategy = Strategy.UI
    android_package: str | None = None
    enabled: bool = False
    adapter: str = "flow"                 # "flow" (generico via YAML) ou nome de modulo proprio
    notes: str = ""
    web_base: str | None = None           # site oficial (para adapters web/API)
    sources: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # chaves especificas do adapter (ex.: gpa_store_id)


class BaseAdapter(ABC):
    def __init__(self, market: MarketInfo, evidence: EvidenceStore):
        self.market = market
        self.evidence = evidence

    @abstractmethod
    def collect(self) -> Iterator[Offer]:
        """Gera ofertas. Pode levantar NeedsHuman. Ofertas ja geradas sao salvas mesmo se falhar depois."""
