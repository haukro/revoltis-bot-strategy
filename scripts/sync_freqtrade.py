"""Copy Freqtrade dry-run trade history to the Revoltis simulation API.

This script only reads the local SQLite database and sends simulated results.
It cannot place orders and does not use exchange credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def first(row: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return default


def iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, UTC).isoformat()
    return str(value).replace(" ", "T")


def read_trades(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Freqtrade databáza neexistuje: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("select * from trades").fetchall()
    finally:
        connection.close()
    result = []
    for item in rows:
        row = dict(item)
        closed = bool(first(row, "is_open", default=0) == 0)
        result.append({
            "id": f"freqtrade-{first(row, 'id')}",
            "pair": first(row, "pair", default="UNKNOWN/USDT"),
            "status": "closed" if closed else "open",
            "opened_at": iso(first(row, "open_date", "open_date_utc", "date_entry_fill_utc")),
            "closed_at": iso(first(row, "close_date", "close_date_utc")) if closed else None,
            "entry_rate": first(row, "open_rate", "open_rate_requested"),
            "exit_rate": first(row, "close_rate", "close_rate_requested"),
            "stake_amount": first(row, "stake_amount"),
            "profit_usdt": first(row, "close_profit_abs", "realized_profit", default=0) or 0,
            "exit_reason": first(row, "exit_reason"),
            "raw": row,
        })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Synchronizuje iba Freqtrade dry-run výsledky.")
    parser.add_argument("--db", required=True, type=Path, help="Cesta k revoltis-v5-dryrun.sqlite")
    parser.add_argument("--api", default="http://localhost:8000", help="Adresa Revoltis FastAPI")
    parser.add_argument("--token", default=os.getenv("REVOLTIS_SYNC_TOKEN"), help="Synchronizačný token z .env")
    arguments = parser.parse_args()
    payload = json.dumps({"source": "freqtrade-dry-run", "trades": read_trades(arguments.db)}).encode()
    headers = {"Content-Type": "application/json"}
    if arguments.token:
        headers["X-Revoltis-Sync-Token"] = arguments.token
    request = urllib.request.Request(f"{arguments.api.rstrip('/')}/api/ingest/freqtrade", data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        print(response.read().decode())


if __name__ == "__main__":
    main()
