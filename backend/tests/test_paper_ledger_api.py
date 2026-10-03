"""Paper-specific validation must happen before exchange or persistence I/O."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.mark.parametrize("label", ["official", "scale_300_150"])
@pytest.mark.parametrize("use_costs", [False, True])
def test_paper_forced_close_is_rejected_before_exchange_access(label, use_costs):
    with patch.object(main, "load_okx_candles", new=AsyncMock(side_effect=httpx.RequestError("exchange offline"))) as candles, \
         patch.object(main, "append_paper_ledger", new=AsyncMock()) as ledger, \
         patch.object(main, "supabase_upsert", new=AsyncMock()) as save, \
         TestClient(main.app) as client:
        response = client.post("/api/simulations/run", json={
            "settings": {"selected_pairs": ["HYPE/USDT"]},
            "paper_label": label, "force_close_at_end": True,
            "use_current_cost_model": use_costs,
        })
    assert response.status_code == 422
    candles.assert_not_awaited()
    ledger.assert_not_awaited()
    save.assert_not_awaited()


def test_historical_backtest_can_still_force_close_without_a_paper_label():
    result = {"trades": [], "metrics": {}, "equity_curve": [],
              "per_pair_metrics": {}, "per_pair_equity_curves": {}, "rejections": {}}
    with patch.object(main, "load_okx_candles", new=AsyncMock(return_value=[])), \
         patch.object(main, "simulate", return_value=result) as simulate, \
         patch.object(main, "append_paper_ledger", new=AsyncMock()) as ledger, \
         patch.object(main, "supabase_upsert", new=AsyncMock()) as save, \
         TestClient(main.app) as client:
        response = client.post("/api/simulations/run", json={
            "settings": {"selected_pairs": ["HYPE/USDT"]}, "force_close_at_end": True,
        })
    assert response.status_code == 200
    assert simulate.call_args.kwargs["force_close_at_end"] is True
    ledger.assert_not_awaited()
    save.assert_awaited_once()


def ledger_trade(profit, opened="2026-09-26T20:49:59.999+00:00", closed="2026-09-26T22:39:59.999+00:00"):
    return {"id": "simulation-x", "pair": "HYPE/USDT", "status": "closed", "opened_at": opened, "closed_at": closed,
            "entry_rate": 91.2, "exit_rate": 91.9, "stake_amount": 50.0, "profit_usdt": profit,
            "exit_reason": "trailing_profit", "raw": {"cost_components": {"book_ts": "1"}}}


def test_local_store_serialises_simultaneous_overlapping_refreshes(tmp_path, monkeypatch):
    import asyncio
    from app.local_store import LocalStore
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("REVOLTIS_DATA_DIR", str(tmp_path))
    store = LocalStore()
    real_get = main.supabase_get

    async def exercise():
        both_read = asyncio.Barrier(2)

        async def racing_get(table, query=""):
            rows = await real_get(table, query)
            await both_read.wait()  # both requests see the empty ledger
            return rows

        with patch.object(main, "local_store", store), patch.object(main, "supabase_get", new=racing_get):
            return await asyncio.gather(
                main.append_paper_ledger("official", [ledger_trade(.257)], "tab-a"),
                main.append_paper_ledger("official", [ledger_trade(-.4, opened="2026-09-26T21:04:59.999+00:00")], "tab-b"))

    results = asyncio.run(exercise())
    assert len(store.get("paper_trade_ledger")) == 1
    assert sorted(r["overlaps_frozen_trade"] for r in results) == [0, 1]


def test_same_trade_in_official_and_scale_is_not_an_overlap_in_local_mode(tmp_path, monkeypatch):
    import asyncio
    from app.local_store import LocalStore
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.setenv("REVOLTIS_DATA_DIR", str(tmp_path))
    store = LocalStore()
    with patch.object(main, "local_store", store):
        asyncio.run(main.append_paper_ledger("official", [ledger_trade(.257)], "a"))
        scale = asyncio.run(main.append_paper_ledger("scale_300_150", [ledger_trade(.77)], "b"))
    assert (scale["newly_recorded"], scale["overlaps_frozen_trade"]) == (1, 0)
    assert len(store.get("paper_trade_ledger")) == 2


def test_supabase_mode_uses_atomic_rpc_and_maps_overlap_outcomes(monkeypatch):
    import asyncio
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test")
    first = ledger_trade(.257)
    second = ledger_trade(.1, opened="2026-09-27T10:00:00+00:00", closed="2026-09-27T11:00:00+00:00")

    async def rpc(name, payload):
        assert name == "append_paper_trade_ledger"
        ids = [row["id"] for row in payload["p_rows"]]
        return [{"ledger_id": ids[0], "outcome": "inserted"}, {"ledger_id": ids[1], "outcome": "overlap"}]

    with patch.object(main, "supabase_get", new=AsyncMock(return_value=[])), \
         patch.object(main, "supabase_rpc", new=AsyncMock(side_effect=rpc)) as call:
        result = asyncio.run(main.append_paper_ledger("official", [first, second], "run"))
    call.assert_awaited_once()
    assert (result["newly_recorded"], result["overlaps_frozen_trade"]) == (1, 1)


def test_supabase_rpc_missing_outcomes_is_a_failed_write(monkeypatch):
    import asyncio
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test")
    with patch.object(main, "supabase_get", new=AsyncMock(return_value=[])), \
         patch.object(main, "supabase_rpc", new=AsyncMock(return_value=[])):
        with pytest.raises(ValueError, match="paper_ledger_rpc_incomplete"):
            asyncio.run(main.append_paper_ledger("official", [ledger_trade(.257)], "run"))
