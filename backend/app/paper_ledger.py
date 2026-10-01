"""Append-only paper trade ledger helpers.

The simulator recomputes the whole paper history on every refresh and the old
`simulated_trades` rows are overwritten. This module freezes each closed paper
trade the first time it is seen. A frozen row is never updated, so a later
refresh with a different order book cannot change an already recorded result.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def closed_candles_only(candles: list[dict[str, Any]], now_ms: int) -> list[dict[str, Any]]:
    """Drop candles that are not finished yet (close_time in the future)."""
    return [candle for candle in candles if int(candle["close_time"]) <= now_ms]


def ledger_key(paper_label: str, trade: dict[str, Any]) -> str:
    """Stable id from what happened, not from the position in the result list."""
    return f"{paper_label}|{trade['pair']}|{trade['opened_at']}|{trade['closed_at']}"


def ledger_rows(paper_label: str, trades: list[dict[str, Any]], run_id: str | None = None,
                recorded_at: str | None = None) -> list[dict[str, Any]]:
    """Closed trades only; open positions are never written."""
    stamp = recorded_at or datetime.now(UTC).isoformat()
    rows = []
    for trade in trades:
        if trade.get("status") != "closed":
            continue
        raw = trade.get("raw") or {}
        profile = raw.get("cost_components") or {}
        rows.append({
            "id": ledger_key(paper_label, trade),
            "paper_label": paper_label,
            "run_id": run_id,
            "pair": trade["pair"],
            "opened_at": trade["opened_at"],
            "closed_at": trade["closed_at"],
            "entry_rate": trade["entry_rate"],
            "exit_rate": trade["exit_rate"],
            "stake_amount": trade["stake_amount"],
            "profit_usdt": trade["profit_usdt"],
            "exit_reason": trade["exit_reason"],
            "cost_book_ts": profile.get("book_ts"),
            "cost_snapshot": profile,
            "raw": raw,
            "recorded_at": stamp,
        })
    return rows


def new_rows(rows: list[dict[str, Any]], existing_ids: set[str]) -> list[dict[str, Any]]:
    """Rows not yet in the ledger. Existing ids are left untouched."""
    return [row for row in rows if row["id"] not in existing_ids]


def ledger_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = [float(row["profit_usdt"]) for row in rows]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "pnl_usdt": round(sum(pnl), 6),
        "win_rate_percent": round(len(wins) / len(rows) * 100, 2) if rows else None,
        "profit_factor": round(sum(wins) / -sum(losses), 4) if losses else None,
    }
