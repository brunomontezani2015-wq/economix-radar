"""Mantem o banco enxuto na nuvem.

Por rede, guarda so as coletas necessarias para os calculos:
  - a ultima coleta valida (painel e busca)
  - a coleta valida anterior (quedas "desde a coleta anterior", enviadas ao Telegram)
  - a coleta-base de ~24h atras (quedas das ultimas 24h no painel)
O historico completo fica no banco local do notebook.
Uso: python scripts/prune_db.py [horas_da_base=24]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from economix.db import Database  # noqa: E402


def main(hours: float = 24) -> None:
    path = ROOT / "data" / "economix.db"
    if not path.exists():
        print("sem banco: nada a podar")
        return
    db = Database(path)
    keep: set[int] = set()
    with db.conn() as c:
        markets = [r[0] for r in c.execute("SELECT DISTINCT market_id FROM runs")]
    for mid in markets:
        keep.update(db.last_two_runs(mid))
        keep.update(db.run_pair_since(mid, hours))
    c = sqlite3.connect(path)
    old = [r[0] for r in c.execute("SELECT id FROM runs") if r[0] not in keep]
    for rid in old:
        c.execute("DELETE FROM observations WHERE run_id=?", (rid,))
        c.execute("DELETE FROM runs WHERE id=?", (rid,))
    c.commit()
    c.execute("VACUUM")
    c.close()
    print(f"podadas {len(old)} coletas; mantidas {len(keep)}; banco com {path.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 24)
