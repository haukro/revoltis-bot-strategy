"""Explicit research cost model. Book snapshots are estimates, not historical L2."""
from math import isfinite

FEE_SCHEDULE = "okx_global_regular"
FEE_TAKER = .001
FEE_MAKER = .0008


def book_costs(book: dict, notional: float, *, role: str = "taker") -> dict:
    if role not in ("taker", "maker") or not isfinite(notional) or notional <= 0:
        raise ValueError("invalid_cost_request")
    asks = [(float(x[0]), float(x[1])) for x in book.get("asks", [])]
    bids = [(float(x[0]), float(x[1])) for x in book.get("bids", [])]
    if not asks or not bids or any(not isfinite(p) or not isfinite(q) or p <= 0 or q <= 0 for p, q in asks + bids):
        raise ValueError("invalid_order_book")
    if asks != sorted(asks) or bids != sorted(bids, reverse=True) or bids[0][0] > asks[0][0]:
        raise ValueError("crossed_or_unsorted_book")
    bid, ask = bids[0][0], asks[0][0]
    mid = (ask + bid) / 2

    def walk(levels):
        # Consume actual base quantities to fill a fixed quote notional.
        remaining, quantity, used = notional, 0.0, 0
        for price, size in levels:
            quote = min(remaining, price * size)
            quantity += quote / price
            remaining -= quote
            used += 1
            if remaining <= 1e-8:
                return notional / quantity, used
        raise ValueError("insufficient_book_depth")

    buy, buy_levels = walk(asks)
    sell, sell_levels = walk(bids)
    half_spread = (ask - bid) / (2 * mid)
    buy_impact, sell_impact = max(0., (buy - ask) / mid), max(0., (bid - sell) / mid)
    fee = FEE_TAKER if role == "taker" else FEE_MAKER
    return {"fee_schedule": FEE_SCHEDULE, "fee_taker": FEE_TAKER, "fee_maker": FEE_MAKER,
            "role": role, "fee_rate": fee, "source": "okx", "book_ts": book.get("ts"),
            "estimate_basis": "current_book_snapshot_not_historical_l2", "notional_usdt": notional,
            "half_spread": half_spread, "buy_impact": buy_impact, "sell_impact": sell_impact,
            "entry_cost_rate": fee + half_spread + buy_impact,
            "exit_cost_rate": fee + half_spread + sell_impact,
            "book_impact_ratio": max(buy_impact, sell_impact),
            "buy_vwap": buy, "sell_vwap": sell, "buy_levels": buy_levels, "sell_levels": sell_levels,
            "ask_l1_usdt": ask * asks[0][1], "bid_l1_usdt": bid * bids[0][1],
            "thin_L1": min(ask * asks[0][1], bid * bids[0][1]) < notional}


def net_return(price_ratio: float, profile: dict) -> float:
    return price_ratio * (1 - profile["exit_cost_rate"]) / (1 + profile["entry_cost_rate"]) - 1
