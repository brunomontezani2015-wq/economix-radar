"""Armazenamento de evidencias (screenshots) com hash para auditoria e deduplicacao."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path


class EvidenceStore:
    def __init__(self, base: Path, market_id: str, run_id: int):
        stamp = datetime.now().strftime("%Y%m%d")
        self.dir = base / market_id / stamp / f"run{run_id}"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._n = 0

    def save_png(self, data: bytes, label: str = "screen") -> Path:
        self._n += 1
        digest = hashlib.sha256(data).hexdigest()[:12]
        path = self.dir / f"{self._n:04d}_{label}_{digest}.png"
        path.write_bytes(data)
        return path

    def save_json(self, data: bytes, label: str = "api") -> Path:
        """Resposta bruta de API, guardada byte a byte como evidencia (equivale ao screenshot)."""
        self._n += 1
        digest = hashlib.sha256(data).hexdigest()[:12]
        safe = "".join(ch if ch.isalnum() else "_" for ch in label)[:40]
        path = self.dir / f"{self._n:04d}_{safe}_{digest}.json"
        path.write_bytes(data)
        return path
