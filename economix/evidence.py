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
        import threading
        self._lock = threading.Lock()

    def save_png(self, data: bytes, label: str = "screen") -> Path:
        self._n += 1
        digest = hashlib.sha256(data).hexdigest()[:12]
        path = self.dir / f"{self._n:04d}_{label}_{digest}.png"
        path.write_bytes(data)
        return path

    def save_json(self, data: bytes, label: str = "api") -> Path:
        """Resposta bruta de API, guardada byte a byte (comprimida em gzip) como evidencia.
        O hash no nome e do conteudo original. Ler com economix.evidence.read_json(path)."""
        import gzip
        with self._lock:
            self._n += 1
            n = self._n
        digest = hashlib.sha256(data).hexdigest()[:12]
        safe = "".join(ch if ch.isalnum() else "_" for ch in label)[:40]
        path = self.dir / f"{n:05d}_{safe}_{digest}.json.gz"
        path.write_bytes(gzip.compress(data, compresslevel=6))
        return path


def read_json(path: str | Path):
    """Le uma evidencia JSON (comprimida .json.gz ou antiga .json)."""
    import gzip
    import json
    p = Path(path)
    raw = gzip.decompress(p.read_bytes()) if p.suffix == ".gz" else p.read_bytes()
    return json.loads(raw)
