"""Small JSON store used until Supabase is configured.

It lets the simulator work locally without credentials.  It stores only
simulated strategies and results; no secrets or exchange credentials.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Lock
from typing import Any


class LocalStore:
    tables = ("strategy_settings", "simulated_trades", "sync_events", "strategy_versions", "backtest_runs", "signal_diagnostics", "simulation_runs", "optimizer_runs")

    def __init__(self) -> None:
        # Vercel functions may write only to their temporary directory.  Real
        # deployments should use Supabase for durable, per-user data.
        default_folder = Path("/tmp/revoltis-runtime") if os.getenv("VERCEL") else Path(__file__).resolve().parents[2] / "runtime_data"
        folder = Path(os.getenv("REVOLTIS_DATA_DIR", str(default_folder)))
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "revoltis-demo.json"
        self.lock = Lock()

    def _read(self) -> dict[str, list[dict[str, Any]]]:
        if not self.path.exists():
            return {table: [] for table in self.tables}
        content = json.loads(self.path.read_text(encoding="utf-8"))
        return {table: content.get(table, []) for table in self.tables}

    def _write(self, content: dict[str, list[dict[str, Any]]]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def get(self, table: str) -> list[dict[str, Any]]:
        with self.lock:
            return list(self._read()[table])

    def upsert(self, table: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self.lock:
            content = self._read()
            indexed = {row["id"]: row for row in content[table]}
            for record in records:
                indexed[record["id"]] = record
            content[table] = list(indexed.values())
            self._write(content)
            return records
