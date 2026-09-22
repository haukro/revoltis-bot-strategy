"""Store a finished Freqtrade backtest against a Revoltis strategy version.

The script reads an exported Freqtrade result; it does not start a bot and
cannot place orders.  It accepts the common JSON layouts used by Freqtrade.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path
from typing import Any


def result_section(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        return {}
    strategies = document.get("strategy") or document.get("strategies")
    if isinstance(strategies, dict) and strategies:
        return next(iter(strategies.values()))
    return document


def pick(section: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in section and section[key] is not None:
            return section[key]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Uloží hotový Freqtrade backtest do Revoltis Bot Strategy.")
    parser.add_argument("--result", required=True, type=Path, help="Exportovaný JSON výsledok Freqtrade")
    parser.add_argument("--version", required=True, help="ID verzie stratégie z Revoltis")
    parser.add_argument("--timerange", required=True, help="Napr. 20260101-20260301")
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--source", default="Freqtrade backtest")
    arguments = parser.parse_args()
    raw = json.loads(arguments.result.read_text(encoding="utf-8"))
    section = result_section(raw)
    metrics = {
        "total_trades": pick(section, "total_trades", "trade_count"),
        "profit_total_percent": pick(section, "profit_total", "profit_total_pct"),
        "profit_total_usdt": pick(section, "profit_total_abs", "profit_total_usdt"),
        "win_rate_percent": pick(section, "winrate", "win_rate"),
        "max_drawdown_percent": pick(section, "max_drawdown", "max_drawdown_account"),
        "wins": pick(section, "wins"),
        "losses": pick(section, "losses"),
    }
    payload = json.dumps({
        "strategy_version_id": arguments.version, "timerange": arguments.timerange,
        "data_source": arguments.source, "metrics": metrics, "report": raw,
    }).encode()
    request = urllib.request.Request(
        f"{arguments.api.rstrip('/')}/api/backtests", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        print(response.read().decode())


if __name__ == "__main__":
    main()
