"""Mantem o banco enxuto na nuvem: guarda so as coletas das ultimas N horas (padrao 26h).

As quedas (hora a hora e 24h) e o painel so precisam desse periodo. O historico completo fica no
banco local do notebook, quando ele sincroniza com a nuvem.
Uso: python scripts/prune_db.py [horas]
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(hours: float = 26) -> None:
    db = ROOT / "data" / "economix.db"
    if not db.exists():
        print("sem banco: nada a podar")
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    c = sqlite3.connect(db)
    # nunca apaga a ultima coleta valida de cada rede, mesmo que antiga
    keep = {r[0] for r in c.execute(
        "SELECT MAX(id) FROM runs WHERE status IN ('ok','partial') GROUP BY market_id")}
    old = [r[0] for r in c.execute("SELECT id FROM runs WHERE started_at < ?", (cutoff,)) if r[0] not in keep]
    for rid in old:
        c.execute("DELETE FROM observations WHERE run_id=?", (rid,))
        c.execute("DELETE FROM runs WHERE id=?", (rid,))
    c.commit()
    c.execute("VACUUM")
    c.close()
    print(f"podadas {len(old)} coletas anteriores a {cutoff[:16]}; banco com {db.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 26)
